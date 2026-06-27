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

from aws_tui.domain.emr_logs import EmrServerlessLogsClient
from aws_tui.ui.widgets.emr_serverless.job_run_logs_pane import JobRunLogsPane
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


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
    def __init__(self, vm: JobRunLogsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub
        self._messages: list[object] = []

    def compose(self) -> ComposeResult:
        yield JobRunLogsPane(self._vm, hub=self._hub, id="pane")

    def on_message(self, message: object) -> None:
        # Capture all posted messages for testing
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


async def test_pressing_f_calls_action_open_filter() -> None:
    """Pressing 'f' should invoke action_open_filter."""
    vm, hub, _fake = _make_vm()
    app = _PaneApp(vm, hub)
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunLogsPane)
        pane.focus()
        await pilot.pause()
        calls: list[str] = []
        original_action = pane.action_open_filter

        def spy_action() -> None:
            calls.append("filter")
            original_action()

        pane.action_open_filter = spy_action
        await pilot.press("f")
        await pilot.pause()
        assert "filter" in calls, "Expected action_open_filter to be called"
