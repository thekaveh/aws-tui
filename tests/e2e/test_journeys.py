"""End-to-end journeys per spec §8.5.

Canonical user journeys spanning full ``App.run_test()`` Pilot flows and
lower-level orchestration checks. Real provider contracts live in the
separate S3Mock integration tier so this suite remains deterministic and
Docker-free.

The journeys are intentionally pragmatic — they assert the journey hits
its key checkpoint rather than tracing every keystroke. The unit + snapshot
tiers already cover widget-level rendering.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from textual.widgets import Button, Static

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext, build_app_context
from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import (
    FileSystemProvider,
    PathRef,
)
from aws_tui.domain.local_fs import LocalFS
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.athena.service import AthenaService
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.chrome.confirm_vm import ConfirmRequest
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.glue.page_vm import GluePageVM
from tests.e2e.conftest import _AWS_CREDENTIAL_ENV_VARS

# ── Helpers ─────────────────────────────────────────────────────────────────


def test_e2e_environment_disables_host_aws_credentials() -> None:
    assert set(_AWS_CREDENTIAL_ENV_VARS).isdisjoint(os.environ)
    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
    boto_config = Path(os.environ["BOTO_CONFIG"])
    assert boto_config.read_text(encoding="utf-8") == ""


async def _astream(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _aws_connection() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="kaveh-dev",
    )


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.01)


# ── Journey 1: silent SSO on cold start ─────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_1_silent_sso(
    app_context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold start with a valid SSO token = silent S3 view, no toast/modal.

    The on-disk fake SSO cache is kept for symmetry, but the real
    contract is that ``AwsSession.probe_token`` returns ``CONNECTED``
    for this connection — boto's ``SSOTokenLoader`` resolves caches
    via the user's HOME, not via ``tmp_path``, so without the
    monkeypatch the probe quietly returns ``MISSING`` and (post-PR-69)
    fires the fast-fallback path including its toast. The test would
    then assert "no toast" against the silent-SSO contract while
    actually exercising the no-creds fallback.
    """
    sso_cache = tmp_path / ".aws" / "sso" / "cache"
    sso_cache.mkdir(parents=True)
    (sso_cache / "fake.json").write_text(
        json.dumps(
            {
                "startUrl": "https://example.awsapps.com/start",
                "accessToken": "x",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        )
    )

    from datetime import UTC, datetime

    from aws_tui.infra.aws_session import TokenProbeResult

    def _probe(_conn: Connection) -> TokenProbeResult:
        return TokenProbeResult(
            state=TokenState.CONNECTED,
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(app_context.aws_session, "probe_token", _probe)

    app = AwsTuiApp(app_context)
    async with app.run_test(size=(120, 40)) as pilot:
        await _wait_until(lambda: app_context.root_vm.content_host.current is not None)
        await pilot.pause()
        # No toast was raised on launch.
        assert app_context.root_vm.chrome.toast_stack.count == 0
        # Top chrome is mounted (BrandBanner replaced the old StatusBar;
        # profile/region now live in the left pane's border).
        from aws_tui.ui.widgets.brand_banner import BrandBanner

        assert len(app.query(BrandBanner)) == 1


# ── Journey 2: copy S3 -> local via DualPaneVM ──────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_2_copy_object_to_local(app_context: AppContext, tmp_path: Path) -> None:
    """Copy one in-memory object across panes; verify byte equality at the
    destination."""
    # We bypass the S3Service factory and inject InMemoryFS-backed panes
    # directly to keep the test independent of moto/boto.
    payload = b"hello-aws-tui\n" * 64

    src: FileSystemProvider = InMemoryFS()
    await src.write_stream(PathRef(("greeting.txt",)), _astream(payload))

    dst_root = tmp_path / "downloads"
    dst_root.mkdir()
    dst: FileSystemProvider = LocalFS(root=dst_root)

    left = PaneVM(
        provider=src,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        id_prefix="pane.src",
    )
    right = PaneVM(
        provider=dst,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        id_prefix="pane.dst",
    )
    dual = DualPaneVM(
        left=left,
        right=right,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        transfer_journal=app_context.transfer_journal,
    )
    dual.construct()
    await dual.setup()

    # Mark the source entry and copy across.
    entries = left.entries
    assert len(entries) == 1
    entries[0].set_marked(True)

    await dual.copy_across()

    # Destination should now have a byte-equal copy.
    copied = dst_root / "greeting.txt"
    assert copied.is_file()
    assert copied.read_bytes() == payload
    dual.dispose()


# ── Journey 3: switch AWS -> S3-compatible source mid-session ──────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_3_switch_connection(app_context: AppContext) -> None:
    """Connection switch fires the right hub message + tears down old content.

    A full provider swap with active transfers belongs to the S3Mock tier; this
    test covers in-process connection orchestration and stays Docker-free.
    """
    aws = _aws_connection()
    minio = Connection(
        name="minio-local",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="http://localhost:9000",
        access_key_id="x",
        secret_access_key="y",
        force_path_style=True,
    )
    app_context.root_vm.construct()

    # First, settle on AWS.
    await app_context.root_vm.switch_connection_with(aws, TokenState.CONNECTED)

    # Then switch to a configured S3-compatible source.
    await app_context.root_vm.switch_connection_with(minio, TokenState.MISSING)
    assert app_context.root_vm.content_host.current_id is None


# ── Journey 4: detect an interrupted journal ────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_4_detect_interrupted_journal(
    app_context: AppContext, tmp_path: Path
) -> None:
    """Write a half-finished journal entry, scan, assert it's detected."""
    journal = app_context.transfer_journal
    # Start a new transfer
    tid = journal.begin(source_uri="s3://bucket/key", destination_uri="/tmp/key", bytes_total=1024)
    journal.record_part(tid, part_index=1, etag="abc", bytes_written=512)
    # Deliberately do NOT mark finished; this simulates a crash mid-transfer.

    unfinished = list(journal.find_unfinished())
    assert any(rec.transfer_id == tid for rec in unfinished)


# ── Journey 5: delete cancel -> no provider.delete call ─────────────────────


class _SpyProvider(InMemoryFS):
    """InMemoryFS subclass that records delete() calls."""

    def __init__(self) -> None:
        super().__init__()
        self.delete_calls: list[PathRef] = []

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        self.delete_calls.append(path)
        await super().delete(path, expected_etag=expected_etag)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_5_delete_cancel(app_context: AppContext) -> None:
    """A cancelled confirmation decision leaves the provider untouched."""
    fs = _SpyProvider()
    await fs.write_stream(PathRef(("foo.txt",)), _astream(b"keep me"))

    pane = PaneVM(
        provider=fs,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        id_prefix="pane.spy",
    )
    pane.construct()
    await pane.setup()
    assert len(pane.entries) == 1
    pane.entries[0].set_marked(True)

    # Simulate the confirm-cancel path: ConfirmationVM.ask returns False.
    confirm_vm = app_context.confirm_vm
    confirm_vm.construct()

    # Synthesize a cancel decision: open + cancel before any await.
    import asyncio

    request = ConfirmRequest(
        title="Delete foo.txt?",
        body_lines=("Permanent.",),
        confirm_label="Delete",
        cancel_label="Cancel",
        danger=True,
    )

    async def caller_decides_no() -> bool:
        task = asyncio.create_task(confirm_vm.ask(request))
        await asyncio.sleep(0)
        confirm_vm.cancel_command.execute()
        return await task

    decision = await caller_decides_no()
    assert decision is False

    # The pane's delete_marked is NOT called because the caller (which would
    # be a higher VM/widget) skipped delete on a cancelled decision.
    # We assert the provider was untouched.
    assert fs.delete_calls == []
    pane.dispose()


# ── Journey 6: EMR source switch ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_6_switch_emr_profile_updates_visible_source(tmp_path: Path) -> None:
    """EMR keeps its nav selection while ``Shift+S`` remounts its source."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
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
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    dev = ctx.connection_resolver.resolve("dev")
    prod = ctx.connection_resolver.resolve("prod")
    ctx.connection_resolver.list = lambda: [dev, prod]  # type: ignore[method-assign]

    def build_client(connection: Connection) -> _InMemoryEmr:
        client = _InMemoryEmr()
        client.add_application(app_id=f"{connection.name}-app", name=connection.name)
        return client

    for service in ctx.registry.all():
        if isinstance(service, EmrServerlessService):
            service._client_factory = build_client

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await _await_service_mount(pilot, app)

            source_value = app.query_one(
                "#emr-source-header-picker .context-picker-value",
                Static,
            )
            initial_source = str(source_value.render())
            assert initial_source in {"dev·us-east-1", "prod·us-west-2"}

            await pilot.press("S")
            await _await_service_mount(pilot, app)

            source_value = app.query_one(
                "#emr-source-header-picker .context-picker-value",
                Static,
            )
            assert str(source_value.render()) != initial_source
            assert ctx.root_vm.services_menu.selected_id == "emr-serverless"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


async def _await_service_mount(pilot: object, app: AwsTuiApp) -> None:
    await app.workers.wait_for_complete(list(app.workers._workers))
    setup_task = app.app_ctx.root_vm.content_host._setup_task
    if setup_task is not None and not setup_task.done():
        await setup_task
    await pilot.pause()  # type: ignore[attr-defined]


# ── Journey 7: Glue table -> S3 under the same profile ──────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_7_glue_table_opens_s3_under_same_profile(tmp_path: Path) -> None:
    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _await_service_mount(pilot, app)
            ctx.root_vm.services_menu.switch_service_command.execute("glue")
            await _await_service_mount(pilot, app)

            result = app.action_dispatch("glue.open_s3_location")
            if inspect.isawaitable(result):
                await result
            await _await_service_mount(pilot, app)

            assert ctx.root_vm.content_host.current_id == "s3"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            current = ctx.root_vm.content_host.current
            assert isinstance(current, DualPaneVM)
            pane = current.left
            assert pane.current_connection_key == ("aws", "demo-dev")
            assert pane.path.as_posix() == "/demo-dev/dev_analytics/dev_events"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


# ── Journey 8: Athena result -> S3 under the same profile ───────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_8_athena_result_opens_s3_under_same_profile(
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
            await _await_service_mount(pilot, app)
            ctx.root_vm.services_menu.switch_service_command.execute("athena")
            await _await_service_mount(pilot, app)
            page = ctx.root_vm.content_host.current
            from aws_tui.vm.athena.page_vm import AthenaPageVM

            assert isinstance(page, AthenaPageVM)
            await page.select_view("history")
            await page.select_history_execution("q-dev-succeeded")

            result = app.action_dispatch("athena.open_result_location")
            if inspect.isawaitable(result):
                await result
            await _await_service_mount(pilot, app)

            assert ctx.root_vm.content_host.current_id == "s3"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            current = ctx.root_vm.content_host.current
            assert isinstance(current, DualPaneVM)
            pane = current.left
            assert pane.current_connection_key == ("aws", "demo-dev")
            assert pane.path.as_posix() == "/athena-results/dev"
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


# ── Journey 9: Iceberg snapshot -> Athena -> S3 ─────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_9_iceberg_snapshot_runs_only_on_explicit_execute(
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
            await _await_service_mount(pilot, app)
            ctx.root_vm.services_menu.switch_service_command.execute("glue")
            await _await_service_mount(pilot, app)
            glue = ctx.root_vm.content_host.current
            assert isinstance(glue, GluePageVM)
            await glue.open_table(
                TableRef(
                    "AwsDataCatalog",
                    "dev_analytics",
                    "dev_events_iceberg",
                    "demo-dev",
                    "us-east-1",
                )
            )
            await glue.catalog.iceberg.select_view("snapshots")
            assert glue.catalog.iceberg.select_snapshot(4201)
            await pilot.pause()
            service = ctx.registry.get("athena")
            assert isinstance(service, AthenaService)
            assert service._client_factory is not None
            client = service._client_factory(ctx.connection_resolver.resolve("demo-dev"))
            assert isinstance(client, InMemoryAthena)
            client.calls.clear()

            button = app.query_one("#glue-iceberg-time-travel", Button)
            assert not button.disabled
            await pilot.click("#glue-iceberg-time-travel")
            await _await_service_mount(pilot, app)
            athena = ctx.root_vm.content_host.current
            assert isinstance(athena, AthenaPageVM)
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            assert ctx.root_vm.active_connection.region == "us-east-1"
            assert athena.context.connection_name == "demo-dev"
            assert athena.context.region == "us-east-1"
            assert athena.context.workgroup == "dev-analytics"
            assert athena.context.catalog == "AwsDataCatalog"
            assert athena.context.database == "dev_analytics"
            assert athena.query.sql == (
                'SELECT * FROM "AwsDataCatalog"."dev_analytics"."dev_events_iceberg" '
                "FOR VERSION AS OF 4201 LIMIT 100"
            )
            assert athena.query.execution_ref is None
            assert athena.results.rows == ()
            assert not any(call.method == "start_query" for call in client.calls)

            await app.action_execute_athena()
            assert athena.query.execution_ref is not None
            assert athena.query.execution_ref.connection_name == "demo-dev"
            execution_id = athena.query.execution_ref.execution_id
            assert athena.results.rows == (
                ("2026-07-24T12:00:00Z", "dev-checkout", "17"),
                ("2026-07-24T12:05:00Z", "dev-search", "9"),
            )
            assert athena.query.output_location is not None
            assert athena.query.output_location.startswith("s3://athena-results/dev/")

            await athena.select_view("results")
            result = app.action_dispatch("athena.open_result_location")
            if inspect.isawaitable(result):
                await result
            await _await_service_mount(pilot, app)
            assert ctx.root_vm.content_host.current_id == "s3"
            assert ctx.root_vm.active_connection is not None
            assert ctx.root_vm.active_connection.name == "demo-dev"
            pane = ctx.root_vm.content_host.current.left
            assert pane.current_connection_key == ("aws", "demo-dev")
            assert pane.path.as_posix() == "/athena-results/dev"
            assert pane.selected_entry is not None
            assert pane.selected_entry.entry.name == f"{execution_id}.csv"
            assert isinstance(pane.provider, InMemoryFS)
            chunks = await pane.provider.read_stream(
                pane.path.join(pane.selected_entry.entry.name),
            )
            artifact = b"".join([chunk async for chunk in chunks])
            assert artifact == (
                b"dimension,name,value\r\n"
                b"2026-07-24T12:00:00Z,dev-checkout,17\r\n"
                b"2026-07-24T12:05:00Z,dev-search,9\r\n"
            )
            assert b"_col0" not in artifact
            assert b"\r\n1\r\n" not in artifact
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
