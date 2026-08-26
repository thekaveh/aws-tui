"""EmrServerlessPage — content-host root for the EMR service.

Composes the top strip + 2-pane body and owns the three auto-refresh
intervals via Textual's ``set_interval``. The intervals are
independent so they back off independently on
:class:`ThrottlingException` (PR-B wires the back-off — PR-A
ships the static cadences from spec §6)."""

from __future__ import annotations

import contextlib
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Literal

from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot

if TYPE_CHECKING:
    from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import OptionList
from vmx import Message, MessageHub

from aws_tui.ui import notifications
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.overlay_option_list import PickerOpenIntent
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.service_source_vm import ServiceSourceContext


class EmrServerlessPage(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    EmrServerlessPage {
        height: 1fr;
        layout: horizontal;
    }
    /* User feedback (post-PR-#92): "I feel like we can further
       reduce the width of the first / left column … to become
       2/7th of the entire width (as opposed to the current 1/3)
       to give more space to the right pane". 2fr / 5fr = 2/7
       LEFT and 5/7 RIGHT. */
    EmrServerlessPage > .emr-left-column {
        width: 2fr;
        height: 1fr;
        layout: vertical;
    }
    /* Source + application share one bordered context box. */
    EmrServerlessPage > .emr-left-column > .emr-app-box {
        height: auto;
        min-height: 2;
        layout: vertical;
    }
    EmrServerlessPage .emr-app-box > .emr-context-row {
        width: 1fr;
        height: auto;
        min-height: 1;
    }
    EmrServerlessPage .emr-context-row > ServiceSourceHeader {
        width: 2fr;
        height: auto;
        min-height: 1;
    }
    EmrServerlessPage .emr-context-row > ApplicationPicker {
        width: 3fr;
    }
    EmrServerlessPage .emr-context-row > ServiceSourceHeader > ContextPicker {
        height: 1;
        min-height: 1;
        border: none;
    }
    EmrServerlessPage .emr-app-box ContextPicker > .context-picker-trigger,
    EmrServerlessPage .emr-app-box ApplicationPicker > .app-trigger {
        height: 1;
        border: none;
        padding: 0 1;
    }
    EmrServerlessPage .emr-app-box ServiceSourceHeader
        > ContextPicker > .context-picker-trigger {
        padding: 0;
    }
    EmrServerlessPage .emr-context-row > ServiceSourceHeader
        ContextPicker > OverlayOptionList {
        width: 30;
    }
    EmrServerlessPage .emr-app-box ApplicationPicker > .app-trigger > Static {
        height: 1;
    }
    EmrServerlessPage > .emr-left-column > JobRunsPane {
        height: 1fr;
    }
    EmrServerlessPage > .emr-right-column {
        width: 5fr;
        height: 1fr;
        layout: vertical;
    }
    /* 50/50 vertical split inside the right column. Both halves get
       ``height: 1fr`` so each takes exactly half regardless of how
       tall the detail content is (it scrolls within its half rather
       than pushing the logs pane off-screen). */
    EmrServerlessPage > .emr-right-column > JobRunDetailPane {
        height: 1fr;
    }
    EmrServerlessPage > .emr-right-column > JobRunLogsPane {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "open_application_picker", "Apps"),
        Binding("tab", "cycle_panes_forward", "Tab"),
        Binding("shift+tab", "cycle_panes_back", "←Tab"),
        # ``emr.clone`` action id — re-runs the currently selected
        # job with all fields pre-populated. The ``c`` keystroke
        # overlaps with the file-manager's ``pane.copy`` but the two
        # never share a focus context (the EMR page is not a
        # DualPaneVM host), so the binding is unambiguous at the
        # widget scope.
        Binding("c", "clone_selected_run", "Clone"),
    ]

    def __init__(
        self,
        vm: EmrServerlessPageVM,
        *,
        hub: MessageHub[Message],
        source_candidates: tuple[ServiceSourceContext, ...] = (),
        focus_coordinator: FocusCoordinatorVM | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: EmrServerlessPageVM = vm
        self._hub: MessageHub[Message] = hub
        self._source_candidates = source_candidates
        self._focus_coordinator: FocusCoordinatorVM | None = focus_coordinator
        self._source_header: ServiceSourceHeader | None = None
        self._picker: ApplicationPicker | None = None
        self._left: JobRunsPane | None = None
        self._right_detail: JobRunDetailPane | None = None
        self._right_logs: JobRunLogsPane | None = None
        self._runs_tick_counter: int = 0
        self._picker_open_intent = PickerOpenIntent()

    def compose(self) -> ComposeResult:
        self._picker = ApplicationPicker(
            self._vm.applications,
            open_intent=self._picker_open_intent,
            id="emr-app-picker",
        )
        self._left = JobRunsPane(self._vm.job_runs, id="emr-runs-pane")
        self._right_detail = JobRunDetailPane(self._vm.job_run_detail, id="emr-detail-pane")
        self._right_logs = JobRunLogsPane(self._vm.job_run_logs, id="emr-logs-pane")
        self._source_header = ServiceSourceHeader(
            self._vm.source,
            candidates=self._source_candidates,
            open_intent=self._picker_open_intent,
            id="emr-source-header",
        )
        # Page layout — 1fr:2fr horizontal split with LEFT column
        # containing the picker + runs pane, and RIGHT column
        # containing detail (top, 1fr) + logs (bottom, 1fr) in a
        # 50/50 vertical split.
        with Vertical(classes="emr-left-column"):
            with (
                Vertical(classes="emr-app-box", id="emr-app-box"),
                Horizontal(classes="emr-context-row"),
            ):
                yield self._source_header
                yield self._picker
            yield self._left
        with Vertical(classes="emr-right-column"):
            yield self._right_detail
            yield self._right_logs

    def on_mount(self) -> None:
        # App-box border title — set here because Textual takes the
        # title from a Python attribute, not from CSS. The matching
        # ``:focus-within`` border-accent style is in the per-theme
        # .tcss so the box highlights when the picker is open.
        try:
            box = self.query_one("#emr-app-box", Vertical)
            box.border_title = "source / application"
        except Exception:
            pass
        # NOTE: ``ContentHostVM.set_content`` already dispatches
        # ``EmrServerlessPageVM.setup()`` as a background asyncio
        # task when the page VM is adopted (see PR #67 — and the
        # follow-up of the maintenance loop confirming the double
        # dispatch). We do NOT re-launch a second setup worker here:
        # that would race the host's task and double the boot-time
        # ``list_applications`` API call on every EMR mount.
        # Set up the three pollers per spec §6.
        # Poller cadence — user feedback (post-PR-#93, restated):
        # "the EMR page still refreshes quite a lot so many times
        # so that it's annoying … Instead of refreshing every 5
        # seconds, let's do every 30 or even 1 min". Applied:
        # apps + runs at 60 s (active), detail at 30 s (active).
        # The cadence-decay path (``_poll_runs_decay``) still skips
        # 5 out of every 6 idle ticks, so an idle EMR page now
        # re-fetches runs at most every ~6 min instead of every
        # ~30 s — orders of magnitude quieter without giving up
        # responsiveness once something is running.
        # Demo poller cadence — user feedback (post-PR-#97): the 5 s
        # demo cadence felt "refreshed every second" because the clone
        # state machine mutates state mid-interval too. Bump apps + runs
        # to 30 s; keep detail at 5 s so the clone state walk
        # (SUBMITTED → SCHEDULED at 1s → RUNNING at 2s → SUCCESS at 5s,
        # ~5 s total) is still visible in the detail pane on the
        # currently-selected run. Prod stays at 60/60/30.
        demo_ctx = getattr(self.app, "app_ctx", None)
        demo_active = bool(demo_ctx and getattr(demo_ctx, "demo", False))
        apps_cadence = 30.0 if demo_active else 60.0
        runs_cadence = 30.0 if demo_active else 60.0
        detail_cadence = 5.0 if demo_active else 30.0
        self.set_interval(apps_cadence, self._tick_applications, name="emr-poll-apps")
        self.set_interval(runs_cadence, self._tick_runs, name="emr-poll-runs")
        self.set_interval(detail_cadence, self._tick_detail, name="emr-poll-detail")
        # Land Textual focus on the LEFT pane so the user gets the
        # same "arrow keys move the cursor immediately" UX as the S3
        # page. Without this, neither pane shows the
        # ``:focus-within`` accent border and the user has to press
        # Tab once before arrows do anything. EXCEPT: if NavMenu (or
        # any widget outside this page) already owns focus when the
        # auto-focus runs, do not steal — the user is mid-arrow-walk
        # on the rail and the page swap was a side-effect of cursor
        # navigation, not an intent to enter the runs pane. User
        # feedback (post-PR-#98): "when I use [arrow] keys to move
        # onto the emr service, it automatically focuses into the job
        # runs and meaningless focus".
        if self._left is not None:
            self.call_after_refresh(self._maybe_focus_left)

    def on_unmount(self) -> None:
        self._picker_open_intent.cancel()
        if self._source_header is not None:
            self._source_header.picker.close(refocus=False)
        if self._picker is not None:
            self._picker.close(refocus=False)

    def _maybe_focus_left(self) -> None:
        """Auto-focus the LEFT pane on initial page mount UNLESS a
        widget outside this page (typically the NavMenu rail) already
        owns Textual focus.

        Round-3 directive §9.bis.11 / PR #99(a) closure: when a
        :class:`FocusCoordinatorVM` is wired, the rail-walk gate
        reads from `focused_slot == NAV_MENU` AND requires Textual
        focus to actually exist on the rail — the coordinator's
        VM-owned slot becomes the authoritative answer to "is the
        user arrow-walking the menu?". When no coordinator is
        wired, or when Textual focus is unset (programmatic
        service-switch in tests), the legacy "focus left when
        nothing else holds focus" semantics still apply.
        """
        if self._left is None:
            return
        textual_focused = self.app.focused
        if (
            self._focus_coordinator is not None
            and textual_focused is not None
            and not self.has_focus_within
        ):
            slot = self._focus_coordinator.focused_slot
            if slot is FocusSlot.NAV_MENU:
                # Rail-walk in progress: VM-owned slot agrees AND
                # Textual focus is on the rail. Leave it alone.
                return
        if textual_focused is None or self.has_focus_within:
            if self._focus_coordinator is not None:
                self._focus_coordinator.project_focused_slot(FocusSlot.EMR_RUNS)
            self._left.focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if event.widget is self.app.focused:
            self._sync_focused_widget(event.widget)

    # ── Public accessors ────────────────────────────────────────────────────

    @property
    def vm(self) -> EmrServerlessPageVM:
        return self._vm

    @property
    def left_pane(self) -> JobRunsPane | None:
        """LEFT pane (job runs list). Public so ``AwsTuiApp``'s
        global priority key handlers can forward Up/Down/Enter/r to
        it the same way the S3 path forwards through
        ``dual.focused_pane``."""
        return self._left

    @property
    def right_pane(self) -> JobRunLogsPane | None:
        """RIGHT-bottom job-run logs pane, one slot in the page focus ring."""
        return self._right_logs

    @property
    def right_detail(self) -> JobRunDetailPane | None:
        """RIGHT-top job-run detail pane, one slot in the page focus ring."""
        return self._right_detail

    # ── Pollers ─────────────────────────────────────────────────────────────

    def _tick_applications(self) -> None:
        # ``exclusive=True`` so a slow ``list_applications`` doesn't
        # have a second tick land while the first is mid-flight —
        # Textual silently skips overlapping ticks rather than
        # queueing them, which is the right semantic for a poller.
        self.run_worker(
            self._vm.refresh_focused("applications"), exclusive=True, group="emr-poll-apps"
        )

    def _tick_runs(self) -> None:
        # Cadence-decay: when no active runs, only refresh every 6th tick (~6 min).
        if not self._vm.job_runs.has_active_runs() and self._poll_runs_decay():
            return
        self.run_worker(self._vm.refresh_job_runs(), exclusive=True, group="emr-poll-runs")

    def _tick_detail(self) -> None:
        # Only poll while the run is non-terminal.
        if self._vm.job_run_detail.is_terminal_state():
            return
        self.run_worker(self._vm.refresh_job_run_detail(), exclusive=True, group="emr-poll-detail")

    def _poll_runs_decay(self) -> bool:
        """Return True if THIS tick should be skipped (6:1 decay)."""
        self._runs_tick_counter = (self._runs_tick_counter + 1) % 6
        return self._runs_tick_counter != 0

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_open_application_picker(self) -> None:
        if self._picker is not None:
            self._picker.toggle_open()

    def on_context_picker_open_changed(self, event: ContextPicker.OpenChanged) -> None:
        event.stop()
        self._queue_picker_reconcile(event.picker, event.is_open, event.intent_epoch)

    def _queue_picker_reconcile(
        self,
        picker: Widget,
        is_open: bool,
        intent_epoch: int | None,
    ) -> None:
        if not self._picker_coordination_available():
            return
        epoch = intent_epoch
        if epoch is None:
            epoch = self._picker_open_intent.observe(picker, is_open)
        self.call_after_refresh(partial(self._reconcile_open_pickers, epoch))

    def _reconcile_open_pickers(self, epoch: int) -> None:
        if not self._picker_coordination_available() or not self._picker_open_intent.is_current(
            epoch
        ):
            return
        pickers: tuple[ContextPicker | ApplicationPicker, ...] = tuple(self.query(ContextPicker))
        if self._picker is not None:
            pickers = (*pickers, self._picker)
        desired = self._picker_open_intent.desired
        if not isinstance(desired, ContextPicker | ApplicationPicker) or (
            not desired.is_attached or not desired.is_open
        ):
            desired = None
        for picker in pickers:
            if picker is not desired and picker.is_open:
                picker.close(refocus=False)

    def _picker_coordination_available(self) -> bool:
        return self.is_running and self.is_attached and self.display

    def on_application_picker_application_committed(
        self, event: ApplicationPicker.ApplicationCommitted
    ) -> None:
        """The picker posts ``ApplicationCommitted`` when the user
        selects a different application (via Enter or click on a
        row). Cascade through ``page_vm.select_application(id)`` so
        the JobRuns and JobRunDetail panes refresh in lockstep with
        the picker. Without this routing, only the picker's own
        ``_selected_id`` flipped — the user saw the picker label
        change but the runs pane below kept showing the old app's
        runs.
        """
        self.run_worker(
            self._vm.select_application(event.app_id),
            exclusive=True,
            group="emr-select-app",
        )

    def on_application_picker_open_changed(self, event: ApplicationPicker.OpenChanged) -> None:
        event.stop()
        self._queue_picker_reconcile(event.picker, event.is_open, event.intent_epoch)

    def action_cycle_panes_forward(self) -> None:
        self._cycle("right")

    def action_cycle_panes_back(self) -> None:
        self._cycle("left")

    def activate_focused(self) -> bool:
        """Activate the focused EMR selector or pane."""
        focused = self.app.focused
        if focused is None or not self._contains_focus(focused):
            return False
        if isinstance(focused, OptionList):
            if self._picker is not None and self._is_within(focused, self._picker):
                self._picker.action_commit()
            else:
                focused.action_select()
            return True
        if self._source_header is not None and self._is_within(focused, self._source_header):
            self._source_header.open()
            return True
        if self._picker is not None and self._is_within(focused, self._picker):
            self._picker.action_activate()
            return True
        if self._left is not None and self._is_within(focused, self._left):
            self._left.action_commit_selection()
            return True
        if self._right_logs is not None and self._is_within(focused, self._right_logs):
            self._right_logs.action_load()
            return True
        # Detail has no activation action but must consume Enter.
        return self._right_detail is not None and self._is_within(focused, self._right_detail)

    def move_focused(self, delta: int) -> bool:
        """Move inside, or open, the currently focused EMR selector or pane."""
        focused = self.app.focused
        if focused is None or not self._contains_focus(focused):
            return False
        if isinstance(focused, OptionList):
            if delta < 0:
                focused.action_cursor_up()
            else:
                focused.action_cursor_down()
            return True
        if isinstance(focused, ContextPicker):
            focused.open()
            return True
        if self._source_header is not None and self._is_within(focused, self._source_header):
            self._source_header.open()
            return True
        if self._picker is not None and self._is_within(focused, self._picker):
            self._picker.toggle_open()
            return True
        if self._left is not None and self._is_within(focused, self._left):
            if delta < 0:
                self._left.action_cursor_up()
            else:
                self._left.action_cursor_down()
            return True
        if self._right_logs is not None and self._is_within(focused, self._right_logs):
            if delta < 0:
                self._right_logs.action_scroll_up()
            else:
                self._right_logs.action_scroll_down()
            return True
        # Detail has no cursor action but must consume Up and Down.
        return self._right_detail is not None and self._is_within(focused, self._right_detail)

    async def action_clone_selected_run(self) -> None:
        """Open the clone modal pre-populated from the currently-
        selected job-run detail.

        Silently no-ops when no detail is loaded (e.g. the user
        pressed ``c`` before the first run was selected). On submit
        the new ``job_run_id`` is surfaced as a success toast; a
        :class:`ProviderError` was already shown inline by the modal
        — we still raise an error toast here for the top-right
        notification channel."""
        detail = self._vm.job_run_detail.detail
        if detail is None:
            return
        clone_vm = JobRunCloneVM(
            detail,
            client=self._vm.client,
            hub=self._hub,
            dispatcher=self._vm.dispatcher,
        )
        clone_vm.construct()
        modal = JobRunCloneModal(clone_vm, hub=self._hub)
        # Unified try/finally so cancellation (CancelledError is a
        # BaseException, NOT an Exception) disposes the VM too.
        # Previously the outer try caught only Exception, so an
        # asyncio cancel during push_screen_wait would skip both
        # the dispose AND the second try/finally — leaking the
        # clone_vm's inner VMx component + hub subscriptions +
        # commands. Mirrors s3_connections_panel._do_delete's
        # try/finally pattern.
        try:
            try:
                new_id = await self.app.push_screen_wait(modal)
            except Exception as exc:
                # The modal raised after dismiss (extremely rare —
                # e.g. the test harness disposed the app mid-flight).
                # Surface an advisory toast and bail.
                self._post_advisory_toast("Job", f"clone aborted ({exc})")
                return
            if new_id is None:
                # User cancelled — silent (Cancel is intentional UX,
                # not an error to advertise).
                return
            self._post_clone_success_toast(new_id)
            # Re-fresh the runs list so the new SUBMITTED row appears
            # immediately rather than waiting for the next 60-s tick.
            self.run_worker(
                self._vm.refresh_job_runs(),
                exclusive=True,
                group="emr-poll-runs",
            )
        finally:
            clone_vm.dispose()

    def _post_clone_success_toast(self, new_id: str) -> None:
        """Reach the canonical ``ToastStackVM`` through the running
        app and post the success toast. The :class:`AwsTuiApp` exposes
        ``_app_ctx``; tests that mount the page under a vanilla
        ``App`` may not — in which case we silently skip."""
        with contextlib.suppress(Exception):
            stack = self.app._app_ctx.root_vm.chrome.toast_stack  # type: ignore[attr-defined]
            notifications.success(
                stack,
                subject="Job",
                message=f"clone submitted ({new_id})",
            )

    def _post_advisory_toast(self, subject: notifications.Subject, message: str) -> None:
        """Surface a Warning-level toast for an unexpected failure
        that doesn't have its own in-pane state. Same defensive
        ``_app_ctx`` access pattern as :meth:`_post_clone_success_toast`
        — tests that mount the page under a vanilla ``App`` see no
        toast (the suppress catches the missing ``_app_ctx``)."""
        with contextlib.suppress(Exception):
            stack = self.app._app_ctx.root_vm.chrome.toast_stack  # type: ignore[attr-defined]
            notifications.advise(stack, subject=subject, message=message)

    def _cycle(self, direction: Literal["left", "right"]) -> None:
        """6-slot Tab cycle: NAV → SOURCE → APPLICATION → RUNS → DETAIL → LOGS.

        Direction:
        - ``"right"``: forward through the order above, then NAV
        - ``"left"``: backward through the order above, then NAV

        Pane widgets in the EMR page DO accept Textual focus (unlike
        the S3 file panes), so the slot ID is derived from
        ``has_focus`` / ``has_focus_within`` rather than from a VM
        flag. The NAV slot lives outside this page — when we're
        about to leave one of our 3 panes for NAV, we drop our
        focus and ask the App to focus the NavMenu.
        """
        if (
            self._source_header is None
            or self._picker is None
            or self._left is None
            or self._right_detail is None
            or self._right_logs is None
        ):
            return
        slots = [
            self._source_header,
            self._picker,
            self._left,
            self._right_detail,
            self._right_logs,
        ]
        # Find which slot currently owns focus (or focus-within for
        # rare picker-open / dropdown-open cases). -1 marks "NAV
        # owns focus or nothing is focused yet".
        focused_idx = -1
        for idx, slot in enumerate(slots):
            if slot.has_focus or slot.has_focus_within:
                focused_idx = idx
                break
        if direction == "right":
            next_idx = focused_idx + 1
            if next_idx >= len(slots):
                # Last slot → NAV (wrap by handing focus back to
                # the rail).
                self._close_departed_selector(focused_idx)
                self._focus_nav_menu()
                return
        else:
            next_idx = focused_idx - 1
            if next_idx < 0:
                # First slot → NAV (Shift+Tab wraps backwards).
                self._close_departed_selector(focused_idx)
                self._focus_nav_menu()
                return
        self._close_departed_selector(focused_idx)
        slots[next_idx].focus()
        # Project the new slot through the coordinator so the
        # ``-rail-active`` Screen class set by NavMenu.on_focus
        # clears — without this, the per-theme ``.-rail-active
        # Pane.-focused`` rule keeps the EMR panes' focused
        # border dim instead of accent-highlighted. Sibling to
        # DualPane's _sync_focus projection.
        if self._focus_coordinator is not None:
            slot_to_project = (
                FocusSlot.EMR_SOURCE,
                FocusSlot.EMR_APPLICATION,
                FocusSlot.EMR_RUNS,
                FocusSlot.EMR_DETAIL,
                FocusSlot.EMR_LOGS,
            )[next_idx]
            self._focus_coordinator.project_focused_slot(slot_to_project)

    def _close_departed_selector(self, focused_idx: int) -> None:
        if focused_idx == 0 and self._source_header is not None:
            self._source_header.picker.close(refocus=False)
        elif focused_idx == 1 and self._picker is not None:
            self._picker.close(refocus=False)

    def _focus_targets(self) -> tuple[tuple[FocusSlot, Widget], ...]:
        targets: list[tuple[FocusSlot, Widget]] = []
        for slot, widget in (
            (FocusSlot.EMR_SOURCE, self._source_header),
            (FocusSlot.EMR_APPLICATION, self._picker),
            (FocusSlot.EMR_RUNS, self._left),
            (FocusSlot.EMR_DETAIL, self._right_detail),
            (FocusSlot.EMR_LOGS, self._right_logs),
        ):
            if widget is not None:
                targets.append((slot, widget))
        return tuple(targets)

    def _project_focus_slot(self, slot: FocusSlot) -> None:
        target = dict(self._focus_targets()).get(slot)
        if target is None:
            return
        if self._focus_coordinator is not None:
            self._focus_coordinator.project_focused_slot(slot)
        self.app.set_focus(target)

    def project_focus_slot(self, slot: FocusSlot) -> None:
        """Project an app-coordinated focus slot onto this page."""
        self._project_focus_slot(slot)

    def commit_open_application_picker(self) -> bool:
        """Commit the highlighted application when its selector is open."""
        if self._picker is None or not self._picker.has_class("-open"):
            return False
        self._picker.action_commit()
        return True

    def _sync_focused_widget(self, focused: Widget) -> None:
        if self._focus_coordinator is None:
            return
        ancestors = set(focused.ancestors_with_self)
        for slot, target in self._focus_targets():
            if target in ancestors:
                self._focus_coordinator.project_focused_slot(slot)
                return

    def _focus_nav_menu(self) -> None:
        """Hand focus back to the App-level NavMenu. The App
        provides ``_focus_active_nav_list`` which lands focus on
        the NavMenu rail widget directly — post-PR-#94 there is
        no internal OptionList; the rail itself is the focusable
        widget. The helper name is kept for back-compat with the
        App and EMR-page call sites."""
        from aws_tui.ui.widgets.nav_menu import NavMenu

        with contextlib.suppress(Exception):
            nav = self.app.query_one("#nav-menu", NavMenu)
            self.app._focus_active_nav_list(nav)  # type: ignore[attr-defined]

    def _contains_focus(self, focused: Widget) -> bool:
        return focused is self or self in focused.ancestors_with_self

    @staticmethod
    def _is_within(focused: Widget, parent: Widget) -> bool:
        return focused is parent or parent in focused.ancestors_with_self

    # ── Message routing ─────────────────────────────────────────────────────

    def on_job_runs_pane_run_selected(self, event: JobRunsPane.RunSelected) -> None:
        self.run_worker(
            self._vm.select_job_run(event.run_id), exclusive=True, group="emr-select-run"
        )

    def on_job_runs_pane_refresh_requested(self, _event: JobRunsPane.RefreshRequested) -> None:
        # Use the same ``emr-poll-runs`` group as ``_tick_runs`` and the
        # clone-success refresh in ``action_clone_selected_run`` so a
        # manual ``r`` press while the periodic poller is mid-flight is
        # silently dropped by Textual rather than allowed to race the
        # poller's worker. Both end up calling ``job_runs.refresh()``,
        # which mutates the same VM state and fires the same ``runs``
        # PropertyChangedMessage — two concurrent calls produced a
        # double UI redraw and an extra ``list_job_runs`` round-trip
        # per overlap.
        self.run_worker(self._vm.refresh_focused("runs"), exclusive=True, group="emr-poll-runs")

    def on_job_run_detail_pane_refresh_requested(
        self, _event: JobRunDetailPane.RefreshRequested
    ) -> None:
        """User pressed r while the detail pane owns focus."""
        self.run_worker(self._vm.refresh_focused("detail"), exclusive=True, group="emr-poll-detail")

    def on_job_runs_pane_load_more_requested(self, _event: JobRunsPane.LoadMoreRequested) -> None:
        """User asked for the next page of runs (PgDn or click on
        the bottom sentinel). Run as ``exclusive=True`` so a slow
        page response can't be double-fired by an impatient
        keypress — the second call is dropped by Textual rather
        than queued behind the first."""
        self.run_worker(
            self._vm.load_more_job_runs(),
            exclusive=True,
            group="emr-load-more",
        )

    def on_job_run_logs_pane_load_requested(self, _event: JobRunLogsPane.LoadRequested) -> None:
        """User pressed Enter to load logs."""
        self.run_worker(self._vm.job_run_logs.load(), exclusive=True, group="emr-logs")

    def on_job_run_logs_pane_refresh_requested(
        self, _event: JobRunLogsPane.RefreshRequested
    ) -> None:
        """User pressed r to refresh/reload logs."""
        self.run_worker(self._vm.job_run_logs.load(), exclusive=True, group="emr-logs")

    def on_job_run_logs_pane_log_file_selected(self, event: JobRunLogsPane.LogFileSelected) -> None:
        """User selected a different log file from the chip strip."""
        self._vm.job_run_logs.select_log_file_key(event.key)
        self.run_worker(self._vm.job_run_logs.load(), exclusive=True, group="emr-logs")

    async def on_job_run_logs_pane_open_filter_requested(
        self, _event: JobRunLogsPane.OpenFilterRequested
    ) -> None:
        """User pressed f to open the filter modal."""
        from aws_tui.ui.widgets.emr_serverless.log_filter_modal import LogFilterModal

        current_filter = self._vm.job_run_logs.filter
        modal = LogFilterModal(current_filter)
        try:
            new_filter = await self.app.push_screen_wait(modal)
            if new_filter is not None and new_filter != current_filter:
                self._vm.job_run_logs.set_filter(new_filter)
                self.run_worker(self._vm.job_run_logs.load(), exclusive=True, group="emr-logs")
        except Exception as exc:
            # Modal raised mid-flight (rare — usually a test-harness
            # teardown race). Surface an advisory toast so the user
            # learns the filter didn't apply.
            self._post_advisory_toast("Settings", f"filter aborted ({exc})")

    def on_job_run_logs_pane_reset_filter_requested(
        self, _event: JobRunLogsPane.ResetFilterRequested
    ) -> None:
        """User pressed Shift+F to reset the log filter to the
        default keyword set without going through the modal."""
        from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER

        if self._vm.job_run_logs.filter == DEFAULT_LOG_FILTER:
            return
        self._vm.job_run_logs.set_filter(DEFAULT_LOG_FILTER)
        self.run_worker(self._vm.job_run_logs.load(), exclusive=True, group="emr-logs")


__all__ = ["EmrServerlessPage"]
