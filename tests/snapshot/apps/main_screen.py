"""Main-screen snapshot harness.

Composes a self-contained Textual App that renders the full chrome +
dual pane against an in-memory file tree. Theme name comes via constructor
argument so the same code parametrizes across the four themes.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from vmx import MessageHub, RxDispatcher

from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.services_menu import ServicesMenu
from aws_tui.ui.widgets.status_bar import StatusBar
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.chrome.status_bar_vm import StatusBarVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.nav_menu_vm import NavMenuVM as ServicesMenuVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry
from tests.snapshot.apps._seed import seed_left, seed_right


class _S3Stub:
    descriptor = ServiceDescriptor(id="s3", label="S3", icon="S3")

    def supports(self, conn: object) -> bool:
        return True

    def build_vm(self, conn: object) -> object:
        return object()


class _EC2Stub:
    descriptor = ServiceDescriptor(id="ec2", label="EC2", icon="EC2")

    def supports(self, conn: object) -> bool:
        return getattr(conn, "kind", None) == "aws"

    def build_vm(self, conn: object) -> object:
        return object()


def _connection() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="kaveh-dev",
    )


class MainScreenApp(App[None]):
    """Renders the full main screen for snapshot tests."""

    CSS = ""  # populated dynamically per theme in __init__

    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self._theme_store = ThemeStore()
        # Inject the theme CSS as the app's stylesheet.
        self.CSS = self._theme_store.load(theme)
        self._theme_name = theme

        # VM tree
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._keymap = KeymapStore()

        self._status_vm = StatusBarVM(hub=self._hub, dispatcher=self._dispatcher)
        self._hint_vm = HintLegendVM(
            hub=self._hub, dispatcher=self._dispatcher, keymap=self._keymap
        )
        self._hint_vm.register_focusable(
            "pane.left",
            (
                "pane.descend",
                "pane.copy",
                "pane.move",
                "pane.delete",
                "pane.refresh",
            ),
        )

        registry = ServiceRegistry()
        registry.register(_S3Stub())
        registry.register(_EC2Stub())
        self._menu_vm = ServicesMenuVM(
            registry=registry, hub=self._hub, dispatcher=self._dispatcher
        )

        self._left_fs = None  # type: ignore[assignment]
        self._right_fs = None  # type: ignore[assignment]
        self._dual_vm: DualPaneVM | None = None
        self._journal_dir = None

    def compose(self) -> ComposeResult:
        # StatusBar at top
        yield StatusBar(self._status_vm, hub=self._hub, id="status-bar")
        with Horizontal(id="main-area"):
            yield ServicesMenu(self._menu_vm, hub=self._hub, id="services-menu")
            # Placeholder for dual pane — actually populated in on_mount after
            # we've awaited the seeds.
            yield Container(id="dual-pane-host")
        yield HintLegend(self._hint_vm, hub=self._hub, id="hint-legend")

    async def on_mount(self) -> None:
        # Construct VMs.
        self._status_vm.construct()
        self._hint_vm.construct()
        self._menu_vm.construct()

        # Now seed FS and build dual pane VM.
        import tempfile
        from pathlib import Path

        self._journal_dir = Path(tempfile.mkdtemp(prefix="aws-tui-snap-"))
        left_fs = await seed_left()
        right_fs = await seed_right()
        left = PaneVM(
            provider=left_fs, hub=self._hub, dispatcher=self._dispatcher, id_prefix="pane.s3"
        )
        right = PaneVM(
            provider=right_fs, hub=self._hub, dispatcher=self._dispatcher, id_prefix="pane.local"
        )
        self._dual_vm = DualPaneVM(
            left=left,
            right=right,
            hub=self._hub,
            dispatcher=self._dispatcher,
            transfer_journal=TransferJournal(base_dir=self._journal_dir),
        )
        self._dual_vm.construct()
        await self._dual_vm.setup()

        # Mount the DualPane widget.
        host = self.query_one("#dual-pane-host", Container)
        host.mount(DualPane(self._dual_vm, hub=self._hub))

        # Drive connection + focus so chrome surfaces real text.
        self._status_vm.update_connection(_connection(), TokenState.CONNECTED)
        self._menu_vm.update_connection(_connection())
        self._menu_vm.switch_service_command.execute("s3")
        from aws_tui.vm.messages import FocusChangedMessage

        self._hub.send(FocusChangedMessage(focused_vm_id="pane.left"))


__all__ = ["MainScreenApp"]
