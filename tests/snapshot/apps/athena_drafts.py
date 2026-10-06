from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from threading import Event

from textual.app import App, ComposeResult
from textual.widgets import TextArea

from aws_tui.infra.athena_draft_store import DraftStoreResult
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal
from aws_tui.ui.widgets.athena.page import AthenaPage
from tests.athena_drafts_helpers import STAMP, runtime_at
from tests.helpers import wait_until
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


class AthenaDraftsApp(App[None]):
    def __init__(self, *, theme: str, state: str) -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._tmp = tempfile.TemporaryDirectory(prefix="athena-drafts-snapshot-")
        self.runtime, self.store = runtime_at(Path(self._tmp.name))
        # Only the display path is stable; persistence remains in the owned temporary directory.
        self.runtime._directory = Path("/fixture/config/athena-drafts")
        self.client = PageClient()

        async def current():
            return True

        self.vm = make_page_vm(self.client, drafts=self.runtime, source_is_current=current)
        self.state = state
        self.gate = Event()
        self.started = Event()
        save = self.store.save

        def stable_save(record, *, permit):
            self.started.set()
            if self.state == "pending" and not self.gate.wait(30):
                return DraftStoreResult(code="io")
            return save(replace(record, created_at=STAMP, updated_at=STAMP), permit=permit)

        self.store.save = stable_save

    def compose(self) -> ComposeResult:
        yield AthenaPage(self.vm, hub=self.vm._hub)

    async def on_mount(self) -> None:
        # Snapshot the recovery surfaces without a blinking background SQL cursor.
        self.query_one("#athena-editor", TextArea).cursor_blink = False
        await self.vm.setup()
        await self.vm.select_workgroup("primary")
        await self.vm.select_catalog("AwsDataCatalog")
        await self.vm.select_database("default")
        self.vm.query.set_sql("SELECT 42 AS draft_render_marker")
        if self.state == "pending":
            await wait_until(self.started.is_set, what="held draft save")
        else:
            await wait_until(
                lambda: self.vm.query.draft_state == "saved", what="saved draft acknowledgement"
            )
        if self.state in ("manager", "stale-context"):
            await self.runtime.refresh()
            if self.state == "stale-context":
                await self.vm.select_workgroup("analysts")

                async def accept():
                    return True

                assert not await self.vm.restore_draft(self.runtime.items[0].id, accept)
            self.push_screen(AthenaDraftsModal(self.vm, hub=self.vm._hub))

    async def on_unmount(self) -> None:
        self.gate.set()
        await self.vm.shutdown()
        self.vm.dispose()
        await self.runtime.shutdown()
        self.runtime.dispose()
        self._tmp.cleanup()
