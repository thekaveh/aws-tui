"""ApplicationPicker — top-strip application selector for the EMR page.

Inline-expanding dropdown. The picker uses ``height: auto`` so it
grows to wrap whatever children are visible: just the trigger row
when closed, trigger + OptionList when open. The parent
``emr-app-box`` grows in lockstep; the sibling ``JobRunsPane``
(``height: 1fr``) shrinks to make room.

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
from rich.markup import escape as _escape_markup
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.file_manager.pane_vm import PaneState

#: Colored Rich-markup glyphs per application state. The glyph SHAPE
#: alone is distinguishable on monochrome terminals (●/◐/◑/◇/○/◌/✗
#: are all visually different); the colour is icing for the colour-
#: capable case. Option rows pair this compact marker with literal state
#: text; the selected trigger stays compact and exposes its state by tooltip.
_APP_STATE_MARKER: dict[ApplicationState, tuple[str, str]] = {
    ApplicationState.STARTED: ("green", "●"),
    ApplicationState.STARTING: ("yellow", "◐"),
    ApplicationState.STOPPING: ("yellow", "◑"),
    ApplicationState.CREATING: ("dim", "◌"),
    ApplicationState.CREATED: ("white", "◇"),
    ApplicationState.STOPPED: ("dim", "○"),
    ApplicationState.TERMINATED: ("red", "✗"),
}


def _state_marker(state: ApplicationState) -> str:
    style, glyph = _APP_STATE_MARKER.get(state, ("white", "?"))
    return f"[{style}]{glyph}[/{style}]"


def _state_option(state: ApplicationState, name: str) -> Text:
    """Render a styled row with a literal state for non-color access."""
    style, glyph = _APP_STATE_MARKER.get(state, ("white", "?"))
    prompt = Text(no_wrap=True, overflow="ellipsis")
    prompt.append(f"{glyph} {state.value}", style=style)
    prompt.append(f" · {name}")
    return prompt


class ApplicationPicker(Widget, can_focus=True):
    """Top-strip application selector — inline-expanding."""

    DEFAULT_CSS: ClassVar[str] = """
    ApplicationPicker {
        width: 1fr;
        height: auto;
        min-height: 3;
        layout: vertical;
    }
    /* Keep the state marker and application name in separate cells.
       Rich's styled marker segment can otherwise consume the compact
       one-row render while leaving the following name clipped. */
    ApplicationPicker > .app-trigger {
        width: 100%;
        height: 3;
        padding: 0 1;
    }
    ApplicationPicker > .app-trigger > .app-marker {
        width: 1;
        min-width: 1;
        height: 1;
    }
    ApplicationPicker > .app-trigger > .app-value {
        width: 1fr;
        min-width: 0;
        height: 1;
        padding-left: 1;
        text-overflow: ellipsis;
        text-style: bold;
    }
    /* OptionList is collapsed by default; ``-open`` flips display
       to block AND the picker's ``height: auto`` grows to wrap the
       newly-visible OptionList. Parent ``emr-app-box`` grows in
       lockstep (its ``height: auto, min-height: 3`` lets it expand
       up to the column's available space; the sibling JobRunsPane
       with ``height: 1fr`` shrinks to make room). */
    ApplicationPicker > OptionList {
        width: 100%;
        height: auto;
        max-height: 16;
        display: none;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ApplicationPicker.-open > OptionList {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "close", "Close"),
        ("enter,space", "activate", "Open application selector"),
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
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: ApplicationsVM = vm
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        # Trigger row + OptionList are direct children of the picker.
        # The OptionList is hidden by default via ``display: none``
        # and revealed when the picker gains the ``-open`` class.
        marker, value = self._trigger_fragments()
        with Horizontal(classes="app-trigger"):
            yield Static(marker, classes="app-marker")
            yield Static(value, classes="app-value")
        yield OptionList(*self._build_options(), id="app-options")

    def on_mount(self) -> None:
        # Round-3 directive §9.bis.11 / PR #103 retirement: subscribe
        # to the VM's per-instance Observable rather than the shared
        # hub. Eliminates the need for `sender_object` filtering —
        # this subscription only fires for THIS ApplicationsVM
        # instance.
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_property_changed)
        self._refresh_accessibility_text()

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
            # Rebuild after the ``-open`` width/layout pass. Building at
            # the closed width leaves OptionList's cached lines clipped
            # to the status glyph even after the picker expands.
            self.call_after_refresh(self._prepare_open_dropdown)

    def action_close(self) -> None:
        self.close()

    def close(self, *, refocus: bool = True) -> None:
        """Collapse the list without stealing an in-progress focus transfer."""

        self.remove_class("-open")
        if refocus:
            self.call_after_refresh(self.focus)

    def action_activate(self) -> None:
        if self.has_class("-open"):
            self.action_commit()
        else:
            self.toggle_open()

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
        self.call_after_refresh(self.focus)

    # ── Internal ────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        # Click on the trigger row toggles open/closed. Click on a
        # row inside the dropdown is handled by Textual's OptionList
        # which posts ``OptionSelected`` — see the handler below.
        if event.widget is not None and getattr(event.widget, "id", None) == "app-options":
            return
        self.toggle_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # ``__placeholder__`` is the synthetic disabled row used for
        # error / loading states (see ``_build_options``). Belt-and-
        # braces guard — Textual's OptionList already suppresses
        # selection on disabled rows, but a key-driven Enter while
        # the row is the only option could slip through.
        if event.option.id is not None and event.option.id != "__placeholder__":
            self._vm.select(event.option.id)
            self.post_message(self.ApplicationCommitted(event.option.id))
        self.remove_class("-open")
        self.call_after_refresh(self.focus)

    def _on_vm_property_changed(self, prop: str) -> None:
        """Round-3 directive: per-VM Observable subscription. The
        Subject only fires for events on THIS VM instance, so no
        `sender_object` filter is needed (PR #103 retirement).

        Trigger label depends on the selected app's name + state
        glyph; refresh it on any of these property changes (cheap).
        The OptionList rebuild is heavier and only fires on
        list-or-state changes (PR #100(b) absorbed at the VM via
        dedup-on-set — no no-change events reach here).
        """
        if prop in {"applications", "selected_id", "state"}:
            self.call_after_refresh(self._refresh_trigger)
        if prop in {"applications", "state"}:
            self.call_after_refresh(self._refresh_options)

    def _refresh_trigger(self) -> None:
        try:
            marker = self.query_one(".app-marker", Static)
            value = self.query_one(".app-value", Static)
        except Exception:
            return
        marker_text, value_text = self._trigger_fragments()
        marker.update(marker_text)
        value.update(value_text)
        self._refresh_accessibility_text(marker=marker, value=value)

    def _refresh_accessibility_text(
        self,
        *,
        marker: Static | None = None,
        value: Static | None = None,
    ) -> None:
        """Expose the selected application's full name and literal state."""

        selected = next(
            (app for app in self._vm.applications if app.id == self._vm.selected_id),
            None,
        )
        tooltip = None if selected is None else f"{selected.name} · {selected.state.value}"
        self.tooltip = tooltip
        with contextlib.suppress(Exception):
            (marker or self.query_one(".app-marker", Static)).tooltip = tooltip
            (value or self.query_one(".app-value", Static)).tooltip = tooltip

    def _trigger_fragments(self) -> tuple[str, str]:
        """Split the selected-state marker from the trigger text."""
        if self._vm.state in {
            PaneState.LOADING,
            PaneState.UNREACHABLE,
            PaneState.AUTH_REQUIRED,
            PaneState.FORBIDDEN,
            PaneState.ERROR,
        }:
            return "", self._trigger_label()
        apps = self._vm.applications
        sid = self._vm.selected_id
        match = next((app for app in apps if app.id == sid), None)
        if match is None:
            return "", self._trigger_label()
        return _state_marker(match.state), _escape_markup(match.name)

    def _refresh_options(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:
            return
        # The dedup-on-set guard that used to live here (PR #100(b)) has
        # moved into ApplicationsVM.refresh() per the round-3 directive
        # (spec §9.bis.11 + §9.bis.9 / Q-A): the VM no-ops on a no-change
        # poll, so a PropertyChangedMessage reaching this handler means
        # the data actually changed. The View just rebuilds.
        options = self._build_options()
        opts.clear_options()
        for opt in options:
            opts.add_option(opt)
        # ``height: auto`` on an OptionList inside an auto-height
        # Horizontal can resolve to the parent's full available height.
        # Give the open list a content-derived bound instead: one row per
        # option plus its border, capped so the runs pane remains usable.
        opts.styles.height = min(len(options) + 2, 8)

    def _focus_dropdown(self) -> None:
        with contextlib.suppress(Exception):
            opts = self.query_one("#app-options", OptionList)
            opts.focus()

    def _prepare_open_dropdown(self) -> None:
        self._refresh_options()
        self._focus_dropdown()

    def _trigger_label(self) -> str:
        """Render the trigger row.

        Format: ``<colored-glyph>  <name>``. The colored glyph
        encodes the state visually (green ● = STARTED, yellow ◐ /
        ◑ = transitional, white ◇ = CREATED, dim ○ = STOPPED,
        dim ◌ = CREATING, red ✗ = terminated) so
        the textual STATE pill is no longer needed. User feedback
        (post-PR-#92): "the dropdown … shows the fire emoji at the
        beginning of every application name, followed by the name
        of the app, and then followed by the status of the app. I
        want this changed to just the status indicator, followed
        by the name. No need for the emoji"."""
        # Surface VM error states explicitly so the trigger reads
        # actionable copy instead of "(no application)" — which is
        # indistinguishable from a successful empty listing. Mirrors
        # the per-state branching JobRunsPane / JobRunDetailPane do
        # for the same PaneState machine.
        # Trigger Static is markup-enabled (line 133), so AWS
        # error_text containing ``[…]`` would crash the trigger
        # render. Escape via _escape_markup — same guard
        # _build_options already applies for the same content.
        state = self._vm.state
        if state is PaneState.UNREACHABLE:
            msg = self._vm.error_text or "endpoint unreachable — press r to retry"
            return f"⚠ {_escape_markup(msg)}"
        if state is PaneState.AUTH_REQUIRED:
            return "⚠ auth required — aws sso login --profile <X>"
        if state is PaneState.FORBIDDEN:
            msg = self._vm.error_text or "permission denied — check IAM policy"
            return f"⚠ {_escape_markup(msg)}"
        if state is PaneState.ERROR:
            msg = self._vm.error_text or "error — press r to retry"
            return f"⚠ {_escape_markup(msg)}"
        if state is PaneState.LOADING:
            return "loading…"
        apps = self._vm.applications
        sid = self._vm.selected_id
        if not apps:
            return "(no application)"
        if sid is None:
            return "(select application)"
        match = next((a for a in apps if a.id == sid), None)
        if match is None:
            return "(select application)"
        marker = _state_marker(match.state)
        # Application name is AWS-controlled — escape any Rich
        # markup characters so a name like ``my-app [v2]`` doesn't
        # crash the parser. The leading marker is the only
        # intentional markup we ship in this string.
        return f"{marker}  {_escape_markup(match.name)}"

    def _build_options(self) -> list[Option]:
        """Build the dropdown options.

        Sort comes from :attr:`ApplicationsVM.sorted_applications` —
        the single source of truth shared with the Shift+A cycle so
        the order the user reads in the dropdown is the order they
        cycle through with the keybinding.

        Prompt: ``<colored-glyph> <STATE> · <name>`` — no fire emoji.
        State-first ordering keeps the literal state visible when a narrow
        selector must ellipsize the application name; matching marker/state
        styling preserves fast scanning.

        Error states (UNREACHABLE / AUTH_REQUIRED / FORBIDDEN /
        ERROR) and LOADING surface as a single non-selectable
        placeholder row carrying ``error_text`` — without this the
        dropdown either shows the stale apps from the prior poll
        (clickable but pointing at a meaningless app id once
        ``list_applications`` resumes) or an empty list with no
        actionable copy. Parity with the trigger label which
        round-13 already branches on the same five states."""
        state = self._vm.state
        if state is PaneState.LOADING:
            return [Option(prompt="loading…", id="__placeholder__", disabled=True)]
        if state is PaneState.UNREACHABLE:
            msg = self._vm.error_text or "endpoint unreachable — press r to retry"
            return [Option(prompt=f"⚠ {_escape_markup(msg)}", id="__placeholder__", disabled=True)]
        if state is PaneState.AUTH_REQUIRED:
            return [
                Option(
                    prompt="⚠ auth required — aws sso login --profile <X>",
                    id="__placeholder__",
                    disabled=True,
                )
            ]
        if state is PaneState.FORBIDDEN:
            msg = self._vm.error_text or "permission denied — check IAM policy"
            return [Option(prompt=f"⚠ {_escape_markup(msg)}", id="__placeholder__", disabled=True)]
        if state is PaneState.ERROR:
            msg = self._vm.error_text or "error — press r to retry"
            return [Option(prompt=f"⚠ {_escape_markup(msg)}", id="__placeholder__", disabled=True)]
        return [
            Option(
                # Name is AWS-controlled — escape Rich markup
                # characters so a name like ``my-app [v2]`` doesn't
                # crash the OptionList renderer. Marker is the only
                # intentional markup in the prompt.
                prompt=_state_option(a.state, a.name),
                id=a.id,
            )
            for a in self._vm.sorted_applications
        ]


__all__ = ["ApplicationPicker"]
