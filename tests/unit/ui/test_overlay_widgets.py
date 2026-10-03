"""Smoke tests for overlay widgets: command palette, confirm modal,
quick look. The runtime transfers UI is :class:`TransfersOverlay`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.command_palette import CommandPalette, CommandPaletteItem
from aws_tui.ui.widgets.confirm_modal import ConfirmModal, TextualDialogService
from aws_tui.ui.widgets.help_modal import HelpModal
from aws_tui.ui.widgets.quick_look import QuickLook
from aws_tui.vm.chrome.command_palette_vm import (
    CommandPaletteVM,
    PaletteEntry,
)
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent, QuickLookVM
from tests.helpers import wait_until

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
            await wait_until(
                lambda: len(app.screen.query(CommandPaletteItem)) == 3,
                what="command palette entries mounted",
            )
            items = app.screen.query(CommandPaletteItem)
            assert len(items) == 3
            # Move + execute via VM commands.
            vm.move_selection_command.execute(1)
            await pilot.pause()
            vm.execute_selected_command.execute()
            await wait_until(
                lambda: captured == ["conn.minio"],
                what="selected palette command executed",
            )
            assert captured == ["conn.minio"]
    finally:
        vm.dispose()
        hub.dispose()


# ── ConfirmModal ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("danger", "expected_button", "resolves_to"),
    [(True, "cancel", False), (False, "confirm", True)],
)
async def test_confirm_modal_reflex_enter_routes_to_the_safe_side(
    danger: bool, expected_button: str, resolves_to: bool
) -> None:
    """A danger prompt must answer a bare Enter with Cancel, not Confirm.

    Nothing pinned this. Flipping the default to ``"confirm"`` left 131 tests
    and 50 modal snapshots green, because ``-focused`` is deliberately not
    painted at mount so every snapshot is byte-identical either way — while a
    reflex Enter on a delete prompt would have destroyed the selected file.
    Asserting the resolved OUTCOME rather than the focus ring, since the ring
    is intentionally invisible here.
    """
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    vm = ConfirmationVM(hub=hub, dispatcher=dispatcher)
    vm.construct()
    try:
        request = ConfirmRequest(
            title="Delete 1 object?",
            confirm_label="Delete",
            cancel_label="Cancel",
            danger=danger,
        )

        answered: list[bool | None] = []

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                self.push_screen(ConfirmModal(vm, request, hub=hub), answered.append)

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal._focused_button_id == expected_button

            # A bare Enter, with no navigation first.
            modal.action_commit_focused()
            await wait_until(
                lambda: answered == [resolves_to],
                what="confirmation resolved to its focused action",
            )

        assert answered == [resolves_to]
    finally:
        vm.dispose()
        hub.dispose()


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


async def test_dialog_mount_cancellation_preserves_caller_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub: MessageHub = MessageHub()
    vm = ConfirmationVM(hub=hub, dispatcher=RxDispatcher.immediate())
    vm.construct()
    app: App[None] = App()
    original_mount = ConfirmModal.on_mount
    try:
        async with app.run_test() as pilot:
            dialogs = TextualDialogService(app, vm, hub=hub)

            def cancel_on_mount(modal: ConfirmModal) -> None:
                original_mount(modal)
                assert not modal._mounted_event.is_set()
                task.cancel()

            monkeypatch.setattr(ConfirmModal, "on_mount", cancel_on_mount)
            task = asyncio.create_task(vm.ask(ConfirmRequest("Confirm"), dialog_service=dialogs))
            with pytest.raises(asyncio.CancelledError):
                await task
            await pilot.pause()
            assert task.cancelled()
            assert not vm.is_open
            assert vm.request is None
            assert not any(isinstance(screen, ConfirmModal) for screen in app.screen_stack)
    finally:
        vm.dispose()
        hub.dispose()


# ── QuickLook ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_help_modal_renders_active_keymap() -> None:
    # f9/f10 rather than single letters: "h" and "p" occur incidentally in
    # "this", "open" and "pane", so the old needles were satisfied by the
    # surrounding prose and the injected keymap was never actually read.
    keymap = KeymapStore(overlay={"app.help": "f9", "app.command_palette": "f10"})

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield from ()

        async def on_mount(self) -> None:
            await self.push_screen(HelpModal(keymap=keymap))

    app = _App()
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        rows = [str(row.render()) for row in app.screen.query(".help-row")]
        rendered = "\n".join(rows)

    assert "open Settings" in rendered
    assert "delete selected entry" in rendered
    assert "cycle the focused pane source" in rendered
    assert "extend selection" in rendered
    # Assert the configured key lands on the row for ITS action, not merely
    # somewhere in the overlay.
    help_row = next(row for row in rows if "open this help overlay" in row)
    palette_row = next(row for row in rows if "open the command palette" in row)
    assert "f9" in help_row
    assert "f10" in palette_row
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
        async with app.run_test(size=(80, 24)):
            from textual.widgets import Static

            await wait_until(
                lambda: "Hello" in str(app.screen.query_one("#quicklook-body", Static).render()),
                what="quick look streamed body rendered",
            )
            body = app.screen.query_one("#quicklook-body", Static)
            assert "Hello" in str(body.render())
    finally:
        vm.dispose()
        hub.dispose()
