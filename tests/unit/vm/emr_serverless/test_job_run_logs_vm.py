from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER
from aws_tui.vm.emr_serverless.job_run_logs_vm import JobRunLogsVM, LogsState


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make() -> JobRunLogsVM:
    hub = _hub()
    vm = JobRunLogsVM(
        session=cast("object", None),  # not used by set_target paths
        region_name="us-east-1",
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


def test_initial_state_is_empty_target() -> None:
    vm = _make()
    assert vm.state is LogsState.EMPTY_TARGET
    assert vm.lines == ()
    vm.dispose()


def test_set_target_with_log_uri_transitions_to_idle() -> None:
    vm = _make()
    vm.set_target("a1", "r1", "s3://b/logs/")
    assert vm.state is LogsState.IDLE
    assert vm.application_id == "a1"
    assert vm.job_run_id == "r1"
    vm.dispose()


def test_set_target_without_log_uri_transitions_to_no_config() -> None:
    vm = _make()
    vm.set_target("a1", "r1", None)
    assert vm.state is LogsState.NO_LOG_CONFIG
    vm.dispose()


def test_set_target_to_none_returns_to_empty_target() -> None:
    vm = _make()
    vm.set_target("a1", "r1", "s3://b/")
    assert vm.state is LogsState.IDLE
    vm.set_target(None, None, None)
    assert vm.state is LogsState.EMPTY_TARGET
    vm.dispose()


def test_set_filter_emits_property_change() -> None:
    vm = _make()
    changes: list[str] = []
    vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: changes.append(getattr(m, "property_name", ""))
    )
    new_filter = DEFAULT_LOG_FILTER.with_(patterns=("FATAL",))
    vm.set_filter(new_filter)
    assert "filter" in changes
    vm.dispose()
