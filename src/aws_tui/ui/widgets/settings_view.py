"""SettingsView — main-area content for the Settings nav destination.

VS Code-style scrollable page of Collapsible sections. The
Connections section is populated by ``S3ConnectionsPanel`` for
sub-project A. Themes and Keymap are visible-but-disabled
placeholders that go live in sub-projects B and C.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Static
from textual.widgets._collapsible import CollapsibleTitle
from vmx import Message, MessageHub

from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.settings_vm import SettingsVM


class SettingsView(Widget):
    """Top-level Settings page."""

    DEFAULT_CSS = """
    SettingsView {
        height: 1fr;
        width: 1fr;
    }
    SettingsView > #settings-title {
        padding: 0 2 1 2;
        text-style: bold;
    }
    SettingsView > VerticalScroll {
        height: 1fr;
        padding: 0 2;
    }
    SettingsView Collapsible {
        margin-bottom: 1;
    }
    """

    def __init__(self, *, vm: SettingsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm: SettingsVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> SettingsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="settings-title")
        with VerticalScroll(id="settings-scroll"):
            with Collapsible(
                title="S3-Compatible Connections",
                collapsed=False,
                id="section-connections",
            ):
                yield S3ConnectionsPanel(vm=self._vm.s3, hub=self._hub)
            with Collapsible(
                title="Themes (coming in v0.8)",
                collapsed=True,
                disabled=True,
                id="section-themes",
            ):
                yield Static("This section is coming in v0.8.")
            with Collapsible(
                title="Keymap (coming in v0.8)",
                collapsed=True,
                disabled=True,
                id="section-keymap",
            ):
                yield Static("This section is coming in v0.8.")

    def on_mount(self) -> None:
        # Land focus on the first (non-disabled) Collapsible's
        # ``CollapsibleTitle`` so the per-theme
        # ``Collapsible:focus-within { border: $accent }`` rule
        # paints the section as the active pane on entry. The user
        # explicitly wants the first section to "come selected with
        # the current theme's accent" — matching how the file-pane
        # row already paints its cursor row on launch.
        #
        # ``Collapsible`` itself has ``can_focus = False`` (it's a
        # container), so calling ``Collapsible.focus()`` is a no-op
        # — the previous PR-#68 attempt at
        # ``call_after_refresh(first.focus)`` looked plausible but
        # never actually moved focus. The user reported it: "I had
        # to toggle its collapse or expansion once so it's correctly
        # rendered as selected". The focusable child is the
        # ``CollapsibleTitle`` (the toggle button at the top of the
        # section); focusing IT does propagate ``:focus-within``
        # back up to the Collapsible.
        try:
            first = self.query_one("#section-connections", Collapsible)
            title = first.query_one(CollapsibleTitle)
        except Exception:
            return
        # ``call_after_refresh`` so the focus call lands AFTER the
        # widget tree finishes its initial focus pass; otherwise
        # Textual's own first-focus walk overwrites this.
        self.call_after_refresh(title.focus)


__all__ = ["SettingsView"]
