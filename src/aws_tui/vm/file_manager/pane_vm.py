"""PaneVM — single Norton-Commander pane facade.

Owns a :class:`FileSystemProvider` and a current :class:`PathRef`. Renders
an observable collection of :class:`EntryVM` children with filter,
multi-select, and reactive :class:`PaneViewModel` projection.

Async-aware operations:

- ``setup()`` populates the initial listing once after construct.
- ``navigate_to(path)`` re-runs the listing under a new path.
- ``refresh()`` re-runs under the current path.

State transitions (LOADING → IDLE / error) happen synchronously around
the async ``provider.list()`` call. Subscribers observe via
``PropertyChangedMessage`` on the hub.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from vmx import (
    ComponentVMOf,
    CompositeVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommand,
    RelayCommandOf,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import (
    EntryKind,
    FileEntry,
    FileSystemProvider,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.entry_vm import EntryState, EntryVM

#: Module-level singleton for the default initial path (root).
_ROOT_PATH: PathRef = PathRef(())


class PaneState(StrEnum):
    """Render state surfaced by ``PaneViewModel`` per spec §7.7."""

    IDLE = "idle"
    LOADING = "loading"
    EMPTY = "empty"
    AUTH_REQUIRED = "auth_required"
    FORBIDDEN = "forbidden"
    UNREACHABLE = "unreachable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PaneViewModel:
    """Immutable projection consumed by the view layer.

    Derived from the underlying entries + cursor + filter + provider state.

    All user-visible strings (``breadcrumb_text``, ``column_header_text``,
    ``placeholder_text``, ``summary``) live here so the view layer owns
    *zero* copy. ``placeholder_severity`` lets the view pick a CSS class
    without branching on :class:`PaneState`.
    """

    breadcrumb: tuple[str, ...]
    state: PaneState
    cursor_index: int
    selection_count: int
    filter_text: str
    error_text: str | None
    summary: str
    breadcrumb_text: str
    column_header_text: str
    placeholder_text: str | None
    placeholder_severity: str  # "", "warning", "error"
    border_title: str  # live path, rendered subtly in the pane's top border
    border_subtitle: str | None  # connection identity, in the bottom border


# State → (user-facing text, severity) — VM-owned per MVVM. Severity maps
# to a CSS class suffix the view appends.
_PLACEHOLDER_FOR_STATE: dict[PaneState, tuple[str, str]] = {
    PaneState.LOADING: ("loading...", ""),
    PaneState.EMPTY: ("empty", ""),
    PaneState.AUTH_REQUIRED: (
        "auth needed - press a to sign in (or `aws sso login --profile <name>`)",
        "warning",
    ),
    PaneState.FORBIDDEN: (
        "access denied\n\n"
        "Possible causes:\n"
        "  - No AWS credentials configured.\n"
        "    Run `aws configure` or `aws configure sso` and relaunch.\n"
        "  - Credentials are valid but the IAM principal lacks\n"
        "    `s3:ListAllMyBuckets` (root listing) or `s3:ListBucket`\n"
        "    (object listing). Check IAM policy on the profile.\n"
        "  - Expired SSO token. Run `aws sso login --profile <name>`.\n\n"
        "Press r to retry. Press ? for the full keymap.",
        "error",
    ),
    PaneState.UNREACHABLE: (
        "endpoint unreachable - press r to retry; check network / VPN / endpoint URL",
        "warning",
    ),
    PaneState.ERROR: ("error", "error"),
}


_COLUMN_HEADER_TEXT: str = f"   {'NAME':<40} {'SIZE':>12}  {'MODIFIED':<18}"


def _summary_text(*, count: int, marked: int, total_bytes: int) -> str:
    """Build the canonical summary line (matches spec §4.x)."""
    if count == 0:
        return "empty"
    size_label = _human_bytes(total_bytes)
    if marked > 0:
        return f"{count} obj · {marked} marked · {size_label}"
    return f"{count} obj · {size_label}"


def _human_bytes(n: int) -> str:
    """Compact byte size used by the summary line."""
    if n < 1024:
        return f"{n} B"
    units = ("K", "M", "G", "T", "P")
    value = float(n)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} E"


def _filter_matches(name: str, query: str) -> bool:
    if not query:
        return True
    return query.casefold() in name.casefold()


class PaneVM:
    """Facade for one file-manager pane."""

    def __init__(
        self,
        *,
        provider: FileSystemProvider,
        initial_path: PathRef = _ROOT_PATH,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "pane",
        identity_label: str | None = None,
        path_protocol: str = "",
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._provider: FileSystemProvider = provider
        self._path: PathRef = initial_path
        self._id_prefix: str = id_prefix
        # Connection identity rendered in the bottom border subtitle
        # (e.g. ``aws · sso-dev · us-east-1`` for the left pane). Stable
        # across the session.
        self._identity_label: str | None = identity_label
        # Scheme prefix prepended to the path label (e.g. ``s3:``). The
        # rendered title becomes ``s3://bucket/folder`` for S3 and just
        # ``/Users/kaveh/...`` for local.
        self._path_protocol: str = path_protocol

        self._entries: list[EntryVM] = []
        self._filtered: tuple[int, ...] = ()  # indices into self._entries
        self._cursor_index: int = 0
        self._filter_text: str = ""
        self._state: PaneState = PaneState.IDLE
        self._error_text: str | None = None
        self._is_multiselect_mode: bool = False

        self._inner: CompositeVM[ComponentVMOf[EntryState]] = (
            CompositeVM[ComponentVMOf[EntryState]]
            .builder()
            .name(id_prefix)
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

        # ── Commands ────────────────────────────────────────────────────────
        self._open_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._cursor_target() is not None)
            .task(self._open_cursor_sync)
            .build()
        )
        self._ascend_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: not self._path.is_root)
            .task(self._ascend_sync)
            .build()
        )
        self._refresh_command: RelayCommand = (
            RelayCommand.builder().task(self._refresh_sync).build()
        )
        self._move_cursor_command: RelayCommandOf[int] = (
            RelayCommandOf[int]
            .builder()
            .predicate(lambda _delta: len(self._filtered) > 0)
            .task(self._move_cursor)
            .build()
        )
        self._toggle_select_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._cursor_target() is not None)
            .task(self._toggle_select_cursor)
            .build()
        )
        self._enter_multiselect_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: not self._is_multiselect_mode)
            .task(lambda: self._set_multiselect(True))
            .build()
        )
        self._exit_multiselect_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: self._is_multiselect_mode)
            .task(lambda: self._set_multiselect(False))
            .build()
        )
        self._select_all_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: len(self._filtered) > 0)
            .task(self._select_all)
            .build()
        )
        self._clear_selection_command: RelayCommand = (
            RelayCommand.builder()
            .predicate(lambda: any(e.is_marked for e in self._entries))
            .task(self._clear_marks)
            .build()
        )
        self._set_filter_command: RelayCommandOf[str] = (
            RelayCommandOf[str].builder().task(self._set_filter_text).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def provider(self) -> FileSystemProvider:
        return self._provider

    @property
    def path(self) -> PathRef:
        return self._path

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def is_multiselect_mode(self) -> bool:
        return self._is_multiselect_mode

    @property
    def cursor_index(self) -> int:
        return self._cursor_index

    @property
    def filter_text(self) -> str:
        return self._filter_text

    @property
    def entries(self) -> tuple[EntryVM, ...]:
        return tuple(self._entries)

    @property
    def filtered_entries(self) -> tuple[EntryVM, ...]:
        return tuple(self._entries[i] for i in self._filtered)

    @property
    def selected_entry(self) -> EntryVM | None:
        return self._cursor_target()

    @property
    def marked_entries(self) -> tuple[EntryVM, ...]:
        return tuple(e for e in self._entries if e.is_marked)

    @property
    def viewmodel(self) -> PaneViewModel:
        marked = sum(1 for e in self._entries if e.is_marked)
        total_bytes = sum((e.entry.size or 0) for e in self._entries)
        placeholder, severity = self._placeholder_for_current_state()
        return PaneViewModel(
            breadcrumb=self._path.segments,
            state=self._state,
            cursor_index=self._cursor_index,
            selection_count=marked,
            filter_text=self._filter_text,
            error_text=self._error_text,
            summary=_summary_text(count=len(self._entries), marked=marked, total_bytes=total_bytes),
            breadcrumb_text="/" if self._path.is_root else "/" + "/".join(self._path.segments),
            column_header_text=_COLUMN_HEADER_TEXT,
            placeholder_text=placeholder,
            placeholder_severity=severity,
            border_title=self._format_border_title(),
            border_subtitle=self._identity_label,
        )

    def _format_border_title(self) -> str:
        """Render the path label that lives in the pane's top border.

        Format:

        - With protocol ``s3:``: ``s3://`` (root), ``s3://bucket/folder``.
        - Without protocol: ``/`` (root), ``/Users/kaveh/repos``.
        """
        base = "/" + "/".join(self._path.segments) if not self._path.is_root else "/"
        if self._path_protocol:
            return f"{self._path_protocol}/{base}" if base != "/" else f"{self._path_protocol}//"
        return base

    def _placeholder_for_current_state(self) -> tuple[str | None, str]:
        if self._state == PaneState.IDLE:
            return None, ""
        text, severity = _PLACEHOLDER_FOR_STATE.get(self._state, ("error", "error"))
        if self._error_text:
            text = f"{text}: {self._error_text}"
        return text, severity

    # ── Commands ────────────────────────────────────────────────────────────

    @property
    def open_command(self) -> RelayCommand:
        return self._open_command

    @property
    def ascend_command(self) -> RelayCommand:
        return self._ascend_command

    @property
    def refresh_command(self) -> RelayCommand:
        return self._refresh_command

    @property
    def move_cursor_command(self) -> RelayCommandOf[int]:
        return self._move_cursor_command

    @property
    def toggle_select_command(self) -> RelayCommand:
        return self._toggle_select_command

    @property
    def enter_multiselect_command(self) -> RelayCommand:
        return self._enter_multiselect_command

    @property
    def exit_multiselect_command(self) -> RelayCommand:
        return self._exit_multiselect_command

    @property
    def select_all_command(self) -> RelayCommand:
        return self._select_all_command

    @property
    def clear_selection_command(self) -> RelayCommand:
        return self._clear_selection_command

    @property
    def set_filter_command(self) -> RelayCommandOf[str]:
        return self._set_filter_command

    # ── VMx lifecycle accessors ─────────────────────────────────────────────

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._open_command.dispose()
        self._ascend_command.dispose()
        self._refresh_command.dispose()
        self._move_cursor_command.dispose()
        self._toggle_select_command.dispose()
        self._enter_multiselect_command.dispose()
        self._exit_multiselect_command.dispose()
        self._select_all_command.dispose()
        self._clear_selection_command.dispose()
        self._set_filter_command.dispose()
        for child in self._entries:
            child.dispose()
        self._entries.clear()
        self._inner.dispose()

    # ── Async operations ────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Load the initial directory listing under the current path."""
        await self._reload()

    async def navigate_to(self, path: PathRef) -> None:
        """Replace ``path`` and re-list."""
        self._path = path
        self._cursor_index = 0
        self._filter_text = ""
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "path"))
        await self._reload()

    async def refresh(self) -> None:
        await self._reload()

    async def activate(self, target_index: int) -> None:
        """Activate the entry at ``target_index`` in :attr:`filtered_entries`.

        Encapsulates the ".." / directory / file dispatch + path arithmetic
        so the view never reaches into :class:`PathRef`. Behavior:

        - ``..`` (synthetic parent link) — ascend if not already at root.
        - Directory — navigate into ``self.path.join(name)``.
        - File — emit ``preview_requested`` for a higher-level VM to handle.

        ``target_index`` out of range is silently ignored.
        """
        if not (0 <= target_index < len(self._filtered)):
            return
        entry_vm = self._entries[self._filtered[target_index]]
        entry = entry_vm.entry
        if entry_vm.is_parent_link:
            if not self._path.is_root:
                await self.navigate_to(self._path.parent())
            return
        if entry.kind is EntryKind.DIRECTORY:
            await self.navigate_to(self._path.join(entry.name))
            return
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "preview_requested"))

    def move_cursor_to(self, target_index: int) -> None:
        """Place the cursor directly at ``target_index`` (clamped). Used by
        view-side input adapters that translate a click coordinate into a
        filtered-row index without computing a delta themselves."""
        if not self._filtered:
            return
        clamped = max(0, min(target_index, len(self._filtered) - 1))
        if clamped == self._cursor_index:
            return
        self._cursor_index = clamped
        self._sync_cursor_selection()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "cursor_index"))
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    async def delete_marked(self) -> None:
        """Delete every marked entry; refresh on success."""
        targets = [e.entry.name for e in self._entries if e.is_marked]
        if not targets:
            return
        for name in targets:
            await self._provider.delete(self._path.join(name))
        await self._reload()

    async def make_directory(self, name: str) -> None:
        if not name:
            return
        await self._provider.mkdir(self._path.join(name))
        await self._reload()

    async def rename_cursor(self, new_name: str) -> None:
        target = self._cursor_target()
        if target is None or not new_name:
            return
        old_path = self._path.join(target.entry.name)
        new_path = self._path.join(new_name)
        await self._provider.rename(old_path, new_path)
        await self._reload()

    # ── External error injection ────────────────────────────────────────────

    def set_auth_required(self) -> None:
        """Called by ``RootVM`` after observing ``AuthExpiredMessage``."""
        self._set_state(PaneState.AUTH_REQUIRED)

    # ── Internal: listing & state ───────────────────────────────────────────

    async def _reload(self) -> None:
        self._set_state(PaneState.LOADING)
        try:
            raw = await self._provider.list(self._path)
        except NotFoundError as exc:
            # Root listing: an unknown path at root means empty (e.g. an
            # empty bucket); deeper paths surface as ERROR.
            if self._path.is_root:
                self._replace_entries([])
                self._set_state(PaneState.EMPTY)
            else:
                self._error_text = str(exc) or None
                self._replace_entries([])
                self._set_state(PaneState.ERROR)
            return
        except PermissionDeniedError as exc:
            self._error_text = str(exc) or None
            self._replace_entries([])
            self._set_state(PaneState.FORBIDDEN)
            return
        except ProviderUnreachableError as exc:
            self._error_text = str(exc) or None
            self._replace_entries([])
            self._set_state(PaneState.UNREACHABLE)
            return
        except ProviderError as exc:
            self._error_text = str(exc) or None
            self._replace_entries([])
            self._set_state(PaneState.ERROR)
            return

        self._error_text = None
        # Prepend a synthetic ".." parent entry on any non-root path so the
        # user can navigate up via Enter / mouse / single keystroke without
        # remembering Backspace.
        materialized: list[FileEntry] = list(raw)
        if not self._path.is_root:
            materialized.insert(
                0,
                FileEntry(name="..", kind=EntryKind.DIRECTORY, size=None, modified=None),
            )
        self._replace_entries(
            [
                EntryVM(
                    entry=fe,
                    hub=self._hub,
                    dispatcher=self._dispatcher,
                    id_prefix=f"{self._id_prefix}.entry",
                )
                for fe in materialized
            ]
        )
        # IDLE if at least one real entry; EMPTY only if neither real entries
        # nor a ".." row is present (i.e. truly root + empty bucket).
        self._set_state(PaneState.IDLE if materialized else PaneState.EMPTY)

    def _replace_entries(self, new_entries: list[EntryVM]) -> None:
        for child in self._entries:
            if child.inner in self._inner:
                self._inner.remove(child.inner)
            child.dispose()
        self._entries = new_entries
        for child in self._entries:
            if self._inner.is_constructed:
                child.construct()
            self._inner.append(child.inner)
        self._cursor_index = 0
        self._recompute_filtered()
        self._sync_cursor_selection()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "entries"))
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    def _set_state(self, value: PaneState) -> None:
        if self._state == value:
            return
        self._state = value
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "state"))
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    # ── Cursor / selection / filter ─────────────────────────────────────────

    def _cursor_target(self) -> EntryVM | None:
        if not self._filtered:
            return None
        idx = max(0, min(self._cursor_index, len(self._filtered) - 1))
        return self._entries[self._filtered[idx]]

    def _move_cursor(self, delta: int | None) -> None:
        if delta is None or not self._filtered:
            return
        new_index = self._cursor_index + delta
        new_index = max(0, min(new_index, len(self._filtered) - 1))
        if new_index == self._cursor_index:
            return
        self._cursor_index = new_index
        self._sync_cursor_selection()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "cursor_index"))
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    def _sync_cursor_selection(self) -> None:
        target = self._cursor_target()
        for entry in self._entries:
            entry.set_selected(entry is target)

    def _toggle_select_cursor(self) -> None:
        target = self._cursor_target()
        if target is None:
            return
        if not self._is_multiselect_mode:
            self._set_multiselect(True)
        target.toggle_mark()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    def _set_multiselect(self, value: bool) -> None:
        if self._is_multiselect_mode == value:
            return
        self._is_multiselect_mode = value
        if not value:
            self._clear_marks()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "is_multiselect_mode"))

    def _select_all(self) -> None:
        if not self._filtered:
            return
        if not self._is_multiselect_mode:
            self._set_multiselect(True)
        for idx in self._filtered:
            self._entries[idx].set_marked(True)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    def _clear_marks(self) -> None:
        for entry in self._entries:
            entry.set_marked(False)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    def _set_filter_text(self, text: str | None) -> None:
        new = text or ""
        if new == self._filter_text:
            return
        self._filter_text = new
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "filter_text"))
        self._recompute_filtered()
        self._cursor_index = 0
        self._sync_cursor_selection()
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "viewmodel"))

    def _recompute_filtered(self) -> None:
        if not self._filter_text:
            self._filtered = tuple(range(len(self._entries)))
            return
        self._filtered = tuple(
            i
            for i, e in enumerate(self._entries)
            if _filter_matches(e.entry.name, self._filter_text)
        )

    # ── Command bridges (sync triggers that delegate to async work) ────────

    def _open_cursor_sync(self) -> None:
        """Synchronous bridge for the open_command.

        For directories we cannot await here (RelayCommand task is sync).
        Callers needing the navigation result must invoke ``navigate_to``
        from an async context. The command is convenient for the cases
        where the caller is already inside an async runtime (CommandPalette,
        keymap router) and wraps the call accordingly.
        """
        target = self._cursor_target()
        if target is None:
            return
        if target.kind == EntryKind.DIRECTORY:
            # Defer: the async navigation must be driven externally.
            # We publish a PropertyChangedMessage so a higher-level VM can
            # schedule navigate_to(...).
            self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "open_requested"))
        else:
            self._hub.send(
                PropertyChangedMessage.create(self, self._inner.name, "preview_requested")
            )

    def _ascend_sync(self) -> None:
        if self._path.is_root:
            return
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "ascend_requested"))

    def _refresh_sync(self) -> None:
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "refresh_requested"))

    # ── Composite children factory ─────────────────────────────────────────

    def _initial_children(self) -> Iterable[ComponentVMOf[EntryState]]:
        return tuple(e.inner for e in self._entries)


__all__ = ["PaneState", "PaneVM", "PaneViewModel"]
