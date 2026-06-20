"""Smoke test for S3ConnectionsPanel construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


def test_s3_connections_panel_can_be_constructed(tmp_path: Path) -> None:
    hub = cast("MessageHub[Message]", MessageHub())
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    try:
        panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)
        assert panel.vm is s3_vm
    finally:
        s3_vm.dispose()
