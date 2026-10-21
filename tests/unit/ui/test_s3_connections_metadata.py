"""Connection rows need metadata; only explicit editing may read secrets."""

from __future__ import annotations

import asyncio
import threading

import pytest
from textual.widgets import Button, Input, Static
from vmx import NULL_DISPATCHER

from aws_tui.demo.connections import DemoConnectionResolver
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keychain import InMemoryKeychain
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from tests.helpers import WAIT_UNTIL_TIMEOUT_SECONDS, drain_workers, focus_and_settle, wait_until
from tests.unit.ui.test_s3_connections_panel import _hub, _PanelHost


class _RecordingKeychain(InMemoryKeychain):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.hold = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.released_while_reading = False
        self.error = None

    def get(self, service, key):
        self.reads.append((service, key, threading.get_ident()))
        if service == "chosen-service":
            if self.hold and not self.entered.is_set():
                self.entered.set()
                self.released_while_reading = self.release.wait(WAIT_UNTIL_TIMEOUT_SECONDS)
            if self.error is not None:
                raise self.error
        return super().get(service, key)


def _panel(tmp_path):
    keychain = _RecordingKeychain()
    store = ConfigStore(path=tmp_path / "config.toml")
    for name in ["chosen", "other"]:
        store.add_connection(
            ConnectionEntry(
                name=name,
                kind="s3-compatible",
                endpoint_url="https://objects.example.test",
                region="us-east-1",
                credentials=f"keychain:{name}-service",
            )
        )
        keychain.set(f"{name}-service", "access_key_id", "synthetic-id")
        keychain.set(f"{name}-service", "secret_access_key", "synthetic-secret")
    resolver = ConnectionResolver(
        config_store=store,
        keychain=keychain,
        aws_config_path=tmp_path / "missing-aws-config",
        aws_credentials_path=tmp_path / "missing-aws-credentials",
    )
    hub = _hub()
    vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=store,
        keychain=keychain,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return S3ConnectionsPanel(vm=vm, hub=hub), keychain


@pytest.mark.asyncio
async def test_initial_render_lists_metadata_without_reading_keychain(tmp_path):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            await pilot.pause()
            assert keychain.reads == []
            assert len(panel.query(".connection-row")) == 2
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_refresh_lists_metadata_without_reading_keychain(tmp_path):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            keychain.reads.clear()
            await panel.refresh_rows()
            await pilot.pause()
            assert keychain.reads == []
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_edit_reads_only_selected_credentials_off_the_ui_thread(tmp_path):
    panel, keychain = _panel(tmp_path)
    ui_thread = threading.get_ident()
    try:
        async with _PanelHost(panel).run_test() as pilot:
            keychain.reads.clear()
            await focus_and_settle(panel.query_one("#edit-0"))
            await pilot.press("enter")
            await drain_workers(pilot.app)
            await pilot.pause()
            assert panel.query_one("#form-name", Input).value == "chosen"
            assert [service for service, _, _ in keychain.reads] == ["chosen-service"] * 3
            assert all(thread != ui_thread for _, _, thread in keychain.reads)
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_delayed_edit_keeps_heartbeat_and_typing_responsive_without_losing_safe_input(
    tmp_path,
):
    panel, keychain = _panel(tmp_path)
    heartbeat = None
    try:
        async with _PanelHost(panel).run_test() as pilot:
            panel._on_add_clicked()
            await pilot.pause()
            region = panel.query_one("#form-region", Input)
            region.value = "safe-region"
            await focus_and_settle(region)
            await pilot.press("end")
            await pilot.pause()
            assert region.has_focus
            keychain.hold = True

            async def type_and_release():
                await wait_until(keychain.entered.is_set, what="controlled keychain read entered")
                await pilot.press("x")
                keychain.release.set()

            heartbeat = asyncio.create_task(type_and_release())
            panel._on_edit_clicked("chosen")
            await heartbeat
            await drain_workers(pilot.app)
            await pilot.pause()
            assert keychain.released_while_reading, (
                "the event loop stalled inside the keychain read"
            )
            assert panel.query_one("#form-name", Input).value == ""
            assert region.value == "safe-regionx"
            assert panel.query_one(ConnectionFormInline).has_class("-open")
    finally:
        keychain.release.set()
        if heartbeat is not None:
            await asyncio.gather(heartbeat, return_exceptions=True)
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_keychain_failure_is_visible_and_keeps_existing_safe_form_input(tmp_path):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            panel._on_add_clicked()
            panel.query_one("#form-name", Input).value = "safe-draft"
            await pilot.pause()
            keychain.error = RuntimeError("unlock refused password=synthetic-secret")
            panel._on_edit_clicked("chosen")
            await drain_workers(pilot.app)
            await pilot.pause()
            text = " ".join(str(s.render()) for s in panel.query(Static) if s.display)
            assert "keychain" in text.lower()
            assert "try" in text.lower()
            assert "synthetic-secret" not in text
            assert panel.query_one("#form-name", Input).value == "safe-draft"
            assert panel.query_one(ConnectionFormInline).has_class("-open")
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_error", [False, True])
@pytest.mark.parametrize(
    "superseding",
    [
        "add",
        "edit",
        "refresh",
        "close",
        "cancel",
        "direct_add",
        "input_aba",
        "hide_show",
        "unmount",
    ],
)
async def test_late_edit_cannot_replace_newer_form_or_page(tmp_path, superseding, late_error):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            panel._on_add_clicked()
            await pilot.pause()
            form = panel.query_one(ConnectionFormInline)
            name = form.query_one("#form-name", Input)
            region = form.query_one("#form-region", Input)
            region.value = "safe-region"
            await pilot.pause()
            keychain.hold = True
            panel._on_edit_clicked("chosen")
            await wait_until(keychain.entered.is_set, what="held edit read started")
            if late_error:
                keychain.error = RuntimeError("obsolete unlock failure password=synthetic-secret")
            if superseding == "add":
                panel._on_add_clicked()
            elif superseding == "edit":
                panel._on_edit_clicked("other")
            elif superseding == "refresh":
                await panel.refresh_rows()
            elif superseding == "close":
                form.close()
            elif superseding == "cancel":
                form.action_cancel()
            elif superseding == "direct_add":
                form.open_for_add()
            elif superseding == "input_aba":
                region.value = "newer-input"
                region.value = "safe-region"
            elif superseding == "hide_show":
                panel.display = False
                await pilot.pause()
                panel.display = True
                await pilot.pause()
            else:
                await panel.remove()
            keychain.release.set()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert name.value == ("other" if superseding == "edit" else "")
            if superseding in {"close", "cancel"}:
                assert not form.has_class("-open")
            elif superseding != "unmount":
                assert form.has_class("-open")
            if superseding in {"refresh", "input_aba", "hide_show"}:
                assert region.value == "safe-region"
            if panel.is_attached:
                assert not panel.query_one("#connection-read-status", Static).display
    finally:
        keychain.release.set()
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_queued_old_cancel_does_not_retire_newer_edit(tmp_path):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            panel._on_add_clicked()
            await pilot.pause()
            panel.query_one(ConnectionFormInline).action_cancel()
            panel._on_edit_clicked("other")
            await drain_workers(pilot.app)
            await pilot.pause()
            assert panel.query_one("#form-name", Input).value == "other"
            assert panel.query_one(ConnectionFormInline).has_class("-open")
            assert [service for service, _, _ in keychain.reads] == ["other-service"] * 3
            assert not panel.query_one("#connection-read-status", Static).display
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_new_form_before_worker_start_avoids_secret_read_and_retires_loading_notice(tmp_path):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            keychain.reads.clear()
            panel._on_edit_clicked("chosen")
            panel.query_one(ConnectionFormInline).open_for_add()
            await drain_workers(pilot.app)
            await pilot.pause()
            assert keychain.reads == []
            assert not panel.query_one("#connection-read-status", Static).display
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "delete"])
async def test_queued_action_from_retired_row_cannot_target_the_reused_slot(tmp_path, action):
    panel, keychain = _panel(tmp_path)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            retired = panel.query_one(f"#{action}-0", Button)
            panel.vm._config_store.remove_connection("chosen")
            await panel.refresh_rows()
            assert not retired.is_attached
            keychain.reads.clear()
            panel.post_message(Button.Pressed(retired))
            await drain_workers(pilot.app)
            await pilot.pause()
            assert keychain.reads == []
            assert not panel.query_one(ConnectionFormInline).has_class("-open")
            assert set(panel.vm._config_store.load().connections) == {"other"}
            assert len(pilot.app.screen_stack) == 1
    finally:
        panel.vm.dispose()


@pytest.mark.asyncio
async def test_demo_resolver_supports_metadata_rows_and_selected_edit(tmp_path):
    hub = _hub()
    vm = S3ConnectionsVM(
        resolver=DemoConnectionResolver(),
        config_store=ConfigStore(path=tmp_path / "demo.toml", read_only=True),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    panel = S3ConnectionsPanel(vm=vm, hub=hub)
    try:
        async with _PanelHost(panel).run_test() as pilot:
            assert len(panel.query(".connection-row")) == 1
            panel._on_edit_clicked("demo-minio")
            await drain_workers(pilot.app)
            await pilot.pause()
            assert panel.query_one("#form-name", Input).value == "demo-minio"
    finally:
        vm.dispose()
