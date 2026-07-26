from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo import seeds
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.domain.filesystem import EntryKind
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from aws_tui.vm.messages import OpenS3LocationRequest


async def _wait_for_service_setup(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> None:
    await app.workers.wait_for_complete(list(app.workers._workers))
    setup_task = ctx.root_vm.content_host._setup_task  # type: ignore[attr-defined]
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


def _athena_service(ctx: AppContext) -> AthenaService:
    service = ctx.registry.get("athena")
    assert isinstance(service, AthenaService)
    return service


def _athena_client(ctx: AppContext, profile: str) -> InMemoryAthena:
    connection = ctx.connection_resolver.resolve(profile)
    factory = _athena_service(ctx)._client_factory
    assert factory is not None
    client = factory(connection)
    assert isinstance(client, InMemoryAthena)
    return client


async def _open_athena(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
    *,
    profile: str = "demo-dev",
) -> AthenaPageVM:
    await _wait_for_service_setup(ctx, app, pilot)
    ctx.root_vm.services_menu.switch_service_command.execute("athena")
    await _wait_for_service_setup(ctx, app, pilot)
    while (
        ctx.root_vm.active_connection is not None and ctx.root_vm.active_connection.name != profile
    ):
        await app.action_swap_source()
        await _wait_for_service_setup(ctx, app, pilot)
    page = ctx.root_vm.content_host.current
    assert isinstance(page, AthenaPageVM)
    return page


async def _invoke_result_handoff(app: AwsTuiApp) -> None:
    result = app.action_dispatch("athena.open_result_location")
    if inspect.isawaitable(result):
        await result


@pytest.mark.asyncio
async def test_history_result_location_opens_same_profile_in_s3(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.select_view("history")
            await page.select_history_execution("q-dev-succeeded")

            await _invoke_result_handoff(app)
            await _wait_for_service_setup(ctx, app, pilot)

            assert ctx.root_vm.content_host.current_id == "s3"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            dual = ctx.root_vm.content_host.current
            assert isinstance(dual, DualPaneVM)
            assert dual.left.current_connection_key == ("aws", "demo-dev")
            assert dual.left.path.as_posix() == "/athena-results/dev"
            assert dual.left.selected_entry is not None
            assert dual.left.selected_entry.entry.name == "q-dev-succeeded.csv"
            assert dual.left.selected_entry.kind is EntryKind.FILE
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_results_handoff_reloads_authoritative_execution_output(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot, profile="demo-prod")
            await page.select_view("history")
            await page.select_history_execution("q-prod-succeeded")
            await page.open_history_results()
            assert ctx.root_vm.content_host.current_id == "athena"
            assert page.active_view == "results"

            client = _athena_client(ctx, "demo-prod")
            client.calls.clear()
            await _invoke_result_handoff(app)
            await _wait_for_service_setup(ctx, app, pilot)

            assert any(
                call.method == "get_query_execution" and call.arguments == ("q-prod-succeeded",)
                for call in client.calls
            )
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-prod"
            dual = ctx.root_vm.content_host.current
            assert isinstance(dual, DualPaneVM)
            assert dual.left.current_connection_key == ("aws", "demo-prod")
            assert dual.left.path.as_posix() == "/athena-results/prod"
            assert dual.left.selected_entry is not None
            assert dual.left.selected_entry.entry.name == "q-prod-succeeded.csv"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_id", "output_location"),
    [
        ("q-dev-missing-output", None),
        ("q-dev-malformed-output", "https://example.test/RESULT_URI_SECRET"),
    ],
)
async def test_missing_or_malformed_result_location_stays_in_athena_and_advises(
    tmp_path: Path,
    execution_id: str,
    output_location: str | None,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        client = _athena_client(ctx, "demo-dev")
        if output_location is not None:
            detail = client.query_executions["q-dev-missing-output"]
            malformed = replace(
                detail,
                summary=replace(
                    detail.summary,
                    ref=replace(detail.summary.ref, execution_id=execution_id),
                ),
                output_location=output_location,
            )
            client.query_executions[execution_id] = malformed
            client.history["dev-analytics"].append(execution_id)

        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.select_view("history")
            await page.select_history_execution(execution_id)

            await _invoke_result_handoff(app)

            assert ctx.root_vm.content_host.current_id == "athena"
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "athena-result-location-invalid"
            assert "selected execution has no valid S3 result location" in toast.text
            ctx.log_sink.flush()
            diagnostic = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
            assert "RESULT_URI_SECRET" not in diagnostic
            assert "example.test" not in diagnostic
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_loading_history_results_never_automatically_hands_off_to_s3(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.select_view("history")
            await page.select_history_execution("q-dev-succeeded")

            await page.open_history_results()
            await pilot.pause()

            assert ctx.root_vm.content_host.current_id == "athena"
            assert page.active_view == "results"
            assert page.results.execution_id == "q-dev-succeeded"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_result_handoff_rejects_execution_identity_mismatch(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        client = _athena_client(ctx, "demo-dev")
        detail = client.query_executions["q-dev-succeeded"]
        forged_context = replace(
            detail.context,
            connection_name="demo-prod",
            workgroup="prod-reporting",
            catalog="ProdDataCatalog",
            database="prod_sales",
        )
        client.query_executions["q-dev-succeeded"] = replace(
            detail,
            summary=replace(
                detail.summary,
                ref=replace(
                    detail.summary.ref,
                    connection_name="demo-prod",
                    workgroup="prod-reporting",
                ),
            ),
            context=forged_context,
            output_location="s3://athena-results/prod/q-prod-succeeded.csv",
        )

        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.results.load("q-dev-succeeded")
            await page.select_view("results")

            await _invoke_result_handoff(app)

            assert ctx.root_vm.content_host.current_id == "athena"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "athena-result-location-invalid"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


def _result_request(*, preferred_pane: str = "left") -> OpenS3LocationRequest:
    return OpenS3LocationRequest(
        connection_name="demo-dev",
        region="us-east-1",
        uri="s3://athena-results/dev/q-dev-succeeded.csv",
        preferred_pane=preferred_pane,  # type: ignore[arg-type]
        reveal_object=True,
    )


async def _open_athena_results(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
) -> tuple[AthenaPageVM, tuple[str, ...]]:
    page = await _open_athena(ctx, app, pilot)
    await page.select_view("history")
    await page.select_history_execution("q-dev-succeeded")
    await page.open_history_results()
    assert page.active_view == "results"
    assert page.results.execution_id == "q-dev-succeeded"
    client = _athena_client(ctx, "demo-dev")
    history_before = tuple(client.history["dev-analytics"])
    return page, history_before


async def _assert_athena_results_restored(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
    history_before: tuple[str, ...],
) -> None:
    await _wait_for_service_setup(ctx, app, pilot)
    assert ctx.root_vm.content_host.current_id == "athena"
    assert ctx.root_vm.services_menu.selected_id == "athena"
    assert ctx.root_vm.active_connection is not None
    assert ctx.root_vm.active_connection.name == "demo-dev"
    page = ctx.root_vm.content_host.current
    assert isinstance(page, AthenaPageVM)
    assert page.active_view == "results"
    assert page.results.execution_id == "q-dev-succeeded"
    client = _athena_client(ctx, "demo-dev")
    assert tuple(client.history["dev-analytics"]) == history_before
    assert len(app.query(DualPane)) == 0


def _assert_redacted_failure(ctx: AppContext, *, toast_id: str) -> None:
    toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
    assert toast.id == toast_id
    ctx.log_sink.flush()
    output = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
    assert "q-dev-succeeded.csv" not in output
    assert "s3://athena-results" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["bind", "navigation", "terminal", "focus"])
async def test_result_handoff_rolls_back_every_post_mount_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            _, history_before = await _open_athena_results(ctx, app, pilot)

            if stage == "bind":

                async def fail_bind(pane: PaneVM, connection: Connection) -> None:
                    del pane, connection
                    raise RuntimeError("bind failed for q-dev-succeeded.csv")

                monkeypatch.setattr(app, "_rebind_pane_to_connection", fail_bind)
                request = _result_request(preferred_pane="right")
            elif stage == "navigation":

                async def fail_navigation(self: PaneVM, path: object) -> None:
                    del self, path
                    raise RuntimeError("navigation failed for q-dev-succeeded.csv")

                monkeypatch.setattr(PaneVM, "navigate_to", fail_navigation)
                request = _result_request()
            elif stage == "terminal":
                original_navigate = PaneVM.navigate_to

                async def terminal_navigation(self: PaneVM, path: object) -> None:
                    await original_navigate(self, path)  # type: ignore[arg-type]
                    self._set_state(PaneState.ERROR)  # type: ignore[attr-defined]

                monkeypatch.setattr(PaneVM, "navigate_to", terminal_navigation)
                request = _result_request()
            else:

                def fail_focus(self: DualPaneVM, focused: object) -> None:
                    del self, focused
                    raise RuntimeError("focus failed for q-dev-succeeded.csv")

                monkeypatch.setattr(DualPaneVM, "set_focused", fail_focus)
                request = _result_request()

            await app._open_s3_location_request(request)

            await _assert_athena_results_restored(
                ctx,
                app,
                pilot,
                history_before,
            )
            failure_stage = "navigation" if stage == "terminal" else stage
            _assert_redacted_failure(
                ctx,
                toast_id=f"s3-handoff-{failure_stage}-failed",
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_result_handoff_cancellation_restores_prior_athena_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            _, history_before = await _open_athena_results(ctx, app, pilot)
            navigation_started = asyncio.Event()
            release_navigation = asyncio.Event()
            original_navigate = PaneVM.navigate_to

            async def pause_navigation(self: PaneVM, path: object) -> None:
                navigation_started.set()
                await release_navigation.wait()
                await original_navigate(self, path)  # type: ignore[arg-type]

            monkeypatch.setattr(PaneVM, "navigate_to", pause_navigation)
            handoff = asyncio.create_task(app._open_s3_location_request(_result_request()))
            await asyncio.wait_for(navigation_started.wait(), timeout=2)

            handoff.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handoff

            await _assert_athena_results_restored(
                ctx,
                app,
                pilot,
                history_before,
            )
    finally:
        release_navigation.set()
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_result_object_at_bucket_root_is_revealed_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        seeds._PROFILE_OBJECTS,  # type: ignore[attr-defined]
        "demo-dev",
        (*seeds._PROFILE_OBJECTS["demo-dev"], ("athena-results/root-result.csv", 6)),  # type: ignore[attr-defined]
    )
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_athena_results(ctx, app, pilot)
            await app._open_s3_location_request(
                replace(
                    _result_request(),
                    uri="s3://athena-results/root-result.csv",
                )
            )
            await _wait_for_service_setup(ctx, app, pilot)

            dual = ctx.root_vm.content_host.current
            assert isinstance(dual, DualPaneVM)
            assert dual.left.path.as_posix() == "/athena-results"
            assert dual.left.selected_entry is not None
            assert dual.left.selected_entry.entry.name == "root-result.csv"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
