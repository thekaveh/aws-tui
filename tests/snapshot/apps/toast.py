"""ToastStack snapshot harness.

Composes one INFO toast (theme-change-style) and one ERROR toast (with
an action label so the action chip is exercised).
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel


class ToastSnapshotApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._theme = theme
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._stack = ToastStackVM(hub=self._hub, dispatcher=self._dispatcher)
        self._stack.construct()
        # Two toasts: one INFO (theme-change-like) and one ERROR with
        # action chip (auth-expired-like).
        self._stack.raise_toast(
            ToastModel(
                id="info-1",
                text=f"Theme changed to: {self._theme}",
                level=ToastLevel.INFO,
                sticky=True,
                timeout_seconds=None,
                action_label=None,
                action_action=None,
            )
        )
        self._stack.raise_toast(
            ToastModel(
                id="err-1",
                text="Auth expired",
                level=ToastLevel.ERROR,
                sticky=True,
                timeout_seconds=None,
                action_label="authenticate",
                action_action="auth.authenticate",
            )
        )

    def compose(self) -> ComposeResult:
        yield Static("aws-tui (behind toasts)", id="placeholder")
        yield ToastStack(self._stack, hub=self._hub)


__all__ = ["ToastSnapshotApp"]
