"""Explicit startup journeys with real app, host, panes and isolated fake providers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context, make_source_check_factory
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import ProviderUnreachableError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.launch import LaunchRequest, resolve_launch
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusSlot
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import drain_workers, wait_until
from tests.s3_readonly import LiteralS3Client


class ReadOnlyFS(InMemoryFS):
    def __init__(self, *, failure=False, entered=None, released=None):
        super().__init__()
        self.reads = []
        self.mutations = []
        self.failure = failure
        self.entered = entered
        self.released = released

    async def list(self, path):
        self.reads.append(path)
        if self.entered is not None:
            self.entered.set()
            try:
                await asyncio.Future()
            finally:
                self.released.set()
        if self.failure:
            raise ProviderUnreachableError("synthetic selected-source failure\nunsafe detail")
        return []

    async def copy(self, *args, **kwargs):
        self.mutations.append("copy")
        pytest.fail("startup must not copy")

    async def delete(self, *args, **kwargs):
        self.mutations.append("delete")
        pytest.fail("startup must not delete")

    async def write(self, *args, **kwargs):
        self.mutations.append("write")
        pytest.fail("startup must not write")

    write_stream = mkdir = rename = delete_empty_directory = atomic_publish_no_replace = (
        atomic_publish_directory_no_replace
    ) = claim_directory = write


class ReadOnlyLocal(LocalFS):
    def __init__(self, *, root=None):
        super().__init__(root=root)
        self.reads = []
        self.mutations = []

    async def list(self, path):
        self.reads.append(path)
        return await super().list(path)

    async def copy(self, *args, **kwargs):
        self.mutations.append("copy")
        pytest.fail("startup must not copy")

    async def delete(self, *args, **kwargs):
        self.mutations.append("delete")
        pytest.fail("startup must not delete")

    async def write(self, *args, **kwargs):
        self.mutations.append("write")
        pytest.fail("startup must not write")

    write_stream = mkdir = rename = delete_empty_directory = atomic_publish_no_replace = (
        atomic_publish_directory_no_replace
    ) = claim_directory = write


@pytest.fixture
def source_files(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text(
        "[defaults]\nconnection = 'other'\n"
        "[connections.selected]\nkind = 'aws'\nprofile = 'analytics'\nregion = 'us-east-1'\n"
        "[connections.other]\nkind = 'aws'\nprofile = 'other'\nregion = 'us-west-2'\n"
        "[connections.analytics]\nkind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\ncredentials = 'env:TEST_S3'\n"
    )
    Path(os.environ["AWS_CONFIG_FILE"]).write_text(
        "[profile analytics]\nregion = eu-west-1\n[profile other]\nregion = us-west-2\n"
    )
    return config


def make_context(config, monkeypatch, *, remote=None, launch_transform=None, **selectors):
    store = ConfigStore(path=config / "config.toml", read_only=True)
    launch = resolve_launch(
        LaunchRequest(**selectors),
        config_store=store,
        resolver=ConnectionResolver(config_store=store, read_credentials=False),
    )
    if launch_transform is not None:
        launch = launch_transform(launch)
    ctx = build_app_context(config_dir=config, cache_dir=config / "cache", launch=launch)
    calls = []
    locals_ = []
    fs = remote or ReadOnlyFS()

    def factory(connection):
        calls.append(connection)
        return fs

    s3 = ctx.registry.get("s3")
    monkeypatch.setattr(s3, "_s3_fs_factory", factory)

    def local_factory():
        provider = ReadOnlyLocal()
        locals_.append(provider)
        return provider

    monkeypatch.setattr(s3, "build_local_provider", local_factory)
    monkeypatch.setattr("aws_tui.services.s3.service.LocalFS", ReadOnlyLocal)

    # A real-context explicit launch must never enter any legacy retry/fallback.
    def forbidden(*args, **kwargs):
        pytest.fail("explicit launch entered legacy account selection/fallback")

    monkeypatch.setattr(AwsTuiApp, "_initial_mount_worker", forbidden)
    monkeypatch.setattr(AwsTuiApp, "_resolve_initial_connection", forbidden)
    monkeypatch.setattr(AwsTuiApp, "_mount_local_only_dual_pane", forbidden)
    monkeypatch.setattr(ctx.config_store, "save", forbidden)
    monkeypatch.setattr(ctx.connection_resolver, "materialize", forbidden)
    return ctx, launch, calls, fs, locals_


async def ready(app, ctx):
    await wait_until(
        lambda: app.launch_ready or app.launch_error is not None,
        what="explicit launch terminal outcome",
    )
    current = ctx.root_vm.content_host.current
    state = (
        current.credential_recovery_state()
        if hasattr(current, "credential_recovery_state")
        else None
    )
    assert app.launch_error is None, state
    await drain_workers(app)
    assert ctx.root_vm.content_host.current_id == ctx.launch.service_id


async def test_s3_prefix_first_read_focus_and_no_mutation(source_files, monkeypatch):
    ctx, launch, calls, fs, locals_ = make_context(
        source_files,
        monkeypatch,
        connection="selected",
        location="s3://reports-bucket/daily//雪%2F?#/",
    )
    before = ctx.config_store.path.read_bytes()
    env = dict(os.environ)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        dual = ctx.root_vm.content_host.current
        assert app.query_one(DualPane).vm is dual
        assert dual.left.path == launch.location.path
        assert fs.reads == [launch.location.path]
        assert dual.left.state is PaneState.IDLE
        assert [entry.name for entry in dual.left.entries] == [".."]
        assert dual.focused_pane is dual.left
        assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_LEFT
        assert app.focused is None
        await wait_until(
            lambda: "-focused" in app.query_one("#pane-left").classes, what="left pane visual focus"
        )
        assert calls == [launch.connection]
    assert ctx.config_store.path.read_bytes() == before
    assert dict(os.environ) == env
    assert fs.mutations == []
    assert all(local.mutations == [] for local in locals_)
    assert all(worker.is_finished for worker in app.workers._workers)


@pytest.mark.parametrize("action", ["file", "child", "parent"])
@pytest.mark.parametrize(
    ("suffix", "prefix", "parent_prefix"),
    [
        ("/daily/", "daily/", ""),
        ("/daily//", "daily//", "daily/"),
        ("/daily///", "daily///", "daily//"),
        ("/daily//雪%2F?#/", "daily//雪%2F?#/", "daily//"),
        ("//daily/", "/daily/", "/"),
        ("//", "/", ""),
        ("/daily", "daily/", ""),
        ("", "", None),
        ("/", "", None),
    ],
)
async def test_literal_s3_listing_child_read_and_parent_identity(
    source_files, monkeypatch, suffix, prefix, parent_prefix, action
):
    client = LiteralS3Client(prefix)
    fs = S3FS(session=client, bucket=None)
    ctx, launch, calls, _, locals_ = make_context(
        source_files,
        monkeypatch,
        remote=fs,
        connection="selected",
        location="s3://reports-bucket" + suffix,
    )
    before = ctx.config_store.path.read_bytes()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        pane = ctx.root_vm.content_host.current.left
        assert pane.path == launch.location.path
        assert client.calls == [
            ("list", {"Bucket": "reports-bucket", "Prefix": prefix, "Delimiter": "/"})
        ]
        assert [entry.name for entry in pane.entries] == ["..", "child", "report.csv"]
        if action == "file":
            path = pane.path.join(pane.entries[2].name)
            content = b"".join([chunk async for chunk in await fs.read_stream(path)])
            assert content == b"listed report"
            assert client.calls[-2:] == [
                (method, {"Bucket": "reports-bucket", "Key": prefix + "report.csv"})
                for method in ("head", "get")
            ]
        elif action == "child":
            await pane.activate(1)
            assert client.calls[-1][1]["Prefix"] == prefix + "child/"
            assert [entry.name for entry in pane.entries] == ["..", "nested.csv"]
            path = pane.path.join(pane.entries[1].name)
            assert (
                b"".join([chunk async for chunk in await fs.read_stream(path)]) == b"nested report"
            )
            await pane.activate(0)
            assert client.calls[-1][1]["Prefix"] == prefix
        else:
            await pane.activate(0)
            if parent_prefix is None:
                assert pane.path.is_root
                assert client.calls[-1][0] == "buckets"
            else:
                assert client.calls[-1][1]["Prefix"] == parent_prefix
                assert parent_prefix != prefix
        assert calls == [launch.connection]
    assert ctx.config_store.path.read_bytes() == before
    assert all(local.mutations == [] for local in locals_)
    assert all(method in {"list", "buckets", "head", "get"} for method, _ in client.calls)
    assert all(worker.is_finished for worker in app.workers._workers)


@pytest.mark.parametrize("local_only", [False, True])
@pytest.mark.parametrize("form", ["absolute", "relative"])
async def test_native_local_directory_opens_right(source_files, monkeypatch, local_only, form):
    directory = source_files / "downloads space 雪"
    directory.mkdir()
    if local_only:
        (source_files / "config.toml").unlink()
        Path(os.environ["AWS_CONFIG_FILE"]).write_text("")
    monkeypatch.chdir(source_files)
    location = str(directory) if form == "absolute" else "./downloads space 雪"
    ctx, launch, calls, fs, locals_ = make_context(source_files, monkeypatch, location=location)
    before = ctx.config_store.path.read_bytes() if ctx.config_store.path.exists() else None
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        dual = ctx.root_vm.content_host.current
        assert dual.right.path == launch.location.path
        assert locals_[-1].canonical_path(dual.right.path) == directory.resolve()
        assert locals_[-1].reads == [launch.location.path]
        assert dual.focused_pane is dual.right
        assert ctx.focus_coordinator.focused_slot is FocusSlot.S3_RIGHT
        assert app.focused is None
        await wait_until(
            lambda: "-focused" in app.query_one("#pane-right").classes,
            what="right pane visual focus",
        )
        if local_only:
            assert calls == []
            assert ctx.root_vm.active_connection is None
            assert ctx.unreachable_connections == set()
    assert (
        ctx.config_store.path.read_bytes() if ctx.config_store.path.exists() else None
    ) == before
    assert all(local.mutations == [] for local in locals_)
    assert fs.mutations == []


@pytest.mark.parametrize(
    ("service_id", "page_type"),
    [
        ("s3", DualPane),
        ("athena", AthenaPage),
        ("glue", GluePage),
        ("emr-serverless", EmrServerlessPage),
    ],
)
@pytest.mark.parametrize("profile", [False, True])
async def test_each_service_exact_identity_region_and_guard(
    source_files, monkeypatch, service_id, page_type, profile
):
    from aws_tui.demo.seeds import seeded_demo_athena, seeded_demo_emr, seeded_demo_glue

    selectors = {"profile": "analytics"} if profile else {"connection": "selected"}
    ctx, launch, calls, _, _ = make_context(
        source_files, monkeypatch, service=service_id, region="ap-southeast-2", **selectors
    )
    actual = []
    service = ctx.registry.get(service_id)
    if service_id != "s3":

        def client_factory(connection):
            actual.append(connection)
            if service_id == "athena":
                client = seeded_demo_athena(
                    "demo-dev", connection_name=connection.name, region=connection.region
                )
                client.start_query = lambda *a, **k: pytest.fail("launch must not submit SQL")
                return client
            if service_id == "glue":
                return seeded_demo_glue()["demo-dev"]
            return seeded_demo_emr("demo-default")

        monkeypatch.setattr(service, "_client_factory", client_factory)
    app = AwsTuiApp(ctx)
    before = ctx.config_store.path.read_bytes()
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        vm = ctx.root_vm.content_host.current
        assert app.query_one(page_type).vm is vm
        assert ctx.root_vm.active_connection == launch.connection
        assert ctx.root_vm.services_menu.selected_id == service_id
        factories = calls if service_id == "s3" else actual
        assert factories == [launch.connection]
        assert launch.connection.region == "ap-southeast-2"
        assert launch.connection.profile == "analytics"
        assert launch.connection.kind == "aws"
        assert launch.connection.name == ("analytics" if profile else "selected")
        if service_id != "s3":
            from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader

            assert calls == []
            assert vm.source.connection_key == (launch.connection.name, "ap-southeast-2")
            header = app.query_one(ServiceSourceHeader)
            assert header.tooltip.plain == vm.source.label
            assert "ap-southeast-2" in header.tooltip.plain
        else:
            assert "ap-southeast-2" in vm.left.identity_label
        check = make_source_check_factory(ctx.config_store, ctx.connection_resolver)(
            launch.connection
        )
        assert await check()
        assert ctx.connection_resolver.resolve(launch.connection.name) == launch.connection
        if profile:
            Path(os.environ["AWS_CONFIG_FILE"]).write_text("[profile other]\nregion = us-west-2\n")
        else:
            ctx.config_store.path.write_text(
                before.decode().replace("region = 'us-east-1'", "region = 'eu-central-1'")
            )
        assert not await check()
    # The intentional source edit above is the only configuration mutation.
    assert ctx.config_store.path.read_bytes() == (
        before if profile else before.replace(b"region = 'us-east-1'", b"region = 'eu-central-1'")
    )


@pytest.mark.parametrize("failure", ["provider", "factory", "adoption", "setup", "timeout"])
async def test_selected_failure_exits_without_other_account(source_files, monkeypatch, failure):
    entered, released = asyncio.Event(), asyncio.Event()
    fs = ReadOnlyFS(
        failure=failure == "provider",
        entered=entered if failure == "timeout" else None,
        released=released,
    )
    if failure == "timeout":
        monkeypatch.setattr("aws_tui.app._BOOT_CHAIN_BUDGET_SECONDS", 1.0)
    ctx, launch, calls, _, _ = make_context(
        source_files, monkeypatch, remote=fs, connection="selected"
    )
    if failure == "factory":

        def fail_factory(connection):
            calls.append(connection)
            raise RuntimeError("sensitive\nprovider detail")

        monkeypatch.setattr(ctx.registry.get("s3"), "_s3_fs_factory", fail_factory)
    elif failure == "adoption":

        def fail_construct(self):
            raise RuntimeError("synthetic adoption error")

        monkeypatch.setattr(
            "aws_tui.vm.file_manager.dual_pane_vm.DualPaneVM.construct", fail_construct
        )
    elif failure == "setup":

        async def fail_setup(self):
            raise RuntimeError("synthetic setup error")

        monkeypatch.setattr("aws_tui.vm.file_manager.dual_pane_vm.DualPaneVM.setup", fail_setup)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await wait_until(lambda: app.launch_error is not None, what="selected-source failure exit")
        assert not app.launch_ready
        assert app.return_code != 0
        assert "\n" not in app.launch_error
        assert app.crash_report is None
        assert calls == [launch.connection]
    assert all(worker.is_finished for worker in app.workers._workers)
    assert ctx.root_vm.content_host.current is None
    if failure == "timeout":
        assert entered.is_set()
        assert released.is_set()


async def test_shutdown_cancels_explicit_read_and_drains_resources(source_files, monkeypatch):
    entered, released = asyncio.Event(), asyncio.Event()
    fs = ReadOnlyFS(entered=entered, released=released)
    ctx, launch, calls, _, _ = make_context(
        source_files, monkeypatch, remote=fs, connection="selected"
    )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await asyncio.wait_for(entered.wait(), timeout=5)
        app.exit()
    assert released.is_set()
    assert not app.launch_ready
    assert app.launch_error is None
    assert calls == [launch.connection]
    assert all(worker.is_finished for worker in app.workers._workers)
    assert ctx.root_vm.content_host.current is None


async def test_keychain_selected_runtime_fetches_only_pinned_credentials(source_files, monkeypatch):
    from aws_tui.infra.keychain import Keyring

    (source_files / "config.toml").write_text(
        "[defaults]\nconnection = 'other'\n"
        "[connections.secret]\nkind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\ncredentials = 'keychain:selected-keychain'\n"
        "[connections.other]\nkind = 's3-compatible'\nendpoint_url = 'http://localhost:9001'\ncredentials = 'keychain:other-keychain'\n"
    )
    reads = []

    def get(self, service, key):
        reads.append((service, key))
        assert service == "selected-keychain"
        return {"access_key_id": "synthetic-access", "secret_access_key": "synthetic-secret"}.get(
            key
        )

    monkeypatch.setattr(Keyring, "get", get)
    ctx, launch, calls, _, _ = make_context(
        source_files, monkeypatch, connection="secret", region="eu-west-1"
    )
    assert reads == []
    assert launch.connection.access_key_id is None
    before = ctx.config_store.path.read_bytes()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        assert len(calls) == 1
        actual = calls[0]
        assert actual.name == "secret"
        assert actual.region == "eu-west-1"
        assert actual.access_key_id == "synthetic-access"
        assert actual.secret_access_key == "synthetic-secret"
        assert ctx.root_vm.active_connection == actual
        assert reads == [
            ("selected-keychain", key)
            for key in ("session_token", "access_key_id", "secret_access_key")
        ]
    assert ctx.config_store.path.read_bytes() == before


async def test_source_reference_changed_before_startup_is_refused(source_files, monkeypatch):
    source = source_files / "config.toml"
    source.write_text(
        "[connections.secret]\nkind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\ncredentials = 'env:FIRST_'\n"
    )
    monkeypatch.setenv("FIRST_ACCESS_KEY_ID", "same-access")
    monkeypatch.setenv("FIRST_SECRET_ACCESS_KEY", "same-secret")
    monkeypatch.setenv("SECOND_ACCESS_KEY_ID", "same-access")
    monkeypatch.setenv("SECOND_SECRET_ACCESS_KEY", "same-secret")
    ctx, _, calls, _, _ = make_context(source_files, monkeypatch, connection="secret")
    source.write_text(source.read_text().replace("env:FIRST_", "env:SECOND_"))
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await wait_until(
            lambda: app.launch_error is not None or app.launch_ready,
            what="changed exact source outcome",
        )
        assert app.launch_error is not None
        assert app.return_code == 1
        assert not app.launch_ready
        assert calls == []


@pytest.mark.parametrize("selection", ["configured", "profile", "s3"])
async def test_source_edit_to_effective_region_fails_before_startup_factories(
    source_files, monkeypatch, selection
):
    source = source_files / "config.toml"
    if selection == "s3":
        source.write_text(
            source.read_text().replace(
                "kind = 'aws'\nprofile = 'analytics'\nregion = 'us-east-1'",
                "kind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\n"
                "region = 'us-east-1'\ncredentials = 'env:FIRST_'",
            )
        )
        for prefix in ("FIRST_", "SECOND_"):
            monkeypatch.setenv(prefix + "ACCESS_KEY_ID", prefix + "synthetic-access")
            monkeypatch.setenv(prefix + "SECRET_ACCESS_KEY", prefix + "synthetic-secret")
    selectors = {"profile": "analytics"} if selection == "profile" else {"connection": "selected"}
    ctx, launch, calls, fs, locals_ = make_context(
        source_files, monkeypatch, region="ap-southeast-2", **selectors
    )
    if selection == "profile":
        source = Path(os.environ["AWS_CONFIG_FILE"])
    source.write_text(
        source.read_text()
        .replace(launch.underlying.region, launch.connection.region)
        .replace("env:FIRST_", "env:SECOND_")
    )
    before = ctx.config_store.path.read_bytes()
    edited = source.read_bytes()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await wait_until(
            lambda: app.launch_error is not None or app.launch_ready,
            what="retained source edit startup refusal",
        )
        assert app.launch_error is not None
        assert app.return_code == 1
        assert not app.launch_ready
        assert calls == []
        assert locals_ == []
        assert ctx.root_vm.content_host.current is None
    assert fs.reads == []
    assert fs.mutations == []
    assert ctx.config_store.path.read_bytes() == before
    assert source.read_bytes() == edited
    assert all(worker.is_finished for worker in app.workers._workers)


async def test_same_reference_credential_refresh_preserves_launch_session(
    source_files, monkeypatch
):
    from types import SimpleNamespace

    from aws_tui.infra.aws_session import TokenState

    source = source_files / "config.toml"
    source.write_text(
        "[connections.selected]\nkind = 's3-compatible'\n"
        "endpoint_url = 'http://localhost:9000'\nregion = 'us-east-1'\n"
        "credentials = 'env:FIRST_'\n"
    )
    monkeypatch.setenv("FIRST_ACCESS_KEY_ID", "original-synthetic-access")
    monkeypatch.setenv("FIRST_SECRET_ACCESS_KEY", "original-synthetic-secret")
    ctx, launch, calls, fs, _ = make_context(
        source_files, monkeypatch, connection="selected", region="ap-southeast-2"
    )
    monkeypatch.setattr(
        ctx.aws_session,
        "probe_token",
        lambda connection: SimpleNamespace(state=TokenState.CONNECTED),
    )
    before = source.read_bytes()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        check = make_source_check_factory(ctx.config_store, ctx.connection_resolver)(calls[0])
        assert await check()
        monkeypatch.setenv("FIRST_ACCESS_KEY_ID", "refreshed-synthetic-access")
        monkeypatch.setenv("FIRST_SECRET_ACCESS_KEY", "refreshed-synthetic-secret")
        assert await check()
        await app._recover_active_source()
        assert len(calls) == 2
        assert calls[0].access_key_id == "original-synthetic-access"
        assert calls[1].access_key_id == "refreshed-synthetic-access"
        assert calls[1].secret_access_key == "refreshed-synthetic-secret"
        assert all(connection.region == launch.connection.region for connection in calls)
        assert all(connection.name == "selected" for connection in calls)
        assert ctx.root_vm.active_connection == calls[1]
        assert app.launch_error is None
    assert source.read_bytes() == before
    assert fs.mutations == []


async def test_region_only_uses_env_profile_alias_after_unknown_default(source_files, monkeypatch):
    from aws_tui.demo.seeds import seeded_demo_athena

    source = source_files / "config.toml"
    source.write_text(source.read_text().replace("connection = 'other'", "connection = 'missing'"))
    monkeypatch.setenv("AWS_PROFILE", "analytics")
    ctx, launch, calls, _, _ = make_context(
        source_files, monkeypatch, region="eu-west-1", service="athena"
    )
    actual = []

    def factory(connection):
        actual.append(connection)
        return seeded_demo_athena(
            "demo-dev", connection_name=connection.name, region=connection.region
        )

    monkeypatch.setattr(ctx.registry.get("athena"), "_client_factory", factory)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        assert calls == []
        assert actual == [launch.connection]
        assert actual[0].name == "selected"
        assert actual[0].profile == "analytics"
        assert actual[0].region == "eu-west-1"


@pytest.mark.parametrize("service_id", ["athena", "glue", "emr-serverless"])
async def test_nonfile_service_readiness_failure_exits_selected_source(
    source_files, monkeypatch, service_id
):
    from aws_tui.demo.seeds import seeded_demo_athena, seeded_demo_emr, seeded_demo_glue

    ctx, launch, s3_calls, _, _ = make_context(
        source_files, monkeypatch, connection="selected", service=service_id
    )
    calls = []
    client = (
        seeded_demo_athena("demo-dev")
        if service_id == "athena"
        else seeded_demo_glue()["demo-dev"]
        if service_id == "glue"
        else seeded_demo_emr("demo-dev")
    )
    method = {
        "athena": "list_workgroups_page",
        "glue": "list_databases_page",
        "emr-serverless": "list_applications",
    }[service_id]

    async def fail_read(*args, **kwargs):
        raise ProviderUnreachableError("synthetic exact source failure")

    monkeypatch.setattr(client, method, fail_read)

    def factory(connection):
        calls.append(connection)
        return client

    monkeypatch.setattr(ctx.registry.get(service_id), "_client_factory", factory)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await wait_until(
            lambda: app.launch_error is not None, what=f"{service_id} readiness failure exit"
        )
        assert not app.launch_ready
        assert app.return_code == 1
        assert app.crash_report is None
        assert calls == [launch.connection]
        assert s3_calls == []
    assert all(worker.is_finished for worker in app.workers._workers)


async def test_rooted_native_location_retains_canonical_path_and_parent_navigation(
    source_files, monkeypatch
):
    from dataclasses import replace

    from aws_tui.domain.filesystem import PathRef

    directory = source_files / "downloads space 雪"
    directory.mkdir()
    (directory / "child").mkdir()

    def root_location(launch):
        return replace(
            launch,
            location=replace(
                launch.location, path=PathRef((directory.name,)), native_root=source_files
            ),
        )

    ctx, launch, calls, fs, _ = make_context(
        source_files,
        monkeypatch,
        connection="selected",
        location=str(directory),
        launch_transform=root_location,
    )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        pane = ctx.root_vm.content_host.current.right
        assert pane.path == launch.location.path
        assert pane.provider.canonical_path(pane.path) == directory.resolve()
        await pane.navigate_to(pane.path.join("child"))
        assert pane.provider.canonical_path(pane.path) == (directory / "child").resolve()
        await pane.navigate_to(pane.path.parent())
        assert pane.provider.canonical_path(pane.path) == directory.resolve()
        assert str(source_files) in pane.identity_label
        assert pane.provider.mutations == []
        assert calls == [launch.connection]
        assert fs.mutations == []


async def test_navigation_cancels_explicit_startup_without_late_replacement(
    source_files, monkeypatch
):
    from aws_tui.demo.seeds import seeded_demo_athena

    entered, released = asyncio.Event(), asyncio.Event()
    ctx, launch, calls, fs, _ = make_context(
        source_files, monkeypatch, connection="selected", service="athena"
    )
    client = seeded_demo_athena(
        "demo-dev", connection_name=launch.connection.name, region=launch.connection.region
    )

    async def block(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            released.set()

    monkeypatch.setattr(client, "list_workgroups_page", block)
    monkeypatch.setattr(ctx.registry.get("athena"), "_client_factory", lambda connection: client)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await asyncio.wait_for(entered.wait(), timeout=5)
        candidate = ctx.root_vm.content_host.current
        ctx.root_vm.services_menu.switch_service_command.execute("s3")
        await wait_until(
            lambda: ctx.root_vm.content_host.current_id == "s3" and bool(app.query(DualPane)),
            what="navigation superseding explicit startup",
        )
        await drain_workers(app)
        assert released.is_set()
        assert ctx.root_vm.content_host.current is not candidate
        assert app.launch_error is None
        assert not app.launch_ready
        assert calls == [launch.connection]
        assert fs.mutations == []
    assert all(worker.is_finished for worker in app.workers._workers)


@pytest.mark.parametrize("profile", [False, True])
async def test_actual_credential_retry_keeps_session_source_and_region(
    source_files, monkeypatch, profile
):
    from types import SimpleNamespace

    from aws_tui.demo.seeds import seeded_demo_athena
    from aws_tui.infra.aws_session import TokenState

    selectors = {"profile": "analytics"} if profile else {"connection": "selected"}
    ctx, launch, s3_calls, _, _ = make_context(
        source_files, monkeypatch, region="eu-west-1", service="athena", **selectors
    )
    calls = []

    def factory(connection):
        calls.append(connection)
        return seeded_demo_athena(
            "demo-dev", connection_name=connection.name, region=connection.region
        )

    monkeypatch.setattr(ctx.registry.get("athena"), "_client_factory", factory)
    monkeypatch.setattr(
        ctx.aws_session,
        "probe_token",
        lambda connection: SimpleNamespace(state=TokenState.CONNECTED),
    )
    before = ctx.config_store.path.read_bytes()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)):
        await ready(app, ctx)
        original = ctx.root_vm.content_host.current
        await app._recover_active_source()
        assert calls == [launch.connection, launch.connection]
        assert ctx.root_vm.active_connection == launch.connection
        assert ctx.root_vm.content_host.current is not original
        assert ctx.root_vm.content_host.current.source.connection_key == (
            launch.connection.name,
            "eu-west-1",
        )
        assert s3_calls == []
        assert app.launch_error is None
    assert ctx.config_store.path.read_bytes() == before
