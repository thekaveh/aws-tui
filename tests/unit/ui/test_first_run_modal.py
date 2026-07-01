"""Smoke tests for the first-run modal."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.ui import notifications as notification_helpers
from aws_tui.ui.widgets.first_run_modal import FirstRunModal
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
from aws_tui.vm.chrome.first_run_vm import FirstRunAction, FirstRunVM, S3CompatForm


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _vm_and_hub() -> tuple[FirstRunVM, MessageHub[Message]]:
    hub = _hub()
    dispatcher = RxDispatcher.immediate()
    vm = FirstRunVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    return vm, hub


@pytest.mark.asyncio
async def test_first_run_modal_has_three_buttons() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = FirstRunVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(FirstRunModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, FirstRunModal)
            buttons = modal.query(ModalButton)
            assert {b.button_id for b in buttons} == {
                "first-run-aws-btn",
                "first-run-s3-btn",
                "first-run-skip-btn",
                "form-cancel-btn",
                "form-save-btn",
            }
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_add_s3_compat_flow_persists_and_dismisses(tmp_path: Path) -> None:
    """Open the inline form via ConnectionFormSubmitted event → on save the
    connection is written to ConfigStore and the modal dismisses with
    ADD_S3_COMPAT result."""
    store = ConfigStore(path=tmp_path / "config.toml")
    vm, hub = _vm_and_hub()
    dismissed_result: list[FirstRunAction] = []

    try:

        class _App(App[None]):
            _app_ctx = SimpleNamespace(config_store=store)  # type: ignore[assignment]

            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(
                    FirstRunModal(vm, hub=hub),
                    callback=dismissed_result.append,
                )

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, FirstRunModal)
            # Open the inline form programmatically.
            modal.query_one(ConnectionFormInline).open_for_add()
            await pilot.pause()
            # Post a well-formed ConnectionFormSubmitted event.
            form_data = S3CompatForm(
                name="test-conn",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="AK",
                secret_access_key="SK",
                force_path_style=True,
                verify_tls=True,
            )
            modal.post_message(
                ConnectionFormSubmitted(form=form_data, mode="add", original_name=None)
            )
            await pilot.pause()
            await pilot.pause()

        # Modal should have dismissed with ADD_S3_COMPAT.
        assert dismissed_result == [FirstRunAction.ADD_S3_COMPAT]
        # Connection must be persisted to the config store.
        assert "test-conn" in store.load().connections
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_add_s3_compat_flow_keeps_modal_open_on_save_failure(tmp_path: Path) -> None:
    """If config_store.add_connection raises, the modal stays open and does
    NOT dismiss — regression that the original bare `except Exception: pass`
    masked by silently ignoring the error and dismissing anyway."""
    store = ConfigStore(path=tmp_path / "config.toml")
    vm, hub = _vm_and_hub()

    # Patch add_connection to always raise.
    original_add = store.add_connection
    store.add_connection = MagicMock(side_effect=PermissionError("disk read-only"))  # type: ignore[method-assign]

    try:

        class _App(App[None]):
            _app_ctx = SimpleNamespace(config_store=store)  # type: ignore[assignment]

            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(FirstRunModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, FirstRunModal)
            notifications: list[tuple[str, dict[str, object]]] = []

            def fake_notify(message: str, **kwargs: object) -> None:
                notifications.append((message, kwargs))

            modal.notify = fake_notify  # type: ignore[method-assign]
            # Open form and post a submit event.
            modal.query_one(ConnectionFormInline).open_for_add()
            await pilot.pause()
            form_data = S3CompatForm(
                name="fail-conn",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="AK",
                secret_access_key="SK",
                force_path_style=True,
                verify_tls=True,
            )
            modal.post_message(
                ConnectionFormSubmitted(form=form_data, mode="add", original_name=None)
            )
            await pilot.pause()
            # The modal must still be the active screen — it must NOT have dismissed.
            assert app.screen is modal, "Modal dismissed despite save failure"
            assert notifications
            assert notifications[0][0] == "Couldn't save connection: disk read-only"
            assert notifications[0][1]["severity"] == "error"
            assert notifications[0][1]["markup"] is False
    finally:
        store.add_connection = original_add  # type: ignore[method-assign]
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_add_s3_compat_save_failure_uses_production_toast_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A production-shaped app context routes failures through the toast stack."""
    store = ConfigStore(path=tmp_path / "config.toml")
    vm, hub = _vm_and_hub()
    toast_stack = object()
    toast_calls: list[dict[str, object]] = []

    original_add = store.add_connection
    store.add_connection = MagicMock(side_effect=PermissionError("disk read-only"))  # type: ignore[method-assign]

    def fake_error(stack: object, **kwargs: object) -> object:
        toast_calls.append({"stack": stack, **kwargs})
        return object()

    monkeypatch.setattr(notification_helpers, "error", fake_error)

    try:

        class _App(App[None]):
            _app_ctx = SimpleNamespace(
                config_store=store,
                root_vm=SimpleNamespace(
                    chrome=SimpleNamespace(toast_stack=toast_stack),
                ),
            )  # type: ignore[assignment]

            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(FirstRunModal(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, FirstRunModal)
            notifications: list[tuple[str, dict[str, object]]] = []

            def fake_notify(message: str, **kwargs: object) -> None:
                notifications.append((message, kwargs))

            modal.notify = fake_notify  # type: ignore[method-assign]
            modal.query_one(ConnectionFormInline).open_for_add()
            await pilot.pause()
            form_data = S3CompatForm(
                name="fail-conn",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="AK",
                secret_access_key="SK",
                force_path_style=True,
                verify_tls=True,
            )
            modal.post_message(
                ConnectionFormSubmitted(form=form_data, mode="add", original_name=None)
            )
            await pilot.pause()

            assert app.screen is modal
            assert notifications == []
            assert toast_calls == [
                {
                    "stack": toast_stack,
                    "subject": "Settings",
                    "message": "Couldn't save connection: disk read-only",
                    "action": "Fix the config path or permissions, then retry.",
                }
            ]
    finally:
        store.add_connection = original_add  # type: ignore[method-assign]
        vm.dispose()
        hub.dispose()
