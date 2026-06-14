"""Pane-state snapshot harnesses.

One App per PaneState placeholder rendered inside a single Pane widget so
the test focuses on the state body rendering and not the dual-pane layout.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from tests.unit.domain._in_memory_fs import InMemoryFS


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


class _PaneStateApp(App[None]):
    def __init__(self, *, theme: str, state: PaneState, error_text: str | None = None) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._fs = InMemoryFS()
        self._vm = PaneVM(
            provider=self._fs,
            initial_path=PathRef(("bucket-prod",)),
            hub=self._hub,
            dispatcher=self._dispatcher,
            id_prefix="pane.state",
        )
        self._target_state = state
        self._error_text = error_text

    def compose(self) -> ComposeResult:
        yield Pane(self._vm, hub=self._hub, id="pane")

    async def on_mount(self) -> None:
        self._vm.construct()
        # We don't call setup() — instead manually set the target state so the
        # placeholder renders deterministically.
        self._vm._state = self._target_state
        if self._error_text:
            self._vm._error_text = self._error_text
        # Mark the pane as focused so the border accent is visible.
        pane = self.query_one(Pane)
        pane.set_focused(True)
        pane._render_body()


def make_loading_app(theme: str) -> _PaneStateApp:
    return _PaneStateApp(theme=theme, state=PaneState.LOADING)


def make_empty_app(theme: str) -> _PaneStateApp:
    return _PaneStateApp(theme=theme, state=PaneState.EMPTY)


def make_auth_required_app(theme: str) -> _PaneStateApp:
    return _PaneStateApp(theme=theme, state=PaneState.AUTH_REQUIRED)


def make_forbidden_app(theme: str) -> _PaneStateApp:
    return _PaneStateApp(
        theme=theme,
        state=PaneState.FORBIDDEN,
        error_text="s3:GetObject denied on bucket-prod",
    )


def make_unreachable_app(theme: str) -> _PaneStateApp:
    return _PaneStateApp(theme=theme, state=PaneState.UNREACHABLE)


def make_error_app(theme: str) -> _PaneStateApp:
    return _PaneStateApp(
        theme=theme,
        state=PaneState.ERROR,
        error_text="invalid bucket name",
    )


__all__ = [
    "make_auth_required_app",
    "make_empty_app",
    "make_error_app",
    "make_forbidden_app",
    "make_loading_app",
    "make_unreachable_app",
]
