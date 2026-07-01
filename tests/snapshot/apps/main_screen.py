"""Production-shaped main-screen snapshot harness.

Composes a self-contained Textual App that renders the production chrome
shape (BrandBanner + NavMenu + content host + HintLegend + overlays)
against an in-memory file tree. Theme name comes via constructor
argument so the same code parametrizes across every built-in theme.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from vmx import MessageHub, RxDispatcher

from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.transfer_vm import TransferModel
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.messages import TransferState
from aws_tui.vm.nav_menu_vm import NavMenuVM as ServicesMenuVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry
from tests.snapshot.apps._seed import seed_left, seed_right


class _S3Stub:
    # Bucket emoji matches the real S3Service.descriptor.icon so the
    # snapshot reflects what production renders.
    descriptor = ServiceDescriptor(id="s3", label="S3", icon="🪣")

    def supports(self, conn: object) -> bool:
        return True

    def build_vm(self, conn: object) -> object:
        return object()


class _EC2Stub:
    # Desktop computer emoji stands in for a future EC2 service icon
    # — matches the icons-only vision for the collapsed rail. U+1F5A5
    # DESKTOP COMPUTER + U+FE0F VARIATION SELECTOR-16 to force the
    # colored emoji presentation.
    descriptor = ServiceDescriptor(id="ec2", label="EC2", icon="🖥️")

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

    LAYOUT_CSS = """
    Screen {
        layers: base dropdown notifications;
    }
    #main-area {
        height: 1fr;
        width: 1fr;
        margin: 0 1;
    }
    BrandBanner {
        margin: 1 1 0 1;
    }
    #content-host {
        height: 1fr;
        width: 1fr;
    }
    """
    CSS = ""  # populated dynamically per theme in __init__

    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self._theme_store = ThemeStore()
        # Inject the theme CSS as the app's stylesheet.
        self.CSS = self.LAYOUT_CSS + "\n" + self._theme_store.load(theme)
        self._theme_name = theme

        # VM tree
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._keymap = KeymapStore()

        self._hint_vm = HintLegendVM(
            hub=self._hub, dispatcher=self._dispatcher, keymap=self._keymap
        )
        self._toast_vm = ToastStackVM(hub=self._hub, dispatcher=self._dispatcher)
        self._transfers_vm = TransfersVM(hub=self._hub, dispatcher=self._dispatcher)
        self._transfers_vm.register(
            TransferModel(
                id="snap-copy-001",
                direction="download",
                source_label="s3://kaveh-dev/etl-input/raw/events/2026-06-27.json.gz",
                destination_label="~/Downloads/2026-06-27.json.gz",
                bytes_done=1_024_000,
                bytes_total=2_310_000,
                state=TransferState.RUNNING,
            )
        )
        self._focus_coordinator = FocusCoordinatorVM(
            hub=self._hub,
            dispatcher=self._dispatcher,
            initial=FocusSlot.S3_LEFT,
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
        yield BrandBanner(
            theme_name=self._theme_name,
            hub=self._hub,
            demo=False,
            id="brand-banner",
        )
        with Horizontal(id="main-area"):
            yield NavMenu(
                vm=self._menu_vm,
                hub=self._hub,
                focus_coordinator=self._focus_coordinator,
                id="nav-menu",
            )
            # Placeholder for dual pane — actually populated in on_mount after
            # we've awaited the seeds.
            yield Container(id="content-host")
        yield HintLegend(self._hint_vm, hub=self._hub, id="hint-legend")
        yield ToastStack(self._toast_vm, hub=self._hub, id="toast-stack")
        yield TransfersOverlay(self._transfers_vm, hub=self._hub, id="transfers-overlay")

    async def on_mount(self) -> None:
        # Construct VMs.
        self._hint_vm.construct()
        self._toast_vm.construct()
        self._transfers_vm.construct()
        self._focus_coordinator.construct()
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
        host = self.query_one("#content-host", Container)
        host.mount(
            DualPane(
                self._dual_vm,
                hub=self._hub,
                focus_coordinator=self._focus_coordinator,
                id="content-dual-pane",
            )
        )

        # Drive connection + focus so chrome surfaces real text.
        self._menu_vm.update_connection(_connection())
        self._menu_vm.switch_service_command.execute("s3")
        from aws_tui.vm.messages import FocusChangedMessage

        self._hub.send(FocusChangedMessage(focused_vm_id="pane.left"))

    def on_ready(self) -> None:
        # Mirror the real app's startup focus: no Textual-focused descendant
        # in NavMenu — the left pane is the active slot via VM focus +
        # priority bindings. Without this, Textual auto-focuses NavMenu's
        # first OptionList and the :focus-within rule paints the rail's
        # accent border, polluting the chrome snapshot. Done in on_ready
        # rather than on_mount so the focus-drop happens AFTER Textual's
        # own initial focus-grant pass.
        self.set_focus(None)


__all__ = ["MainScreenApp"]
