"""Smoke test for SettingsModal construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_modal import SettingsModal
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def test_settings_modal_can_be_constructed(tmp_path: Path) -> None:
    hub = cast("MessageHub[Message]", MessageHub())
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        modal = SettingsModal(vm=vm, hub=hub)
        assert modal.vm is vm
    finally:
        vm.dispose()
        s3.dispose()
