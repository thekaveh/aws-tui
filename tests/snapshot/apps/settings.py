"""Test app for SettingsModal snapshots."""

from __future__ import annotations

import tempfile
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.settings_modal import SettingsModal
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


def _seed_entry(name: str, region: str = "us-east-1") -> ConnectionEntry:
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        region=region,
        endpoint_url=f"http://{name}.local:9000",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


class SettingsModalEmptyApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub[Message] = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._tmp = Path(tempfile.mkdtemp(prefix="aws-tui-snap-settings-empty-"))
        store = ConfigStore(path=self._tmp / "config.toml")
        resolver = ConnectionResolver(config_store=store)
        self._s3 = S3ConnectionsVM(
            resolver=resolver,
            config_store=store,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        self._s3.construct()
        self._vm = SettingsVM(s3=self._s3, hub=self._hub, dispatcher=self._dispatcher)
        self._vm.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)", id="placeholder")

    async def on_mount(self) -> None:
        await self.push_screen(SettingsModal(vm=self._vm, hub=self._hub))


class SettingsModalPopulatedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub[Message] = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._tmp = Path(tempfile.mkdtemp(prefix="aws-tui-snap-settings-populated-"))
        store = ConfigStore(path=self._tmp / "config.toml")
        store.add_connection(_seed_entry("minio-local"))
        store.add_connection(_seed_entry("ceph-staging", region="us-west-2"))
        resolver = ConnectionResolver(config_store=store)
        self._s3 = S3ConnectionsVM(
            resolver=resolver,
            config_store=store,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        self._s3.construct()
        self._vm = SettingsVM(s3=self._s3, hub=self._hub, dispatcher=self._dispatcher)
        self._vm.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)", id="placeholder")

    async def on_mount(self) -> None:
        await self.push_screen(SettingsModal(vm=self._vm, hub=self._hub))


__all__ = ["SettingsModalEmptyApp", "SettingsModalPopulatedApp"]
