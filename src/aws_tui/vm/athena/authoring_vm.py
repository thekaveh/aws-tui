"""Page-owned local SQL document state, independent of Textual and execution.

QueryVM owns SQL and its origin guards. This collaborator records which local
document that text belongs to, fences asynchronous decisions, and drains file
workers instead of abandoning a possibly published write on cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_authoring import FormatResult, format_sql
from aws_tui.infra.sql_authoring_store import (
    MAX_SQL_BYTES,
    SqlFileStore,
    SqlStorageError,
    SqlWritePermit,
)
from aws_tui.infra.sql_snippet_store import SqlSnippet, SqlSnippetError, SqlSnippetStore
from aws_tui.vm.athena.query_vm import AthenaQueryVM

Confirm = Callable[[str], Awaitable[bool]]
T = TypeVar("T")
P = ParamSpec("P")
_ORIGINS = frozenset(
    {
        "Local editor",
        "Local file",
        "AWS named query",
        "AWS prepared statement",
        "Table query",
        "Local draft",
    }
)


@dataclass(frozen=True, slots=True)
class AuthoringDocumentSnapshot:
    path: str | None = field(default=None, repr=False)
    baseline: str = field(default="", repr=False)
    origin: str = "Local editor"


@dataclass(frozen=True, slots=True)
class _EditorOwner:
    document: int
    revision: int
    context: QueryContext = field(repr=False)
    context_revision: int
    sql: str = field(repr=False)
    recovery_guard_revision: int


def _owned_action(
    action: Callable[Concatenate[AthenaAuthoringVM, P], Awaitable[T]],
) -> Callable[Concatenate[AthenaAuthoringVM, P], Awaitable[T]]:
    """Own the whole logical action, including nested refresh/confirmation."""

    @wraps(action)
    async def run(self: AthenaAuthoringVM, /, *args: P.args, **kwargs: P.kwargs) -> T:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("SQL authoring requires an owned async action")
        self._actions[task] = self._actions.get(task, 0) + 1
        try:
            return await action(self, *args, **kwargs)
        finally:
            remaining = self._actions[task] - 1
            if remaining:
                self._actions[task] = remaining
            else:
                del self._actions[task]

    return run


class AthenaAuthoringVM:
    def __init__(
        self,
        *,
        query: AthenaQueryVM,
        client: Any,
        files: SqlFileStore | None = None,
        snippets: SqlSnippetStore | None = None,
        context_revision: Callable[[], int] = lambda: 0,
        is_current: Callable[[], bool] = lambda: True,
        notify: Callable[[], None] = lambda: None,
    ) -> None:
        self._query = query
        self._client = client
        self._files = files or SqlFileStore()
        self._store = snippets or SqlSnippetStore()
        self._context_revision = context_revision
        self._is_current = is_current
        self._notify = notify
        self._document = AuthoringDocumentSnapshot()
        self._document_id = 0
        self._replacement_revision = 0
        self._status = ""
        self._snippets: tuple[SqlSnippet, ...] = ()
        self._closed = False
        self._action_lock = asyncio.Lock()
        self._io_tasks: set[asyncio.Task[Any]] = set()
        self._permits: set[SqlWritePermit] = set()
        self._actions: dict[asyncio.Task[Any], int] = {}
        self._snippet_generation = 0

    @property
    def path(self) -> Path | None:
        return Path(self._document.path) if self._document.path is not None else None

    @property
    def ownership_revision(self) -> int:
        return self._document_id

    @property
    def replacement_revision(self) -> int:
        """Whole-document history boundary; saving never clears native undo."""
        return self._replacement_revision

    @property
    def dirty(self) -> bool:
        return self._query.sql != self._document.baseline

    @property
    def status(self) -> str:
        return self._status

    @property
    def snippets(self) -> tuple[SqlSnippet, ...]:
        return self._snippets

    def _set_status(self, message: str) -> None:
        self._status = message
        if not self._closed:
            self._notify()

    def report_invalid_path(self) -> None:
        self._set_status("Local SQL path could not be resolved. Choose an accessible .sql path.")

    def _alive(self) -> bool:
        return not self._closed and self._is_current()

    def _can_admit(self) -> bool:
        task = asyncio.current_task()
        return self._alive() and (task is None or task.cancelling() == 0)

    def _owner(self) -> _EditorOwner:
        return _EditorOwner(
            self._document_id,
            self._query.editor_revision,
            self._query.context,
            self._context_revision(),
            self._query.sql,
            self._query._draft_recovery_guard_revision,
        )

    def _owns(self, owner: _EditorOwner, *, text: bool = True) -> bool:
        return (
            self._alive()
            and owner.document == self._document_id
            and owner.context == self._query.context
            and owner.context_revision == self._context_revision()
            and owner.recovery_guard_revision == self._query._draft_recovery_guard_revision
            and (
                not text
                or (owner.revision == self._query.editor_revision and owner.sql == self._query.sql)
            )
        )

    async def _io(
        self, operation: Callable[[], T], permit: SqlWritePermit | None = None
    ) -> tuple[T, bool]:
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._io_tasks.add(task)
        task.add_done_callback(self._io_tasks.discard)
        if permit is not None:
            self._permits.add(permit)
        cancelled = False
        try:
            while True:
                try:
                    return await asyncio.shield(task), cancelled
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                    cancelled = True
                    if permit is not None:
                        permit.cancel()
                    # The real thread may already have published. Drain it and
                    # let the caller acknowledge its actual terminal result.
        finally:
            self._io_tasks.discard(task)
            if permit is not None:
                self._permits.discard(permit)

    @staticmethod
    def snapshot_is_valid(value: object) -> bool:
        if type(value) is not AuthoringDocumentSnapshot:
            return False
        if (
            type(value.baseline) is not str
            or type(value.origin) is not str
            or value.origin not in _ORIGINS
        ):
            return False
        try:
            if len(value.baseline.encode("utf-8")) > MAX_SQL_BYTES:
                return False
            if value.path is not None:
                if type(value.path) is not str or "\x00" in value.path:
                    return False
                value.path.encode("utf-8")
                path = Path(value.path)
                if not path.is_absolute() or path.suffix.lower() != ".sql":
                    return False
            return True
        except (ValueError, UnicodeError):
            return False

    def export_snapshot(self) -> AuthoringDocumentSnapshot:
        return self._document

    def install_snapshot(self, snapshot: AuthoringDocumentSnapshot, *, notify: bool = True) -> None:
        if not self._alive() or not self.snapshot_is_valid(snapshot):
            raise ValueError("SQL document snapshot is unavailable")
        self._document = snapshot
        self._document_id += 1
        self._replacement_revision += 1
        if notify:
            self._notify()

    def _adopt(self, sql: str, *, path: Path | None, origin: str) -> bool:
        proposed = AuthoringDocumentSnapshot(str(path) if path is not None else None, sql, origin)
        if not self.snapshot_is_valid(proposed):
            self._set_status("SQL document exceeds supported authoring limits")
            return False
        owner = self._owner()
        expected_revision = owner.revision + int(sql != owner.sql)
        previous = self._document
        self._document = proposed
        self._document_id += 1
        self._replacement_revision += 1
        adopted_id = self._document_id
        if origin == "Table query":
            self._query.stage_table_document(sql)
        else:
            self._query.set_sql(sql)
        # SQL notification observers run synchronously. Equality alone misses
        # a newer document, A→B→A edit, changed context choice or closed intake.
        if (
            not self._alive()
            or self._document_id != adopted_id
            or self._query.context != owner.context
            or self._context_revision() != owner.context_revision
            or self._query.editor_revision != expected_revision
            or self._query.sql != sql
        ):
            # Do not attach a load to a later edit/context, and never roll
            # back another document installed by a synchronous observer.
            if self._document_id == adopted_id:
                self._document = previous
                self._document_id += 1
                if not self._closed:
                    self._notify()
            return False
        self._notify()
        return True

    @_owned_action
    async def confirm_replacement(
        self,
        *,
        confirm: Confirm,
        still_selected: Callable[[], bool] = lambda: True,
    ) -> bool:
        """Authorize only the exact current editor; never reserve future edits."""
        if not self._can_admit():
            return False
        owner = self._owner()
        if self.dirty and not await confirm("Replace modified SQL in the editor?"):
            return False
        if not self._can_admit() or not self._owns(owner) or not still_selected():
            self._set_status("SQL replacement is no longer current")
            return False
        return True

    @_owned_action
    async def replace_sql(
        self,
        sql: str,
        *,
        confirm: Confirm,
        origin: str,
        still_selected: Callable[[], bool] = lambda: True,
    ) -> bool:
        """The shared guard for user-requested whole-document replacement."""
        if not await self.confirm_replacement(confirm=confirm, still_selected=still_selected):
            return False
        return self._adopt(sql, path=None, origin=origin)

    @_owned_action
    async def format_document(self) -> FormatResult | None:
        """Compute an explicit layout edit off-pump; the view owns native undo."""
        if not self._can_admit():
            return None
        owner = self._owner()
        result, cancelled = await self._io(lambda: format_sql(owner.sql))
        if cancelled or not self._owns(owner):
            return None
        self._set_status(result.reason or "SQL layout formatted")
        return result if self._can_admit() and self._owns(owner) else None

    @_owned_action
    async def open_sql(self, path: Path, *, confirm: Confirm) -> bool:
        if not self._can_admit():
            return False
        owner = self._owner()
        async with self._action_lock:
            if not self._can_admit() or not self._owns(owner):
                return False
            try:
                loaded, cancelled = await self._io(lambda: self._files.read(path))
                if cancelled or not self._owns(owner):
                    self._set_status("SQL Open cancelled or no longer current")
                    return False
                if self.dirty and not await confirm(
                    "Replace modified SQL with the selected local file?"
                ):
                    return False
                if not self._can_admit() or not self._owns(owner):
                    self._set_status("SQL Open is no longer current")
                    return False
                adopted = self._adopt(loaded.sql, path=loaded.path, origin="Local file")
                if adopted:
                    self._set_status("SQL file opened")
                return adopted
            except SqlStorageError as error:
                self._set_status(str(error))
                return False

    @_owned_action
    async def save_sql(self, path: Path | None = None, *, confirm: Confirm) -> bool:
        if not self._can_admit():
            return False
        owner = self._owner()
        target = path if path is not None else self.path
        if target is None:
            self._set_status("Choose a .sql path with Save As")
            return False
        async with self._action_lock:
            if not self._can_admit() or not self._owns(owner):
                return False
            try:
                previous, cancelled = await self._io(lambda: self._files.inspect(target))
                if cancelled or not self._owns(owner):
                    self._set_status("SQL Save cancelled or no longer current")
                    return False
                if previous is not None and not await confirm(
                    "Replace the existing SQL file with this editor text?"
                ):
                    return False
                if not self._can_admit() or not self._owns(owner):
                    self._set_status("SQL Save is no longer current")
                    return False
                permit = SqlWritePermit()
                saved, cancelled = await self._io(
                    lambda: self._files.write(
                        target,
                        owner.sql,
                        expected=previous.version if previous is not None else None,
                        permit=permit,
                    ),
                    permit,
                )
                owned = self._owns(owner, text=False)
                if owned:
                    self._document = AuthoringDocumentSnapshot(
                        str(saved.path), saved.sql, self._document.origin
                    )
                    self._document_id += 1
                message = "SQL file saved before cancellation" if cancelled else "SQL file saved"
                if not owned:
                    message += " for the earlier document"
                if saved.version is None:
                    message += "; inspect its version before the next overwrite"
                self._set_status(message)
                return True
            except SqlStorageError as error:
                self._set_status(str(error))
                return False

    @_owned_action
    async def load_snippets(self) -> bool:
        if not self._can_admit():
            return False
        self._snippet_generation += 1
        generation = self._snippet_generation
        try:
            records, cancelled = await self._io(self._store.list)
            if cancelled or not self._alive() or generation != self._snippet_generation:
                return False
            self._snippets = records
            self._notify()
            return True
        except SqlSnippetError as error:
            if self._alive() and generation == self._snippet_generation:
                self._set_status(str(error))
            return False

    @_owned_action
    async def save_snippet(self, name: str) -> bool:
        if not self._can_admit():
            return False
        owner = self._owner()
        origin = self._document.origin
        provenance = f"{origin} · {owner.context.connection_name} · {owner.context.region}"
        if len(provenance) > 256:
            provenance = provenance[:255] + "…"
        try:
            permit = SqlWritePermit()
            _, cancelled = await self._io(
                lambda: self._store.add(name, owner.sql, provenance=provenance, permit=permit),
                permit,
            )
            if self._alive():
                await self.load_snippets()
            self._set_status(
                "Local snippet saved before cancellation" if cancelled else "Local snippet saved"
            )
            return True
        except SqlSnippetError as error:
            self._set_status(str(error))
            return False

    def snippet_sql(self, snippet_id: str) -> str | None:
        if not self._alive():
            return None
        return next((record.sql for record in self._snippets if record.id == snippet_id), None)

    def close_input_admission(self) -> None:
        self._closed = True
        for permit in tuple(self._permits):
            permit.cancel()
        if self._actions:
            current = asyncio.current_task()
            for task in tuple(self._actions):
                if task is not current and not task.done():
                    task.cancel()

    async def shutdown(self) -> None:
        self.close_input_admission()
        current = asyncio.current_task()
        if current in self._actions:
            # An action/confirmation cleanup can request retirement, but must
            # never join other actions: two such cleanups could await each
            # other. The external page lifecycle owner performs the full drain.
            return
        tasks = tuple(task for task in self._io_tasks | self._actions.keys() if task is not current)
        if not tasks:
            return
        draining = asyncio.gather(*tasks, return_exceptions=True)
        while not draining.done():
            try:
                await asyncio.shield(draining)
            except asyncio.CancelledError:
                continue
