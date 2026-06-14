"""Top-level Textual application — composes RootVM + chrome + content host.

This is the real composition that replaces the M0 hello-world placeholder.
The actual layer wiring lives in :mod:`aws_tui.composition` so this module
stays focused on the Textual side (compose, mounting, action handlers).
"""

from __future__ import annotations

import contextlib
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.widgets import Static

from aws_tui.composition import AppContext, build_app_context
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_menu import ServicesMenu
from aws_tui.ui.widgets.status_bar import StatusBar
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.version import __version__


class AwsTuiApp(App[None]):
    """The aws-tui Textual application.

    Composition root, real version. Constructor accepts an optional
    :class:`AppContext` so tests / E2E journeys can inject pre-wired
    state instead of touching ``~/.config/aws-tui``.
    """

    TITLE = "aws-tui"
    SUB_TITLE = f"v{__version__}"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(self, context: AppContext | None = None) -> None:
        super().__init__()
        self._app_ctx = context if context is not None else build_app_context()
        # Theme CSS is injected as the App's stylesheet (see on_mount).
        self._actions = ActionRegistry()
        self._resolver = BindingResolver(
            keymap=self._app_ctx.keymap_store,
            actions=self._actions,
        )
        # Register handlers for the action ids the BindingResolver advertises.
        self._actions.register("app.quit", self._handle_quit)

    @property
    def app_ctx(self) -> AppContext:
        return self._app_ctx

    def compose(self) -> ComposeResult:
        ctx = self._app_ctx
        yield StatusBar(ctx.root_vm.chrome.status_bar, hub=ctx.hub, id="status-bar")
        with Horizontal(id="main-area"):
            yield ServicesMenu(ctx.root_vm.services_menu, hub=ctx.hub, id="services-menu")
            yield Container(id="content-host")
        yield HintLegend(ctx.root_vm.chrome.hint_legend, hub=ctx.hub, id="hint-legend")
        yield ToastStack(ctx.root_vm.chrome.toast_stack, hub=ctx.hub, id="toast-stack")

    async def on_mount(self) -> None:
        ctx = self._app_ctx
        # Construct the VM tree.
        ctx.root_vm.construct()
        ctx.transfers_vm.construct()
        ctx.confirm_vm.construct()
        ctx.quick_look_vm.construct()
        ctx.command_palette_vm.construct()

        # Apply the active theme as additional stylesheet rules.
        try:
            theme_css = ctx.theme_store.load(ctx.initial_theme)
            self.stylesheet.add_source(theme_css)
            self.stylesheet.parse()
            self.stylesheet.update(self)
        except Exception:
            ctx.log_sink.error("theme.load.failed", name=ctx.initial_theme)

        # Show a placeholder until a connection is selected.
        with contextlib.suppress(Exception):
            host = self.query_one("#content-host", Container)
            host.mount(Static("no service selected", id="content-placeholder"))

    # ── Action handlers ────────────────────────────────────────────────────

    async def action_app_quit(self) -> None:
        await self._aws_tui_shutdown()
        self.exit()

    def _handle_quit(self) -> None:
        self.exit()

    async def _aws_tui_shutdown(self) -> None:
        """Graceful shutdown per spec sec 5.4.

        Renamed away from ``_shutdown`` to avoid colliding with the
        internal ``App._shutdown`` lifecycle hook on Textual.
        """
        ctx = self._app_ctx
        with contextlib.suppress(Exception):
            ctx.transfers_vm.cancel_all_command.execute()
        with contextlib.suppress(Exception):
            await ctx.aws_session.aclose_all_clients()
        with contextlib.suppress(Exception):
            ctx.log_sink.flush()
            ctx.log_sink.close()
        with contextlib.suppress(Exception):
            ctx.command_palette_vm.dispose()
            ctx.quick_look_vm.dispose()
            ctx.confirm_vm.dispose()
            ctx.transfers_vm.dispose()
            ctx.root_vm.dispose()


def main() -> None:
    """Run the Textual app. Invoked by ``aws-tui`` console script and ``python -m aws_tui``."""
    AwsTuiApp().run()


if __name__ == "__main__":
    main()


__all__ = ["AwsTuiApp", "main"]
