"""JobRunsPane state-filter chip + key-binding tests.

Covers H5 of the Pass-1 test-review gaps. The pane maps digit keys
``1``-``5`` to ``JobRunState`` toggles via ``_KEY_TO_STATE`` and
flips the ``-active`` class on the matching chip. VM-level toggling
is already tested in ``test_job_runs_vm.py``; these tests pin the
WIDGET-level binding contract — pressing ``1`` actually reaches
``vm.toggle_state_filter(SUCCESS)`` and the chip class follows.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make_vm() -> tuple[JobRunsVM, MessageHub[Message], _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub, fake


class _PaneApp(App[None]):
    def __init__(self, vm: JobRunsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub

    def compose(self) -> ComposeResult:
        yield JobRunsPane(self._vm, id="pane")


# ── Key-binding contract: digit toggles state filter on / off ────────────────


async def test_pressing_1_toggles_success_off() -> None:
    """Default filter contains every state. Pressing ``1`` toggles
    SUCCESS OFF — the VM's ``state_filter`` no longer includes it."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        pane.focus()
        await pilot.pause()
        assert JobRunState.SUCCESS in vm.state_filter
        await pilot.press("1")
        await pilot.pause()
        assert JobRunState.SUCCESS not in vm.state_filter


async def test_pressing_1_twice_toggles_success_back_on() -> None:
    """Toggle is symmetric: a second press of the same digit
    re-adds the state to the filter set."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        pane.focus()
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert JobRunState.SUCCESS not in vm.state_filter
        await pilot.press("1")
        await pilot.pause()
        assert JobRunState.SUCCESS in vm.state_filter


async def test_pressing_2_toggles_running_off() -> None:
    """Smoke: a second digit binding still works (not just ``1``)."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        pane.focus()
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert JobRunState.RUNNING not in vm.state_filter


# ── Chip class follows VM state ──────────────────────────────────────────────


async def test_chip_active_class_follows_filter_state() -> None:
    """The ``-active`` chip class mirrors which states are in the
    filter set: in the filter → active; toggled out → not active."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        success_chip = pane.query_one("#runs-chip-success", Static)
        # Default — all states in filter → SUCCESS chip is active.
        assert "-active" in success_chip.classes
        # Toggle SUCCESS off via the VM (skip key routing).
        vm.toggle_state_filter(JobRunState.SUCCESS)
        await pilot.pause()
        assert "-active" not in success_chip.classes
        # Toggle back on.
        vm.toggle_state_filter(JobRunState.SUCCESS)
        await pilot.pause()
        assert "-active" in success_chip.classes


async def test_chip_row_renders_all_five_chips() -> None:
    """The chip row renders the five visible states from
    ``_KEY_TO_STATE`` — pin the contract so a refactor that
    accidentally drops a chip is caught."""
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        for state in (
            JobRunState.SUCCESS,
            JobRunState.RUNNING,
            JobRunState.PENDING,
            JobRunState.FAILED,
            JobRunState.CANCELLED,
        ):
            chips = pane.query(f"#runs-chip-{state.value.lower()}")
            assert len(chips) == 1, f"Expected exactly one chip for {state.value}; got {len(chips)}"


# ── Placeholder rendering: PROVIDER-error states beat empty-cache fallback ───
#
# Pass-2 H-1: prior branch order checked ``EMPTY or not runs`` before
# UNREACHABLE / AUTH_REQUIRED, so a pane in an error state with an
# empty cache (the typical post-error case) silently rendered
# ``(no runs)`` instead of the actionable placeholder. FORBIDDEN /
# ERROR were not handled at all. The fix mirrors
# ``JobRunDetailPane._refresh``'s ordering: error states FIRST, then
# LOADING, then EMPTY / empty-cache fallback.


def _placeholder_text(pane: JobRunsPane) -> str:
    body = pane.query_one("#runs-body", VerticalScroll)
    placeholders = body.query(".runs-placeholder")
    assert len(placeholders) == 1, f"Expected exactly one placeholder; got {len(placeholders)}"
    return str(placeholders[0].render())


async def test_unreachable_state_renders_endpoint_unreachable_placeholder() -> None:
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        vm._state = PaneState.UNREACHABLE  # type: ignore[attr-defined]
        vm._error_text = None  # type: ignore[attr-defined]
        pane._refresh_rows()  # type: ignore[attr-defined]
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "unreachable" in text
        assert "no runs" not in text


async def test_unreachable_uses_vm_error_text_when_set() -> None:
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        vm._state = PaneState.UNREACHABLE  # type: ignore[attr-defined]
        vm._error_text = "boom: dns lookup failed"  # type: ignore[attr-defined]
        pane._refresh_rows()  # type: ignore[attr-defined]
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "boom: dns lookup failed" in text


async def test_auth_required_state_renders_sso_login_placeholder() -> None:
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        vm._state = PaneState.AUTH_REQUIRED  # type: ignore[attr-defined]
        pane._refresh_rows()  # type: ignore[attr-defined]
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "authentication required" in text
        assert "no runs" not in text


async def test_forbidden_state_renders_permission_denied_placeholder() -> None:
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        vm._state = PaneState.FORBIDDEN  # type: ignore[attr-defined]
        vm._error_text = None  # type: ignore[attr-defined]
        pane._refresh_rows()  # type: ignore[attr-defined]
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "permission denied" in text
        assert "no runs" not in text


async def test_error_state_renders_generic_error_placeholder() -> None:
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        vm._state = PaneState.ERROR  # type: ignore[attr-defined]
        vm._error_text = None  # type: ignore[attr-defined]
        pane._refresh_rows()  # type: ignore[attr-defined]
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "error" in text
        assert "no runs" not in text


async def test_loading_state_renders_loading_placeholder() -> None:
    vm, hub, _fake = _make_vm()
    async with _PaneApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(JobRunsPane)
        vm._state = PaneState.LOADING  # type: ignore[attr-defined]
        pane._refresh_rows()  # type: ignore[attr-defined]
        await pilot.pause()
        text = _placeholder_text(pane)
        assert "loading" in text
