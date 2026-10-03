"""Production-shaped setup chrome without reading the caller's AWS files."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.connection_resolver import Connection, ConnectionDiscovery
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.brand_banner import BrandBanner
from aws_tui.ui.widgets.first_run import FIRST_RUN_FORM_CSS, FirstRunView
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry
from tests.snapshot.apps.main_screen import MainScreenApp


class FirstRunApp(App[None]):
    def __init__(self, *, theme: str, case: str = "actions") -> None:
        super().__init__()
        self.CSS = MainScreenApp.LAYOUT_CSS + ThemeStore().load(theme) + FIRST_RUN_FORM_CSS
        self.theme_name = theme
        self.open_form = case == "form"
        self.case = case
        self.hub = MessageHub()
        dispatcher = RxDispatcher.immediate()
        self.menu = NavMenuVM(registry=ServiceRegistry(), hub=self.hub, dispatcher=dispatcher)
        self.hints = HintLegendVM(hub=self.hub, dispatcher=dispatcher, keymap=KeymapStore())

    def compose(self) -> ComposeResult:
        yield BrandBanner(theme_name=self.theme_name, hub=self.hub, demo=False, id="brand-banner")
        with Horizontal(id="main-area"):
            yield NavMenu(vm=self.menu, hub=self.hub, id="nav-menu")
            with Container(id="content-host"):
                yield FirstRunView(Path("/fixture/config.toml"), self.hub)
        yield HintLegend(self.hints, hub=self.hub, id="hint-legend")

    async def on_mount(self) -> None:
        self.menu.construct()
        self.hints.construct()
        snapshot = ConnectionDiscovery(
            tuple(
                Connection(name=name, kind="aws", region="us-east-1", source=source)
                for name, source in (
                    ("local", "config"),
                    ("work", "auto-aws-profile"),
                    ("sample", "demo"),
                )
            )
        )
        await self.query_one(NavMenu).show_first_run_connections(snapshot)
        view = self.query_one(FirstRunView)
        if self.case == "actions":
            snapshot = ConnectionDiscovery(())
            await self.query_one(NavMenu).show_first_run_connections(snapshot)
        view.show_discovery(snapshot)
        if self.open_form:
            self.query_one(ConnectionFormInline).open_for_add()
        else:
            self.call_after_refresh(view.focus_default)
