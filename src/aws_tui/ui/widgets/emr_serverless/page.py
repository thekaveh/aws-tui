"""EmrServerlessPage — content-host root for the EMR service.

Composes the top strip + 2-pane body and owns the three auto-refresh
intervals via Textual's ``set_interval``. The intervals are
independent so they back off independently on
:class:`ThrottlingException` (PR-B wires the back-off — PR-A
ships the static cadences from spec §6)."""

from __future__ import annotations

import contextlib
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui import notifications
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM


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
    /* Apps box grows in-place when the application picker is open
       (its OptionList drops down inside the box). ``min-height: 3``
       holds the closed-state row height; ``height: auto`` lets the
       box expand up to the column's available space, with
       JobRunsPane ``1fr`` shrinking to make room. */
    EmrServerlessPage > .emr-left-column > .emr-app-box {
        height: auto;
        min-height: 3;
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
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: EmrServerlessPageVM = vm
        self._hub: MessageHub[Message] = hub
        self._picker: ApplicationPicker | None = None
        self._left: JobRunsPane | None = None
        self._right_detail: JobRunDetailPane | None = None
        self._right_logs: JobRunLogsPane | None = None
        self._runs_tick_counter: int = 0

    def compose(self) -> ComposeResult:
        self._picker = ApplicationPicker(self._vm.applications, hub=self._hub, id="emr-app-picker")
        self._left = JobRunsPane(self._vm.job_runs, hub=self._hub, id="emr-runs-pane")
        self._right_detail = JobRunDetailPane(
            self._vm.job_run_detail, hub=self._hub, id="emr-detail-pane"
        )
        self._right_logs = JobRunLogsPane(self._vm.job_run_logs, hub=self._hub, id="emr-logs-pane")
        # Page layout — 1fr:2fr horizontal split with LEFT column
        # containing the picker + runs pane, and RIGHT column
        # containing detail (top, 1fr) + logs (bottom, 1fr) in a
        # 50/50 vertical split.
        with Vertical(classes="emr-left-column"):
            with Horizontal(classes="emr-app-box", id="emr-app-box"):
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
            box = self.query_one("#emr-app-box", Horizontal)
            # Singular ``application`` reads better in the UI —
            # the box shows the CURRENT app (one) and a dropdown to
            # switch to a different one; it's not a list of apps.
            box.border_title = "application"
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
        # Poller cadence — user feedback (post-PR-#92): "the EMR
        # job runs list seems to be refreshed automatically way too
        # frequently which may not be necessary. We can lower the
        # frequency." Runs poller went 10 s → 30 s; combined with
        # the 6-tick cadence decay on idle (see ``_poll_runs_decay``)
        # that's ~3 min effective when no runs are active.
        self.set_interval(30.0, self._tick_applications, name="emr-poll-apps")
        self.set_interval(30.0, self._tick_runs, name="emr-poll-runs")
        self.set_interval(5.0, self._tick_detail, name="emr-poll-detail")
        # Land Textual focus on the LEFT pane so the user gets the
        # same "arrow keys move the cursor immediately" UX as the S3
        # page. Without this, neither pane shows the
        # ``:focus-within`` accent border and the user has to press
        # Tab once before arrows do anything.
        if self._left is not None:
            self.call_after_refresh(self._left.focus)

    # ── Public accessors ────────────────────────────────────────────────────

    @property
    def left_pane(self) -> JobRunsPane | None:
        """LEFT pane (job runs list). Public so ``AwsTuiApp``'s
        global priority key handlers can forward Up/Down/Enter/r to
        it the same way the S3 path forwards through
        ``dual.focused_pane``."""
        return self._left

    @property
    def right_pane(self) -> JobRunLogsPane | None:
        """RIGHT pane (job-run logs — the focusable half of the right
        column). Public for the same reason as :attr:`left_pane`.

        Note: the detail pane is the top half of the right column but
        is non-focusable and not part of the 2-slot Tab cycle, so it's
        not returned here. Callers can reach it via ``right_detail`` if
        needed for testing or direct access."""
        return self._right_logs

    @property
    def right_detail(self) -> JobRunDetailPane | None:
        """RIGHT-top pane (job-run detail — the passive, non-focusable
        display half of the right column). Exposed for testing/debugging;
        the main Tab cycle is only LEFT ↔ RIGHT-logs."""
        return self._right_detail

    # ── Pollers ─────────────────────────────────────────────────────────────

    def _tick_applications(self) -> None:
        # ``exclusive=True`` so a slow ``list_applications`` doesn't
        # have a second tick land while the first is mid-flight —
        # Textual silently skips overlapping ticks rather than
        # queueing them, which is the right semantic for a poller.
        self.run_worker(self._vm.applications.refresh(), exclusive=True, group="emr-poll-apps")

    def _tick_runs(self) -> None:
        # Cadence-decay: when no active runs, only refresh every 6th tick (~3 min).
        if not self._vm.job_runs.has_active_runs() and self._poll_runs_decay():
            return
        self.run_worker(self._vm.job_runs.refresh(), exclusive=True, group="emr-poll-runs")

    def _tick_detail(self) -> None:
        # Only poll while the run is non-terminal.
        if self._vm.job_run_detail.is_terminal_state():
            return
        self.run_worker(self._vm.job_run_detail.refresh(), exclusive=True, group="emr-poll-detail")

    def _poll_runs_decay(self) -> bool:
        """Return True if THIS tick should be skipped (6:1 decay)."""
        self._runs_tick_counter = (self._runs_tick_counter + 1) % 6
        return self._runs_tick_counter != 0

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_open_application_picker(self) -> None:
        if self._picker is not None:
            self._picker.toggle_open()

    def action_cycle_application_forward(self) -> None:
        """Select the next application in the picker's list
        (wraps at the end). Drives the ``Shift+S`` "switch app"
        affordance — user feedback: the keypress should ACTUALLY
        switch, not just open the picker. The picker is still
        opened explicitly with ``a``.
        """
        self.run_worker(
            self._vm.cycle_application(1),
            exclusive=True,
            group="emr-cycle-app",
        )

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

    def action_cycle_panes_forward(self) -> None:
        self._cycle("right")

    def action_cycle_panes_back(self) -> None:
        self._cycle("left")

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
        try:
            new_id = await self.app.push_screen_wait(modal)
        except Exception as exc:
            # The modal raised after dismiss (extremely rare — e.g.
            # the test harness disposed the app mid-flight). Don't
            # crash the page; surface an advisory toast so the user
            # learns the action didn't land instead of silently
            # losing the click.
            clone_vm.dispose()
            self._post_advisory_toast("Job", f"clone aborted ({exc})")
            return
        try:
            if new_id is None:
                # User cancelled — silent (Cancel is intentional UX,
                # not an error to advertise).
                return
            self._post_clone_success_toast(new_id)
            # Re-fresh the runs list so the new SUBMITTED row appears
            # immediately rather than waiting for the next 30-s tick.
            self.run_worker(
                self._vm.job_runs.refresh(),
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
        # 2-slot cycle: LEFT (JobRunsPane) ↔ RIGHT (JobRunLogsPane).
        # The detail pane is a passive, non-focusable display and doesn't
        # participate in the Tab cycle.
        if self._left is None or self._right_logs is None:
            return
        if self._left.has_focus_within or self._left.has_focus:
            self._right_logs.focus()
        else:
            self._left.focus()

    # ── Message routing ─────────────────────────────────────────────────────

    def on_job_runs_pane_run_selected(self, event: JobRunsPane.RunSelected) -> None:
        self.run_worker(
            self._vm.select_job_run(event.run_id), exclusive=True, group="emr-select-run"
        )

    def on_job_runs_pane_refresh_requested(self, _event: JobRunsPane.RefreshRequested) -> None:
        self.run_worker(self._vm.refresh_focused("runs"), exclusive=True, group="emr-refresh")

    def on_job_runs_pane_load_more_requested(self, _event: JobRunsPane.LoadMoreRequested) -> None:
        """User asked for the next page of runs (PgDn or click on
        the bottom sentinel). Run as ``exclusive=True`` so a slow
        page response can't be double-fired by an impatient
        keypress — the second call is dropped by Textual rather
        than queued behind the first."""
        self.run_worker(
            self._vm.job_runs.load_more(),
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
        self._vm.job_run_logs.select_log_file(event.kind)
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
