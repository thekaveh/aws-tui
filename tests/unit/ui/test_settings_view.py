"""Tests for SettingsView."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3


def test_settings_view_can_be_constructed(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    try:
        view = SettingsView(vm=vm, hub=_hub())
        assert view is not None
    finally:
        vm.dispose()
        s3.dispose()


@pytest.mark.asyncio
async def test_settings_view_shows_connections_section_expanded_by_default(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: SettingsView) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    view = SettingsView(vm=vm, hub=_hub())
    app = _Host(view)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Collapsible

            conn_section = view.query_one("#section-connections", Collapsible)
            assert conn_section.collapsed is False
            themes_section = view.query_one("#section-themes", Collapsible)
            assert themes_section.collapsed is True
            assert themes_section.disabled is True
    finally:
        vm.dispose()
        s3.dispose()
