"""Cross-provider stream copy/move.

A :class:`CrossFsCopy` reads from any :class:`FileSystemProvider` and
writes to any other (or the same) provider. The two roles are
symmetric: the same code path runs for local↔local, local↔s3, s3↔s3,
and any future pair.

For directories, the copy is recursive, but ONLY when the destination
provider can publish a directory transactionally. ``_require_directory_transaction``
demands :class:`AtomicNoReplacePublisher`, :class:`AtomicDirectoryPublisher`
and :class:`ExclusiveDirectoryClaimer`; :class:`LocalFS` implements all three
and :class:`S3FS` implements none. Copying a directory TO an S3 destination
therefore raises :class:`ProviderError` before any bytes move, while the
S3-to-local direction works. See ``docs/services/s3.md`` §1.2.

Conflict resolution is configurable per call:

- ``ERROR``: raise :class:`ConflictError` if a destination file exists.
- ``OVERWRITE``: replace whatever is at the destination.
- ``SKIP``: no-op if a destination file exists.
- ``RENAME``: append ``" (1)"``, ``" (2)"``, ... to a conflicting leaf name
  until a free slot is found.

An existing destination directory is a merge root for ``ERROR``, ``SKIP``,
and ``RENAME``; the selected policy is applied independently to each child.
``OVERWRITE`` replaces the complete destination tree when the provider can do
so atomically.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, NoReturn
from uuid import uuid4

from aws_tui.domain.filesystem import (
    AtomicDirectoryPublisher,
    AtomicNoReplacePublisher,
    ConflictError,
    EntryKind,
    ExclusiveDirectoryClaimer,
    FileEntry,
    FileSystemProvider,
    MoveRevisionPreflight,
    NotFoundError,
    PathRef,
    ProgressCallback,
    ProviderError,
    StageManifestEntry,
)

#: Maximum ``" (N)"`` suffixes the ``RENAME`` conflict resolver will try
#: before giving up. 1000 is plenty for any plausible user-driven copy
#: workflow and bounds the loop in pathological mass-collision cases.
_MAX_RENAME_ATTEMPTS: Final[int] = 1000
_MAX_RECURSIVE_ENTRIES: int = 10_000
_MAX_RECURSION_DEPTH: int = 128


@dataclass(slots=True)
class _TraversalBudget:
    entries: int = 0

    def enter(self, path: PathRef, *, depth: int) -> None:
        if depth > _MAX_RECURSION_DEPTH:
            raise ProviderError(f"recursive depth safety limit exceeded at {path.as_posix()}")
        self.entries += 1
        if self.entries > _MAX_RECURSIVE_ENTRIES:
            raise ProviderError(f"recursive entry safety limit exceeded at {path.as_posix()}")


@dataclass(frozen=True, slots=True)
class _DurableResult:
    cancelled: bool
    error: BaseException | None
    value: object | None = None


@dataclass(frozen=True, slots=True)
class _OwnedStage:
    path: PathRef
    revision: str
    kind: EntryKind
    container: PathRef | None = None
    container_revision: str | None = None
    manifest: tuple[StageManifestEntry, ...] = ()


async def _durably_run(
    operation: Coroutine[Any, Any, object],
) -> _DurableResult:
    """Drain a mutation/cleanup and retain caller cancellation plus failure."""
    task = asyncio.create_task(operation)
    current = asyncio.current_task()
    cancellation_count = current.cancelling() if current is not None else 0
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current_count = current.cancelling() if current is not None else 0
            if current_count > cancellation_count:
                cancelled = True
                cancellation_count = current_count
        except BaseException:
            pass
    error: BaseException | None = None
    value: object | None = None
    if task.cancelled():
        error = asyncio.CancelledError()
    else:
        try:
            value = task.result()
        except BaseException as exc:
            error = exc
    return _DurableResult(cancelled=cancelled, error=error, value=value)


def _finish_durable(
    failure: BaseException | None,
    *,
    context: str,
    outcomes: list[_DurableResult],
    ignore_not_found: bool = False,
) -> None:
    """Compose original, cancellation, and drained failures consistently."""
    related = [
        outcome.error
        for outcome in outcomes
        if outcome.error is not None
        and outcome.error is not failure
        and not (ignore_not_found and isinstance(outcome.error, NotFoundError))
    ]
    outcome_cancellation = next(
        (
            outcome.error
            for outcome in outcomes
            if isinstance(outcome.error, asyncio.CancelledError)
        ),
        None,
    )
    cancelled = (
        isinstance(failure, asyncio.CancelledError)
        or outcome_cancellation is not None
        or any(outcome.cancelled for outcome in outcomes)
    )
    if cancelled:
        if isinstance(failure, asyncio.CancelledError):
            cancellation = failure
        elif isinstance(outcome_cancellation, asyncio.CancelledError):
            cancellation = outcome_cancellation
        else:
            cancellation = asyncio.CancelledError()
        related = [error for error in related if error is not cancellation]
        cancellation_details = (
            [failure] if failure is not None and failure is not cancellation else []
        ) + related
        for detail in cancellation_details:
            cancellation.add_note(f"{context}: {type(detail).__name__}: {detail}")
        cause = failure if failure is not None and failure is not cancellation else None
        if cause is None and related:
            cause = related[0]
        raise cancellation from cause
    if related:
        reported = ([failure] if failure is not None else []) + related
        error_details = "; ".join(f"{type(error).__name__}: {error}" for error in reported)
        error = ProviderError(f"{context} failed: {error_details}")
        cause = failure or related[0]
        raise error from cause
    if failure is not None:
        raise failure.with_traceback(failure.__traceback__)


def _raise_durable(
    result: _DurableResult,
    *,
    context: str,
) -> NoReturn:
    _finish_durable(result.error, context=context, outcomes=[result])
    raise AssertionError("durable failure expected")


class ConflictResolution(StrEnum):
    ERROR = "error"
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"


class CrossFsCopy:
    """Streaming copy between two providers (possibly the same one)."""

    def __init__(
        self,
        *,
        source: FileSystemProvider,
        destination: FileSystemProvider,
    ) -> None:
        self._source = source
        self._destination = destination

    async def copy(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None = None,
        on_conflict: ConflictResolution = ConflictResolution.ERROR,
    ) -> bool:
        """Copy ``src`` to ``dst``. Recurses if ``src`` is a directory.

        Notes
        -----
        - ``progress`` is forwarded to the destination's ``write_stream``
          for *each* file copied (not aggregated across a recursive
          subtree — that aggregation is the caller's responsibility).
        - ``on_conflict`` is consulted *before* opening the source stream
          so we don't waste bandwidth.
        """
        return await self._copy_entry(
            src,
            dst,
            progress=progress,
            on_conflict=on_conflict,
            budget=_TraversalBudget(),
            depth=0,
        )

    async def _copy_entry(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None,
        on_conflict: ConflictResolution,
        budget: _TraversalBudget,
        depth: int,
    ) -> bool:
        budget.enter(src, depth=depth)
        src_entry = await self._source.stat(src)
        self._validate_same_storage_destination(src, dst, src_entry.kind)
        if src_entry.kind == EntryKind.DIRECTORY:
            return await self._copy_directory(
                src,
                dst,
                progress=progress,
                on_conflict=on_conflict,
                budget=budget,
                depth=depth,
            )

        if bool(getattr(self._destination, "atomic_write_replaces", False)):
            return await self._copy_file_atomically(
                src,
                dst,
                src_entry=src_entry,
                progress=progress,
                on_conflict=on_conflict,
            )

        publisher = self._require_publisher(EntryKind.FILE, dst)
        effective_dst = await self._resolve_conflict(dst, on_conflict)
        if effective_dst is None:
            return False
        destination_exists = await self._exists(effective_dst)
        staged = await self._write_unique_file_stage(
            publisher,
            src,
            effective_dst,
            total_size=src_entry.size,
            progress=progress,
        )
        return await self._publish_staged(
            publisher,
            staged,
            effective_dst,
            requested_destination=dst,
            destination_exists=destination_exists,
            on_conflict=on_conflict,
        )

    async def _copy_file_atomically(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        src_entry: FileEntry,
        progress: ProgressCallback | None,
        on_conflict: ConflictResolution,
    ) -> bool:
        for _attempt in range(_MAX_RENAME_ATTEMPTS):
            effective_dst = await self._resolve_conflict(dst, on_conflict)
            if effective_dst is None:
                return False
            try:
                await self._copy_file(
                    src,
                    effective_dst,
                    total_size=src_entry.size,
                    progress=progress,
                    overwrite=on_conflict == ConflictResolution.OVERWRITE,
                )
            except ConflictError:
                if on_conflict == ConflictResolution.RENAME:
                    continue
                if on_conflict == ConflictResolution.SKIP:
                    return False
                raise
            return True
        raise ConflictError(f"no available destination name for {dst.as_posix()}")

    async def _write_unique_file_stage(
        self,
        publisher: AtomicNoReplacePublisher,
        src: PathRef,
        destination: PathRef,
        *,
        total_size: int | None,
        progress: ProgressCallback | None,
    ) -> _OwnedStage:
        if not isinstance(self._destination, ExclusiveDirectoryClaimer):
            raise ProviderError(
                "safe file publication requires an exclusive private stage container: "
                f"{destination.as_posix()}"
            )
        claimer = self._destination
        for _attempt in range(_MAX_RENAME_ATTEMPTS):
            container = self._operation_sibling(destination, "stage")
            claimed = await _durably_run(claimer.claim_directory(container))
            if isinstance(claimed.error, ConflictError) and not claimed.cancelled:
                continue
            if claimed.error is not None:
                _raise_durable(claimed, context="file stage container claim")
            if not isinstance(claimed.value, str) or not claimed.value:
                raise ProviderError(
                    f"file stage container has no ownership revision: {container.as_posix()}"
                )
            if claimed.cancelled:
                cleanup = await self._cleanup_empty_claim(container, claimed.value)
                _finish_durable(
                    None,
                    context="file stage container claim",
                    outcomes=[claimed, cleanup],
                    ignore_not_found=True,
                )
            await self._verify_stage_claim(publisher, container, claimed.value)
            staged = container.join("payload")
            try:
                await self._copy_file(
                    src,
                    staged,
                    total_size=total_size,
                    progress=progress,
                    overwrite=False,
                )
            except ConflictError:
                cleanup = await self._cleanup_empty_claim(
                    container,
                    claimed.value,
                )
                _finish_durable(
                    None,
                    context="file stage container cleanup",
                    outcomes=[claimed, cleanup],
                    ignore_not_found=True,
                )
                continue
            except BaseException as exc:
                await self._capture_file_stage(
                    publisher,
                    staged,
                    container=container,
                    failure=exc,
                )
                raise AssertionError("file stage failure should have been raised") from None
            return await self._capture_file_stage(
                publisher,
                staged,
                container=container,
            )
        raise ConflictError(f"no free staging path beside {destination.as_posix()}")

    async def _capture_file_stage(
        self,
        publisher: AtomicNoReplacePublisher,
        staged: PathRef,
        *,
        container: PathRef,
        failure: BaseException | None = None,
    ) -> _OwnedStage:
        captured = await _durably_run(publisher.capture_stage_revision(staged))
        if captured.error is not None:
            container_capture = await _durably_run(publisher.capture_stage_revision(container))
            cleanup = (
                await self._cleanup_empty_claim(container, container_capture.value)
                if isinstance(container_capture.value, str)
                else _DurableResult(cancelled=False, error=container_capture.error)
            )
            _finish_durable(
                failure or captured.error,
                context="file stage ownership capture",
                outcomes=[captured, container_capture, cleanup],
                ignore_not_found=failure is not None,
            )
            raise AssertionError("stage ownership capture should have failed")
        if not isinstance(captured.value, str) or not captured.value:
            missing = ProviderError(f"file stage has no ownership revision: {staged.as_posix()}")
            _finish_durable(
                failure or missing,
                context="file stage ownership capture",
                outcomes=[captured],
            )
            raise AssertionError("stage ownership revision should have been required")

        container_capture = await _durably_run(publisher.capture_stage_revision(container))
        if container_capture.error is not None:
            _finish_durable(
                failure or container_capture.error,
                context="file stage container ownership capture",
                outcomes=[captured, container_capture],
            )
            raise AssertionError("container ownership capture should have failed")
        if not isinstance(container_capture.value, str) or not container_capture.value:
            raise ProviderError(
                f"file stage container has no ownership revision: {container.as_posix()}"
            )
        owned = _OwnedStage(
            staged,
            captured.value,
            EntryKind.FILE,
            container=container,
            container_revision=container_capture.value,
            manifest=(StageManifestEntry(PathRef(()), EntryKind.FILE, captured.value),),
        )
        if failure is not None or captured.cancelled:
            cleanup = await self._cleanup_owned_stage(owned)
            _finish_durable(
                failure,
                context="file stage cleanup",
                outcomes=[captured, container_capture, cleanup],
                ignore_not_found=True,
            )
            raise AssertionError("file stage cleanup should have failed")
        return owned

    async def _copy_file(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        total_size: int | None,
        progress: ProgressCallback | None,
        overwrite: bool,
    ) -> None:
        stream = await self._source.read_stream(src)
        failure: BaseException | None = None
        try:
            await self._destination.write_stream(
                dst,
                stream,
                total_size=total_size,
                progress=progress,
                overwrite=overwrite,
            )
        except BaseException as exc:
            failure = exc

        outcomes: list[_DurableResult] = []
        aclose = getattr(stream, "aclose", None)
        if callable(aclose):
            outcomes.append(await _durably_run(aclose()))
        _finish_durable(
            failure,
            context="file stream cleanup",
            outcomes=outcomes,
        )

    async def _publish_staged(
        self,
        publisher: AtomicNoReplacePublisher,
        staged: _OwnedStage,
        destination: PathRef,
        *,
        requested_destination: PathRef,
        destination_exists: bool,
        on_conflict: ConflictResolution,
    ) -> bool:
        current_destination = destination
        current_exists = destination_exists
        for _attempt in range(_MAX_RENAME_ATTEMPTS):
            if on_conflict == ConflictResolution.OVERWRITE and current_exists:
                await self._commit_overwrite(
                    publisher,
                    staged,
                    current_destination,
                )
                return True

            published = await _durably_run(
                self._atomic_publish(publisher, staged, current_destination)
            )
            if published.error is None:
                container_cleanup = await self._cleanup_published_container(staged)
                _finish_durable(
                    None,
                    context="staged publish",
                    outcomes=[published, container_cleanup],
                    ignore_not_found=True,
                )
                return True
            if isinstance(published.error, ConflictError) and not published.cancelled:
                if not await self._stage_is_owned(publisher, staged):
                    cleanup = await self._cleanup_owned_stage(staged)
                    _finish_durable(
                        ConflictError(f"stage changed: {staged.path.as_posix()}"),
                        context="stage ownership failure",
                        outcomes=[cleanup],
                    )
                if on_conflict == ConflictResolution.RENAME:
                    resolved = await self._resolve_conflict(
                        requested_destination, ConflictResolution.RENAME
                    )
                    assert resolved is not None
                    current_destination = resolved
                    current_exists = False
                    continue
                if on_conflict == ConflictResolution.OVERWRITE:
                    current_exists = True
                    continue
                cleanup = await self._cleanup_owned_stage(staged)
                if on_conflict == ConflictResolution.SKIP:
                    _finish_durable(
                        None,
                        context="file stage cleanup",
                        outcomes=[cleanup],
                        ignore_not_found=True,
                    )
                    return False
                _finish_durable(
                    published.error,
                    context="file stage cleanup",
                    outcomes=[published, cleanup],
                    ignore_not_found=True,
                )
                raise AssertionError("conflict should have been raised")

            cleanup = await self._cleanup_owned_stage(staged)
            _finish_durable(
                published.error,
                context="staged publish",
                outcomes=[published, cleanup],
                ignore_not_found=True,
            )
            raise AssertionError("publish failure should have been raised")

        cleanup = await self._cleanup_owned_stage(staged)
        failure = ConflictError(f"no available destination name for {requested_destination}")
        _finish_durable(
            failure,
            context="file stage cleanup",
            outcomes=[cleanup],
            ignore_not_found=True,
        )
        raise AssertionError("rename exhaustion should have been raised")

    async def _stage_is_owned(
        self,
        publisher: AtomicNoReplacePublisher,
        staged: _OwnedStage,
    ) -> bool:
        if staged.kind == EntryKind.DIRECTORY:
            try:
                await self._verify_stage_manifest(staged)
            except ProviderError:
                return False
            return True
        captured = await _durably_run(publisher.capture_stage_revision(staged.path))
        if captured.error is not None:
            _raise_durable(captured, context="stage ownership verification")
        _finish_durable(None, context="stage ownership verification", outcomes=[captured])
        return captured.value == staged.revision

    async def _atomic_publish(
        self,
        publisher: AtomicNoReplacePublisher,
        staged: _OwnedStage,
        destination: PathRef,
    ) -> str:
        if staged.kind == EntryKind.DIRECTORY:
            if not isinstance(publisher, AtomicDirectoryPublisher):
                raise ProviderError(
                    "transactional directory publication requires manifest validation: "
                    f"{destination.as_posix()}"
                )
            await self._verify_stage_manifest(staged)
            return await publisher.atomic_publish_directory_no_replace(
                staged.path,
                destination,
                expected_manifest=staged.manifest,
            )
        return await publisher.atomic_publish_no_replace(
            staged.path,
            destination,
            expected_source_revision=staged.revision,
        )

    async def _cleanup_owned_stage(self, staged: _OwnedStage) -> _DurableResult:
        return await _durably_run(self._cleanup_stage_manifest(staged))

    async def _cleanup_stage_manifest(self, staged: _OwnedStage) -> None:
        failures: list[BaseException] = []
        entries = sorted(
            staged.manifest,
            key=lambda entry: len(entry.relative_path.segments),
            reverse=True,
        )
        for entry in entries:
            path = self._manifest_path(staged.path, entry.relative_path)
            try:
                if entry.kind == EntryKind.DIRECTORY:
                    observed = await self._destination.stat(path)
                    if observed.kind != entry.kind or observed.etag != entry.revision:
                        raise ConflictError(f"stage changed: {path.as_posix()}")
                    await self._destination.delete_empty_directory(path)
                else:
                    await self._destination.delete(path, expected_etag=entry.revision)
            except NotFoundError:
                continue
            except BaseException as exc:
                failures.append(exc)
        if staged.container is not None:
            try:
                await self._destination.delete_empty_directory(staged.container)
            except NotFoundError:
                pass
            except BaseException as exc:
                failures.append(exc)
        if failures:
            cancellation = next(
                (failure for failure in failures if isinstance(failure, asyncio.CancelledError)),
                None,
            )
            if isinstance(cancellation, asyncio.CancelledError):
                related = [failure for failure in failures if failure is not cancellation]
                for failure in related:
                    cancellation.add_note(
                        f"stage cleanup incomplete: {type(failure).__name__}: {failure}"
                    )
                raise cancellation from (related[0] if related else None)
            details = "; ".join(f"{type(exc).__name__}: {exc}" for exc in failures)
            raise ProviderError(f"stage cleanup incomplete: {details}") from failures[0]

    async def _cleanup_published_container(self, staged: _OwnedStage) -> _DurableResult:
        if staged.container is None:
            return _DurableResult(cancelled=False, error=None)
        return await _durably_run(self._destination.delete_empty_directory(staged.container))

    async def _cleanup_empty_claim(self, path: PathRef, revision: str) -> _DurableResult:
        async def cleanup() -> None:
            observed = await self._destination.stat(path)
            if observed.etag != revision:
                raise ConflictError(f"stage changed: {path.as_posix()}")
            await self._destination.delete_empty_directory(path)

        return await _durably_run(cleanup())

    async def _verify_stage_claim(
        self,
        publisher: AtomicNoReplacePublisher,
        path: PathRef,
        revision: str,
    ) -> None:
        captured = await _durably_run(publisher.capture_stage_revision(path))
        if captured.error is None and captured.value == revision:
            _finish_durable(None, context="stage claim verification", outcomes=[captured])
            return
        failure = captured.error or ProviderError(
            f"stage claim changed before use: {path.as_posix()}"
        )
        cleanup = await self._cleanup_empty_claim(path, revision)
        _finish_durable(
            failure,
            context="stage claim verification",
            outcomes=[captured, cleanup],
            ignore_not_found=True,
        )

    @staticmethod
    def _manifest_path(root: PathRef, relative: PathRef) -> PathRef:
        return PathRef(root.segments + relative.segments)

    async def _verify_stage_manifest(self, staged: _OwnedStage) -> None:
        observed = await self._capture_tree_manifest(staged.path)
        if observed != staged.manifest:
            raise ProviderError(f"stage manifest changed: {staged.path.as_posix()}")

    async def _commit_overwrite(
        self,
        publisher: AtomicNoReplacePublisher,
        staged: _OwnedStage,
        destination: PathRef,
    ) -> None:
        backup: _OwnedStage | None = None
        for _attempt in range(_MAX_RENAME_ATTEMPTS):
            try:
                destination_entry = await self._destination.stat(destination)
            except NotFoundError:
                published = await _durably_run(self._atomic_publish(publisher, staged, destination))
                if published.error is None:
                    container_cleanup = await self._cleanup_published_container(staged)
                    _finish_durable(
                        None,
                        context="overwrite publish",
                        outcomes=[published, container_cleanup],
                        ignore_not_found=True,
                    )
                    return
                if (
                    isinstance(published.error, ConflictError)
                    and not published.cancelled
                    and await self._stage_is_owned(publisher, staged)
                ):
                    continue
                cleanup = await self._cleanup_owned_stage(staged)
                _finish_durable(
                    published.error,
                    context="overwrite publish",
                    outcomes=[published, cleanup],
                    ignore_not_found=True,
                )
                return

            if not publisher.supports_atomic_publish(destination_entry.kind):
                cleanup = await self._cleanup_owned_stage(staged)
                failure = ProviderError(
                    f"atomic overwrite backup is unsupported for {destination.as_posix()}"
                )
                _finish_durable(
                    failure,
                    context="overwrite stage cleanup",
                    outcomes=[cleanup],
                    ignore_not_found=True,
                )
                return
            if destination_entry.etag is None:
                cleanup = await self._cleanup_owned_stage(staged)
                failure = ProviderError(
                    f"overwrite destination has no revision: {destination.as_posix()}"
                )
                _finish_durable(
                    failure,
                    context="overwrite stage cleanup",
                    outcomes=[cleanup],
                    ignore_not_found=True,
                )
                return

            candidate = self._operation_sibling(destination, "backup")
            if destination_entry.kind == EntryKind.DIRECTORY:
                backup_manifest = await self._capture_tree_manifest(destination)
            else:
                backup_manifest = (
                    StageManifestEntry(
                        PathRef(()),
                        destination_entry.kind,
                        destination_entry.etag,
                    ),
                )
            backup_source = _OwnedStage(
                destination,
                destination_entry.etag,
                destination_entry.kind,
                manifest=backup_manifest,
            )
            result = await _durably_run(self._atomic_publish(publisher, backup_source, candidate))
            if isinstance(result.error, (ConflictError, NotFoundError)) and not result.cancelled:
                continue
            if result.error is not None:
                cleanup = await self._cleanup_owned_stage(staged)
                _finish_durable(
                    result.error,
                    context="overwrite backup publish",
                    outcomes=[result, cleanup],
                    ignore_not_found=True,
                )
                return
            if not isinstance(result.value, str) or not result.value:
                cleanup = await self._cleanup_owned_stage(staged)
                failure = ProviderError(
                    f"overwrite backup has no ownership revision: {candidate.as_posix()}"
                )
                _finish_durable(
                    failure,
                    context="overwrite backup publish",
                    outcomes=[result, cleanup],
                    ignore_not_found=True,
                )
                return
            backup = _OwnedStage(
                candidate,
                result.value,
                destination_entry.kind,
                manifest=self._replace_manifest_root_revision(
                    backup_manifest,
                    result.value,
                ),
            )
            if result.cancelled:
                restore = await _durably_run(self._atomic_publish(publisher, backup, destination))
                cleanup = await self._cleanup_owned_stage(staged)
                _finish_durable(
                    asyncio.CancelledError(),
                    context="overwrite rollback",
                    outcomes=[result, restore, cleanup],
                    ignore_not_found=True,
                )
                return
            break
        if backup is None:
            cleanup = await self._cleanup_owned_stage(staged)
            failure = ConflictError(f"overwrite target kept changing: {destination.as_posix()}")
            _finish_durable(
                failure,
                context="overwrite stage cleanup",
                outcomes=[cleanup],
                ignore_not_found=True,
            )
            return

        committed = await _durably_run(self._atomic_publish(publisher, staged, destination))
        if committed.error is not None:
            restore = await _durably_run(self._atomic_publish(publisher, backup, destination))
            cleanup = await self._cleanup_owned_stage(staged)
            _finish_durable(
                committed.error,
                context="overwrite failed and rollback was incomplete",
                outcomes=[committed, restore, cleanup],
                ignore_not_found=True,
            )
            return

        cleanup = await self._cleanup_owned_stage(backup)
        container_cleanup = await self._cleanup_published_container(staged)
        _finish_durable(
            None,
            context="overwrite committed but backup cleanup",
            outcomes=[committed, cleanup, container_cleanup],
            ignore_not_found=True,
        )

    @staticmethod
    def _operation_sibling(path: PathRef, purpose: str) -> PathRef:
        return path.with_name(f".{path.name}.aws-tui-{purpose}-{uuid4().hex}")

    def _require_publisher(self, kind: EntryKind, destination: PathRef) -> AtomicNoReplacePublisher:
        if not isinstance(self._destination, AtomicNoReplacePublisher) or not (
            self._destination.supports_atomic_publish(kind)
        ):
            label = "directory" if kind == EntryKind.DIRECTORY else "file"
            raise ProviderError(
                f"transactional {label} publication is unsupported by this provider: "
                f"{destination.as_posix()}"
            )
        return self._destination

    def _require_directory_transaction(
        self, destination: PathRef
    ) -> tuple[AtomicNoReplacePublisher, ExclusiveDirectoryClaimer]:
        publisher = self._require_publisher(EntryKind.DIRECTORY, destination)
        if not isinstance(self._destination, AtomicDirectoryPublisher):
            raise ProviderError(
                "transactional directory publication requires manifest validation: "
                f"{destination.as_posix()}"
            )
        if not isinstance(self._destination, ExclusiveDirectoryClaimer):
            raise ProviderError(
                "transactional directory publication requires exclusive directory claims: "
                f"{destination.as_posix()}"
            )
        return publisher, self._destination

    async def _exists(self, path: PathRef) -> bool:
        try:
            await self._destination.stat(path)
        except NotFoundError:
            return False
        return True

    async def _copy_directory(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None,
        on_conflict: ConflictResolution,
        budget: _TraversalBudget,
        depth: int,
    ) -> bool:
        try:
            destination_entry = await self._destination.stat(dst)
        except NotFoundError:
            destination_entry = None

        if (
            destination_entry is not None
            and destination_entry.kind == EntryKind.DIRECTORY
            and on_conflict != ConflictResolution.OVERWRITE
        ):
            await self._preflight_directory_merge(
                src,
                dst,
                on_conflict,
                budget=_TraversalBudget(),
                depth=0,
            )
            copied_all = True
            for child in await self._source.list(src):
                copied = await self._copy_entry(
                    src.join(child.name),
                    dst.join(child.name),
                    progress=progress,
                    on_conflict=on_conflict,
                    budget=budget,
                    depth=depth + 1,
                )
                copied_all = copied_all and copied
            return copied_all

        if destination_entry is not None:
            if on_conflict == ConflictResolution.ERROR:
                raise ConflictError(dst.as_posix())
            if on_conflict == ConflictResolution.SKIP:
                return False
            if on_conflict == ConflictResolution.RENAME:
                effective_dst = await self._resolve_conflict(dst, on_conflict)
                assert effective_dst is not None
                destination_exists = False
            else:
                effective_dst = dst
                destination_exists = True
        else:
            effective_dst = dst
            destination_exists = False

        return await self._copy_directory_transaction(
            src,
            effective_dst,
            requested_destination=dst,
            progress=progress,
            on_conflict=on_conflict,
            destination_exists=destination_exists,
        )

    async def _copy_directory_transaction(
        self,
        src: PathRef,
        destination: PathRef,
        *,
        requested_destination: PathRef,
        progress: ProgressCallback | None,
        on_conflict: ConflictResolution,
        destination_exists: bool,
    ) -> bool:
        publisher, claimer = self._require_directory_transaction(destination)

        staged: _OwnedStage | None = None
        for _attempt in range(_MAX_RENAME_ATTEMPTS):
            container = self._operation_sibling(destination, "stage")
            claimed = await _durably_run(claimer.claim_directory(container))
            if isinstance(claimed.error, ConflictError) and not claimed.cancelled:
                continue
            if claimed.error is not None:
                _raise_durable(claimed, context="directory stage claim")
            if not isinstance(claimed.value, str) or not claimed.value:
                raise ProviderError(
                    f"directory claim has no ownership revision: {container.as_posix()}"
                )
            if claimed.cancelled:
                cleanup = await self._cleanup_empty_claim(container, claimed.value)
                _finish_durable(
                    None,
                    context="directory stage claim",
                    outcomes=[claimed, cleanup],
                    ignore_not_found=True,
                )
            await self._verify_stage_claim(publisher, container, claimed.value)
            payload = container.join("payload")
            manifest: list[StageManifestEntry] = []
            try:
                await self._stage_directory_tree(
                    claimer,
                    publisher,
                    src,
                    payload,
                    payload,
                    manifest,
                    progress=progress,
                    budget=_TraversalBudget(),
                    depth=0,
                )
                container_revision = await publisher.capture_stage_revision(container)
            except BaseException as exc:
                partial = _OwnedStage(
                    payload,
                    manifest[0].revision if manifest else claimed.value,
                    EntryKind.DIRECTORY,
                    container=container,
                    container_revision=claimed.value,
                    manifest=tuple(manifest),
                )
                cleanup = await self._cleanup_owned_stage(partial)
                _finish_durable(
                    exc,
                    context="directory stage cleanup",
                    outcomes=[claimed, cleanup],
                    ignore_not_found=True,
                )
                raise AssertionError("directory copy failure should have been raised") from None
            staged = _OwnedStage(
                payload,
                manifest[0].revision,
                EntryKind.DIRECTORY,
                container=container,
                container_revision=container_revision,
                manifest=tuple(manifest),
            )
            break
        if staged is None:
            raise ConflictError(f"no free directory stage beside {destination.as_posix()}")

        return await self._publish_staged(
            publisher,
            staged,
            destination,
            requested_destination=requested_destination,
            destination_exists=destination_exists,
            on_conflict=on_conflict,
        )

    async def _preflight_directory_merge(
        self,
        src: PathRef,
        dst: PathRef,
        on_conflict: ConflictResolution,
        *,
        budget: _TraversalBudget,
        depth: int,
    ) -> None:
        budget.enter(src, depth=depth)
        for child in await self._source.list(src):
            source_path = src.join(child.name)
            destination_path = dst.join(child.name)
            source_entry = await self._source.stat(source_path)
            try:
                destination_entry = await self._destination.stat(destination_path)
            except NotFoundError:
                destination_entry = None

            if source_entry.kind != EntryKind.DIRECTORY:
                budget.enter(source_path, depth=depth + 1)
                if not bool(getattr(self._destination, "atomic_write_replaces", False)):
                    self._require_publisher(EntryKind.FILE, destination_path)
                if destination_entry is not None and on_conflict == ConflictResolution.ERROR:
                    raise ConflictError(destination_path.as_posix())
                continue

            if (
                destination_entry is not None
                and destination_entry.kind == EntryKind.DIRECTORY
                and on_conflict != ConflictResolution.OVERWRITE
            ):
                await self._preflight_directory_merge(
                    source_path,
                    destination_path,
                    on_conflict,
                    budget=budget,
                    depth=depth + 1,
                )
                continue
            if destination_entry is not None and on_conflict == ConflictResolution.ERROR:
                raise ConflictError(destination_path.as_posix())
            if destination_entry is not None and on_conflict == ConflictResolution.SKIP:
                continue
            self._require_directory_transaction(destination_path)
            await self._preflight_absent_tree(
                source_path,
                destination_path,
                budget=budget,
                depth=depth + 1,
            )

    async def _preflight_absent_tree(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        budget: _TraversalBudget,
        depth: int,
    ) -> None:
        budget.enter(src, depth=depth)
        self._require_directory_transaction(dst)
        for child in await self._source.list(src):
            source_path = src.join(child.name)
            destination_path = dst.join(child.name)
            source_entry = await self._source.stat(source_path)
            if source_entry.kind == EntryKind.DIRECTORY:
                await self._preflight_absent_tree(
                    source_path,
                    destination_path,
                    budget=budget,
                    depth=depth + 1,
                )
            else:
                budget.enter(source_path, depth=depth + 1)
            if source_entry.kind != EntryKind.DIRECTORY and not bool(
                getattr(self._destination, "atomic_write_replaces", False)
            ):
                self._require_publisher(EntryKind.FILE, destination_path)

    async def _stage_directory_tree(
        self,
        claimer: ExclusiveDirectoryClaimer,
        publisher: AtomicNoReplacePublisher,
        src: PathRef,
        dst: PathRef,
        root: PathRef,
        manifest: list[StageManifestEntry],
        *,
        progress: ProgressCallback | None,
        budget: _TraversalBudget,
        depth: int,
    ) -> None:
        budget.enter(src, depth=depth)
        revision = await claimer.claim_directory(dst)
        await self._verify_stage_claim(publisher, dst, revision)
        relative = PathRef(dst.segments[len(root.segments) :])
        manifest.append(StageManifestEntry(relative, EntryKind.DIRECTORY, revision))
        for child in await self._source.list(src):
            source_path = src.join(child.name)
            destination_path = dst.join(child.name)
            source_entry = await self._source.stat(source_path)
            if source_entry.kind == EntryKind.DIRECTORY:
                await self._stage_directory_tree(
                    claimer,
                    publisher,
                    source_path,
                    destination_path,
                    root,
                    manifest,
                    progress=progress,
                    budget=budget,
                    depth=depth + 1,
                )
                continue
            budget.enter(source_path, depth=depth + 1)
            failure: BaseException | None = None
            try:
                await self._copy_file(
                    source_path,
                    destination_path,
                    total_size=source_entry.size,
                    progress=progress,
                    overwrite=False,
                )
            except BaseException as exc:
                failure = exc
            captured = await _durably_run(publisher.capture_stage_revision(destination_path))
            if captured.error is None and isinstance(captured.value, str):
                child_relative = PathRef(destination_path.segments[len(root.segments) :])
                manifest.append(
                    StageManifestEntry(child_relative, source_entry.kind, captured.value)
                )
            _finish_durable(
                failure or captured.error,
                context="directory child ownership capture",
                outcomes=[captured],
                ignore_not_found=failure is not None,
            )
        refreshed = await publisher.capture_stage_revision(dst)
        for index, entry in enumerate(manifest):
            if entry.relative_path == relative:
                manifest[index] = StageManifestEntry(relative, EntryKind.DIRECTORY, refreshed)
                break

    async def _capture_tree_manifest(
        self,
        root: PathRef,
        path: PathRef | None = None,
        *,
        _budget: _TraversalBudget | None = None,
        _depth: int = 0,
    ) -> tuple[StageManifestEntry, ...]:
        budget = _budget or _TraversalBudget()
        current = root if path is None else path
        budget.enter(current, depth=_depth)
        entry = await self._destination.stat(current)
        if entry.etag is None:
            raise ProviderError(f"stage entry has no revision: {current.as_posix()}")
        relative = PathRef(current.segments[len(root.segments) :])
        manifest = [StageManifestEntry(relative, entry.kind, entry.etag)]
        if entry.kind == EntryKind.DIRECTORY:
            for child in await self._destination.list(current):
                manifest.extend(
                    await self._capture_tree_manifest(
                        root,
                        current.join(child.name),
                        _budget=budget,
                        _depth=_depth + 1,
                    )
                )
        return tuple(manifest)

    @staticmethod
    def _replace_manifest_root_revision(
        manifest: tuple[StageManifestEntry, ...],
        revision: str,
    ) -> tuple[StageManifestEntry, ...]:
        return tuple(
            StageManifestEntry(entry.relative_path, entry.kind, revision)
            if entry.relative_path.is_root
            else entry
            for entry in manifest
        )

    async def _resolve_conflict(
        self,
        dst: PathRef,
        on_conflict: ConflictResolution,
    ) -> PathRef | None:
        """Decide what to do when ``dst`` may already exist.

        Returns the effective destination, or ``None`` to skip.
        """
        try:
            destination_entry = await self._destination.stat(dst)
        except NotFoundError:
            return dst
        # Destination exists.
        if on_conflict == ConflictResolution.ERROR:
            raise ConflictError(dst.as_posix())
        if on_conflict == ConflictResolution.SKIP:
            return None
        if on_conflict == ConflictResolution.OVERWRITE:
            if destination_entry.kind != EntryKind.FILE:
                raise ConflictError(
                    f"cannot overwrite non-file destination with a file: {dst.as_posix()}"
                )
            return dst
        # RENAME: try " (1)", " (2)", ... up to the safety bound.
        for i in range(1, _MAX_RENAME_ATTEMPTS + 1):
            candidate = dst.with_name(self._rename(dst.name, i))
            try:
                await self._destination.stat(candidate)
            except NotFoundError:
                return candidate
        raise ConflictError(f"could not rename to a free slot: {dst.as_posix()}")

    @staticmethod
    def _rename(name: str, idx: int) -> str:
        """Append ``" (idx)"`` to a filename, preserving the extension."""
        if "." in name and not name.startswith("."):
            stem, _, ext = name.rpartition(".")
            return f"{stem} ({idx}).{ext}"
        return f"{name} ({idx})"

    def _same_storage(self) -> bool:
        if self._source is self._destination:
            return True
        source_identity = getattr(self._source, "storage_identity", None)
        destination_identity = getattr(self._destination, "storage_identity", None)
        return source_identity is not None and source_identity == destination_identity

    def _same_storage_path(self, src: PathRef, dst: PathRef) -> bool:
        return src == dst and self._same_storage()

    def _validate_same_storage_destination(
        self,
        src: PathRef,
        dst: PathRef,
        source_kind: EntryKind,
    ) -> None:
        source_host = self._canonical_host_path(self._source, src)
        destination_host = self._canonical_host_path(self._destination, dst)
        if source_host is not None and destination_host is not None:
            if source_host == destination_host:
                raise ConflictError(f"source and destination are the same path: {src.as_posix()}")
            if source_kind == EntryKind.DIRECTORY and destination_host.is_relative_to(source_host):
                raise ConflictError(f"destination is inside source directory: {dst.as_posix()}")
            return
        if not self._same_storage():
            return
        if src == dst:
            raise ConflictError(f"source and destination are the same path: {src.as_posix()}")
        if (
            source_kind == EntryKind.DIRECTORY
            and len(dst.segments) > len(src.segments)
            and dst.segments[: len(src.segments)] == src.segments
        ):
            raise ConflictError(f"destination is inside source directory: {dst.as_posix()}")

    @staticmethod
    def _canonical_host_path(provider: FileSystemProvider, path: PathRef) -> Path | None:
        resolver = getattr(provider, "canonical_path", None)
        if not callable(resolver):
            return None
        resolved = resolver(path)
        return resolved if isinstance(resolved, Path) else Path(resolved)


class CrossFsMove(CrossFsCopy):
    """Copy + delete source. The source is only deleted after the copy succeeds."""

    async def move(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None = None,
        on_conflict: ConflictResolution = ConflictResolution.ERROR,
    ) -> bool:
        if self._same_storage_path(src, dst):
            raise ConflictError(f"source and destination are the same path: {src.as_posix()}")
        src_entry = await self._source.stat(src)
        self._validate_same_storage_destination(src, dst, src_entry.kind)
        await self._preflight_move_tree(
            src,
            src_entry,
            budget=_TraversalBudget(),
            depth=0,
        )
        return await self._move_entry(
            src,
            dst,
            progress=progress,
            on_conflict=on_conflict,
            budget=_TraversalBudget(),
            depth=0,
        )

    async def _move_entry(
        self,
        src: PathRef,
        dst: PathRef,
        *,
        progress: ProgressCallback | None,
        on_conflict: ConflictResolution,
        budget: _TraversalBudget,
        depth: int,
    ) -> bool:
        budget.enter(src, depth=depth)
        src_entry = await self._source.stat(src)
        if src_entry.kind != EntryKind.DIRECTORY:
            await self._preflight_move_revision(src, src_entry.etag)
            copied = await self.copy(
                src,
                dst,
                progress=progress,
                on_conflict=on_conflict,
            )
            if copied:
                await self._delete_observed_source(src, src_entry.etag)
            return copied

        try:
            destination_entry = await self._destination.stat(dst)
        except NotFoundError:
            destination_entry = None
        if (
            destination_entry is not None
            and destination_entry.kind == EntryKind.DIRECTORY
            and on_conflict != ConflictResolution.OVERWRITE
        ):
            await self._preflight_directory_merge(
                src,
                dst,
                on_conflict,
                budget=_TraversalBudget(),
                depth=0,
            )
            moved_all = True
            for child in await self._source.list(src):
                moved = await self._move_entry(
                    src.join(child.name),
                    dst.join(child.name),
                    progress=progress,
                    on_conflict=on_conflict,
                    budget=budget,
                    depth=depth + 1,
                )
                moved_all = moved_all and moved
            if moved_all:
                await self._source.delete_empty_directory(src)
            return moved_all

        observed_tree = await self._observe_tree(
            src,
            src_entry,
            budget=_TraversalBudget(),
            depth=0,
        )
        copied = await self.copy(src, dst, progress=progress, on_conflict=on_conflict)
        if copied:
            await self._delete_observed_tree(observed_tree)
        return copied

    async def _preflight_move_tree(
        self,
        path: PathRef,
        entry: FileEntry,
        *,
        budget: _TraversalBudget,
        depth: int,
    ) -> None:
        budget.enter(path, depth=depth)
        if entry.kind != EntryKind.DIRECTORY:
            await self._preflight_move_revision(path, entry.etag)
            return
        for child in await self._source.list(path):
            child_path = path.join(child.name)
            child_entry = await self._source.stat(child_path)
            await self._preflight_move_tree(
                child_path,
                child_entry,
                budget=budget,
                depth=depth + 1,
            )

    async def _preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
        if isinstance(self._source, MoveRevisionPreflight):
            await self._source.preflight_move_revision(path, revision)

    async def _observe_tree(
        self,
        path: PathRef,
        entry: FileEntry,
        *,
        budget: _TraversalBudget,
        depth: int,
    ) -> list[tuple[PathRef, FileEntry]]:
        budget.enter(path, depth=depth)
        observed: list[tuple[PathRef, FileEntry]] = []
        for child in await self._source.list(path):
            child_path = path.join(child.name)
            observed_child = await self._source.stat(child_path)
            if observed_child.kind == EntryKind.DIRECTORY:
                observed.extend(
                    await self._observe_tree(
                        child_path,
                        observed_child,
                        budget=budget,
                        depth=depth + 1,
                    )
                )
            else:
                budget.enter(child_path, depth=depth + 1)
                observed.append((child_path, observed_child))
        observed.append((path, entry))
        return observed

    async def _delete_observed_tree(self, observed: list[tuple[PathRef, FileEntry]]) -> None:
        for path, entry in observed:
            if entry.kind == EntryKind.DIRECTORY:
                await self._source.delete_empty_directory(path)
            else:
                await self._delete_observed_source(path, entry.etag)

    async def _delete_observed_source(self, path: PathRef, etag: str | None) -> None:
        if etag is None:
            await self._source.delete(path)
            return
        await self._source.delete(path, expected_etag=etag)


__all__ = ["ConflictResolution", "CrossFsCopy", "CrossFsMove"]
