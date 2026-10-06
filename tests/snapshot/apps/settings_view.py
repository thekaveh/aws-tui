"""Test apps for SettingsView snapshots — empty / populated / form-open."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.athena_draft_store import AthenaDraftStore
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
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
        credentials="static",
        access_key_id="AKIATEST",
        secret_access_key="SECRETTEST",
        force_path_style=True,
        verify_tls=True,
    )


def _build(
    seed_count: int,
) -> tuple[SettingsVM, S3ConnectionsVM, Path]:
    hub: MessageHub[Message] = MessageHub()
    tmp = Path(tempfile.mkdtemp(prefix="settingsview-"))
    store = ConfigStore(path=tmp / "config.toml")
    for i in range(seed_count):
        store.add_connection(_seed_entry(f"conn-{i}"))
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    s3.construct()
    drafts = AthenaDraftsVM(
        store=AthenaDraftStore(config=store, directory=tmp / "athena-drafts"),
        enabled=False,
        read_only=False,
        directory=Path("/fixture/config/athena-drafts"),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm = SettingsVM(s3=s3, athena_drafts=drafts, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3, tmp


class _SettingsSnapshotApp(App[None]):
    async def on_unmount(self) -> None:
        if self._vm.athena_drafts is not None:
            await self._vm.athena_drafts.shutdown()
            self._vm.athena_drafts.dispose()
        self._vm.dispose()
        self._s3.dispose()
        shutil.rmtree(self._tmp)


class SettingsViewEmptyApp(_SettingsSnapshotApp):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm, self._s3, self._tmp = _build(seed_count=0)

    def compose(self) -> ComposeResult:
        yield SettingsView(vm=self._vm, hub=MessageHub())


class SettingsViewPopulatedApp(_SettingsSnapshotApp):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm, self._s3, self._tmp = _build(seed_count=2)

    def compose(self) -> ComposeResult:
        yield SettingsView(vm=self._vm, hub=MessageHub())


class SettingsViewFormOpenApp(_SettingsSnapshotApp):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm, self._s3, self._tmp = _build(seed_count=1)

    def compose(self) -> ComposeResult:
        yield SettingsView(vm=self._vm, hub=MessageHub())

    async def on_mount(self) -> None:
        # Open the inline form so the snapshot captures it visible.
        form = self.query_one(ConnectionFormInline)
        form.open_for_add()


__all__ = [
    "SettingsViewEmptyApp",
    "SettingsViewFormOpenApp",
    "SettingsViewPopulatedApp",
]
