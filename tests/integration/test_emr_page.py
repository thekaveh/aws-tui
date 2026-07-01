"""End-to-end integration: EMR nav row appears on AWS connections
and disappears on s3-compatible. Selecting the row mounts
EmrServerlessPage in the content host."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.nav_menu import NavMenu
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _prep(tmp_path: Path, toml_text: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


def _make_ctx_with_emr_fake(config_dir: Path, cache_dir: Path) -> tuple[object, _InMemoryEmr]:
    ctx = build_app_context(config_dir=config_dir, cache_dir=cache_dir)
    fake = _InMemoryEmr()
    fake.add_application(app_id="00emr", name="etl")
    # Swap the registered EmrServerlessService's client factory for
    # the test fake so no boto3 calls escape.
    for svc in ctx.root_vm._registry.all():  # type: ignore[attr-defined]
        if isinstance(svc, EmrServerlessService):
            svc._client_factory = lambda _conn: fake  # type: ignore[assignment]
    return ctx, fake


async def _await_emr_mount(pilot: object, app: AwsTuiApp) -> None:
    await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
    setup_task = app.app_ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


_AWS_TOML = (
    "[connections.dev]\n"
    'kind = "aws"\n'
    'profile = "dev"\n'
    'region = "us-east-1"\n'
    "[defaults]\n"
    'connection = "dev"\n'
)

_S3COMPAT_TOML = (
    "[connections.minio]\n"
    'kind = "s3-compatible"\n'
    'endpoint_url = "http://127.0.0.1:1"\n'
    'region = "us-east-1"\n'
    'access_key_id = "x"\n'
    'secret_access_key = "y"\n'
    "[defaults]\n"
    'connection = "minio"\n'
)


@pytest.mark.asyncio
async def test_emr_page_mounts_on_aws_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # Switch to EMR via the menu VM (avoids keymap routing).
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)
            host = pilot.app.query_one("#content-host")
            assert len(host.query(EmrServerlessPage)) == 1, (
                "expected EmrServerlessPage mounted in #content-host"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_nav_row_hidden_on_s3_compatible_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _S3COMPAT_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # The nav menu's items must NOT include "emr-serverless" when
            # the active connection is s3-compatible.
            ids = [item.descriptor.id for item in ctx.root_vm.services_menu.items]
            assert "emr-serverless" not in ids, (
                f"EMR must be filtered out on s3-compatible connections, got {ids}"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_page_tab_cycle_includes_nav_then_left_detail_logs(tmp_path: Path) -> None:
    """Post-PR-#94 contract: Tab on the EMR page cycles through 4
    slots — NAV → LEFT → DETAIL → LOGS → NAV (and reverse on
    Shift+Tab). User feedback: "I also want the menu pane be
    treated like any other pane in the app, which mean tab
    switching should allow for it being among the switchable panes
    to be selected / focused: … On EMR, should be able to switch
    among the menu, left application job runs pane, and the right
    job details pane" — clarified in follow-up as 4-slot to keep
    Logs reachable for ``Enter``-to-load.
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # Switch to EMR.
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            left = pilot.app.query_one(JobRunsPane)
            right_detail = pilot.app.query_one(JobRunDetailPane)
            right_logs = pilot.app.query_one(JobRunLogsPane)
            nav = pilot.app.query_one(NavMenu)

            # The page lands focus on the LEFT pane on mount.
            await pilot.pause()
            assert left.has_focus or left.has_focus_within

            # LEFT → DETAIL.
            await pilot.press("tab")
            await pilot.pause()
            assert right_detail.has_focus or right_detail.has_focus_within, (
                f"Tab on LEFT should move to DETAIL; got {pilot.app.focused!r}."
            )

            # DETAIL → LOGS.
            await pilot.press("tab")
            await pilot.pause()
            assert right_logs.has_focus or right_logs.has_focus_within, (
                f"Tab on DETAIL should move to LOGS; got {pilot.app.focused!r}."
            )

            # LOGS → NAV (wraps to nav menu — the post-PR-#94
            # contract adds NavMenu as a real cycle slot).
            await pilot.press("tab")
            await pilot.pause()
            assert nav.has_focus_within, (
                f"Tab on LOGS should move to NAV; got {pilot.app.focused!r}."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_left_pane_auto_focuses_and_arrow_keys_move_cursor(tmp_path: Path) -> None:
    """User-reported regression: arrow keys did nothing in the EMR LEFT pane
    AND the selected-row highlight didn't read as "active" because no pane
    had focus by default. Two-part fix mirrors the S3 page's behaviour:

    1. ``EmrServerlessPage.on_mount`` lands Textual focus on the LEFT
       pane via ``call_after_refresh(self._left.focus)`` so the
       ``:focus-within`` accent border kicks in immediately and the
       user sees the same "active pane" treatment S3 gives the file
       pane.
    2. ``AwsTuiApp.action_move_up/down`` / ``action_descend`` /
       ``action_refresh`` route through new ``_emr_page()`` +
       ``_emr_active_pane()`` helpers so the App-level priority
       bindings don't silently swallow Up/Down/Enter/r on the EMR
       page (the same hijack we fixed for Tab in PR #77).
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    # Seed two runs so cursor movement is observable.
    fake.add_job_run(
        application_id="00emr",
        job_run_id="r-001",
        name="run-1",
    )
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-001")
    fake.add_job_run(
        application_id="00emr",
        job_run_id="r-002",
        name="run-2",
    )
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-002")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            left = pilot.app.query_one(JobRunsPane)

            # (1) Auto-focus: LEFT pane has Textual focus after mount
            # without the user needing to press Tab.
            assert left.has_focus or left.has_focus_within, (
                f"LEFT pane should auto-focus on EMR mount (mirrors S3 "
                f"dual.focused=LEFT default). Got {pilot.app.focused!r}."
            )

            # (2) Arrow keys move the cursor. The pane's internal
            # cursor index starts at 0; Down should advance to 1.
            initial_cursor = left._cursor_index()  # type: ignore[attr-defined]
            await pilot.press("down")
            await pilot.pause()
            assert left._cursor_index() == initial_cursor + 1, (  # type: ignore[attr-defined]
                f"Down arrow on EMR LEFT pane did not advance the cursor — "
                f"App-level priority binding hijacked the keystroke. "
                f"Got cursor={left._cursor_index()!r}, expected {initial_cursor + 1}."  # type: ignore[attr-defined]
            )

            # And Up moves it back.
            await pilot.press("up")
            await pilot.pause()
            assert left._cursor_index() == initial_cursor, (  # type: ignore[attr-defined]
                f"Up arrow did not retract the cursor. Got "
                f"{left._cursor_index()!r}, expected {initial_cursor}."  # type: ignore[attr-defined]
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_left_cursor_move_repoints_right_detail(tmp_path: Path) -> None:
    """User-reported bug: "The right pane showing the details for
    each job doesn't automatically get repopulated when the selected
    option changes on the left side." Master-detail UX:
    moving the cursor on LEFT fires ``RunSelected`` which the
    page widget routes to ``page_vm.select_job_run`` — the detail
    VM's ``set_target`` flips to the new (app_id, run_id) and
    ``refresh()`` populates it. Without the cursor-fires-RunSelected
    wiring the detail only updated on Enter / click."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="first")
    fake.add_job_run_detail(
        application_id="00emr", job_run_id="r-001", entry_point="s3://b/first.py"
    )
    fake.add_job_run(application_id="00emr", job_run_id="r-002", name="second")
    fake.add_job_run_detail(
        application_id="00emr", job_run_id="r-002", entry_point="s3://b/second.py"
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            # First run is auto-selected by the page VM's setup().
            detail_vm = ctx.root_vm.content_host.current.job_run_detail
            initial_run_id = detail_vm.detail.job_run_id if detail_vm.detail else None
            # Drive a cursor move.
            await pilot.press("down")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # The detail should now point at the OTHER run, even
            # without Enter being pressed.
            new_run_id = detail_vm.detail.job_run_id if detail_vm.detail else None
            assert new_run_id != initial_run_id, (
                f"Detail pane did not follow the cursor: still showing "
                f"{new_run_id!r} after pressing Down (was {initial_run_id!r}). "
                f"Master-detail wiring broken — cursor-fires-RunSelected "
                f"missing from JobRunsPane.action_cursor_down."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_left_pane_click_selects_and_repoints_detail(tmp_path: Path) -> None:
    """User-reported bug: "Mouse also doesn't work when browsing
    through the items under the left pane of emr like it does for
    s3." Each row mounts as a ``_JobRunRow`` widget that the pane's
    ``on_click`` handler walks back to via ``event.widget``; the
    matched ``run_id`` triggers cursor move + ``RunSelected``."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="first")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-001")
    fake.add_job_run(application_id="00emr", job_run_id="r-002", name="second")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-002")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            from aws_tui.ui.widgets.emr_serverless.job_runs_pane import _JobRunRow

            left = pilot.app.query_one(JobRunsPane)
            rows = list(left.query(_JobRunRow))
            assert len(rows) == 2

            # Click the second row.
            target = next(r for r in rows if r.run_id == "r-002")
            await pilot.click(target)
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            # Cursor moved to row 1 + detail flipped to r-002.
            assert left._cursor_index() == 1, (  # type: ignore[attr-defined]
                f"Click did not move cursor to row 1. Got {left._cursor_index()!r}."  # type: ignore[attr-defined]
            )
            detail_vm = ctx.root_vm.content_host.current.job_run_detail
            assert detail_vm.detail is not None
            assert detail_vm.detail.job_run_id == "r-002", (
                f"Click did not re-point detail. Got {detail_vm.detail.job_run_id!r}."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_page_c_key_pushes_clone_modal(tmp_path: Path) -> None:
    """Pressing ``c`` on the EMR page opens the clone-job-run modal
    pre-populated from the currently-selected job run.

    Verifies the full wiring from PR-C-clone: the page widget's
    ``c`` binding routes to ``action_clone_selected_run``, which
    builds a ``JobRunCloneVM`` from the page VM's
    ``job_run_detail.detail`` and pushes ``JobRunCloneModal``
    onto Textual's screen stack. No submit is exercised here — the
    submit/Submit-failure paths live in the unit tests."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="nightly")
    fake.add_job_run_detail(
        application_id="00emr",
        job_run_id="r-001",
        entry_point="s3://b/job.py",
        entry_point_arguments=("--in", "s3://b/in/"),
        spark_submit_parameters="--conf spark.executor.instances=4",
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            # The page VM should now hold a detail for r-001 via the
            # auto-select-first-run path in ``EmrServerlessPageVM.setup``.
            detail_vm = ctx.root_vm.content_host.current.job_run_detail
            assert detail_vm.detail is not None, (
                "Precondition: clone needs a selected detail before pressing c."
            )

            # Press c on the focused EMR page.
            await pilot.press("c")
            await pilot.pause()

            # The modal should be on top of the screen stack.
            modals = [s for s in pilot.app.screen_stack if isinstance(s, JobRunCloneModal)]
            assert len(modals) == 1, (
                f"Expected JobRunCloneModal pushed by 'c' binding; got stack={pilot.app.screen_stack!r}"
            )
            modal = modals[0]
            # Form is pre-populated from the detail.
            assert modal.vm.entry_point == "s3://b/job.py"
            assert modal.vm.entry_point_arguments == ("--in", "s3://b/in/")
            assert modal.vm.spark_submit_parameters == "--conf spark.executor.instances=4"
            # Dismiss to leave the test in a clean state.
            modal.dismiss(None)
            await pilot.pause()
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_picker_commit_cascades_to_runs_pane(tmp_path: Path) -> None:
    """User-reported: selecting a different application in the picker
    dropdown did NOT update the JobRuns pane below.

    Root cause: the picker called ``ApplicationsVM.select(id)``
    which only flips the picker's own ``_selected_id``; the sibling
    ``JobRunsVM`` doesn't observe it. The fix has the picker post
    ``ApplicationPicker.ApplicationCommitted``; the page widget
    catches it and runs ``page_vm.select_application(id)`` which
    cascades through ``job_runs.set_application`` +
    ``job_runs.refresh`` + ``job_run_detail.set_target``.

    This test seeds two applications, mounts the EMR page,
    commits a selection to the SECOND app via the picker's
    ``action_commit``, and asserts ``job_runs.application_id``
    flipped to that second app.
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    # Add a second application so the picker has somewhere to switch.
    fake.add_application(app_id="00other", name="ad-hoc")
    fake.add_job_run(application_id="00other", job_run_id="r-other", name="other-run")
    fake.add_job_run_detail(application_id="00other", job_run_id="r-other")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page_vm = ctx.root_vm.content_host.current
            page = pilot.app.query_one(EmrServerlessPage)
            picker = page._picker
            assert picker is not None
            # Pre-condition: the runs pane is currently bound to the
            # first app's id (auto-selected by ``setup``).
            initial_app_id = page_vm.applications.selected_id
            assert initial_app_id is not None
            assert page_vm.job_runs.application_id == initial_app_id
            other_app_id = "00emr" if initial_app_id == "00other" else "00other"

            # Open the picker, highlight the OTHER app's row, commit.
            picker.toggle_open()
            await pilot.pause()
            opts = picker.query_one("#app-options")
            for idx in range(opts.option_count):
                opt = opts.get_option_at_index(idx)
                if opt.id == other_app_id:
                    opts.highlighted = idx
                    break
            picker.action_commit()
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            # The cascade ran: picker's ``selected_id`` AND the
            # ``JobRunsVM.application_id`` both flipped to the new app.
            assert page_vm.applications.selected_id == other_app_id
            assert page_vm.job_runs.application_id == other_app_id, (
                "Picker commit should cascade through "
                "page_vm.select_application(); JobRunsVM "
                "must be re-scoped to the new app or the runs pane "
                "shows stale data."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_shift_s_cycles_to_next_application(tmp_path: Path) -> None:
    """``Shift+S`` on the EMR page cycles to the next application
    (wraps at the end). User feedback: pre-fix it just opened the
    picker; user expected an actual app switch.

    The keystroke routes through ``AwsTuiApp.action_swap_source``
    which short-circuits to ``EmrServerlessPage.action_cycle_application_forward``
    when the EMR page is mounted.
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_application(app_id="00other", name="ad-hoc")
    fake.add_job_run(application_id="00other", job_run_id="r-other")
    fake.add_job_run_detail(application_id="00other", job_run_id="r-other")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page_vm = ctx.root_vm.content_host.current
            initial_app_id = page_vm.applications.selected_id
            assert initial_app_id is not None

            await pilot.press("S")  # Shift+S
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            # Selection moved off the initial app.
            assert page_vm.applications.selected_id != initial_app_id, (
                "Shift+S should actually switch the application — pre-fix "
                "it only opened the picker, which the user reported as a bug."
            )
            # Cascade ran: runs pane is bound to the new app.
            assert page_vm.job_runs.application_id == page_vm.applications.selected_id
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_tab_cycle_visits_detail_now_part_of_ring(tmp_path: Path) -> None:
    """Post-PR-#94 cycle now includes the Detail pane as a real
    slot — Detail's ``can_focus = True``. Verifies one full
    rotation LEFT → DETAIL → LOGS → NAV → LEFT lands each slot
    once and detail isn't skipped (the prior 2-slot cycle did skip
    it).
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="test-run")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-001")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            left = pilot.app.query_one(JobRunsPane)
            right_logs = pilot.app.query_one(JobRunLogsPane)
            right_detail = pilot.app.query_one(JobRunDetailPane)
            nav = pilot.app.query_one(NavMenu)

            # Focus the LEFT pane (the page auto-focuses it on mount).
            left.focus()
            await pilot.pause()
            assert left.has_focus or left.has_focus_within

            # LEFT → DETAIL → LOGS → NAV → LEFT (one full rotation).
            await pilot.press("tab")
            await pilot.pause()
            assert right_detail.has_focus or right_detail.has_focus_within
            await pilot.press("tab")
            await pilot.pause()
            assert right_logs.has_focus or right_logs.has_focus_within
            await pilot.press("tab")
            await pilot.pause()
            assert nav.has_focus_within
            await pilot.press("tab")
            await pilot.pause()
            assert left.has_focus or left.has_focus_within, (
                f"Full Tab rotation should return to LEFT; got {pilot.app.focused!r}."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_detail_focus_swallows_cursor_keys(tmp_path: Path) -> None:
    """Detail is a real focus slot, so global cursor bindings must not
    fall back to the runs pane while the visible focus border is on
    detail."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="first")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-001")
    fake.add_job_run(application_id="00emr", job_run_id="r-002", name="second")
    fake.add_job_run_detail(application_id="00emr", job_run_id="r-002")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            left = pilot.app.query_one(JobRunsPane)
            right_detail = pilot.app.query_one(JobRunDetailPane)
            page_vm = ctx.root_vm.content_host.current
            selected_before = page_vm.job_run_detail.detail.job_run_id
            cursor_before = left._cursor_index()  # type: ignore[attr-defined]

            right_detail.focus()
            await pilot.pause()
            assert right_detail.has_focus or right_detail.has_focus_within

            await pilot.press("down")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            assert left._cursor_index() == cursor_before  # type: ignore[attr-defined]
            assert page_vm.job_run_detail.detail.job_run_id == selected_before
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_detail_focus_refreshes_detail_pane(tmp_path: Path) -> None:
    """Pressing r on focused detail refreshes detail, not the runs list."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="first")
    fake.add_job_run_detail(
        application_id="00emr",
        job_run_id="r-001",
        entry_point="s3://bucket/original.py",
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            right_detail = pilot.app.query_one(JobRunDetailPane)
            page_vm = ctx.root_vm.content_host.current
            assert page_vm.job_run_detail.detail.entry_point == "s3://bucket/original.py"

            fake.add_job_run_detail(
                application_id="00emr",
                job_run_id="r-001",
                entry_point="s3://bucket/refreshed.py",
            )
            right_detail.focus()
            await pilot.pause()
            assert right_detail.has_focus or right_detail.has_focus_within

            await pilot.press("r")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            assert page_vm.job_run_detail.detail.entry_point == "s3://bucket/refreshed.py"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_logs_pane_starts_idle_on_run_select(tmp_path: Path) -> None:
    """Task 14 optional: on-demand contract — logs VM transitions
    EMPTY_TARGET → IDLE when a run is selected, WITHOUT auto-loading.

    This pins the core design: logs are fetched on-demand (user presses
    Enter in the logs pane to load), not automatically when a run is
    selected. The IDLE state indicates a target is set but no fetch has
    happened yet.

    After the page mounts and its setup() auto-selects the first run
    (via selection), the logs VM should:
    - Have state=IDLE (not EMPTY_TARGET, not LOADING)
    - Have empty lines tuple (not loaded)
    - Be ready for the user to press Enter and trigger load()
    """
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    # Seed with s3_monitoring_log_uri so the VM transitions to IDLE
    # instead of NO_LOG_CONFIG.
    fake.add_job_run(application_id="00emr", job_run_id="r-001", name="test-run")
    fake.add_job_run_detail(
        application_id="00emr",
        job_run_id="r-001",
        s3_monitoring_log_uri="s3://my-bucket/path/to/logs",
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page_vm = ctx.root_vm.content_host.current
            logs_vm = page_vm.job_run_logs

            # After setup auto-selects the first run, the logs VM should
            # have transitioned from EMPTY_TARGET to IDLE.
            from aws_tui.vm.emr_serverless.job_run_logs_vm import LogsState

            assert logs_vm.state is LogsState.IDLE, (
                f"On-demand contract: logs VM must be IDLE (target set, "
                f"not loaded) after run selection. Got state={logs_vm.state!r}. "
                f"This pins that load() is NOT auto-invoked on run selection."
            )
            assert logs_vm.application_id == "00emr", (
                f"Logs VM target should have app_id set. Got {logs_vm.application_id!r}."
            )
            assert logs_vm.job_run_id == "r-001", (
                f"Logs VM target should have run_id set. Got {logs_vm.job_run_id!r}."
            )
            assert logs_vm.lines == (), (
                f"On-demand contract: logs should be empty (not loaded yet). "
                f"Got {len(logs_vm.lines)} lines — load() was invoked automatically, "
                f"violating the on-demand contract."
            )
            assert logs_vm.available_files == (), (
                f"On-demand contract: available_files should be empty until "
                f"load() is invoked. Got {logs_vm.available_files!r}."
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
