from __future__ import annotations

from typing import Literal

from textual.app import App, ComposeResult
from textual.containers import Container

from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.domain.query import (
    AthenaQueryError,
    QueryExecutionRef,
    QueryState,
    ResultColumn,
    ResultPage,
)
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm

AthenaFixture = Literal[
    "empty-query",
    "running",
    "success-results",
    "failure-detail",
    "history",
    "saved",
    "forbidden",
    "missing-result-config",
    "focused-rebound-tabs",
]


class _SnapshotAthena(PageClient):
    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> ResultPage:
        assert start_token is None
        return ResultPage(
            (
                ResultColumn("event[id]", "bigint", "NOT_NULL"),
                ResultColumn("event[id]", "varchar", "NULLABLE"),
                ResultColumn("active", "boolean", "NULLABLE"),
                ResultColumn("payload", "array(varchar)", "NULLABLE"),
                ResultColumn("note", "varchar", "NULLABLE"),
            ),
            (
                ("184467", "source-a", "true", '["alpha","beta"]', ""),
                ("184468", "source-b", "false", "[]", None),
                ("184469", "source-c", "true", "[literal][/bold]", "AWS result"),
            ),
            None,
        )


def _client(fixture: AthenaFixture) -> _SnapshotAthena:
    client = _SnapshotAthena(
        connection_name="analytics-prod",
        region="us-west-2",
    )
    if fixture == "forbidden":
        client.workgroup_error = PermissionDeniedError("permission denied by IAM or Lake Formation")
    if fixture == "saved":
        client.workgroups.reverse()
    return client


class AthenaPageApp(App[None]):
    def __init__(
        self,
        *,
        theme: str,
        fixture: AthenaFixture,
        open_picker: bool = False,
    ) -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._fixture = fixture
        self._client = _client(fixture)
        self._vm: AthenaPageVM = make_page_vm(self._client)
        self._keymap = (
            KeymapStore(
                overlay={
                    "athena.query": "7",
                    "athena.history": "8",
                    "athena.results": "9",
                    "athena.saved": "0",
                }
            )
            if fixture == "focused-rebound-tabs"
            else KeymapStore()
        )
        self._open_picker = open_picker

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        await self._vm.setup()
        await self._seed_fixture()
        await self.query_one("#content-host", Container).mount(
            AthenaPage(
                self._vm,
                hub=self._vm._hub,  # type: ignore[attr-defined]
                keymap=self._keymap,
                source_candidates=(
                    self._vm.source,
                    ServiceSourceContext("analytics-dev", "dev-sso", "us-east-1"),
                ),
                id="athena-page",
            )
        )
        if self._open_picker:
            self.query_one("#athena-database").open()
        if self._fixture == "focused-rebound-tabs":
            self.query_one("#athena-tab-query").focus()

    async def _seed_fixture(self) -> None:
        fixture = self._fixture
        query = self._vm.query
        if fixture == "running":
            query._sql = "SELECT event_id, payload FROM analytics.events LIMIT 100"  # type: ignore[attr-defined]
            query._execution_ref = QueryExecutionRef(  # type: ignore[attr-defined]
                "q-20260726-running",
                "analytics-prod",
                "us-west-2",
                "primary",
            )
            query._state = QueryState.RUNNING  # type: ignore[attr-defined]
            query._busy = True  # type: ignore[attr-defined]
            query._owns_active_query = True  # type: ignore[attr-defined]
            query._pane_state = PaneState.LOADING  # type: ignore[attr-defined]
            return
        if fixture == "success-results":
            query._sql = "SELECT * FROM analytics.events"  # type: ignore[attr-defined]
            await self._vm.results.load("q-20260726-success")
            await self._vm.select_view("results")
            return
        if fixture == "failure-detail":
            query._sql = "SELECT * FROM restricted.events"  # type: ignore[attr-defined]
            query._execution_ref = QueryExecutionRef(  # type: ignore[attr-defined]
                "q-20260726-failed",
                "analytics-prod",
                "us-west-2",
                "primary",
            )
            query._state = QueryState.FAILED  # type: ignore[attr-defined]
            query._state_reason = "TABLE_NOT_FOUND: restricted.events"  # type: ignore[attr-defined]
            query._query_error = AthenaQueryError(  # type: ignore[attr-defined]
                category=2,
                error_type=1004,
                retryable=False,
                message="Table restricted.events does not exist",
            )
            query._error_text = "Athena query failed"  # type: ignore[attr-defined]
            query._pane_state = PaneState.ERROR  # type: ignore[attr-defined]
            return
        if fixture == "history":
            await self._vm.select_view("history")
            await self._vm.select_history_execution("history-primary")
            return
        if fixture == "saved":
            await self._vm.select_view("saved")
            await self._vm.select_named_query("named-1")
            return
        if fixture == "missing-result-config":
            query._sql = "SELECT current_date"  # type: ignore[attr-defined]
            query._error_text = "Athena result configuration is required"  # type: ignore[attr-defined]
            query._state_reason = (  # type: ignore[attr-defined]
                "Configure an S3 output location on workgroup primary"
            )
            query._pane_state = PaneState.ERROR  # type: ignore[attr-defined]


__all__ = ["AthenaFixture", "AthenaPageApp"]
