"""Pane widget — single Norton-Commander column bound to :class:`PaneVM`.

Strict MVVM: the view computes nothing user-facing. All display strings
(``display_name``, ``size_display``, ``modified_display``, ``mark_glyph``,
``cursor_glyph``, ``breadcrumb_text``, ``column_header_text``,
``placeholder_text``, ``placeholder_severity``) live on the VM. The view
just lays them out and routes input to VM commands.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.file_manager.entry_vm import EntryVM
from aws_tui.vm.file_manager.pane_vm import PaneVM


class EntryRow(Widget):
    """One entry row in a pane — bound to a single :class:`EntryVM`."""

    DEFAULT_CSS = """
    EntryRow {
        height: 1;
    }
    """

    def __init__(
        self,
        entry_vm: EntryVM,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = " ".join(c for c in (classes, "entry-row") if c)
        super().__init__(id=id, classes=merged)
        self._entry_vm = entry_vm

    @property
    def entry_vm(self) -> EntryVM:
        return self._entry_vm

    def render(self) -> Text:
        vm = self._entry_vm
        cursor_style = "bold cyan" if vm.is_selected else ""
        text = Text()
        text.append(vm.cursor_glyph, style=cursor_style)
        text.append(
            f"{vm.mark_glyph} {vm.display_name:<32} {vm.size_display:>10}  {vm.modified_display}"
        )
        return text

    async def on_click(self, _event: object) -> None:
        """Mouse click on a row.

        - First click on an unfocused row: switches pane focus + moves the
          cursor to this row (no domain reasoning here — pure index math).
        - Click on the already-selected row: delegates to
          :meth:`PaneVM.activate`, which owns the ``..``/dir/file dispatch.
        """
        host: Pane | None = None
        node: object | None = self
        while node is not None:
            if isinstance(node, Pane):
                host = node
                break
            node = getattr(node, "parent", None)
        if host is None:
            return

        await host.on_click(_event)

        filtered = host.vm.filtered_entries
        try:
            target_index = filtered.index(self._entry_vm)
        except ValueError:
            return

        if not self._entry_vm.is_selected:
            host.vm.move_cursor_to(target_index)
            return

        await host.vm.activate(target_index)

    def update_state(self) -> None:
        """Sync CSS classes to mirror VM flags (purely cosmetic)."""
        if self._entry_vm.is_selected:
            self.add_class("-selected")
        else:
            self.remove_class("-selected")
        if self._entry_vm.is_marked:
            self.add_class("-marked")
        else:
            self.remove_class("-marked")
        if self._entry_vm.is_directory:
            self.add_class("-dir")
        else:
            self.remove_class("-dir")
        self.refresh()


class Pane(HubSubscriberMixin, Widget):
    """Single file-manager pane."""

    DEFAULT_CSS = """
    Pane {
        layout: vertical;
        height: 1fr;
    }
    """

    PROP_NAMES: ClassVar[frozenset[str]] = frozenset(
        {"entries", "viewmodel", "state", "cursor_index", "filter_text", "path"}
    )

    def __init__(
        self,
        vm: PaneVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: PaneVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> PaneVM:
        return self._vm

    def compose(self) -> ComposeResult:
        vm = self._vm.viewmodel
        yield Static(vm.breadcrumb_text, classes="breadcrumb")
        yield Static(vm.column_header_text, classes="column-header")
        yield Vertical(id="pane-body")
        yield Static(vm.summary, classes="pane-footer")

    def on_mount(self) -> None:
        self._render_body()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def set_focused(self, value: bool) -> None:
        if value:
            self.add_class("-focused")
        else:
            self.remove_class("-focused")

    async def on_click(self, _event: object) -> None:
        """Clicking anywhere in a pane switches focus to it (when applicable)."""
        node: object | None = self
        while node is not None:
            if type(node).__name__ == "DualPane":
                dual_vm = getattr(node, "_vm", None)
                if dual_vm is None:
                    return
                from aws_tui.vm.file_manager.dual_pane_vm import FocusedPane

                want = FocusedPane.LEFT if self._vm is dual_vm.left else FocusedPane.RIGHT
                if dual_vm.focused is not want:
                    dual_vm.switch_focus_command.execute()
                return
            node = getattr(node, "parent", None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name in self.PROP_NAMES:
            self.call_after_refresh(self._refresh_all)

    def _refresh_all(self) -> None:
        try:
            breadcrumb = self.query_one(".breadcrumb", Static)
            header = self.query_one(".column-header", Static)
            footer = self.query_one(".pane-footer", Static)
        except Exception:
            return
        vm = self._vm.viewmodel
        breadcrumb.update(vm.breadcrumb_text)
        header.update(vm.column_header_text)
        footer.update(vm.summary)
        self._render_body()

    def _render_body(self) -> None:
        try:
            body = self.query_one("#pane-body", Vertical)
        except Exception:
            return
        for child in list(body.children):
            child.remove()

        vm = self._vm.viewmodel
        if vm.placeholder_text is not None:
            placeholder_class = "pane-placeholder"
            if vm.placeholder_severity:
                placeholder_class = f"{placeholder_class} -{vm.placeholder_severity}"
            body.mount(Static(vm.placeholder_text, classes=placeholder_class))
            return

        for entry_vm in self._vm.filtered_entries:
            row = EntryRow(entry_vm)
            body.mount(row)
        self.call_after_refresh(self._refresh_row_states)

    def _refresh_row_states(self) -> None:
        for row in self.query(EntryRow):
            row.update_state()


__all__ = ["EntryRow", "Pane"]
