"""Smoke test for S3ConnectionsPanel construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
from aws_tui.ui.widgets.settings.s3_connections_panel import S3ConnectionsPanel
from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _entry(name: str) -> ConnectionEntry:
    """Minimal s3-compatible ConnectionEntry for test pre-seeding."""
    return ConnectionEntry(
        name=name,
        kind="s3-compatible",
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        credentials="static",
        access_key_id="K",
        secret_access_key="S",
        session_token="TOK",
        force_path_style=True,
        verify_tls=True,
    )


def test_s3_connections_panel_can_be_constructed(tmp_path: Path) -> None:
    hub = _hub()
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
    hub = _hub()
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


@pytest.mark.asyncio
async def test_panel_routes_form_submission_to_vm_add(tmp_path: Path) -> None:
    """When ConnectionFormSubmitted fires with mode='add', the panel
    calls vm.add(entry_from_form(form))."""
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)

    class _Host(App[None]):
        def __init__(self, w: S3ConnectionsPanel) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    app = _Host(panel)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            form = S3CompatForm(
                name="from-event",
                endpoint_url="http://localhost:9999",
                region="us-east-1",
                access_key_id="K",
                secret_access_key="S",
                force_path_style=True,
                verify_tls=True,
            )
            panel.post_message(ConnectionFormSubmitted(form=form, mode="add", original_name=None))
            await pilot.pause()
        # After event handling the row must be persisted.
        assert "from-event" in store.load().connections
    finally:
        s3_vm.dispose()


@pytest.mark.asyncio
async def test_edit_preserves_hidden_session_token(tmp_path: Path) -> None:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(_entry("sts"))
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)
    app = _PanelHost(panel)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            form = panel.query_one(ConnectionFormInline)
            panel._on_edit_clicked("sts")
            submitted = form._form_vm.model
            assert submitted.session_token == "TOK"
            panel.post_message(
                ConnectionFormSubmitted(form=submitted, mode="edit", original_name="sts")
            )
            await pilot.pause()

        assert store.load().connections["sts"].session_token == "TOK"
    finally:
        s3_vm.dispose()


@pytest.mark.asyncio
async def test_duplicate_name_keeps_form_open_and_surfaces_error(tmp_path: Path) -> None:
    """Regression: when vm.add raises ValueError (duplicate name), the
    panel must keep the form open + mark the name field invalid + show
    a toast (not silently swallow the error)."""
    from textual.widgets import Input

    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    # Pre-seed a conflict.
    store.add_connection(_entry("dup"))
    resolver = ConnectionResolver(config_store=store)
    s3_vm = S3ConnectionsVM(
        resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER
    )
    s3_vm.construct()
    panel = S3ConnectionsPanel(vm=s3_vm, hub=hub)

    class _Host(App[None]):
        def __init__(self, w: S3ConnectionsPanel) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    app = _Host(panel)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            form_obj = S3CompatForm(
                name="dup",  # duplicate!
                endpoint_url="http://localhost:9999",
                region="us-east-1",
                access_key_id="K",
                secret_access_key="S",
                force_path_style=True,
                verify_tls=True,
            )
            form = panel.query_one(ConnectionFormInline)
            form.open_for_add()
            await pilot.pause()
            form.post_message(
                ConnectionFormSubmitted(form=form_obj, mode="add", original_name=None)
            )
            await pilot.pause()
            # Form must still be open
            assert form.has_class("-open")
            # Name field must be marked invalid
            assert pilot.app.query_one("#form-name", Input).has_class("-invalid")
    finally:
        s3_vm.dispose()
