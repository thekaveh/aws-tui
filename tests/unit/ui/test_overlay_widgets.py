"""Smoke tests for overlay widgets: command palette, confirm modal,
quick look. The runtime transfers UI is :class:`TransfersOverlay`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
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


@pytest.mark.asyncio
async def test_help_modal_renders_active_keymap() -> None:
    keymap = KeymapStore(overlay={"app.help": "h", "app.command_palette": "p"})

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield from ()

        async def on_mount(self) -> None:
            await self.push_screen(HelpModal(keymap=keymap))

    app = _App()
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        rendered = "\n".join(str(row.render()) for row in app.screen.query(".help-row"))

    assert "open Settings" in rendered
    assert "delete selected entry" in rendered
    assert "cycle the focused pane source" in rendered
    assert "extend selection" in rendered
    assert "h" in rendered
    assert "open this help overlay" in rendered
    assert "p" in rendered
    assert "open the command palette" in rendered
    assert "?  or  :" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("theme_name", ThemeStore.BUILTIN_NAMES)
async def test_help_modal_uses_theme_surface_tokens(theme_name: str) -> None:
    theme = ThemeStore().load(theme_name)
    expected_text = next(
        line.split(":", maxsplit=1)[1].strip().rstrip(";")
        for line in theme.splitlines()
        if line.strip().startswith("$text:")
    )
    expected_background = next(
        line.split(":", maxsplit=1)[1].strip().rstrip(";")
        for line in theme.splitlines()
        if line.strip().startswith("$bg-elev:")
    )

    class _App(App[None]):
        CSS = theme

        def compose(self) -> ComposeResult:
            yield from ()

        async def on_mount(self) -> None:
            await self.push_screen(HelpModal(keymap=KeymapStore()))

    app = _App()
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        frame = app.screen.query_one("#help-frame", Vertical)
        assert frame.styles.color.hex6.casefold() == expected_text.casefold()
        assert frame.styles.background.hex6.casefold() == expected_background.casefold()
        assert frame.styles.background.a == 1.0


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
