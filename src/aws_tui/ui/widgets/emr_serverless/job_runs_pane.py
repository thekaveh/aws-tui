# src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py
"""JobRunsPane — LEFT pane of the EMR page.

Pane chrome + state-filter chip row + column header + scrollable run
list. Master-detail; the parent ``EmrServerlessPage`` listens for
the ``RunSelected`` message and re-points the RIGHT detail pane.

Selection follows the cursor: ``Up`` / ``Down`` move the cursor AND
fire ``RunSelected`` (the detail follows immediately, no Enter
required — same UX as a list-master-detail UI). ``Enter`` and click
are also valid commits. ``r`` posts ``RefreshRequested``.

Row layout — three columns, strictly aligned per row:

         NAME              DATE & TIME
    ●    nightly-job      2026-06-25 12:01
    ●    ad-hoc           2026-06-27 12:05
    ●    retry-7b3        2026-06-26 11:58

User feedback (post-PR-#90): the picker drops the textual
``STARTED``/``STOPPED`` labels in favour of a colored Rich-markup
glyph; the job-run row inherits the same semantics. The leading
indicator is a 1-cell colored glyph (green ● SUCCESS, blue ●
RUNNING, dim ⏸ pending, etc.) — no ``SUCCESS``/``FAILED`` text.
The trailing column carries ``YYYY-MM-DD HH:MM`` (was just
``HH:MM:SS`` which user noticed was missing the date AND was being
truncated by row text-overflow).
"""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static

from aws_tui.domain.emr_serverless import JobRunState, JobRunSummary
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState

#: Plain (non-markup) glyph per job-run state. Used by the chip
#: row at the top of the pane where filter-active styling already
#: drives the colour via CSS classes.
_STATE_GLYPH: dict[JobRunState, str] = {
    JobRunState.SUCCESS: "✓",
    JobRunState.RUNNING: "●",
    JobRunState.PENDING: "⏸",
    JobRunState.SUBMITTED: "↻",
    JobRunState.SCHEDULED: "↻",
    JobRunState.QUEUED: "↻",
    JobRunState.FAILED: "✗",
    JobRunState.CANCELLED: "⊘",
    JobRunState.CANCELLING: "⊘",
}

#: Colored Rich-markup glyph per job-run state, used as the
#: indicator cell in the runs list. Glyph SHAPES MUST mirror the
#: chip-row glyphs in :data:`_STATE_GLYPH` — the chip strip is
#: the visual legend, and the row indicators are the items the
#: legend describes. User feedback (post-PR-#92): "the green
#: indicator shown before each job run item doesn't match the
#: legend shown at the top of the runs pane … You need to use
#: indicators chosen from among the element items". Colour is
#: derived from semantics (green ✓ done, cyan ● running, yellow
#: ⏸/↻ waiting, red ✗ failed, dim ⊘ cancelled).
_STATE_MARKER: dict[JobRunState, str] = {
    JobRunState.SUCCESS: "[green]✓[/green]",
    JobRunState.RUNNING: "[cyan]●[/cyan]",
    JobRunState.PENDING: "[yellow]⏸[/yellow]",
    JobRunState.SUBMITTED: "[yellow]↻[/yellow]",
    JobRunState.SCHEDULED: "[yellow]↻[/yellow]",
    JobRunState.QUEUED: "[yellow]↻[/yellow]",
    JobRunState.FAILED: "[red]✗[/red]",
    JobRunState.CANCELLED: "[dim]⊘[/dim]",
    JobRunState.CANCELLING: "[dim]⊘[/dim]",
}

#: Human-readable label per chip (driven by the `1..5` key
#: bindings). Surfaced as the chip's hover tooltip so the user
#: discovers what each chip filters without having to memorise
#: glyphs. User feedback (post-PR-#92): "the legend shown at the
#: top of the runs pane … has no meaningful description for its
#: items". Pairs with the chip key in :data:`_KEY_TO_STATE`.
_CHIP_TOOLTIP: dict[JobRunState, str] = {
    JobRunState.SUCCESS: "Filter: SUCCESS  (press 1 to toggle)",
    JobRunState.RUNNING: "Filter: RUNNING  (press 2 to toggle)",
    JobRunState.PENDING: "Filter: PENDING  (press 3 to toggle)",
    JobRunState.FAILED: "Filter: FAILED  (press 4 to toggle)",
    JobRunState.CANCELLED: "Filter: CANCELLED  (press 5 to toggle)",
}

_KEY_TO_STATE: dict[str, JobRunState] = {
    "1": JobRunState.SUCCESS,
    "2": JobRunState.RUNNING,
    "3": JobRunState.PENDING,
    "4": JobRunState.FAILED,
    "5": JobRunState.CANCELLED,
}

#: Datetime column = ``YYYY-MM-DD HH:MM`` = 16 chars. The detail
#: pane carries the full ISO with seconds; the list shows
#: minute-resolution to keep the column compact.
_DATETIME_FORMAT: str = "%Y-%m-%d %H:%M"
_DATETIME_COL_WIDTH: int = 16
#: Indicator column = 1 visible cell for the glyph + breathing
#: room. Allocated 3 chars so the glyph sits centred.
_INDICATOR_COL_WIDTH: int = 3


class _LoadMoreSentinel(Static):
    """Sentinel row appended at the bottom of the runs list when
    the VM has another page available (``has_more=True``). Click
    or PgDn invokes :meth:`JobRunsPane.action_load_more` and the
    VM appends the next page. Carries no run id — the pane's
    ``on_click`` matches by ``isinstance`` to route clicks here
    instead of treating them as a row selection."""

    pass


class _JobRunRow(Horizontal):
    """One job-run row, laid out as a 3-cell Horizontal so each
    column has its own width and the trailing datetime column
    stays visible while the middle NAME cell ellipsizes on long
    names. Carries ``run_id`` so the pane's
    :meth:`JobRunsPane.on_click` can map a clicked row to the
    underlying run without sharing a Textual widget id between
    paint cycles (which would race ``remove_children`` / ``mount``
    and crash with ``DuplicateIds`` when the pane re-renders).
    """

    def __init__(
        self,
        run: JobRunSummary,
        *,
        run_id: str,
        classes: str | None = None,
    ) -> None:
        super().__init__(classes=classes)
        self.run_id: str = run_id
        self._run: JobRunSummary = run

    def compose(self) -> ComposeResult:
        marker = _STATE_MARKER.get(self._run.state, "?")
        name = self._run.name or self._run.job_run_id
        ts = self._run.created_at.strftime(_DATETIME_FORMAT)
        # Indicator cell USES intentional Rich markup (the colored
        # glyph in ``_STATE_MARKER`` — e.g. ``[green]✓[/green]``)
        # so it stays markup-on. NAME is AWS-controlled — job
        # submitters can give a job any name including ``[brackets]``
        # and Rich's parser would crash on it. Datetime is safe but
        # rendered with ``markup=False`` defensively.
        yield Static(marker, classes="runs-cell-indicator")
        yield Static(name, classes="runs-cell-name", markup=False)
        yield Static(ts, classes="runs-cell-datetime", markup=False)


class JobRunsPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = f"""
    JobRunsPane {{
        height: 1fr;
        layout: vertical;
    }}
    JobRunsPane > .runs-chip-row {{
        height: 1;
        layout: horizontal;
        padding: 0 1;
    }}
    JobRunsPane > .runs-chip-row > .runs-chip {{
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
    }}
    JobRunsPane > .runs-column-header {{
        height: 1;
        padding: 0 1;
        layout: horizontal;
    }}
    JobRunsPane > VerticalScroll {{
        height: 1fr;
    }}
    /* Row = Horizontal of 3 cells (indicator | name | datetime).
       The indicator and datetime cells are fixed-width so the
       columns align across rows; the name cell takes the flex
       middle and ellipsizes long names without nudging the
       trailing datetime out of view. */
    JobRunsPane .runs-row {{
        height: 1;
        padding: 0 1;
        layout: horizontal;
    }}
    JobRunsPane .runs-cell-indicator {{
        width: {_INDICATOR_COL_WIDTH};
        height: 1;
        content-align: left middle;
    }}
    JobRunsPane .runs-cell-name {{
        width: 1fr;
        height: 1;
        padding-right: 1;
        text-overflow: ellipsis;
    }}
    JobRunsPane .runs-cell-datetime {{
        width: {_DATETIME_COL_WIDTH};
        height: 1;
        content-align: right middle;
    }}
    /* "Load more" sentinel row sits at the bottom of the runs list
       when the VM has another page available. Dimmed + italic so
       it visually reads as an affordance, not a real run. */
    JobRunsPane .runs-load-more {{
        text-style: dim italic;
        content-align: center middle;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "commit_selection", "Open"),
        Binding("r", "request_refresh", "Refresh"),
        # PgDn = "load more". Shows in the hint legend so the user
        # discovers the affordance without having to hover the
        # sentinel row at the bottom of the list.
        Binding("pagedown", "load_more", "More"),
        *[
            Binding(k, f"toggle_state_filter('{s.value}')", show=False)
            for k, s in _KEY_TO_STATE.items()
        ],
    ]

    class RunSelected(TextualMessage):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class RefreshRequested(TextualMessage):
        pass

    class LoadMoreRequested(TextualMessage):
        """User asked for the next page of runs (PgDn or click on
        the "Load more" sentinel row). The page widget runs
        :meth:`JobRunsVM.load_more` and the new rows append into
        the existing list."""

        pass

    def __init__(
        self,
        vm: JobRunsVM,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunsVM = vm
        # Round-3 directive §9.bis.11: the View no longer holds a
        # hand-rolled cursor index. The position is derived on demand
        # from ``vm.selected_id`` (the VM-owned canonical slot).
        # Arrow / click handlers call ``vm.select(next_id)`` which
        # mutates the composite's ``current`` slot; the resulting
        # `selected_id` event drives the same `_repaint_selection`
        # path, plus we paint synchronously in the handler to avoid
        # an event-loop hop's worth of cursor-trail.
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="runs-chip-row"):
            for state in (
                JobRunState.SUCCESS,
                JobRunState.RUNNING,
                JobRunState.PENDING,
                JobRunState.FAILED,
                JobRunState.CANCELLED,
            ):
                chip = Static(
                    f" {_STATE_GLYPH[state]} ",
                    classes=f"runs-chip runs-chip-{state.value.lower()}",
                    id=f"runs-chip-{state.value.lower()}",
                )
                chip.tooltip = _CHIP_TOOLTIP[state]
                yield chip
        with Horizontal(classes="runs-column-header"):
            yield Static("", classes="runs-cell-indicator")
            yield Static("NAME", classes="runs-cell-name")
            yield Static("DATE & TIME", classes="runs-cell-datetime")
        yield VerticalScroll(id="runs-body")

    def on_mount(self) -> None:
        self.border_title = "runs"
        self._refresh_chips()
        self._refresh_rows()
        # Round-3 directive §9.bis.11 / PR #103 retirement: subscribe
        # to JobRunsVM's per-instance Observable. This Subject only
        # fires for THIS VM, so no `sender_object` filtering is
        # needed against cross-VM `state` echoes from sibling EMR
        # VMs sharing the hub.
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_property_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_toggle_state_filter(self, state_value: str) -> None:
        self._vm.toggle_state_filter(JobRunState(state_value))

    def _cursor_index(self) -> int:
        """Derived cursor index over ``vm.selected_id``. Returns 0
        when no run is selected (the default ENTER target)."""
        runs = self._vm.runs
        if not runs:
            return 0
        sel = self._vm.selected_id
        if sel is None:
            return 0
        for idx, r in enumerate(runs):
            if r.job_run_id == sel:
                return idx
        return 0

    def _cursor_is_on_visible_row(self) -> bool:
        """True when ``vm.selected_id`` actually points at a row in
        the current ``vm.runs`` projection. False when no selection,
        or when a state-filter chip toggle hid the selected row (the
        VM's selection is intentionally non-destructive over filter
        projections — see test_state_filter_does_not_lose_selection_on
        _filter_change)."""
        sel = self._vm.selected_id
        if sel is None:
            return False
        return any(r.job_run_id == sel for r in self._vm.runs)

    def action_cursor_up(self) -> None:
        runs = self._vm.runs
        if not runs:
            return
        # When the prior selection is hidden by the active state
        # filter, ``_cursor_index()`` reports 0 even though no row
        # is visually cursored — pressing Up would silently no-op.
        # Snap to the LAST visible row so Up has the intuitive "wrap
        # in from below" effect on a stale-selection list.
        if not self._cursor_is_on_visible_row():
            target_id = runs[-1].job_run_id
            self._vm.select(target_id)
            self._repaint_selection()
            self._post_run_selected_for_cursor()
            return
        cur = self._cursor_index()
        if cur <= 0:
            return
        target_id = runs[cur - 1].job_run_id
        # Mutate the VM (the canonical cursor slot) BEFORE painting
        # so a subsequent _repaint_selection reads the new selected
        # id from the VM. Same lightweight class-flip repaint, same
        # master-detail RunSelected post — only the cursor source of
        # truth moved.
        self._vm.select(target_id)
        self._repaint_selection()
        self._post_run_selected_for_cursor()

    def action_cursor_down(self) -> None:
        runs = self._vm.runs
        if not runs:
            return
        # Same hidden-selection handling as ``action_cursor_up`` — snap
        # to the first visible row so Down works symmetrically.
        if not self._cursor_is_on_visible_row():
            target_id = runs[0].job_run_id
            self._vm.select(target_id)
            self._repaint_selection()
            self._post_run_selected_for_cursor()
            return
        cur = self._cursor_index()
        if cur + 1 >= len(runs):
            return
        target_id = runs[cur + 1].job_run_id
        self._vm.select(target_id)
        self._repaint_selection()
        self._post_run_selected_for_cursor()

    def action_commit_selection(self) -> None:
        # Enter is a no-op-vs-Up/Down: the cursor-move already fired
        # RunSelected. We still post it on commit so a click or Enter
        # without any prior arrow press also lands the detail.
        self._post_run_selected_for_cursor()

    def _post_run_selected_for_cursor(self) -> None:
        runs = self._vm.runs
        idx = self._cursor_index()
        if not runs or not (0 <= idx < len(runs)):
            return
        run_id = runs[idx].job_run_id
        self.post_message(self.RunSelected(run_id))

    def action_request_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

    def action_load_more(self) -> None:
        """Post LoadMoreRequested if the VM has another page. No-op
        when the list is already fully drained — the user can
        still press PgDn idly without triggering a wasted call."""
        if self._vm.has_more:
            self.post_message(self.LoadMoreRequested())

    # ── Mouse ───────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        """Click on a row → move cursor to that row + select it.

        Mirrors S3's pane click behaviour. Click on the "Load more"
        sentinel row at the bottom fires :class:`LoadMoreRequested`
        instead of a selection. Rows mount as :class:`_JobRunRow`
        widgets carrying ``run_id`` as a Python attribute; the
        sentinel is a :class:`_LoadMoreSentinel` (matched by
        ``isinstance`` so it can't collide with a row). We walk the
        event widget chain to find whichever marker comes first.
        """
        target: object | None = event.widget
        while target is not None:
            if isinstance(target, _LoadMoreSentinel):
                self.action_load_more()
                return
            if isinstance(target, _JobRunRow):
                # Click → cursor moves to that run via the VM's
                # canonical select() slot; same downstream paint +
                # post as the arrow path.
                self._vm.select(target.run_id)
                self._repaint_selection()
                self.post_message(self.RunSelected(target.run_id))
                return
            target = getattr(target, "parent", None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, prop: str) -> None:
        """Round-3 directive: per-VM Observable subscription. The
        cross-VM `state` collision the PR #103 hub-filter was
        guarding against can't reach this handler because the
        Subject is scoped to JobRunsVM only.

        Note: ``selected_id`` is deliberately NOT in the redraw set.
        The pane's visible selection is driven by the cursor index
        (already updated synchronously in ``action_cursor_up`` /
        ``_down``); reacting to ``selected_id`` here would force a
        full ``remove_children`` + re-mount on every arrow press —
        the visible flash the user reported post-PR-#98.
        """
        if prop == "state_filter":
            self.call_after_refresh(self._refresh_chips)
            self.call_after_refresh(self._refresh_rows)
        elif prop in {"runs", "state"}:
            self.call_after_refresh(self._refresh_rows)

    def _refresh_chips(self) -> None:
        active = self._vm.state_filter
        for state in (
            JobRunState.SUCCESS,
            JobRunState.RUNNING,
            JobRunState.PENDING,
            JobRunState.FAILED,
            JobRunState.CANCELLED,
        ):
            try:
                chip = self.query_one(f"#runs-chip-{state.value.lower()}", Static)
            except Exception:
                continue
            if state in active:
                chip.add_class("-active")
            else:
                chip.remove_class("-active")

    def _repaint_selection(self) -> None:
        """Flip the ``-selected`` class on the rows so the visible
        cursor matches the VM's `selected_id` — WITHOUT re-mounting.
        Used by arrow / click handlers where only the cursor
        position changed; the underlying run rows are unchanged so
        wiping them and rebuilding (``_refresh_rows``) is pure
        flicker. Under round-3 (§9.bis.11) the cursor source of
        truth is the VM's `selected_id`; the View is a derived
        projection."""
        target_run_id = self._vm.selected_id
        for row in self.query(_JobRunRow):
            row.set_class(row.run_id == target_run_id, "-selected")

    def _refresh_rows(self) -> None:
        try:
            body = self.query_one("#runs-body", VerticalScroll)
        except Exception:
            return
        body.remove_children()
        state = self._vm.state
        runs = self._vm.runs
        # Order mirrors ``JobRunDetailPane._refresh``: PROVIDER-error
        # states FIRST (UNREACHABLE / AUTH_REQUIRED / FORBIDDEN /
        # ERROR), then LOADING, then EMPTY / empty-cache fallback,
        # then the rows. The old ordering checked ``EMPTY or not
        # runs`` before the error branches, so an UNREACHABLE pane
        # with an empty ``runs`` cache (the typical post-error case)
        # silently rendered "(no runs)" instead of the actionable
        # placeholder.
        if state is PaneState.LOADING:
            body.mount(Static("loading…", classes="runs-placeholder"))
            return
        if state is PaneState.UNREACHABLE:
            body.mount(
                Static(
                    self._vm.error_text or "endpoint unreachable — press r to retry",
                    classes="runs-placeholder",
                )
            )
            return
        if state is PaneState.AUTH_REQUIRED:
            body.mount(
                Static(
                    "authentication required — aws sso login --profile <X>",
                    classes="runs-placeholder",
                )
            )
            return
        if state is PaneState.FORBIDDEN:
            body.mount(
                Static(
                    self._vm.error_text or "permission denied — check IAM policy",
                    classes="runs-placeholder",
                )
            )
            return
        if state is PaneState.ERROR:
            body.mount(
                Static(
                    self._vm.error_text or "error — press r to retry",
                    classes="runs-placeholder",
                )
            )
            return
        if state is PaneState.EMPTY or not runs:
            body.mount(Static("(no runs)", classes="runs-placeholder"))
            return
        # The cursor position is derived from `vm.selected_id`; on
        # first mount or after a refresh that clears the prior
        # selection, the VM-side restore logic re-points current
        # (or leaves it None). Either way, paint via id match —
        # there is no View-side fallback clamp needed.
        cursor_run_id = self._vm.selected_id
        for r in runs:
            row_classes = "runs-row"
            if r.job_run_id == cursor_run_id:
                row_classes += " -selected"
            body.mount(_JobRunRow(r, run_id=r.job_run_id, classes=row_classes))
        if self._vm.has_more:
            body.mount(
                _LoadMoreSentinel(
                    "↓  Load more  (PgDn)",
                    classes="runs-row runs-load-more",
                )
            )


__all__ = ["JobRunsPane"]
