"""End-to-end integration: EMR nav row appears on AWS connections
and disappears on s3-compatible. Selecting the row mounts
EmrServerlessPage in the content host."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest

from aws_tui import app as app_module
from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.context_picker import ContextPicker
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
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


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


_AWS_TOML = (
    "[connections.dev]\n"
    'kind = "aws"\n'
    'profile = "dev"\n'
    'region = "us-east-1"\n'
    "[defaults]\n"
    'connection = "dev"\n'
)

_MULTI_PROFILE_AWS_TOML = (
    "[connections.dev]\n"
    'kind = "aws"\n'
    'profile = "dev"\n'
    'region = "us-east-1"\n'
    "[connections.prod]\n"
    'kind = "aws"\n'
    'profile = "prod"\n'
    'region = "us-west-2"\n'
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
async def test_emr_page_mounts_on_aws_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    factory_calls: list[str] = []
    original_factory = app_module.build_service_view

    def recording_factory(service_id: str, vm: object, **kwargs: object) -> object:
        factory_calls.append(service_id)
        return original_factory(service_id, vm, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app_module, "build_service_view", recording_factory)
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
            assert "emr-serverless" in factory_calls
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_nav_row_hidden_on_s3_compatible_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _S3COMPAT_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    minio = ctx.connection_resolver.resolve("minio")
    ctx.connection_resolver.list = lambda: [minio]  # type: ignore[assignment,method-assign]
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
async def test_emr_page_tab_cycle_includes_source_application_and_panes(
    tmp_path: Path,
) -> None:
    """The EMR ring includes every visible selector and pane."""
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
            source = pilot.app.query_one(ServiceSourceHeader)
            application = pilot.app.query_one(ApplicationPicker)

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

            # NAV → SOURCE → APPLICATION → RUNS.
            await pilot.press("tab")
            await pilot.pause()
            assert source.has_focus or source.has_focus_within
            await pilot.press("tab")
            await pilot.pause()
            assert application.has_focus or application.has_focus_within
            await pilot.press("tab")
            await pilot.pause()
            assert left.has_focus or left.has_focus_within
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_focus_projects_bidirectionally_through_coordinator(tmp_path: Path) -> None:
    """Direct Textual focus and app-level slot projection stay in sync."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            left = app.query_one(JobRunsPane)
            await _wait_until(lambda: left.has_focus_within)

            source = app.query_one(ServiceSourceHeader)
            source.focus()
            await _wait_until(lambda: ctx.focus_coordinator.focused_slot is FocusSlot.EMR_SOURCE)
            assert ctx.focus_coordinator.focused_slot is FocusSlot.EMR_SOURCE

            application = app.query_one(ApplicationPicker)
            application.focus()
            await _wait_until(
                lambda: ctx.focus_coordinator.focused_slot is FocusSlot.EMR_APPLICATION
            )
            assert ctx.focus_coordinator.focused_slot is FocusSlot.EMR_APPLICATION

            app._project_focus_slot(FocusSlot.EMR_DETAIL)
            await pilot.pause()
            assert app.query_one(JobRunDetailPane).has_focus_within
            assert ctx.focus_coordinator.focused_slot is FocusSlot.EMR_DETAIL

            app.focus_active_service_pane()
            await pilot.pause()
            assert app.query_one(JobRunsPane).has_focus_within
            assert ctx.focus_coordinator.focused_slot is FocusSlot.EMR_RUNS
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
async def test_emr_public_routing_delegates_to_focused_panes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    calls: list[str] = []
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page = app.query_one(EmrServerlessPage)
            left = app.query_one(JobRunsPane)
            detail = app.query_one(JobRunDetailPane)
            logs = app.query_one(JobRunLogsPane)
            monkeypatch.setattr(left, "action_cursor_down", lambda: calls.append("runs-down"))
            monkeypatch.setattr(left, "action_commit_selection", lambda: calls.append("runs-enter"))
            monkeypatch.setattr(logs, "action_scroll_down", lambda: calls.append("logs-down"))
            monkeypatch.setattr(logs, "action_load", lambda: calls.append("logs-enter"))

            left.focus()
            await pilot.pause()
            assert page.move_focused(1)
            assert page.activate_focused()

            detail.focus()
            await pilot.pause()
            assert page.move_focused(1)
            assert page.activate_focused()

            logs.focus()
            await pilot.pause()
            assert page.move_focused(1)
            assert page.activate_focused()

            assert calls == ["runs-down", "runs-enter", "logs-down", "logs-enter"]
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
        async with app.run_test(size=(80, 30)) as pilot:
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
async def test_emr_application_picker_closes_on_outside_click(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _MULTI_PROFILE_AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 30)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            picker = app.query_one(ApplicationPicker)
            picker.toggle_open()
            await pilot.pause()
            assert picker.is_open

            source_picker = app.query_one("#emr-source-header-picker", ContextPicker)
            await pilot.click(source_picker)
            await pilot.pause()

            assert not picker.is_open
            assert source_picker.is_open
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_application_picker_overlay_preserves_global_geometry(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            widgets = (
                app.query_one(ApplicationPicker),
                app.query_one("#emr-app-box"),
                app.query_one(".emr-context-row"),
                app.query_one(ServiceSourceHeader),
                app.query_one(JobRunsPane),
                app.query_one(JobRunDetailPane),
                app.query_one(NavMenu),
                app.query_one("#content-host"),
            )
            closed_regions = tuple(widget.region for widget in widgets)
            picker = app.query_one(ApplicationPicker)

            picker.toggle_open()
            await pilot.pause()
            assert tuple(widget.region for widget in widgets) == closed_regions

            await pilot.press("escape")
            await pilot.pause()
            assert tuple(widget.region for widget in widgets) == closed_regions
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_switching_away_from_emr_closes_open_application_picker(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(80, 30)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page = app.query_one(EmrServerlessPage)
            picker = app.query_one(ApplicationPicker)
            refocus_calls = 0

            def record_refocus() -> None:
                nonlocal refocus_calls
                refocus_calls += 1

            picker._refocus = record_refocus  # type: ignore[method-assign]
            picker.toggle_open()
            await pilot.pause()
            assert page.has_class("-application-picker-open")

            ctx.root_vm.services_menu.switch_service_command.execute("s3")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            assert not picker.is_open
            assert not picker.is_running
            assert not page.has_class("-application-picker-open")
            assert refocus_calls == 0
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
async def test_emr_shift_s_rebuilds_under_current_profile_when_only_one_exists(
    tmp_path: Path,
) -> None:
    """``Shift+S`` remounts EMR without cycling its application selection."""
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    dev = ctx.connection_resolver.resolve("dev")
    ctx.connection_resolver.list = lambda: [dev]  # type: ignore[method-assign]
    fake.add_application(app_id="00other", name="ad-hoc")
    fake.add_job_run(application_id="00other", job_run_id="r-other")
    fake.add_job_run_detail(application_id="00other", job_run_id="r-other")

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            await ctx.root_vm.switch_connection_with(dev, TokenState.CONNECTED)
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page_vm = ctx.root_vm.content_host.current
            initial_app_id = page_vm.applications.selected_id
            assert initial_app_id is not None

            await app.action_swap_source()
            await _await_emr_mount(pilot, app)

            replacement = ctx.root_vm.content_host.current
            assert replacement is not page_vm
            assert replacement.source.connection_key == ("dev", "us-east-1")
            assert replacement.applications.selected_id == initial_app_id
            assert replacement.job_runs.application_id == initial_app_id
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_shift_s_switches_profile_and_shift_a_cycles_application(
    tmp_path: Path,
) -> None:
    config_dir = _prep(tmp_path, _MULTI_PROFILE_AWS_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    dev = ctx.connection_resolver.resolve("dev")
    prod = ctx.connection_resolver.resolve("prod")
    ctx.connection_resolver.list = lambda: [dev, prod]  # type: ignore[method-assign]

    def build_client(connection: Connection) -> _InMemoryEmr:
        fake = _InMemoryEmr()
        if connection == dev:
            fake.add_application(app_id="dev-app", name="development")
        else:
            fake.add_application(app_id="prod-app-a", name="analytics")
            fake.add_application(app_id="prod-app-b", name="reporting")
        return fake

    for service in ctx.root_vm._registry.all():  # type: ignore[attr-defined]
        if isinstance(service, EmrServerlessService):
            service._client_factory = build_client  # type: ignore[assignment]

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            await ctx.root_vm.switch_connection_with(dev, TokenState.CONNECTED)
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_emr_mount(pilot, app)

            page = ctx.root_vm.content_host.current
            initial_source = page.source.connection_key
            initial_application = page.applications.selected_id
            assert initial_application == "dev-app"

            await pilot.press("S")
            await _await_emr_mount(pilot, app)

            page = ctx.root_vm.content_host.current
            assert page.source.connection_key != initial_source
            assert page.applications.selected_id != initial_application
            selected_after_source_switch = page.applications.selected_id

            await pilot.press("A")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            assert page.applications.selected_id != selected_after_source_switch
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_tab_cycle_visits_detail_now_part_of_ring(tmp_path: Path) -> None:
    """One full rotation visits selectors as well as all three data panes."""
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
            source = pilot.app.query_one(ServiceSourceHeader)
            application = pilot.app.query_one(ApplicationPicker)

            # Focus the LEFT pane (the page auto-focuses it on mount).
            left.focus()
            await pilot.pause()
            assert left.has_focus or left.has_focus_within

            # RUNS → DETAIL → LOGS → NAV → SOURCE → APPLICATION → RUNS.
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
            assert source.has_focus or source.has_focus_within
            await pilot.press("tab")
            await pilot.pause()
            assert application.has_focus or application.has_focus_within
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
