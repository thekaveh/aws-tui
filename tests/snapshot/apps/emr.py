"""Snapshot test app for the EMR page — wraps EmrServerlessPage with
a pre-seeded :class:`_InMemoryEmr` so the rendered SVG never depends
on boto3 or a network."""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.containers import Container
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import ApplicationState, JobRunState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.service_source_header import ServiceSourceHeader
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.unit.domain._in_memory_emr import _InMemoryEmr

_FIXED_TS = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


def _seeded_fake() -> _InMemoryEmr:
    fake = _InMemoryEmr()
    fake.add_application(
        app_id="00abc",
        name="etl-pipeline-1",
        state=ApplicationState.STARTED,
        created_at=_FIXED_TS,
    )
    fake.add_job_run(
        application_id="00abc",
        job_run_id="r-001",
        name="nightly-2026-06-25",
        state=JobRunState.SUCCESS,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
    )
    fake.add_job_run_detail(
        application_id="00abc",
        job_run_id="r-001",
        entry_point="s3://my-bucket/jobs/etl.py",
        entry_point_arguments=(
            "--input",
            "s3://my-bucket/raw/",
            "--output",
            "s3://my-bucket/curated/",
        ),
        spark_submit_parameters="--conf spark.executor.instances=8",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
    )
    fake.add_job_run(
        application_id="00abc",
        job_run_id="r-002",
        state=JobRunState.RUNNING,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
    )
    fake.add_job_run_detail(application_id="00abc", job_run_id="r-002")
    return fake


def _build_page_vm(client: _InMemoryEmr) -> EmrServerlessPageVM:
    hub: MessageHub[Message] = MessageHub()
    page = EmrServerlessPageVM(
        client=client,
        logs_client=client.make_logs_client(),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        connection=Connection(
            name="demo-prod",
            kind="aws",
            region="us-east-1",
            source="config",
            profile="demo-prod",
        ),
    )
    page.construct()
    return page


class EmrPageApp(App[None]):
    """Renders the populated EMR page (1 app, 2 runs, detail visible)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._page_vm: EmrServerlessPageVM = _build_page_vm(_seeded_fake())
        self._source_candidates: tuple[ServiceSourceContext, ...] = ()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        await self._page_vm.setup()
        host = self.query_one("#content-host", Container)
        page = EmrServerlessPage(
            self._page_vm,
            hub=self._page_vm.hub,
            source_candidates=self._source_candidates,
            id="emr-page",
        )
        await host.mount(page)


class EmrPageEmptyApp(App[None]):
    """Renders the empty state (no applications seeded)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._page_vm: EmrServerlessPageVM = _build_page_vm(_InMemoryEmr())

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        await self._page_vm.setup()
        host = self.query_one("#content-host", Container)
        page = EmrServerlessPage(
            self._page_vm,
            hub=self._page_vm.hub,
            id="emr-page",
        )
        await host.mount(page)


class EmrPageOpenPickerApp(EmrPageApp):
    """Renders populated EMR with the application selector expanded."""

    def on_mount(self) -> None:
        # Textual also dispatches the inherited mount handler, which
        # asynchronously mounts the page. Open after that refresh instead
        # of calling ``super()`` and mounting a duplicate ``#emr-page``.
        self.call_after_refresh(self._open_picker)

    def _open_picker(self) -> None:
        self.query_one(ApplicationPicker).toggle_open()


class EmrPageOpenSourcePickerApp(EmrPageApp):
    """Renders similarly prefixed AWS profiles with the source list expanded."""

    def __init__(self, theme: str) -> None:
        super().__init__(theme)
        self._source_candidates = (
            ServiceSourceContext("demo-prod-east", "demo-prod-east", "us-east-1"),
            ServiceSourceContext("demo-prod-west", "demo-prod-west", "us-west-2"),
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._open_source_picker)

    def _open_source_picker(self) -> None:
        self.query_one(ServiceSourceHeader).open()


__all__ = [
    "EmrPageApp",
    "EmrPageEmptyApp",
    "EmrPageOpenPickerApp",
    "EmrPageOpenSourcePickerApp",
]
