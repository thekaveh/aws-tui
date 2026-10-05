from dataclasses import replace

import pytest

from aws_tui.composition import build_app_context
from aws_tui.infra.athena_draft_store import AthenaDraftStore
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keychain import InMemoryKeychain
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.settings.settings_vm import SettingsVM
from tests.athena_drafts_helpers import runtime_at
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


@pytest.fixture(autouse=True)
def memory_keychain(monkeypatch):
    monkeypatch.setattr("aws_tui.composition.Keyring", InMemoryKeychain)


@pytest.mark.parametrize(("demo", "configured"), [(False, False), (True, True)])
async def test_disabled_and_demo_composition_do_no_draft_io(
    tmp_path, monkeypatch, demo, configured
):
    config = ConfigStore(path=tmp_path / "config.toml")
    if configured:
        config.set_athena_sql_drafts(True)

    def forbidden(*args, **kwargs):
        pytest.fail("disabled drafts accessed disk")

    monkeypatch.setattr(AthenaDraftStore, "list", forbidden)
    monkeypatch.setattr(AthenaDraftStore, "save", forbidden)
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path / "cache", demo=demo)
    runtime = ctx.athena_drafts_vm
    try:
        assert not runtime.enabled
        assert runtime.read_only == demo
        service = ctx.registry.get("athena")
        service._client_factory = lambda c: PageClient(connection_name=c.name, region=c.region)
        page = service.build_vm(
            Connection(name="analytics", kind="aws", region="us-west-2", source="test")
        )
        await page.setup()
        page.query.set_sql("SELECT 1")
        await page.shutdown()
        page.dispose()
        await runtime.refresh()
        if demo:
            assert not await runtime.set_enabled(True)
        assert (await runtime.shutdown()).unpersisted == 0
        assert runtime._worker._thread is None
        assert not (tmp_path / "athena-drafts").exists()
    finally:
        ctx.close_unstarted()


def test_pages_and_settings_share_one_owner_and_unstarted_close_is_inert(tmp_path):
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path / "cache", demo=True)
    runtime = ctx.athena_drafts_vm
    service = ctx.registry.get("athena")
    connection = Connection(name="demo-default", kind="aws", region="us-east-1", source="test")
    first, second = service.build_vm(connection), service.build_vm(connection)
    settings = SettingsVM(
        s3=ctx.s3_connections_vm, athena_drafts=runtime, hub=ctx.hub, dispatcher=ctx.dispatcher
    )
    assert first.drafts is second.drafts is settings.athena_drafts is runtime
    settings.dispose()
    assert not runtime._disposed
    first.dispose()
    second.dispose()
    ctx.close_unstarted()
    assert runtime._disposed
    assert runtime._worker._thread is None
    assert runtime._worker._closed


def test_failed_build_closes_draft_intake_without_starting_worker(tmp_path, monkeypatch):
    owners = []
    original = AthenaDraftsVM.dispose

    def dispose(vm):
        owners.append(vm)
        original(vm)

    monkeypatch.setattr(AthenaDraftsVM, "dispose", dispose)

    def fail(vm):
        raise RuntimeError("construction failed")

    monkeypatch.setattr("aws_tui.composition.TableClipboardVM.construct", fail)
    with pytest.raises(RuntimeError, match="construction failed"):
        build_app_context(config_dir=tmp_path, cache_dir=tmp_path / "cache")
    assert len(owners) == 1
    assert owners[0]._worker._thread is None
    assert owners[0]._worker._closed


def test_bad_config_disables_drafts_with_fixed_preference_error(tmp_path):
    (tmp_path / "config.toml").write_text("broken = [")
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path / "cache")
    try:
        assert not ctx.athena_drafts_vm.enabled
        assert (
            ctx.athena_drafts_vm.error_text
            == "Athena SQL draft operation failed. Retry the operation."
        )
    finally:
        ctx.close_unstarted()


async def test_off_setup_baseline_rejects_later_credentials_selector_remap(tmp_path):
    from aws_tui.composition import make_source_check_factory

    runtime, _ = runtime_at(tmp_path, enabled=False)
    config = ConfigStore(path=tmp_path / "config.toml")
    entry = ConnectionEntry(
        name="analytics", kind="aws", region="us-west-2", credentials="env:TEAM_"
    )
    config.add_connection(entry)
    connection = Connection(name="analytics", kind="aws", region="us-west-2", source="config")

    class Resolver:
        def resolve_selected(self, name):
            assert name == "analytics"
            return connection

    check = make_source_check_factory(config, Resolver())(connection)
    client = PageClient()
    page = make_page_vm(client, drafts=runtime, source_is_current=check)
    try:
        await page.setup()
        assert runtime._worker._thread is None
        assert not (tmp_path / "athena-drafts").exists()
        page.query.set_sql("SELECT 1")
        await page.query.execute_command.execute_async()
        assert len(client.start_calls) == 1
        client.start_calls.clear()
        page.query.set_sql("")
        assert runtime._worker._thread is None
        assert not (tmp_path / "athena-drafts").exists()
        config.update_connection("analytics", replace(entry, credentials="env:OTHER_"))
        assert await runtime.set_enabled(True)
        assert not await check()
        page.query.set_sql("SELECT 1")
        await page.query.execute_command.execute_async()
        assert not client.start_calls
        assert page.query.validation_error == "Draft context is unavailable or changed."
    finally:
        await runtime.shutdown()
        await page.shutdown()


@pytest.mark.parametrize(
    "change", ["removed", "shadowed", "route", "selector", "failure", "race", "rotation"]
)
async def test_source_factory_exact_lookup_and_private_identity(tmp_path, change):
    from aws_tui.composition import make_source_check_factory

    config = ConfigStore(path=tmp_path / "config.toml")
    entry = ConnectionEntry(name="analytics", kind="aws", credentials="env:TEAM_")
    if change != "shadowed":
        config.add_connection(entry)
    connection = Connection(name="analytics", kind="aws", region="us-west-2", source="config")

    class Resolver:
        current = connection

        def resolve_selected(self, name):
            assert name == "analytics"
            if change == "failure" and self.current is None:
                raise RuntimeError("PRIVATE_SECRET")
            if change == "race" and self.current is None:
                config.update_connection("analytics", replace(entry, credentials="env:NEW_"))
                return connection
            return self.current

    resolver = Resolver()
    check = make_source_check_factory(config, resolver)(connection)
    assert await check()
    if change == "removed":
        config.remove_connection("analytics")
    elif change == "shadowed":
        config.add_connection(entry)
    elif change == "route":
        resolver.current = replace(connection, region="us-east-2")
    elif change == "selector":
        config.update_connection("analytics", replace(entry, credentials="env:OTHER_"))
    elif change == "rotation":
        resolver.current = replace(connection, access_key_id="ROTATED", secret_access_key="SECRET")
    else:
        resolver.current = None
    assert await check() == (change == "rotation")


async def test_enabled_composition_uses_config_relative_store_and_shared_service(
    tmp_path, monkeypatch
):
    config = ConfigStore(path=tmp_path / "config.toml")
    config.set_athena_sql_drafts(True)
    connection = Connection(name="analytics", kind="aws", region="us-west-2", source="test")
    ctx = build_app_context(config_dir=tmp_path, cache_dir=tmp_path / "cache")
    owner = ctx.athena_drafts_vm
    monkeypatch.setattr(ctx.connection_resolver, "resolve_selected", lambda name: connection)
    service = ctx.registry.get("athena")
    service._client_factory = lambda c: PageClient(connection_name=c.name, region=c.region)
    page = service.build_vm(connection)
    try:
        assert owner.enabled
        assert owner.directory == tmp_path / "athena-drafts"
        assert owner._worker._thread is None
        assert not owner.directory.exists()
        await page.setup()
        page.query.set_sql("SELECT 1")
        assert (await owner.shutdown()).unpersisted == 0
        await page.shutdown()
        assert owner._store.list().records[0].context == page.query.context.cache_key
    finally:
        page.dispose()
        ctx.close_unstarted()
