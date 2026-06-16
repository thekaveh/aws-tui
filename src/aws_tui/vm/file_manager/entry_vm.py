"""EntryVM — single file/directory entry facade in a file-manager pane.

An entry wraps a :class:`aws_tui.domain.filesystem.FileEntry` (immutable
domain DTO) and two reactive flags: ``is_selected`` (whether the cursor row
is the focused entry) and ``is_marked`` (whether the entry is part of the
multi-select bag the pane will operate on next).

The facade follows the M3 pattern: a VMx ``ComponentVMOf[EntryState]`` is
held as ``self._inner``; the facade exposes typed properties and
``RelayCommand`` instances; ``construct/destruct/dispose`` forward to the
inner VM.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.filesystem import EntryKind, FileEntry


def _format_size(size: int | None, kind: EntryKind) -> str:
    """Human-readable size string. ``<DIR>`` for directories; ``?`` for unknown."""
    if kind is EntryKind.DIRECTORY:
        return "<DIR>"
    if size is None:
        return "?"
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("K", "M", "G", "T"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} P"


def _format_modified(when: datetime | None) -> str:
    if when is None:
        return "-"
    return when.strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True, slots=True)
class EntryState:
    """Immutable model surfaced by ``EntryVM``.

    Parameters
    ----------
    entry:
        The underlying domain :class:`FileEntry`.
    is_selected:
        True when the pane cursor is on this entry. Exactly one entry in a
        pane is selected at any time (or zero when the pane is empty).
    is_marked:
        True when the entry is part of the multi-select bag — independent of
        cursor position. Used by batch operations (copy/move/delete).
    """

    entry: FileEntry
    is_selected: bool = False
    is_marked: bool = False


class EntryVM:
    """Facade for one file/directory entry in a pane."""

    def __init__(
        self,
        *,
        entry: FileEntry,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "entry",
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        initial_state = EntryState(entry=entry, is_selected=False, is_marked=False)
        # Name is unique-enough within a pane (entries within a directory
        # have distinct names). Pane-level uniqueness is the caller's job.
        self._inner: ComponentVMOf[EntryState] = (
            ComponentVMOf[EntryState]
            .builder()
            .name(f"{id_prefix}.{entry.name or '_root'}")
            .model(initial_state)
            .services(hub, dispatcher)
            .build()
        )

        self._toggle_select_command: RelayCommand = (
            RelayCommand.builder().task(self.toggle_select).build()
        )
        self._toggle_mark_command: RelayCommand = (
            RelayCommand.builder().task(self.toggle_mark).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def state(self) -> EntryState:
        return self._inner.model

    @property
    def entry(self) -> FileEntry:
        return self._inner.model.entry

    @property
    def name(self) -> str:
        return self._inner.model.entry.name

    @property
    def kind(self) -> EntryKind:
        return self._inner.model.entry.kind

    @property
    def is_selected(self) -> bool:
        return self._inner.model.is_selected

    @property
    def is_marked(self) -> bool:
        return self._inner.model.is_marked

    # ── Display projections (VM owns formatting — view just renders) ──────

    @property
    def display_name(self) -> str:
        """Filename suffixed with ``/`` on directories so the view doesn't
        branch on :class:`EntryKind`."""
        entry = self._inner.model.entry
        if entry.kind is EntryKind.DIRECTORY and entry.name != "..":
            return f"{entry.name}/"
        return entry.name

    @property
    def size_display(self) -> str:
        entry = self._inner.model.entry
        return _format_size(entry.size, entry.kind)

    @property
    def modified_display(self) -> str:
        return _format_modified(self._inner.model.entry.modified)

    @property
    def mark_glyph(self) -> str:
        """``*`` when marked, space otherwise — multi-select indicator."""
        return "*" if self._inner.model.is_marked else " "

    @property
    def cursor_glyph(self) -> str:
        """Heavy left bar on the selected row; blank otherwise."""
        return "▌" if self._inner.model.is_selected else " "

    @property
    def is_parent_link(self) -> bool:
        """``True`` for the synthetic ``..`` parent-link row."""
        return self._inner.model.entry.name == ".."

    @property
    def is_directory(self) -> bool:
        return self._inner.model.entry.kind is EntryKind.DIRECTORY

    @property
    def toggle_select_command(self) -> RelayCommand:
        return self._toggle_select_command

    @property
    def toggle_mark_command(self) -> RelayCommand:
        return self._toggle_mark_command

    @property
    def inner(self) -> ComponentVMOf[EntryState]:
        """Underlying VMx VM — used by the parent ``CompositeVM`` only."""
        return self._inner

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def vm_name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._toggle_select_command.dispose()
        self._toggle_mark_command.dispose()
        self._inner.dispose()

    # ── State mutators ─────────────────────────────────────────────────────

    def set_selected(self, value: bool) -> None:
        """Imperative setter used by ``PaneVM`` when the cursor moves."""
        if self._inner.model.is_selected == value:
            return
        self._inner.model = replace(self._inner.model, is_selected=value)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "is_selected"))

    def set_marked(self, value: bool) -> None:
        """Imperative setter used by ``PaneVM`` for select-all / clear-marks."""
        if self._inner.model.is_marked == value:
            return
        self._inner.model = replace(self._inner.model, is_marked=value)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "is_marked"))

    def toggle_select(self) -> None:
        self.set_selected(not self._inner.model.is_selected)

    def toggle_mark(self) -> None:
        self.set_marked(not self._inner.model.is_marked)


__all__ = ["EntryState", "EntryVM"]
