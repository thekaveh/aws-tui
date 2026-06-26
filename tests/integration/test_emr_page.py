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
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
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
async def test_emr_page_tab_cycles_between_panes_not_to_nav_rail(tmp_path: Path) -> None:
    """Spec §2 / PR #66 contract: Tab on the EMR page cycles LEFT ↔ RIGHT,
    NEVER falls through to the App-level priority binding that focuses
    the nav rail. Mirrors the S3 page's Tab-cycle contract.
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
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            left = pilot.app.query_one(JobRunsPane)
            right = pilot.app.query_one(JobRunDetailPane)
            nav = pilot.app.query_one(NavMenu)

            # Focus the LEFT pane first.
            left.focus()
            await pilot.pause()

            # Press Tab — must move focus to RIGHT pane, NOT to nav rail.
            await pilot.press("tab")
            await pilot.pause()

            assert pilot.app.focused is right or right.has_focus_within, (
                f"Tab on EMR LEFT pane focused {pilot.app.focused!r} — expected RIGHT pane. "
                f"This is the spec §2 'exactly 2 slots' regression "
                f"(App-level priority Tab binding hijacking)."
            )
            assert not nav.has_focus_within, (
                "Tab on EMR page focused the nav rail — same UX bug PR #66 fixed for S3."
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
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

            left = pilot.app.query_one(JobRunsPane)

            # (1) Auto-focus: LEFT pane has Textual focus after mount
            # without the user needing to press Tab.
            assert left.has_focus or left.has_focus_within, (
                f"LEFT pane should auto-focus on EMR mount (mirrors S3 "
                f"dual.focused=LEFT default). Got {pilot.app.focused!r}."
            )

            # (2) Arrow keys move the cursor. The pane's internal
            # cursor index starts at 0; Down should advance to 1.
            initial_cursor = left._cursor_index  # type: ignore[attr-defined]
            await pilot.press("down")
            await pilot.pause()
            assert left._cursor_index == initial_cursor + 1, (  # type: ignore[attr-defined]
                f"Down arrow on EMR LEFT pane did not advance the cursor — "
                f"App-level priority binding hijacked the keystroke. "
                f"Got cursor={left._cursor_index!r}, expected {initial_cursor + 1}."  # type: ignore[attr-defined]
            )

            # And Up moves it back.
            await pilot.press("up")
            await pilot.pause()
            assert left._cursor_index == initial_cursor, (  # type: ignore[attr-defined]
                f"Up arrow did not retract the cursor. Got "
                f"{left._cursor_index!r}, expected {initial_cursor}."  # type: ignore[attr-defined]
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
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

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
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

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
            assert left._cursor_index == 1, (  # type: ignore[attr-defined]
                f"Click did not move cursor to row 1. Got {left._cursor_index!r}."  # type: ignore[attr-defined]
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
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()

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
