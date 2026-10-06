"""Snapshot test apps for the JobRunLogsPane — one per LogsState.

Each app mounts a JobRunLogsPane bound to a JobRunLogsVM in a specific
state, seeding only what's necessary for that state to render correctly."""

from __future__ import annotations

import aioboto3
from textual.app import App, ComposeResult
from textual.containers import Container
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_logs import EmrServerlessLogsClient, LogFile, LogFileKind
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


def _build_logs_vm() -> JobRunLogsVM:
    """Build a JobRunLogsVM with a mock aioboto3 session."""
    session = aioboto3.Session()
    hub: MessageHub[Message] = MessageHub()
    logs_client = EmrServerlessLogsClient(
        session=session,
        region_name="us-east-1",
    )
    vm = JobRunLogsVM(
        client=logs_client,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


class JobRunLogsEmptyTargetApp(App[None]):
    """Fixture: EMPTY_TARGET state (no run selected yet)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._vm: JobRunLogsVM = _build_logs_vm()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        host = self.query_one("#content-host", Container)
        pane = JobRunLogsPane(self._vm, id="logs-pane")
        await host.mount(pane)


class JobRunLogsIdleApp(App[None]):
    """Fixture: IDLE state (target set, not loaded yet)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._vm: JobRunLogsVM = _build_logs_vm()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self._vm.set_target("a1", "r1", "s3://my-bucket/logs/")
        host = self.query_one("#content-host", Container)
        pane = JobRunLogsPane(self._vm, id="logs-pane")
        await host.mount(pane)


class JobRunLogsLoadingApp(App[None]):
    """Fixture: LOADING state (currently streaming logs)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._vm: JobRunLogsVM = _build_logs_vm()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self._vm.set_target("a1", "r1", "s3://my-bucket/logs/")
        # Manually transition to LOADING state and seed progress data
        self._vm._state = LogsState.LOADING
        self._vm._lines = ("INFO startup", "WARN noisy")
        self._vm._bytes_read = 12_345
        self._vm._lines_scanned = 500
        self._vm._current_file = LogFile(
            key="logs/applications/a1/jobs/r1/SPARK_DRIVER/stderr.gz",
            kind=LogFileKind.DRIVER_STDERR,
        )
        host = self.query_one("#content-host", Container)
        pane = JobRunLogsPane(self._vm, id="logs-pane")
        await host.mount(pane)


class JobRunLogsReadyApp(App[None]):
    """Fixture: READY state (logs fully loaded and rendered)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._vm: JobRunLogsVM = _build_logs_vm()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self._vm.set_target("a1", "r1", "s3://my-bucket/logs/")
        # Manually transition to READY state with sample log lines
        self._vm._state = LogsState.READY
        self._vm._lines = (
            "2026-06-26 12:00:00 ERROR something broke",
            "Caused by: java.lang.NullPointerException",
            "WARN noisy",
        )
        self._vm._bytes_read = 256_789
        self._vm._lines_scanned = 2_500
        self._vm._current_file = LogFile(
            key="logs/applications/a1/jobs/r1/SPARK_DRIVER/stderr.gz",
            kind=LogFileKind.DRIVER_STDERR,
        )
        host = self.query_one("#content-host", Container)
        pane = JobRunLogsPane(self._vm, id="logs-pane")
        await host.mount(pane)


class JobRunLogsNoLogConfigApp(App[None]):
    """Fixture: NO_LOG_CONFIG state (job has no log monitoring)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._vm: JobRunLogsVM = _build_logs_vm()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self._vm.set_target("a1", "r1", None)
        host = self.query_one("#content-host", Container)
        pane = JobRunLogsPane(self._vm, id="logs-pane")
        await host.mount(pane)


class JobRunLogsErrorApp(App[None]):
    """Fixture: ERROR state (error fetching logs)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        self._vm: JobRunLogsVM = _build_logs_vm()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self._vm.set_target("a1", "r1", "s3://my-bucket/logs/")
        # Manually transition to ERROR state with error text
        self._vm._state = LogsState.ERROR
        self._vm._error_text = "ResourceNotFoundException — bucket not found"
        host = self.query_one("#content-host", Container)
        pane = JobRunLogsPane(self._vm, id="logs-pane")
        await host.mount(pane)


__all__ = [
    "JobRunLogsEmptyTargetApp",
    "JobRunLogsErrorApp",
    "JobRunLogsIdleApp",
    "JobRunLogsLoadingApp",
    "JobRunLogsNoLogConfigApp",
    "JobRunLogsReadyApp",
]


class CloudWatchLogsApp(App[None]):
    """Deterministic CloudWatch pane states with literal body content."""

    def __init__(self, theme: str, variant: str = "ready") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm = _build_logs_vm()
        self._variant = variant

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogStream
        from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
        from aws_tui.vm.emr_serverless.job_run_logs_vm import LogSource, LogSourceState

        vm = self._vm
        vm.set_target(
            "app",
            "run",
            "s3://bucket/logs" if self._variant == "both" else None,
            cloudwatch=CloudWatchLogConfiguration(True, "/demo/emr"),
        )
        if self._variant == "both":
            vm.select_source(LogSource.CLOUDWATCH)
        streams = tuple(
            CloudWatchLogStream(
                f"/applications/app/jobs/run/attempts/{i}/SPARK_DRIVER", "SPARK_DRIVER", i
            )
            for i in (1, 2)
        )
        vm._available_streams = streams
        vm._current_stream = streams[1]
        vm._lines = ("ERROR [literal] CloudWatch demo failure",)
        vm._last_successful_read_at_ms = 100_000
        vm._last_event_at_ms = 90_000
        vm._state = LogsState.READY
        if self._variant == "missing":
            vm._available_streams = ()
            vm._current_stream = None
            vm._lines = ()
            vm._state = LogsState.NO_FILES
            vm._cloudwatch_status(LogSourceState.UNAVAILABLE, "not created yet")
        elif self._variant == "denied":
            vm._state = LogsState.ERROR
            vm._error_text = "CloudWatch log access denied"
            vm._cloudwatch_status(LogSourceState.ACCESS_DENIED)
        elif self._variant == "follow":
            vm._following = True
        host = self.query_one("#content-host", Container)
        await host.mount(JobRunLogsPane(self._vm, id="logs-pane"))


class CompactCloudWatchPageApp(App[None]):
    """Whole EMR page at 80x24, retaining one visible log body line."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        from dataclasses import replace

        from aws_tui.domain.emr_serverless import CloudWatchLogConfiguration
        from tests.snapshot.apps.emr import _build_page_vm, _seeded_fake

        self.CSS = _load_css(theme)
        fake = _seeded_fake()
        fake._details[("00abc", "r-001")] = replace(
            fake._details[("00abc", "r-001")],
            cloudwatch_monitoring=CloudWatchLogConfiguration(True, "/demo/emr"),
        )
        self._page_vm = _build_page_vm(fake)

    async def on_mount(self) -> None:
        from aws_tui.domain.emr_cloudwatch_logs import CloudWatchLogStream
        from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage

        await self._page_vm.setup()
        await self._page_vm.select_job_run("r-001")
        vm = self._page_vm.job_run_logs
        stream = CloudWatchLogStream(
            "/applications/00abc/jobs/r-001/attempts/2/SPARK_DRIVER", "SPARK_DRIVER", 2
        )
        vm._available_streams = (stream,)
        vm._current_stream = stream
        vm._lines = ("ERROR CloudWatch demo failure",)
        vm._last_successful_read_at_ms = 100_000
        vm._last_event_at_ms = 90_000
        vm._state = LogsState.READY
        await self.mount(EmrServerlessPage(self._page_vm, hub=self._page_vm.hub))
