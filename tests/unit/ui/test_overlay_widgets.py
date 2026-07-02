"""Smoke tests for overlay widgets: command palette, confirm modal,
quick look. The runtime transfers UI is :class:`TransfersOverlay`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.ui.widgets.command_palette import CommandPalette, CommandPaletteItem
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.quick_look import QuickLook
from aws_tui.vm.chrome.command_palette_vm import (
    CommandPaletteVM,
    PaletteEntry,
)
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent, QuickLookVM

# ── CommandPalette ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_palette_renders_entries() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = CommandPaletteVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    captured: list[str] = []
    for spec in [
        ("conn.aws-dev", "connection: kaveh-dev", "connection"),
        ("conn.minio", "connection: minio-local", "connection"),
        ("theme.carbon", "theme: carbon", "theme"),
    ]:
        entry_id, label, category = spec
        vm.register_entry(
            PaletteEntry(id=entry_id, label=label, category=category),
            lambda _eid=entry_id: captured.append(_eid),
        )
    vm.open_command.execute()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(CommandPalette(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            items = app.screen.query(CommandPaletteItem)
            assert len(items) == 3
            # Move + execute via VM commands.
            vm.move_selection_command.execute(1)
            await pilot.pause()
            vm.execute_selected_command.execute()
            await pilot.pause()
            assert captured == ["conn.minio"]
    finally:
        vm.dispose()
        hub.dispose()


# ── ConfirmModal ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_modal_renders_request() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = ConfirmationVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                request = ConfirmRequest(
                    title="Delete 3 objects?",
                    body_lines=("data/foo.txt", "data/bar.txt", "data/baz.txt"),
                    confirm_label="Delete",
                    cancel_label="Cancel",
                    danger=True,
                )
                await self.push_screen(ConfirmModal(vm, request, hub=hub))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert "-danger" in modal.classes
    finally:
        vm.dispose()
        hub.dispose()


# ── QuickLook ───────────────────────────────────────────────────────────────


def test_help_modal_lists_current_shipped_app_bindings() -> None:
    source = HelpModal.compose.__code__.co_consts
    rendered_text = "\n".join(str(item) for item in source)

    for expected in (
        "open Settings",
        "delete selected entry",
        "cycle the focused pane source",
        "extend selection",
    ):
        assert expected in rendered_text


async def _bytes_iter(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.mark.asyncio
async def test_quick_look_streams_content() -> None:
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = QuickLookVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    content = QuickLookContent(
        title="readme.md",
        mime="text/markdown",
        chunks=_bytes_iter(b"# Hello\nworld\n"),
        line_count_estimate=2,
    )
    vm.open_command.execute(content)
    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(QuickLook(vm, hub=hub))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import Static

            body = app.screen.query_one("#quicklook-body", Static)
            assert "Hello" in str(body.render())
    finally:
        vm.dispose()
        hub.dispose()
