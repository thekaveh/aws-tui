"""Tests for ``EmrServerlessPage`` poller cadence + decay.

Covers H1 of the Pass-1 test-review gaps: the 6:1 cadence-decay
counter (``_poll_runs_decay``), the runs-tick skip path when no
active runs are present, and the terminal-state suppression on
``_tick_detail``.

These are pure-function tests against the widget's instance methods
— we construct the widget object directly (no ``run_test`` event
loop) and drive the counter without ever mounting it. The runs +
detail ticks are exercised by stubbing the page's child VMs so we
can observe whether ``run_worker`` would have been dispatched.
"""

from __future__ import annotations

import contextlib
from typing import Any

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _build_page() -> tuple[EmrServerlessPage, EmrServerlessPageVM, _InMemoryEmr]:
    """Construct a page widget bound to a real page VM + in-memory client.

    The widget is NOT mounted in an App — we only call its instance
    methods directly. Side-effect-free ones (``_poll_runs_decay``) work
    out of the box; tick methods that would call ``run_worker`` get a
    stubbed dispatcher (see ``_capture_workers``).
    """
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    conn = Connection(
        name="test",
        kind="aws",
        region="us-east-1",
        source="test",
        profile=None,
    )
    vm = EmrServerlessPageVM(
        client=fake,
        logs_client=fake.make_logs_client(),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        connection=conn,
    )
    vm.construct()
    page = EmrServerlessPage(vm, hub=hub)
    return page, vm, fake


def _capture_workers(page: EmrServerlessPage) -> list[str]:
    """Replace ``run_worker`` with a capture list so tick methods can
    be observed without an event loop."""
    captured: list[str] = []

    def _fake_run_worker(coro: Any, *args: Any, **kwargs: Any) -> None:
        captured.append(kwargs.get("group", "<no-group>"))
        # The coroutine never executes; close it so pytest doesn't
        # emit a RuntimeWarning about an un-awaited coroutine.
        with contextlib.suppress(Exception):
            coro.close()

    page.run_worker = _fake_run_worker  # type: ignore[method-assign]
    return captured


# ── _poll_runs_decay (pure counter % 6) ──────────────────────────────────────


def test_poll_runs_decay_first_five_calls_return_true() -> None:
    """Counter starts at 0 → first call increments to 1 (% 6 != 0 → skip)."""
    page, _vm, _fake = _build_page()
    assert [page._poll_runs_decay() for _ in range(5)] == [True, True, True, True, True]


def test_poll_runs_decay_sixth_call_returns_false_then_repeats() -> None:
    """Every 6th call lands on counter==0 — must NOT skip (decay False)."""
    page, _vm, _fake = _build_page()
    results = [page._poll_runs_decay() for _ in range(12)]
    # Pattern: 5 True (skips) then 1 False (refresh) -- twice.
    assert results == [True, True, True, True, True, False] * 2


def test_poll_runs_decay_counter_is_independent_per_widget() -> None:
    """Two widgets must not share counter state — the counter lives on
    the instance, not the class."""
    page_a, _vm_a, _fake_a = _build_page()
    page_b, _vm_b, _fake_b = _build_page()
    # Advance A through three ticks; B stays at 0.
    for _ in range(3):
        page_a._poll_runs_decay()
    # A's next call is the 4th → still skipping.
    assert page_a._poll_runs_decay() is True
    # B's first call is the 1st → also skipping but counter is its own.
    assert page_b._poll_runs_decay() is True


# ── _tick_runs (skip when no active runs AND decay says skip) ────────────────


def test_tick_runs_dispatches_when_active_runs_present() -> None:
    """When the VM reports ``has_active_runs() == True``, every tick
    must dispatch — no decay."""
    page, vm, _fake = _build_page()
    captured = _capture_workers(page)
    # Force has_active_runs to True without seeding real data.
    vm.job_runs.has_active_runs = lambda: True  # type: ignore[method-assign]
    for _ in range(7):
        page._tick_runs()
    # All 7 ticks dispatched a worker.
    assert captured == ["emr-poll-runs"] * 7


def test_tick_runs_skips_5_of_6_when_no_active_runs() -> None:
    """No active runs → decay kicks in. Out of 6 consecutive ticks,
    exactly one (the 6th) should dispatch."""
    page, vm, _fake = _build_page()
    captured = _capture_workers(page)
    vm.job_runs.has_active_runs = lambda: False  # type: ignore[method-assign]
    for _ in range(6):
        page._tick_runs()
    assert captured == ["emr-poll-runs"], (
        f"Expected exactly one dispatch in 6 ticks with no active runs; got {captured!r}"
    )


def test_tick_runs_skips_with_empty_in_memory_client() -> None:
    """Integration smoke: drive several ticks while the VM holds no
    runs at all. ``has_active_runs()`` returns False → 5-of-6 skip
    pattern stands."""
    page, _vm, _fake = _build_page()
    captured = _capture_workers(page)
    # _vm.job_runs has an empty _runs_cache → has_active_runs is False.
    for _ in range(12):
        page._tick_runs()
    # 12 ticks → 2 dispatches (every 6th).
    assert captured == ["emr-poll-runs", "emr-poll-runs"]


# ── _tick_detail (suppress when terminal) ─────────────────────────────────────


def test_tick_detail_suppressed_when_in_terminal_state() -> None:
    """When the detail VM reports terminal, every tick must be a no-op."""
    page, vm, _fake = _build_page()
    captured = _capture_workers(page)
    vm.job_run_detail.is_terminal_state = lambda: True  # type: ignore[method-assign]
    for _ in range(5):
        page._tick_detail()
    assert captured == []


def test_tick_detail_dispatches_when_not_terminal() -> None:
    """When the run is still pre-terminal, every tick must dispatch
    a refresh worker."""
    page, vm, _fake = _build_page()
    captured = _capture_workers(page)
    vm.job_run_detail.is_terminal_state = lambda: False  # type: ignore[method-assign]
    for _ in range(3):
        page._tick_detail()
    assert captured == ["emr-poll-detail", "emr-poll-detail", "emr-poll-detail"]


def test_tick_applications_always_dispatches() -> None:
    """Applications poller has no decay nor terminal gate — every
    tick must dispatch."""
    page, _vm, _fake = _build_page()
    captured = _capture_workers(page)
    for _ in range(4):
        page._tick_applications()
    assert captured == ["emr-poll-apps"] * 4
