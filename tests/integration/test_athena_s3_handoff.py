from __future__ import annotations

import contextlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.services.athena.service import AthenaService
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM


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
        client.query_executions["q-dev-succeeded"] = replace(
            detail,
            summary=replace(
                detail.summary,
                ref=replace(detail.summary.ref, connection_name="demo-prod"),
            ),
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
