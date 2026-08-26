from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest
from textual.css.query import NoMatches

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo import seeds
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import (
    EntryKind,
    NotFoundError,
    PathRef,
)
from aws_tui.domain.query import QueryContext, QueryState, ResultColumn
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.services.s3.service import S3Service
from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.glue.detail_rows import ResourceListPane
from aws_tui.vm.athena.page_vm import AthenaPageSnapshot, AthenaPageVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from aws_tui.vm.messages import OpenS3LocationRequest

_HOSTILE_S3_URIS = [
    pytest.param(
        "s3://[2001:db8::1]/URI_SECRET.csv",
        id="ipv6-literal",
    ),
    pytest.param(
        "s3://valid-bucket:443/URI_SECRET.csv",
        id="port",
    ),
    pytest.param(
        "s3://URI_SECRET@valid-bucket/result.csv",
        id="userinfo",
    ),
    pytest.param(
        "s3://valid-bucket/URI_SECRET\x00.csv",
        id="raw-control",
    ),
    pytest.param(
        "s3://valid bucket/URI_SECRET.csv",
        id="raw-whitespace",
    ),
    pytest.param(
        "s3://valid-bucket/URI_SECRET\n.csv",
        id="raw-newline",
    ),
    pytest.param(
        "s3://valid-bucket/URI_SECRET%00.csv",
        id="encoded-control",
    ),
    pytest.param(
        "s3://valid-bucket/URI_SECRET%20.csv",
        id="encoded-whitespace",
    ),
    pytest.param(
        "s3://valid-bucket/URI_SECRET%0A.csv",
        id="encoded-newline",
    ),
    pytest.param(
        "s3://valid-bucket/result.csv?URI_SECRET=1",
        id="query",
    ),
    pytest.param(
        "s3://valid-bucket/result.csv#URI_SECRET",
        id="fragment",
    ),
    pytest.param(
        "s3:///URI_SECRET.csv",
        id="empty-bucket",
    ),
    pytest.param(
        "s3://URI_SECRET_invalid/result.csv",
        id="invalid-bucket",
    ),
]


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


def _s3_provider(ctx: AppContext, profile: str) -> InMemoryFS:
    service = ctx.registry.get("s3")
    assert isinstance(service, S3Service)
    factory = service._s3_fs_factory
    assert factory is not None
    provider = factory(ctx.connection_resolver.resolve(profile))
    assert isinstance(provider, InMemoryFS)
    return provider


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
async def test_pruning_history_view_ignores_queued_refresh(
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
            await _open_athena(ctx, app, pilot)
            history = app.query_one(AthenaHistoryView)
            listing = history.query_one("#athena-history-pane", ResourceListPane)
            option_list = listing.option_list
            refreshes = 0
            refresh = history._refresh

            def observe_pruned_refresh() -> None:
                nonlocal refreshes
                refreshes += 1
                with pytest.raises(NoMatches):
                    _ = listing.option_list
                refresh()

            monkeypatch.setattr(history, "_refresh", observe_pruned_refresh)
            removal = option_list.remove()
            history._vm._notify("items")
            await removal
            await pilot.pause()

            assert history.is_mounted
            assert listing.is_mounted
            assert refreshes == 1
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_id", "output_location"),
    [
        ("q-dev-missing-output", None),
        ("q-dev-malformed-output", "https://example.test/RESULT_URI_SECRET"),
        ("q-dev-hostile-output", "s3://[RESULT_URI_SECRET"),
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


@pytest.mark.asyncio
async def test_history_handoff_rejects_coherent_foreign_profile_detail(
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
        foreign_context = QueryContext(
            "demo-prod",
            "us-east-1",
            "dev-analytics",
            "ProdDataCatalog",
            "prod_sales",
        )
        foreign_ref = replace(
            detail.summary.ref,
            connection_name=foreign_context.connection_name,
            region=foreign_context.region,
        )
        client.query_executions["q-dev-succeeded"] = replace(
            detail,
            summary=replace(detail.summary, ref=foreign_ref),
            context=foreign_context,
            output_location="s3://athena-results/prod/q-prod-succeeded.csv",
        )

        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.select_view("history")
            await page.select_history_execution("q-dev-succeeded")

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


@pytest.mark.asyncio
async def test_results_hostile_s3_uri_stays_in_athena_and_advises_redacted(
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
            output_location="s3://[RESULTS_HOSTILE_SECRET",
        )

        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.results.load("q-dev-succeeded")
            await page.select_view("results")

            await _invoke_result_handoff(app)

            assert ctx.root_vm.content_host.current_id == "athena"
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "athena-result-location-invalid"
            ctx.log_sink.flush()
            diagnostic = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
            assert "RESULTS_HOSTILE_SECRET" not in diagnostic
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_app_validation_rejects_hostile_s3_uri_without_navigation(
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
            await _open_athena(ctx, app, pilot)

            await app._open_s3_location_request(
                replace(
                    _result_request(),
                    uri="s3://[APP_HOSTILE_SECRET",
                )
            )

            assert ctx.root_vm.content_host.current_id == "athena"
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "s3-handoff-invalid-location"
            ctx.log_sink.flush()
            diagnostic = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
            assert "APP_HOSTILE_SECRET" not in diagnostic
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("hostile_uri", _HOSTILE_S3_URIS)
async def test_hostile_s3_uri_is_advisory_redacted_and_non_navigating_at_every_boundary(
    tmp_path: Path,
    hostile_uri: str,
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
            output_location=hostile_uri,
        )

        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            await page.select_view("history")
            await page.select_history_execution("q-dev-succeeded")

            await _invoke_result_handoff(app)

            assert ctx.root_vm.content_host.current_id == "athena"
            assert ctx.root_vm.content_host.current is page
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "athena-result-location-invalid"
            )

            await page.results.load("q-dev-succeeded")
            await page.select_view("results")
            await _invoke_result_handoff(app)

            assert ctx.root_vm.content_host.current_id == "athena"
            assert ctx.root_vm.content_host.current is page
            assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
                "athena-result-location-invalid"
            )

            await app._open_s3_location_request(replace(_result_request(), uri=hostile_uri))

            assert ctx.root_vm.content_host.current_id == "athena"
            assert ctx.root_vm.content_host.current is page
            toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
            assert toast.id == "s3-handoff-invalid-location"
            ctx.log_sink.flush()
            diagnostic = f"{toast.text}\n{ctx.log_sink.path.read_text(encoding='utf-8')}"
            assert "URI_SECRET" not in diagnostic
            assert hostile_uri not in diagnostic
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def _read_bytes(fs: InMemoryFS, path: PathRef) -> bytes:
    chunks = await fs.read_stream(path)
    return b"".join([chunk async for chunk in chunks])


async def _advance_to_success(
    client: InMemoryAthena,
    execution_id: str,
) -> None:
    states = [(await client.get_query_execution(execution_id)).summary.state for _ in range(3)]
    assert states == [QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED]


@pytest.mark.asyncio
async def test_demo_query_artifacts_are_profile_local_replay_safe_and_distinct(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    try:
        dev = _athena_client(ctx, "demo-dev")
        prod = _athena_client(ctx, "demo-prod")
        dev_s3 = _s3_provider(ctx, "demo-dev")
        prod_s3 = _s3_provider(ctx, "demo-prod")
        dev_context = QueryContext(
            "demo-dev",
            "us-east-1",
            "dev-analytics",
            "DevDataCatalog",
            "dev_events",
        )
        prod_context = QueryContext(
            "demo-prod",
            "us-east-1",
            "prod-reporting",
            "ProdDataCatalog",
            "prod_sales",
        )

        first = await dev.start_query(
            "SELECT 1",
            dev_context,
            request_token="dev-first".ljust(32, "-"),
        )
        replay = await dev.start_query(
            "SELECT 1",
            dev_context,
            request_token="dev-first".ljust(32, "-"),
        )
        second = await dev.start_query(
            "SELECT 1",
            dev_context,
            request_token="dev-second".ljust(32, "-"),
        )
        prod_ref = await prod.start_query(
            "SELECT 1",
            prod_context,
            request_token="prod-first".ljust(32, "-"),
        )
        for ref, client in ((first, dev), (second, dev), (prod_ref, prod)):
            await _advance_to_success(client, ref.execution_id)

        first_path = PathRef.from_posix(f"/athena-results/dev/{first.execution_id}.csv")
        second_path = PathRef.from_posix(f"/athena-results/dev/{second.execution_id}.csv")
        prod_path = PathRef.from_posix(f"/athena-results/prod/{prod_ref.execution_id}.csv")
        assert replay == first
        assert first_path != second_path
        assert await _read_bytes(dev_s3, first_path) == b"_col0\r\n1\r\n"
        assert await _read_bytes(dev_s3, second_path) == b"_col0\r\n1\r\n"
        assert await _read_bytes(prod_s3, prod_path) == b"_col0\r\n1\r\n"
        with pytest.raises(NotFoundError):
            await prod_s3.stat(first_path)
        with pytest.raises(NotFoundError):
            await dev_s3.stat(prod_path)
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", range(8))
async def test_app_started_demo_query_opens_its_exact_result_object(
    tmp_path: Path,
    attempt: int,
) -> None:
    run_path = tmp_path / f"attempt-{attempt}"
    ctx = build_app_context(
        config_dir=run_path / "config",
        cache_dir=run_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            page = await _open_athena(ctx, app, pilot)
            page.query.set_sql("SELECT 1")

            await page.query.execute()
            assert page.query.state is QueryState.SUCCEEDED
            assert page.query.execution_ref is not None
            execution_id = page.query.execution_ref.execution_id
            await page.select_view("results")

            await _invoke_result_handoff(app)
            await _wait_for_service_setup(ctx, app, pilot)

            assert ctx.root_vm.content_host.current_id == "s3"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            dual = ctx.root_vm.content_host.current
            assert isinstance(dual, DualPaneVM)
            assert dual.left.path.as_posix() == "/athena-results/dev"
            assert dual.left.selected_entry is not None
            assert dual.left.selected_entry.entry.name == f"{execution_id}.csv"
            assert dual.left.selected_entry.kind is EntryKind.FILE
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_runtime_alias_uses_lazy_cached_athena_and_exact_s3_result_store(
    tmp_path: Path,
) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    alias = Connection(
        name="runtime-dev",
        kind="aws",
        region="us-east-1",
        source="test",
        profile="demo-dev",
    )
    original_connections = tuple(ctx.connection_resolver.list())
    connections = (alias, *original_connections)
    by_name = {connection.name: connection for connection in connections}
    ctx.connection_resolver.list = lambda: connections  # type: ignore[method-assign]
    ctx.connection_resolver.resolve = lambda name: by_name[name]  # type: ignore[method-assign]
    athena_factory = _athena_service(ctx)._client_factory
    s3_service = ctx.registry.get("s3")
    assert athena_factory is not None
    assert isinstance(s3_service, S3Service)
    assert s3_service._s3_fs_factory is not None

    client = athena_factory(alias)
    store = s3_service._s3_fs_factory(alias)
    assert isinstance(client, InMemoryAthena)
    assert isinstance(store, InMemoryFS)
    assert athena_factory(alias) is client
    assert s3_service._s3_fs_factory(alias) is store
    assert client.connection_name == alias.name
    assert client.region == alias.region

    context = QueryContext(
        alias.name,
        alias.region,
        "dev-analytics",
        "DevDataCatalog",
        "dev_events",
    )
    ref = await client.start_query(
        "SELECT 1",
        context,
        request_token="runtime-alias".ljust(32, "-"),
    )
    await _advance_to_success(client, ref.execution_id)
    detail = await client.get_query_execution(ref.execution_id)
    assert detail.output_location is not None

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_for_service_setup(ctx, app, pilot)
            await app._open_s3_location_request(
                OpenS3LocationRequest(
                    connection_name=alias.name,
                    region=alias.region,
                    uri=detail.output_location,
                    preferred_pane="left",
                    reveal_object=True,
                )
            )
            await _wait_for_service_setup(ctx, app, pilot)

            dual = ctx.root_vm.content_host.current
            assert isinstance(dual, DualPaneVM)
            assert ctx.root_vm.active_connection == alias
            assert dual.left.provider is store
            assert dual.left.selected_entry is not None
            assert dual.left.selected_entry.entry.name == f"{ref.execution_id}.csv"
            assert dual.left.selected_entry.kind is EntryKind.FILE
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_demo_athena_and_s3_caches_are_isolated_between_app_contexts(
    tmp_path: Path,
) -> None:
    alias = Connection(
        name="runtime-dev",
        kind="aws",
        region="us-east-1",
        source="test",
        profile="demo-dev",
    )
    first = build_app_context(
        config_dir=tmp_path / "first-config",
        cache_dir=tmp_path / "first-cache",
        demo=True,
    )
    second = build_app_context(
        config_dir=tmp_path / "second-config",
        cache_dir=tmp_path / "second-cache",
        demo=True,
    )
    try:
        first_athena_factory = _athena_service(first)._client_factory
        second_athena_factory = _athena_service(second)._client_factory
        first_s3 = first.registry.get("s3")
        second_s3 = second.registry.get("s3")
        assert first_athena_factory is not None
        assert second_athena_factory is not None
        assert isinstance(first_s3, S3Service)
        assert isinstance(second_s3, S3Service)
        assert first_s3._s3_fs_factory is not None
        assert second_s3._s3_fs_factory is not None

        first_client = first_athena_factory(alias)
        second_client = second_athena_factory(alias)
        first_store = first_s3._s3_fs_factory(alias)
        second_store = second_s3._s3_fs_factory(alias)

        assert first_client is not second_client
        assert first_store is not second_store
        assert first_athena_factory(alias) is first_client
        assert second_athena_factory(alias) is second_client
        assert first_s3._s3_fs_factory(alias) is first_store
        assert second_s3._s3_fs_factory(alias) is second_store

        shared_alias = replace(
            alias,
            name="runtime-shared",
            region="us-west-2",
            profile="demo-shared",
        )
        shared_client = first_athena_factory(shared_alias)
        shared_workgroups, _ = await shared_client.list_workgroups_page()
        assert shared_client.connection_name == "runtime-shared"
        assert [(row.name, row.state) for row in shared_workgroups] == [
            ("shared-retired", "DISABLED"),
            ("shared-insights", "ENABLED"),
        ]
    finally:
        with contextlib.suppress(Exception):
            first.root_vm.dispose()
        with contextlib.suppress(Exception):
            second.root_vm.dispose()


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
) -> tuple[AthenaPageVM, AthenaPageSnapshot, tuple[str, ...]]:
    page = await _open_athena(ctx, app, pilot)
    client = _athena_client(ctx, "demo-dev")
    assert page.context is not None
    client.add_query_result(
        "SELECT 'S3_ROLLBACK_SQL_MARKER'",
        page.context,
        columns=(ResultColumn("_col0", "varchar", "NULLABLE"),),
        rows=(("S3_ROLLBACK_RESULT",),),
    )
    page.query.set_sql("SELECT 'S3_ROLLBACK_SQL_MARKER'")
    await page.select_view("query")
    await page.query.execute()
    await page.select_view("results")
    assert page.active_view == "results"
    assert page.results.rows == (("S3_ROLLBACK_RESULT",),)
    snapshot = page.export_snapshot()
    history_before = tuple(client.history["dev-analytics"])
    return page, snapshot, history_before


async def _assert_athena_results_restored(
    ctx: AppContext,
    app: AwsTuiApp,
    pilot: object,
    snapshot: AthenaPageSnapshot,
    history_before: tuple[str, ...],
) -> None:
    await _wait_for_service_setup(ctx, app, pilot)
    assert ctx.root_vm.content_host.current_id == "athena"
    assert ctx.root_vm.services_menu.selected_id == "athena"
    assert ctx.root_vm.active_connection is not None
    assert ctx.root_vm.active_connection.name == "demo-dev"
    page = ctx.root_vm.content_host.current
    assert isinstance(page, AthenaPageVM)
    assert page.export_snapshot() == snapshot
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
            _, snapshot, history_before = await _open_athena_results(ctx, app, pilot)

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
                snapshot,
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
            _, snapshot, history_before = await _open_athena_results(ctx, app, pilot)
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
                snapshot,
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
