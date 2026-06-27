"""ApplicationPicker — top-strip application selector for the EMR page.

Inline-expanding dropdown. The picker uses ``height: auto`` so it
grows to wrap whatever children are visible: just the trigger row
when closed, trigger + OptionList when open. The parent
``emr-app-box`` is ``height: auto, min-height: 3`` so it grows
in lockstep; the sibling ``JobRunsPane`` (``height: 1fr``)
shrinks to make room.

Why not a floating overlay: the prior layered-overlay approaches
(PR #83 declaring ``dropdown`` on Screen, PR #85 mounting the
OptionList directly to the Screen) both broke the popover —
layers are z-order only (don't escape parent clipping in PR #83)
and Screen-mount put the popover after Screen's vertical-flow
children (so it ended up below the Commands pane in PR #85).
The inline-expanding pattern is simpler and reliable: the
OptionList stays a normal child of the picker, no layers, no
absolute positioning, no cross-widget message routing.
"""

from __future__ import annotations

import contextlib
from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM

#: Colored Rich-markup glyphs per application state. The glyph SHAPE
#: alone is distinguishable on monochrome terminals (●/◐/◑/○/◌/✗
#: are all visually different); the colour is icing for the colour-
#: capable case. User feedback: "show a green block circle to denote
#: they have already been started … similarly apt status indicator
#: for the rest … we don't need to show the STARTED OR STOPPED text".
_APP_STATE_MARKER: dict[ApplicationState, str] = {
    ApplicationState.STARTED: "[green]●[/green]",
    ApplicationState.STARTING: "[yellow]◐[/yellow]",
    ApplicationState.STOPPING: "[yellow]◑[/yellow]",
    ApplicationState.CREATING: "[dim]◌[/dim]",
    ApplicationState.CREATED: "[white]○[/white]",
    ApplicationState.STOPPED: "[dim]○[/dim]",
    ApplicationState.TERMINATED: "[red]✗[/red]",
}


class ApplicationPicker(Widget):
    """Top-strip application selector — inline-expanding."""

    DEFAULT_CSS: ClassVar[str] = """
    ApplicationPicker {
        width: 1fr;
        height: auto;
        min-height: 3;
        layout: vertical;
    }
    /* The trigger row is always 3 cells tall (matches the apps-box
       minimum). Wrapped in a Horizontal so its width takes the full
       picker; the Static fills that Horizontal. */
    ApplicationPicker > Horizontal {
        width: 1fr;
        height: 3;
    }
    ApplicationPicker > Horizontal > .app-trigger {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: left middle;
        text-style: bold;
    }
    /* OptionList is collapsed by default; ``-open`` flips display
       to block AND the picker's ``height: auto`` grows to wrap the
       newly-visible OptionList. Parent ``emr-app-box`` grows in
       lockstep (its ``height: auto, min-height: 3`` lets it expand
       up to the column's available space; the sibling JobRunsPane
       with ``height: 1fr`` shrinks to make room). */
    ApplicationPicker > OptionList {
        width: 1fr;
        height: auto;
        max-height: 16;
        display: none;
    }
    ApplicationPicker.-open > OptionList {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "close", "Close"),
        ("enter", "commit", "Pick"),
    ]

    class ApplicationCommitted(TextualMessage):
        """Posted when the user commits a selection (via Enter or
        click on a row). The parent ``EmrServerlessPage`` catches
        this and routes through ``page_vm.select_application()`` so
        the JobRuns and JobRunDetail panes refresh in lockstep —
        ``ApplicationsVM.select(id)`` alone only updates the picker's
        own ``_selected_id`` and the sibling VMs don't see it.
        """

        def __init__(self, app_id: str) -> None:
            super().__init__()
            self.app_id = app_id

    def __init__(
        self,
        vm: ApplicationsVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: ApplicationsVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        # Trigger row + OptionList both as children of the picker.
        # The OptionList is hidden by default via ``display: none``
        # and revealed when the picker gains the ``-open`` class.
        with Horizontal():
            yield Static(self._trigger_label(), classes="app-trigger")
        yield OptionList(*self._build_options(), id="app-options")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Public API ──────────────────────────────────────────────────────────

    def toggle_open(self) -> None:
        if "-open" in self.classes:
            self.remove_class("-open")
        else:
            self.add_class("-open")
            # Focus the dropdown so arrow keys / Enter / Esc are
            # routed there immediately. ``call_after_refresh`` waits
            # for the layout-pass that the ``-open`` class triggered
            # so the OptionList is laid out and focusable.
            self.call_after_refresh(self._focus_dropdown)
            self._refresh_options()

    def action_close(self) -> None:
        self.remove_class("-open")

    def action_commit(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:
            return
        if opts.highlighted is None:
            return
        opt = opts.get_option_at_index(opts.highlighted)
        if opt.id is not None:
            self._vm.select(opt.id)
            # Post up so the page widget can cascade through
            # ``page_vm.select_application(id)`` — see the
            # ``ApplicationCommitted`` docstring.
            self.post_message(self.ApplicationCommitted(opt.id))
        self.remove_class("-open")

    # ── Internal ────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        # Click on the trigger row toggles open/closed. Click on a
        # row inside the dropdown is handled by Textual's OptionList
        # which posts ``OptionSelected`` — see the handler below.
        if event.widget is not None and getattr(event.widget, "id", None) == "app-options":
            return
        self.toggle_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._vm.select(event.option.id)
            self.post_message(self.ApplicationCommitted(event.option.id))
        self.remove_class("-open")

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name not in {"applications", "selected_id", "state"}:
            return
        self.call_after_refresh(self._refresh_trigger)
        self.call_after_refresh(self._refresh_options)

    def _refresh_trigger(self) -> None:
        try:
            trigger = self.query_one(".app-trigger", Static)
        except Exception:
            return
        trigger.update(self._trigger_label())

    def _refresh_options(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:
            return
        opts.clear_options()
        for opt in self._build_options():
            opts.add_option(opt)

    def _focus_dropdown(self) -> None:
        with contextlib.suppress(Exception):
            opts = self.query_one("#app-options", OptionList)
            opts.focus()

    def _trigger_label(self) -> str:
        """Render the trigger row.

        Format: ``🔥  <name>  ·  <colored-glyph>``. The colored glyph
        encodes the state visually (green ● = STARTED, yellow ◐ /
        ◑ = transitional, dim ○ / ◌ = idle, red ✗ = terminated) so
        the textual STATE pill is no longer needed. User feedback:
        "If we do this then we don't need to show the STARTED OR
        STOPPED ETC statuses next to them"."""
        apps = self._vm.applications
        sid = self._vm.selected_id
        if not apps:
            return "(no application)"
        if sid is None:
            return "(select application)"
        match = next((a for a in apps if a.id == sid), None)
        if match is None:
            return "(select application)"
        marker = _APP_STATE_MARKER.get(match.state, "?")
        return f"🔥  {match.name}  ·  {marker}"

    def _build_options(self) -> list[Option]:
        """Build the dropdown options.

        Sort comes from :attr:`ApplicationsVM.sorted_applications` —
        the single source of truth shared with the Shift+S cycle so
        the order the user reads in the dropdown is the order they
        cycle through with the keybinding.

        Prompt: ``🔥  <name>  ·  <colored-glyph>`` — no textual
        state name. The colour + shape of the glyph carries the
        state semantics; the prompt stays compact even with long
        application names."""
        return [
            Option(
                prompt=f"🔥  {a.name}  ·  {_APP_STATE_MARKER.get(a.state, '?')}",
                id=a.id,
            )
            for a in self._vm.sorted_applications
        ]


__all__ = ["ApplicationPicker"]
