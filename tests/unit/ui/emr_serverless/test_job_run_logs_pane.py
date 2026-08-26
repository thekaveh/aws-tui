"""JobRunLogsPane state rendering + key binding tests.

Tests the widget-level rendering contract: different LogsState values
produce different placeholders; pressing enter posts LoadRequested;
file selector chips follow the current file.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_logs import EmrServerlessLogsClient, LogFile, LogFileKind
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM


def _make_vm() -> tuple[JobRunLogsVM, MessageHub[Message], _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    logs_client = EmrServerlessLogsClient(
        session=fake._session,
        region_name=None,
    )
    vm = JobRunLogsVM(client=logs_client, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub, fake


class _PaneApp(App[None]):
    def __init__(
        self,
        vm: JobRunLogsVM,
        hub: MessageHub[Message],
        *,
        keymap: KeymapStore | None = None,
    ) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub
        self._keymap = keymap
        self._messages: list[object] = []

    def compose(self) -> ComposeResult:
        yield JobRunLogsPane(self._vm, keymap=self._keymap, id="pane")

    def on_message(self, message: object) -> None:
        # Capture all posted messages for testing
        self._messages.append(message)

    def on_job_run_logs_pane_log_file_selected(
        self,
        message: JobRunLogsPane.LogFileSelected,
    ) -> None:
        self._messages.append(message)


def _placeholder_text(pane: JobRunLogsPane) -> str:
    """Extract the text from the body placeholder."""
    body = pane.query_one("#logs-body", VerticalScroll)
    placeholders = body.query(".logs-placeholder")
    assert len(placeholders) == 1, f"Expected exactly one placeholder; got {len(placeholders)}"
    return str(placeholders[0].render()).strip()


# ── State rendering tests ────────────────────────────────────────────────────


async def test_fresh_vm_renders_no_run_selected() -> None:
    """A widget mounted with a fresh VM (EMPTY_TARGET state) renders
    the '(no run selected)' placeholder."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        text = _placeholder_text(pane)
        assert "(no run selected)" in text


async def test_after_set_target_renders_press_enter() -> None:
    """After set_target is called, the widget transitions to IDLE state
    and renders '(press Enter to load logs)'."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        vm.set_target("app-123", "run-456", "s3://bucket/logs/")
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "(press Enter to load logs)" in text


async def test_no_log_config_renders_correct_placeholder() -> None:
    """When the run has no log monitoring configured, the widget renders
    the appropriate placeholder message."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        # Trigger NO_LOG_CONFIG state by setting target with no log_uri
        vm.set_target("app-123", "run-456", None)
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "(no log monitoring configured" in text


# ── Key binding tests ────────────────────────────────────────────────────────


async def test_pressing_enter_calls_action_load() -> None:
    """When the pane is in focus and has an IDLE target, pressing Enter
    should invoke action_load."""
    vm, hub, _fake = _make_vm()
    app = _PaneApp(vm, hub)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        pane.focus()
        await pilot.pause()
        vm.set_target("app-123", "run-456", "s3://bucket/logs/")
        await pilot.pause()
        # Track that action_load was invoked
        calls: list[str] = []
        original_action = pane.action_load

        def spy_action() -> None:
            calls.append("load")
            original_action()

        pane.action_load = spy_action
        await pilot.press("enter")
        await pilot.pause()
        assert "load" in calls, "Expected action_load to be called"


async def test_pressing_r_calls_action_reload() -> None:
    """Pressing 'r' should invoke action_reload."""
    vm, hub, _fake = _make_vm()
    app = _PaneApp(vm, hub)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        pane.focus()
        await pilot.pause()
        calls: list[str] = []
        original_action = pane.action_reload

        def spy_action() -> None:
            calls.append("reload")
            original_action()

        pane.action_reload = spy_action
        await pilot.press("r")
        await pilot.pause()
        assert "reload" in calls, "Expected action_reload to be called"


async def test_filter_row_uses_the_configured_filter_key() -> None:
    vm, hub, _fake = _make_vm()
    app = _PaneApp(vm, hub, keymap=KeymapStore(overlay={"emr.logs.filter": "ctrl+f"}))
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        assert "ctrl+f edit" in str(pane.query_one("#logs-filter").render())


async def test_left_and_right_select_exact_duplicate_kind_log_files() -> None:
    vm, hub, _fake = _make_vm()
    first = LogFile("logs/SPARK_EXECUTOR/1/stderr.gz", LogFileKind.EXECUTOR_STDERR)
    second = LogFile("logs/SPARK_EXECUTOR/2/stderr.gz", LogFileKind.EXECUTOR_STDERR)
    vm._available_files = (first, second)
    vm._current_file = first
    app = _PaneApp(vm, hub)

    async with app.run_test() as pilot:
        pane = pilot.app.query_one(JobRunLogsPane)
        pane.focus()
        await pilot.press("right")
        await pilot.pause()

        selected = [
            message
            for message in app._messages
            if isinstance(message, JobRunLogsPane.LogFileSelected)
        ]
        assert selected[-1].key == second.key

        vm.select_log_file_key(second.key)
        await pilot.press("left")
        await pilot.pause()
        selected = [
            message
            for message in app._messages
            if isinstance(message, JobRunLogsPane.LogFileSelected)
        ]
        assert selected[-1].key == first.key
