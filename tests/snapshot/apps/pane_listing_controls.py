"""Deterministic Carbon listing with a closed filter editor."""

from textual.app import App, ComposeResult
from vmx import MessageHub, RxDispatcher

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.file_manager.pane_vm import PaneSortField, PaneVM


class FixedFS(InMemoryFS):
    async def list(self, path):
        return [
            FileEntry(name=name, kind=EntryKind.FILE, size=10, modified=None)
            for name in ("alpha.txt", "beta.txt", "gamma.txt")
        ]


class ListingApp(App):
    def __init__(self, query):
        super().__init__()
        self.CSS = ThemeStore().load("carbon")
        self.hub = MessageHub()
        self.vm = PaneVM(
            provider=FixedFS(),
            initial_path=PathRef(("loaded",)),
            hub=self.hub,
            dispatcher=RxDispatcher.immediate(),
            id_prefix="listing",
        )
        self.filter_query = query

    def compose(self) -> ComposeResult:
        yield Pane(self.vm, hub=self.hub)

    async def on_mount(self):
        self.vm.construct()
        await self.vm.setup()
        self.vm.set_filter_command.execute(self.filter_query)
        self.vm.set_sort(PaneSortField.NAME)
        self.query_one(Pane).set_focused(True)
