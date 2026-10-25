from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


@pytest.mark.asyncio
async def test_all_connection_policies_can_be_saved_without_reading_secrets(tmp_path):
    from aws_tui.vm.settings.connection_protection_vm import ConnectionProtectionVM

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(
        ConnectionEntry(
            name="objects",
            kind="s3-compatible",
            endpoint_url="https://objects.example.test",
            credentials="env:SYNTHETIC_",
        )
    )
    aws_config = tmp_path / "aws-config"
    aws_config.write_text("[profile discovered]\nregion=us-west-2\n")
    resolver = ConnectionResolver(
        config_store=store, aws_config_path=aws_config, aws_credentials_path=tmp_path / "absent"
    )
    hub = MessageHub()
    vm = ConnectionProtectionVM(config=store, resolver=resolver, hub=hub)
    rows = await vm.rows()
    assert [c.name for c in rows] == ["objects", "discovered"]
    for row in rows:
        await vm.set_protected(row, True)
    assert all(c.protected for c in await vm.rows())
    entries = store.load().connections
    assert entries["objects"].credentials == "env:SYNTHETIC_"
    assert entries["discovered"].profile == "discovered"


@pytest.mark.asyncio
async def test_policy_edit_rejects_stale_source_and_demo_writes(tmp_path):
    from aws_tui.vm.settings.connection_protection_vm import ConnectionProtectionVM

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(ConnectionEntry(name="saved", kind="aws", region="us-east-1"))
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=tmp_path / "absent",
        aws_credentials_path=tmp_path / "absent",
    )
    vm = ConnectionProtectionVM(config=store, resolver=resolver, hub=MessageHub())
    row = (await vm.rows())[0]
    store.update_connection("saved", replace(store.load().connections["saved"], region="eu-west-1"))
    with pytest.raises(ValueError, match="changed"):
        await vm.set_protected(row, True)
    readonly = ConnectionProtectionVM(
        config=ConfigStore(path=store.path, read_only=True), resolver=resolver, hub=MessageHub()
    )
    with pytest.raises(ValueError, match="demo"):
        await readonly.set_protected((await readonly.rows())[0], True)
    assert store.load().connections["saved"].protected is False


def test_credential_edit_preserves_separately_managed_protection(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    original = ConnectionEntry(
        name="objects",
        kind="s3-compatible",
        endpoint_url="https://objects.example.test",
        credentials="static",
        access_key_id="synthetic",
        secret_access_key="synthetic",
        protected=True,
    )
    store.add_connection(original)
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=tmp_path / "absent",
        aws_credentials_path=tmp_path / "absent",
    )
    vm = S3ConnectionsVM(
        config_store=store, resolver=resolver, hub=MessageHub(), dispatcher=NULL_DISPATCHER
    )
    vm.update(
        "objects", replace(original, endpoint_url="https://other.example.test", protected=False)
    )
    assert store.load().connections["objects"].protected is True
