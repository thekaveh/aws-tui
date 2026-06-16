"""Pane widget — single Norton-Commander column bound to :class:`PaneVM`.

Layout:

    +-- breadcrumb (single row) ---------------+
    | NAME    SIZE     MODIFIED                |
    | entry row 1                              |
    | entry row 2                              |
    | ...                                      |
    +-- footer summary -----------------------+

State placeholders (``loading`` / ``empty`` / ``auth_required`` /
``forbidden`` / ``unreachable`` / ``error``) replace the entry body when
``PaneVM.state != IDLE`` per spec §7.7.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.domain.filesystem import EntryKind
from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.file_manager.entry_vm import EntryVM
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM


def _format_size(size: int | None, kind: EntryKind) -> str:
    if kind is EntryKind.DIRECTORY:
        return "<DIR>"
    if size is None:
        return "?"
    if size < 1024:
        return f"{size} B"
    units = ("K", "M", "G", "T")
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} P"


def _format_modified(when: datetime | None) -> str:
    if when is None:
        return "-"
    return when.strftime("%Y-%m-%d %H:%M")


# State -> placeholder text + class suffix.
_PLACEHOLDER_TEXT: dict[PaneState, tuple[str, str]] = {
    PaneState.LOADING: ("loading...", ""),
    PaneState.EMPTY: ("empty", ""),
    PaneState.AUTH_REQUIRED: (
        "auth needed - press a to sign in (or `aws sso login --profile <name>`)",
        "-warning",
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
        "-error",
    ),
    PaneState.UNREACHABLE: (
        "endpoint unreachable - press r to retry; check network / VPN / endpoint URL",
        "-warning",
    ),
    PaneState.ERROR: ("error", "-error"),
}


class EntryRow(Widget):
    """One entry row in a pane."""

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
        entry = self._entry_vm.entry
        marker = "*" if self._entry_vm.is_marked else " "
        # Left-edge accent column on the selected row (a half-block at column 0
        # in the accent color). Replaces the older ">" prefix — looks closer
        # to the design-spec mockups and avoids stealing a character cell from
        # the name area when the row is unselected.
        cursor_bar = "▌" if self._entry_vm.is_selected else " "
        cursor_style = "bold cyan" if self._entry_vm.is_selected else ""
        name = entry.name + ("/" if entry.kind is EntryKind.DIRECTORY else "")
        size = _format_size(entry.size, entry.kind)
        modified = _format_modified(entry.modified)
        text = Text()
        text.append(cursor_bar, style=cursor_style)
        text.append(f"{marker} {name:<32} {size:>10}  {modified}")
        return text

    def update_state(self) -> None:
        # Sync CSS classes to mirror VM flags.
        if self._entry_vm.is_selected:
            self.add_class("-selected")
        else:
            self.remove_class("-selected")
        if self._entry_vm.is_marked:
            self.add_class("-marked")
        else:
            self.remove_class("-marked")
        if self._entry_vm.entry.kind is EntryKind.DIRECTORY:
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
        yield Static(self._breadcrumb_text(), classes="breadcrumb")
        yield Static(self._column_header_text(), classes="column-header")
        yield Vertical(id="pane-body")
        yield Static(self._footer_text(), classes="pane-footer")

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
        """Clicking anywhere in a pane switches focus to it (when applicable).

        Walks up the widget tree to find the ``DualPane`` parent and toggles
        focus via its VM's ``switch_focus_command`` if this pane isn't already
        the focused side. The deeper-than-Tab affordance asked for in the
        post-v0.7 usability feedback.
        """
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
        breadcrumb.update(self._breadcrumb_text())
        header.update(self._column_header_text())
        footer.update(self._footer_text())
        self._render_body()

    def _render_body(self) -> None:
        try:
            body = self.query_one("#pane-body", Vertical)
        except Exception:
            return
        # Clear any existing children.
        for child in list(body.children):
            child.remove()

        state = self._vm.state
        if state in _PLACEHOLDER_TEXT and (state != PaneState.IDLE):
            text, suffix = _PLACEHOLDER_TEXT[state]
            if self._vm.viewmodel.error_text:
                text = f"{text}: {self._vm.viewmodel.error_text}"
            placeholder_class = "pane-placeholder"
            if suffix:
                placeholder_class = f"{placeholder_class} {suffix}"
            body.mount(Static(text, classes=placeholder_class))
            return

        for entry_vm in self._vm.filtered_entries:
            row = EntryRow(entry_vm)
            body.mount(row)
        # Sync cursor / marked classes after mount.
        self.call_after_refresh(self._refresh_row_states)

    def _refresh_row_states(self) -> None:
        for row in self.query(EntryRow):
            row.update_state()

    def _breadcrumb_text(self) -> str:
        path = self._vm.path
        if path.is_root:
            return "/"
        return "/" + "/".join(path.segments)

    @staticmethod
    def _column_header_text() -> str:
        return f"   {'NAME':<32} {'SIZE':>10}  MODIFIED"

    def _footer_text(self) -> str:
        return self._vm.viewmodel.summary


__all__ = ["EntryRow", "Pane"]
