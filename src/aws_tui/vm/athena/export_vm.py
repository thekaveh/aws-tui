"""Page-owned, reviewed result exports with bounded streaming and local publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeVar

from aws_tui.domain.athena import ResultArtifactAccessError
from aws_tui.domain.athena_export import (
    ExportDataError,
    ExportLimitError,
    ExportLimits,
    ResultEncoder,
)
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ThrottledError,
)
from aws_tui.domain.query import (
    QueryContext,
    QueryExecutionDetail,
    QueryState,
    ResultColumn,
    ResultPage,
)
from aws_tui.domain.s3_uri import parse_s3_uri
from aws_tui.infra.result_export_store import (
    ExportDestination,
    ExportPermit,
    ExportStage,
    ExportStorageError,
    ResultExportStore,
)
from aws_tui.vm.athena.results_vm import AthenaResultsVM

ExportScope = Literal["loaded", "complete"]
ExportFormat = Literal["csv", "json", "original"]
T = TypeVar("T")
_DEFAULT_LIMITS = ExportLimits()
_ACCESS_DENIED_MESSAGE = "Export access denied; check source credentials and result permissions"
_ARTIFACT_ACCESS_MESSAGES = {
    "Athena": "Athena result access denied; check Athena result read permissions",
    "S3": "S3 result access denied; check S3 object read permissions",
    "encryption": "Result encryption access denied; check KMS key permissions",
}


class _UnsupportedOriginalError(Exception):
    def __init__(self, *, requires_complete: bool) -> None:
        super().__init__()
        self.requires_complete = requires_complete


@dataclass(frozen=True, slots=True)
class ExportReview:
    scope: ExportScope
    format: ExportFormat
    destination: ExportDestination = field(repr=False)
    context: QueryContext = field(repr=False)
    execution_id: str = field(repr=False)
    row_count: int | None
    columns: tuple[ResultColumn, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    path: Path = field(repr=False)
    rows: int | None
    bytes_written: int
    scope: ExportScope
    format: ExportFormat
    execution_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Owner:
    execution_id: str = field(repr=False)
    generation: int
    context: QueryContext = field(repr=False)
    revision: int


class AthenaExportVM:
    def __init__(
        self,
        *,
        client: Any,
        results: AthenaResultsVM,
        context: Callable[[], QueryContext],
        context_revision: Callable[[], int] = lambda: 0,
        is_current: Callable[[], bool] = lambda: True,
        notify: Callable[[], None] = lambda: None,
        files: ResultExportStore | None = None,
        limits: ExportLimits = _DEFAULT_LIMITS,
    ) -> None:
        self._client = client
        self._results = results
        self._context = context
        self._context_revision = context_revision
        self._is_current = is_current
        self._notify = notify
        self._files = files or ResultExportStore()
        # Injectable budgets may tighten the write size, never exceed its hard cap.
        self._limits = replace(limits, chunk_bytes=min(limits.chunk_bytes, 64 * 1024))
        self._status = ""
        self._review: ExportReview | None = None
        self._receipt: ExportReceipt | None = None
        self._owner: _Owner | None = None
        self._rows: tuple[tuple[str | None, ...], ...] = ()
        self._detail: QueryExecutionDetail | None = None
        self._requests = 0
        self._prepare_seconds = 0.0
        self._deadline = 0.0
        self._action: asyncio.Task[Any] | None = None
        self._workers: set[asyncio.Task[Any]] = set()
        self._permit: ExportPermit | None = None
        self._cancelled = False
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._action is not None

    @property
    def status(self) -> str:
        return self._status

    @property
    def review(self) -> ExportReview | None:
        return self._review

    @property
    def receipt(self) -> ExportReceipt | None:
        return self._receipt

    def _set_status(self, message: str) -> None:
        self._status = message
        self._changed()

    def _changed(self) -> None:
        # A UI observer cannot unwind resource ownership or hide an actual save.
        if not self._closed:
            with suppress(Exception):
                self._notify()

    def _owns(self) -> bool:
        owner = self._owner
        return (
            owner is not None
            and not self._closed
            and self._is_current()
            and owner.execution_id == self._results.execution_id
            and owner.generation == self._results.projection_generation
            and owner.context == self._context()
            and owner.revision == self._context_revision()
        )

    def _check(self) -> None:
        if asyncio.get_running_loop().time() >= self._deadline:
            raise TimeoutError
        if self._cancelled or not self._owns():
            raise ExportStorageError("cancelled")
        if self._permit is not None:
            self._permit.check()

    def _reserve(self, count: int = 1) -> None:
        self._check()
        if self._requests + count > self._limits.max_requests:
            raise ExportLimitError()
        self._requests += count

    async def _io(self, operation: Callable[[], T]) -> T:
        """Never abandon a worker, including begin() returning a new private stage."""
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._workers.add(task)
        try:
            while True:
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                    self._cancelled = True
                    if self._permit is not None:
                        self._permit.cancel()
        finally:
            self._workers.discard(task)

    def _admit(self) -> bool:
        task = asyncio.current_task()
        if self.busy or self._closed or not self._is_current() or task is None or task.cancelling():
            return False
        self._action = task
        self._cancelled = False
        return True

    async def prepare(
        self, *, scope: ExportScope, format: ExportFormat, path: Path
    ) -> ExportReview | None:
        if not self._admit():
            return None
        self._review = None
        self._receipt = None
        self._rows = ()
        self._detail = None
        self._owner = None
        self._requests = 0
        started = asyncio.get_running_loop().time()
        self._deadline = started + self._limits.timeout_seconds
        try:
            if (
                scope not in ("loaded", "complete")
                or format not in ("csv", "json", "original")
                or not isinstance(path, Path)
                or type(self._results.execution_id) is not str
                or not self._results.execution_id
            ):
                raise ExportDataError()
            if scope == "loaded" and format == "original":
                raise _UnsupportedOriginalError(requires_complete=True)
            self._owner = _Owner(
                self._results.execution_id,
                self._results.projection_generation,
                self._context(),
                self._context_revision(),
            )
            columns = self._results.columns
            rows = self._results.visible_rows if scope == "loaded" else ()
            # The encoder validates positional column metadata without encoding data.
            ResultEncoder(columns, format="csv", limits=self._limits)
            if type(rows) is not tuple:
                raise ExportDataError()
            context = self._owner.context
            self._set_status("Preparing export review")
            async with asyncio.timeout(self._limits.timeout_seconds):
                self._check()
                destination = await self._io(lambda: self._files.inspect(path))
                self._check()
                if scope == "complete":
                    self._reserve()
                    detail = await self._client.get_query_execution(self._owner.execution_id)
                    self._check()
                    self._validate_detail(detail)
                    if format == "original":
                        location = parse_s3_uri(detail.output_location)
                        if (
                            detail.summary.statement_type != "DML"
                            or detail.managed_query_results_enabled
                            or location is None
                            or not location.path.endswith(".csv")
                        ):
                            raise _UnsupportedOriginalError(requires_complete=False)
                    self._detail = detail
                    context = detail.context
                self._check()
                review = ExportReview(
                    scope,
                    format,
                    destination,
                    context,
                    self._owner.execution_id,
                    len(rows) if scope == "loaded" else None,
                    columns,
                )
                self._rows = rows
                self._review = review
                self._set_status("Review the export destination and scope before saving")
                self._check()
                return review
        except asyncio.CancelledError:
            self._set_status("Export cancelled before publication")
        except Exception as error:
            self._failure(error)
        finally:
            self._prepare_seconds = asyncio.get_running_loop().time() - started
            self._action = None
            self._changed()
        self._review = None
        self._rows = ()
        self._detail = None
        return None

    def _validate_detail(self, detail: QueryExecutionDetail) -> None:
        owner = self._owner
        if type(detail) is not QueryExecutionDetail or owner is None:
            raise ExportDataError()
        ref = detail.summary.ref
        expected = owner.context
        if (
            detail.summary.state is not QueryState.SUCCEEDED
            or ref.execution_id != owner.execution_id
            or (ref.connection_name, ref.region, ref.workgroup)
            != (expected.connection_name, expected.region, expected.workgroup)
            or (detail.context.connection_name, detail.context.region, detail.context.workgroup)
            != (ref.connection_name, ref.region, ref.workgroup)
        ):
            raise ExportDataError()
        previous = self._detail
        if previous is not None and (
            detail.summary.ref != previous.summary.ref
            or detail.context != previous.context
            or detail.output_location != previous.output_location
            or detail.managed_query_results_enabled != previous.managed_query_results_enabled
            or detail.summary.statement_type != previous.summary.statement_type
        ):
            raise ExportDataError()

    async def run(self, review: ExportReview, *, overwrite: bool = False) -> ExportReceipt | None:
        if review is not self._review or review is None or not self._admit():
            return None
        self._review = None  # Exact issued review is consumed, even on refusal/failure.
        stage: ExportStage | None = None
        written = 0
        rows: int | None = None
        self._permit = ExportPermit()
        remaining = self._limits.timeout_seconds - self._prepare_seconds
        self._deadline = asyncio.get_running_loop().time() + remaining
        try:
            self._check()
            if review.destination.version is not None and overwrite is not True:
                raise ExportStorageError("exists")
            self._set_status("Export in progress")
            async with asyncio.timeout(max(0, remaining)):
                self._check()
                if review.scope == "complete":
                    if review.format == "original":
                        self._reserve(3)
                    else:
                        self._reserve()
                        detail = await self._client.get_query_execution(review.execution_id)
                        self._check()
                        self._validate_detail(detail)
                permit = self._permit
                stage = await self._io(lambda: self._files.begin(review.destination, permit=permit))
                self._check()
                if review.format == "original":
                    assert self._detail is not None
                    stream = self._client.iter_result_artifact(
                        self._detail,
                        max_bytes=self._limits.max_bytes,
                        chunk_bytes=min(self._limits.chunk_bytes, 64 * 1024),
                    )
                    async with aclosing(stream):
                        async for chunk in stream:
                            self._check()
                            if type(chunk) is not bytes or len(chunk) > min(
                                self._limits.chunk_bytes, 64 * 1024
                            ):
                                raise ExportDataError()
                            if written + len(chunk) > self._limits.max_bytes:
                                raise ExportLimitError()
                            await self._io(partial(stage.write, chunk))
                            written += len(chunk)
                            self._check()
                else:
                    encoder = await self._normalized(review, stage)
                    written, rows = encoder.bytes_written, encoder.rows_written
                self._check()
                await self._io(stage.publish)
        except asyncio.CancelledError:
            self._cancelled = True
            self._permit.cancel()
            self._set_status("Export cancelled before publication")
        except Exception as error:
            self._failure(error)
        finally:
            if stage is not None:
                # Publication is irrevocable. Cleanup failure must not hide its receipt.
                with suppress(Exception, asyncio.CancelledError):
                    await self._io(stage.abort)
            published = stage is not None and stage.published
            if published:
                self._receipt = ExportReceipt(
                    review.destination.path,
                    rows,
                    written,
                    review.scope,
                    review.format,
                    review.execution_id,
                )
                message = "Export saved"
                if self._cancelled or not self._owns():
                    message = "Export saved before cancellation for the reviewed execution"
                self._set_status(message)
            self._permit = None
            self._rows = ()
            self._detail = None
            self._action = None
            self._changed()
        return self._receipt if published else None

    async def _flush(self, stage: ExportStage, buffer: bytearray) -> None:
        self._check()
        if buffer:
            await self._io(partial(stage.write, bytes(buffer)))
            buffer.clear()
            self._check()

    async def _write(self, stage: ExportStage, chunks: Iterable[bytes], buffer: bytearray) -> None:
        """Exhaust one encoder operation, coalescing into one bounded write buffer."""
        for chunk in chunks:
            self._check()
            pending = memoryview(chunk)
            while pending:
                size = min(self._limits.chunk_bytes - len(buffer), len(pending))
                buffer.extend(pending[:size])
                pending = pending[size:]
                if len(buffer) == self._limits.chunk_bytes:
                    await self._flush(stage, buffer)

    async def _normalized(self, review: ExportReview, stage: ExportStage) -> ResultEncoder:
        assert review.format in ("csv", "json")
        buffer = bytearray()
        if review.scope == "loaded":
            encoder = ResultEncoder(review.columns, format=review.format, limits=self._limits)
            await self._write(stage, encoder.start(), buffer)
            for row in self._rows:
                await self._write(stage, encoder.write_row(row), buffer)
                if encoder.rows_written % 128 == 0:
                    await asyncio.sleep(0)
        else:
            token: str | None = None
            seen: set[str] = set()
            encoder = None
            columns: tuple[ResultColumn, ...] | None = None
            while True:
                self._reserve()
                page = await self._client.get_results_page(review.execution_id, start_token=token)
                self._check()
                if type(page) is not ResultPage:
                    raise ExportDataError()
                if columns is None:
                    columns = page.columns
                    encoder = ResultEncoder(columns, format=review.format, limits=self._limits)
                    await self._write(stage, encoder.start(), buffer)
                elif page.columns != columns:
                    raise ExportDataError()
                if (
                    type(page.rows) is not tuple
                    or len(page.rows) > 1000
                    or not all(
                        type(row) is tuple
                        and len(row) == len(columns)
                        and all(cell is None or type(cell) is str for cell in row)
                        for row in page.rows
                    )
                ):
                    raise ExportDataError()
                token = page.next_token
                if token is not None:
                    if type(token) is not str or not 1 <= len(token) <= 1024 or token in seen:
                        raise ExportDataError()
                    seen.add(token)
                assert encoder is not None
                for row in page.rows:
                    await self._write(stage, encoder.write_row(row), buffer)
                    if encoder.rows_written % 128 == 0:
                        await asyncio.sleep(0)
                del page  # Release the previous page before asking for the next one.
                if token is None:
                    break
                # Empty advancing pages also need a cooperative cancellation point.
                await asyncio.sleep(0)
        assert encoder is not None
        await self._write(stage, encoder.finish(), buffer)
        await self._flush(stage, buffer)
        return encoder

    def _failure(self, error: Exception) -> None:
        if isinstance(error, ExportStorageError):
            # Construct the fixed message again; never trust a provider's exception text.
            message = str(ExportStorageError(error.code))
        elif isinstance(error, (ExportLimitError, TimeoutError)):
            message = "Export limit reached; choose a smaller result or loaded view"
        elif isinstance(error, _UnsupportedOriginalError):
            message = (
                "Original AWS CSV requires Complete execution; select Complete execution or normalized CSV/JSON"
                if error.requires_complete
                else "Original AWS CSV is unavailable for this execution; choose normalized CSV/JSON"
            )
        elif isinstance(error, ExportDataError):
            message = "Export data or execution changed; reload results and review again"
        elif isinstance(error, ResultArtifactAccessError):
            service = getattr(error, "service", None)
            message = (
                _ARTIFACT_ACCESS_MESSAGES.get(service, _ACCESS_DENIED_MESSAGE)
                if type(service) is str
                else _ACCESS_DENIED_MESSAGE
            )
        elif isinstance(error, (AuthRequiredError, PermissionDeniedError)):
            message = _ACCESS_DENIED_MESSAGE
        elif isinstance(error, NotFoundError):
            message = "Export results are unavailable or expired; reload the execution"
        elif isinstance(error, ThrottledError):
            message = "Export source is busy; retry later"
        else:
            message = "Export could not be completed; check source access and destination"
        self._set_status(message)

    def cancel(self) -> None:
        already_cancelled = self._cancelled
        self._cancelled = True
        self._review = None
        if self._permit is not None:
            self._permit.cancel()
        task = self._action
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        if task is None:
            # Retire ownership before observers re-enter invalidate(). Modal close
            # after publication must preserve the actual completed outcome.
            self._owner = None
            self._rows = ()
            self._detail = None
            if not already_cancelled and self._receipt is None:
                self._set_status("Export cancelled before publication")

    def invalidate(self) -> None:
        if self._owner is not None and not self._owns():
            self.cancel()

    async def aclose(self) -> None:
        self._closed = True
        self.cancel()
        current = asyncio.current_task()
        if self._action is current:
            return
        tasks = set(self._workers)
        if self._action is not None:
            tasks.add(self._action)
        if tasks:
            draining = asyncio.gather(*tasks, return_exceptions=True)
            while not draining.done():
                try:
                    await asyncio.shield(draining)
                except asyncio.CancelledError:
                    continue


__all__ = ["AthenaExportVM", "ExportFormat", "ExportReceipt", "ExportReview", "ExportScope"]
