"""ThemePickerModal snapshot harness.

Composes a real ``ThemePickerVM`` with all 10 themes registered and
cursor on row 4 (``amber``). Pushes the modal so the snapshot
captures the picker on top of an empty base.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM
from tests.snapshot.conftest import THEMES


class ThemePickerSnapshotApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._theme = theme
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        # noop callback — snapshot captures static state, doesn't actually
        # switch themes during the screenshot
        self._picker = ThemePickerVM(
            themes=THEMES,
            active_theme=theme,
            on_pick=lambda _name: None,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        self._picker.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui (behind picker)", id="placeholder")

    async def on_mount(self) -> None:
        modal = ThemePickerModal(picker=self._picker, hub=self._hub)
        await self.push_screen(modal)
        await self.refresh_bindings()
        # Move cursor to row 3 (amber) so the snapshot exercises the
        # cursor highlight (different theme position per row keeps the
        # snapshot informative).
        modal.action_move_down()
        modal.action_move_down()
        modal.action_move_down()


__all__ = ["ThemePickerSnapshotApp"]
