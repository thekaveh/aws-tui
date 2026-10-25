from dataclasses import replace

import pytest

from aws_tui.infra.config_store import ConfigError, ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver


def resolver_for(store, tmp_path):
    config = tmp_path / "aws-config"
    config.write_text("[profile discovered]\nregion=us-west-2\n")
    return ConnectionResolver(
        config_store=store, aws_config_path=config, aws_credentials_path=tmp_path / "no-credentials"
    )


def test_protection_defaults_off_and_roundtrips_without_credentials(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    store.path.write_text('[connections.saved]\nkind="aws"\nprofile="discovered"\n')
    resolver = resolver_for(store, tmp_path)
    assert store.load().connections["saved"].protected is False
    entry = replace(store.load().connections["saved"], protected=True)
    store.update_connection("saved", entry)
    assert store.load().connections["saved"].protected is True
    assert resolver.resolve_selected("saved").protected is True
    assert next(c for c in resolver.list_metadata() if c.name == "saved").protected is True
    assert resolver.materialize("saved").protected is True
    assert "protected = true" in store.path.read_text()


def test_discovery_materialization_preserves_policy(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = resolver_for(store, tmp_path)
    assert resolver.resolve_selected("discovered").protected is False
    entry = resolver.materialize("discovered")
    store.update_connection(entry.name, replace(entry, protected=True))
    assert resolver.materialize("discovered").protected is True
    assert resolver.resolve_selected("discovered").protected is True


def test_s3_metadata_resolution_preserves_protection(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(
        ConnectionEntry(
            name="objects",
            kind="s3-compatible",
            endpoint_url="https://objects.example.test",
            credentials="static",
            access_key_id="synthetic",
            secret_access_key="synthetic",
            protected=True,
        )
    )
    resolver = resolver_for(store, tmp_path)
    assert resolver.resolve_selected("objects").protected is True
    assert resolver.list_metadata()[0].protected is True


def test_protection_rejects_non_boolean_config(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    store.path.write_text('[connections.saved]\nkind="aws"\nprotected="true"\n')
    with pytest.raises(ConfigError, match="protected"):
        store.load()


@pytest.mark.asyncio
async def test_fresh_guard_tracks_policy_on_captured_source_not_active_selection(tmp_path):
    from aws_tui.composition import make_mutation_check_factory
    from aws_tui.domain.filesystem import PermissionDeniedError

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(ConnectionEntry(name="saved", kind="aws", profile="discovered"))
    store.add_connection(ConnectionEntry(name="other", kind="aws", region="eu-west-1"))
    resolver = resolver_for(store, tmp_path)
    captured = resolver.resolve_selected("saved")
    check = make_mutation_check_factory(store, resolver)(captured)
    await check()
    entry = store.load().connections["saved"]
    store.update_connection("saved", replace(entry, protected=True))
    with pytest.raises(PermissionDeniedError, match="protected"):
        await check()
    store.update_connection("saved", replace(entry, protected=False))
    await check()
    store.update_connection("saved", replace(entry, region="eu-west-1"))
    with pytest.raises(PermissionDeniedError, match="source"):
        await check()


@pytest.mark.asyncio
async def test_guard_fails_closed_on_removed_or_invalid_source(tmp_path):
    from aws_tui.composition import make_mutation_check_factory
    from aws_tui.domain.filesystem import PermissionDeniedError

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(ConnectionEntry(name="saved", kind="aws"))
    resolver = resolver_for(store, tmp_path)
    check = make_mutation_check_factory(store, resolver)(resolver.resolve_selected("saved"))
    store.remove_connection("saved")
    with pytest.raises(PermissionDeniedError):
        await check()
    store.path.write_text("[invalid")
    with pytest.raises(PermissionDeniedError):
        await check()


@pytest.mark.asyncio
async def test_auto_profile_policy_materialization_does_not_retarget_guard(tmp_path):
    from aws_tui.composition import make_mutation_check_factory
    from aws_tui.domain.filesystem import PermissionDeniedError

    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = resolver_for(store, tmp_path)
    check = make_mutation_check_factory(store, resolver)(resolver.resolve_selected("discovered"))
    entry = resolver.materialize("discovered")
    store.update_connection(entry.name, replace(entry, protected=True))
    with pytest.raises(PermissionDeniedError, match="protected"):
        await check()
    store.update_connection(entry.name, replace(entry, protected=False))
    await check()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement", ["", "[invalid", "[profile discovered]\nregion=us-west-2\nbad line\n"]
)
async def test_guard_rejects_unavailable_underlying_named_profile(tmp_path, replacement):
    from aws_tui.composition import make_mutation_check_factory
    from aws_tui.domain.filesystem import PermissionDeniedError

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(
        ConnectionEntry(name="saved", kind="aws", profile="discovered", region="us-west-2")
    )
    resolver = resolver_for(store, tmp_path)
    check = make_mutation_check_factory(store, resolver)(resolver.resolve_selected("saved"))
    await check()
    (tmp_path / "aws-config").write_text(replacement)
    with pytest.raises(PermissionDeniedError, match="source"):
        await check()


@pytest.mark.asyncio
async def test_guard_rechecks_policy_if_config_changes_during_metadata_read(tmp_path, monkeypatch):
    from aws_tui.composition import make_mutation_check_factory
    from aws_tui.domain.filesystem import PermissionDeniedError

    store = ConfigStore(path=tmp_path / "config.toml")
    entry = ConnectionEntry(name="saved", kind="aws", region="us-west-2")
    store.add_connection(entry)
    resolver = resolver_for(store, tmp_path)
    check = make_mutation_check_factory(store, resolver)(resolver.resolve_selected("saved"))
    original = resolver.resolve_selected_metadata

    def changed(name):
        rows = original(name)
        store.update_connection("saved", replace(entry, protected=True))
        return rows

    monkeypatch.setattr(resolver, "resolve_selected_metadata", changed)
    with pytest.raises(PermissionDeniedError, match="protected"):
        await check()


@pytest.mark.asyncio
async def test_materializing_only_policy_preserves_existing_read_source_check(tmp_path):
    from aws_tui.composition import make_source_check_factory

    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = resolver_for(store, tmp_path)
    check = make_source_check_factory(store, resolver)(resolver.resolve_selected("discovered"))
    assert await check()
    entry = resolver.materialize("discovered")
    store.update_connection(entry.name, replace(entry, protected=True))
    assert await check()


def test_exact_profile_resolution_displays_only_matching_connection_policy(tmp_path):
    from aws_tui.vm.service_source_vm import ServiceSourceContext

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(
        ConnectionEntry(name="discovered", kind="aws", profile="discovered", protected=True)
    )
    resolver = resolver_for(store, tmp_path)
    selected = resolver.resolve_profile("discovered")
    assert selected.protected is True
    assert "Protected" in ServiceSourceContext.from_connection(selected).label
    store.remove_connection("discovered")
    store.add_connection(
        ConnectionEntry(
            name="discovered",
            kind="s3-compatible",
            endpoint_url="https://objects.example.test",
            credentials="env:SYNTHETIC_",
            protected=True,
        )
    )
    assert resolver.resolve_profile("discovered").protected is False
