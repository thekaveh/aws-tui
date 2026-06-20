"""Smoke test for S3ConnectionsPanel construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
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


class _PanelHost(App[None]):
    def __init__(self, panel: S3ConnectionsPanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


@pytest.mark.asyncio
async def test_refresh_rows_safe_post_mount(tmp_path: Path) -> None:
    """Regression: refresh_rows must NOT use compose-phase context
    managers; it must work after the panel is mounted."""
    hub = cast("MessageHub[Message]", MessageHub())
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    try:
        panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)
        app = _PanelHost(panel)
        async with app.run_test() as pilot:
            await pilot.pause()
            # If this raises IndexError, the regression has returned.
            await panel.refresh_rows()
            await pilot.pause()
    finally:
        s3_vm.dispose()
