"""Top-level Textual application — composes RootVM + chrome + content host.

This is the real composition that replaces the M0 hello-world placeholder.
The actual layer wiring lives in :mod:`aws_tui.composition` so this module
stays focused on the Textual side (compose, mounting, action handlers).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from datetime import UTC, datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.widgets import Static

from aws_tui.composition import AppContext, build_app_context
from aws_tui.infra.crash_dump import CrashDump
from aws_tui.ui.actions import ActionRegistry
from aws_tui.ui.bindings import BindingResolver
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_menu import ServicesMenu
from aws_tui.ui.widgets.status_bar import StatusBar
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.version import __version__
from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashReport, CrashVM

_ACTION_RING_SIZE = 100


class AwsTuiApp(App[None]):
    """The aws-tui Textual application.

    Composition root, real version. Constructor accepts an optional
    :class:`AppContext` so tests / E2E journeys can inject pre-wired
    state instead of touching ``~/.config/aws-tui``.
    """

    TITLE = "aws-tui"
    SUB_TITLE = f"v{__version__}"

    # Declare the notifications layer so ToastStack floats above the
    # main layout instead of consuming flow space.
    CSS = """
    Screen {
        layers: base notifications;
    }
    """

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
        # Action ring buffer feeds the crash dump per spec §7.10. Each entry
        # is a short ISO-timestamped action id string; we keep the most
        # recent ``_ACTION_RING_SIZE`` to bound memory.
        self._action_ring: deque[str] = deque(maxlen=_ACTION_RING_SIZE)
        self._last_action_id: str | None = None
        # Populated by ``_handle_exception`` when Textual surfaces an
        # unhandled exception so ``main()`` can print the dump path and
        # re-raise after the app has torn down.
        self._crash_report: CrashReport | None = None

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

        # Pick the initial connection — respect config's defaults.connection if
        # set, else fall back to the first auto-discovered profile. With no
        # connections at all, leave the screen unwired (a follow-up wires the
        # first-run modal here).
        try:
            cfg = ctx.config_store.load()
        except Exception:
            cfg = None
        connections = ctx.connection_resolver.list()
        initial_conn = None
        if cfg is not None and cfg.defaults.connection:
            initial_conn = next(
                (c for c in connections if c.name == cfg.defaults.connection),
                None,
            )
        if initial_conn is None and connections:
            initial_conn = connections[0]

        if initial_conn is not None:
            auth_state = ctx.aws_session.probe_token(initial_conn).state
            await ctx.root_vm.switch_connection_with(initial_conn, auth_state)
            with contextlib.suppress(Exception):
                await ctx.root_vm.switch_service("s3")
            # After switch_service, mount the service's view into the host.
            # The VM tree is updated by switch_service, but the View layer
            # has to follow — Textual won't infer that from VMx state.
            try:
                current_vm = ctx.root_vm.content_host.current
                if current_vm is not None:
                    host = self.query_one("#content-host", Container)
                    host.remove_children()
                    host.mount(DualPane(current_vm, hub=ctx.hub, id="content-dual-pane"))
            except Exception:
                ctx.log_sink.error("app.mount_service_view.failed", service_id="s3")
        else:
            with contextlib.suppress(Exception):
                host = self.query_one("#content-host", Container)
                host.mount(
                    Static(
                        "no AWS profile or S3-compatible connection found.\n"
                        "configure one in ~/.config/aws-tui/config.toml or "
                        "run `aws configure` then relaunch.",
                        id="content-placeholder",
                    )
                )

    # ── Action handlers ────────────────────────────────────────────────────

    async def action_app_quit(self) -> None:
        await self._aws_tui_shutdown()
        self.exit()

    def _handle_quit(self) -> None:
        self.exit()

    # ── Crash handling ─────────────────────────────────────────────────────

    def record_action(self, action_id: str) -> None:
        """Record an action id in the ring buffer and track it as the latest.

        Called by the input router / action invokers so the crash modal can
        decide whether ``continue`` is safe and the dump can include the
        last 100 user actions per spec §7.10.
        """
        ts = datetime.now(UTC).isoformat()
        self._action_ring.append(f"{ts} {action_id}")
        self._last_action_id = action_id

    @property
    def last_action_id(self) -> str | None:
        return self._last_action_id

    def _build_crash_report(self, exc: BaseException) -> CrashReport:
        """Write the dump and assemble the matching :class:`CrashReport`.

        Side effects: a new file under ``~/.cache/aws-tui/crash/`` and an
        ``ERROR``-level log line tagged ``crash.captured``. Always
        succeeds (falls back to a side-channel path if the write fails).
        """
        ctx = self._app_ctx
        dump = CrashDump(base_dir=ctx.log_sink.path.parent.parent / "crash")
        log_path = ctx.log_sink.path
        try:
            dump_path = dump.write(
                exc=exc,
                log_path=log_path,
                action_ring=list(self._action_ring),
            )
        except Exception:
            dump_path = log_path.parent / "crash-fallback.txt"
        last_id = self._last_action_id
        report = CrashReport(
            timestamp=datetime.now(UTC),
            exception_type=type(exc).__name__,
            exception_message=str(exc) or repr(exc),
            traceback_short=CrashDump.short_traceback(exc),
            dump_path=dump_path,
            can_continue=CrashReport.is_safe_to_continue(last_id),
            last_action_id=last_id,
        )
        with contextlib.suppress(Exception):
            logging.getLogger("aws_tui").error(
                "crash.captured",
                extra={
                    "json_fields": {
                        "exception_type": report.exception_type,
                        "dump_path": str(report.dump_path),
                        "last_action_id": report.last_action_id,
                    }
                },
            )
        return report

    def _handle_exception(self, error: Exception) -> None:
        """Override Textual's fatal handler to write a crash dump first.

        We still defer to the upstream behavior (which sets ``_return_code``
        and tears down) — the dump and report are the only thing we add
        before the app exits.
        """
        try:
            self._crash_report = self._build_crash_report(error)
        finally:
            super()._handle_exception(error)

    async def show_crash_modal(self, report: CrashReport) -> CrashChoice:
        """Push the crash modal for ``report`` and await the user's choice.

        Public so tests and recovery flows can drive the modal without
        also having to raise an exception. Mostly used from
        :func:`run_with_crash_capture`.
        """
        ctx = self._app_ctx
        crash_vm = CrashVM(report, hub=ctx.hub, dispatcher=ctx.dispatcher)
        crash_vm.construct()
        try:
            ask_task = asyncio.create_task(crash_vm.ask())
            await self.push_screen(CrashModal(crash_vm, hub=ctx.hub))
            return await ask_task
        finally:
            crash_vm.dispose()

    @property
    def crash_report(self) -> CrashReport | None:
        """The last crash report captured via ``_handle_exception``."""
        return self._crash_report

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
    """Run the Textual app with unhandled-exception capture.

    Invoked by the ``aws-tui`` console script and ``python -m aws_tui``.
    If the app surfaces an unhandled exception, ``_handle_exception``
    writes a crash dump under ``~/.cache/aws-tui/crash/`` and the
    saved :class:`CrashReport` is printed here before the exception is
    re-raised so the user knows where the dump landed.
    """
    app = AwsTuiApp()
    try:
        app.run()
    except BaseException as exc:
        report = app.crash_report
        if report is None:
            report = app._build_crash_report(exc)
        # Print to stderr (after Textual has restored the terminal).
        import sys

        print(
            "\naws-tui crashed.\n"
            f"  {report.exception_type}: {report.exception_message}\n"
            f"  dump: {report.dump_path}\n",
            file=sys.stderr,
        )
        raise
    else:
        # Normal exit; crash report would be set only if `_handle_exception`
        # fired and Textual swallowed the exception (it does this when
        # rendering a fatal panel).
        report = app.crash_report
        if report is not None:
            import sys

            print(
                "\naws-tui crashed.\n"
                f"  {report.exception_type}: {report.exception_message}\n"
                f"  dump: {report.dump_path}\n",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()


__all__ = ["AwsTuiApp", "main"]
