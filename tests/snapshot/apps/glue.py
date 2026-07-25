from __future__ import annotations

from typing import Literal

from textual.app import App, ComposeResult
from textual.containers import Container
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.glue.page_vm import GluePageVM, GlueView
from tests.unit.vm.glue._fake_glue import InMemoryGlue

GlueFixture = Literal["populated", "empty", "forbidden"]


class _ForbiddenGlue(InMemoryGlue):
    async def list_databases_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list, str | None]:
        raise PermissionDeniedError("permission denied by Lake Formation")


def _client(fixture: GlueFixture) -> InMemoryGlue:
    if fixture == "forbidden":
        return _ForbiddenGlue(connection_name="analytics-prod", region="us-west-2")
    fake = InMemoryGlue(connection_name="analytics-prod", region="us-west-2")
    if fixture == "empty":
        return fake
    table = fake.add_table("analytics", "events")
    fake.add_table("analytics", "sessions")
    fake.add_partition(table.ref, "dt=2026-07-25")
    fake.add_run("nightly", "jr-20260725", "RUNNING")
    fake.add_run("nightly", "jr-20260724", "SUCCEEDED")
    fake.add_crawler("ready-crawler", "READY")
    fake.add_crawler("running-crawler", "RUNNING")
    return fake


class GluePageApp(App[None]):
    def __init__(
        self,
        *,
        theme: str,
        view: GlueView = "catalog",
        fixture: GlueFixture = "populated",
    ) -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        hub: MessageHub[Message] = MessageHub()
        self._vm = GluePageVM(
            client=_client(fixture),
            connection=Connection(
                name="analytics-prod",
                kind="aws",
                region="us-west-2",
                source="test",
                profile="analytics-prod",
            ),
            hub=hub,
            dispatcher=NULL_DISPATCHER,
        )
        self._vm.construct()
        self._view = view

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        await self._vm.setup()
        if self._view != "catalog":
            await self._vm.select_view(self._view)
        await self.query_one("#content-host", Container).mount(
            GluePage(self._vm, hub=self._vm.hub, id="glue-page")
        )


__all__ = ["GluePageApp"]
