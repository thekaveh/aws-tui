"""Smoke test for SettingsModal construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_modal import SettingsModal
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _make_modal(tmp_path: Path) -> tuple[SettingsModal, SettingsVM, S3ConnectionsVM]:
    hub = cast("MessageHub[Message]", MessageHub())
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    modal = SettingsModal(vm=vm, hub=hub)
    return modal, vm, s3


def test_settings_modal_can_be_constructed(tmp_path: Path) -> None:
    modal, vm, s3 = _make_modal(tmp_path)
    try:
        assert modal.vm is vm
    finally:
        vm.dispose()
        s3.dispose()


@pytest.mark.asyncio
async def test_swap_body_safe_post_mount(tmp_path: Path) -> None:
    """Regression: _swap_body must await remove_children + mount; a missed
    await re-introduces the AwaitRemove race the panel had in Task 6."""
    from textual.app import App, ComposeResult

    modal, vm, s3 = _make_modal(tmp_path)

    class _Host(App[None]):
        def __init__(self, modal: SettingsModal) -> None:
            super().__init__()
            self._modal = modal

        async def on_mount(self) -> None:
            await self.push_screen(self._modal)

        def compose(self) -> ComposeResult:
            return iter([])

    app = _Host(modal)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # Exercise the async swap path directly — same code future
            # themes/keymap panels will trigger via _on_section_highlighted.
            await modal._swap_body()
            await pilot.pause()
    finally:
        vm.dispose()
        s3.dispose()
