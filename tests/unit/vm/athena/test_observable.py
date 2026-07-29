from __future__ import annotations

import asyncio

import pytest

from aws_tui.vm.athena._observable import ObserverSafeSubject


def test_observer_safe_subject_isolates_on_next_and_notifies_remaining_subscribers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    subject = ObserverSafeSubject[str]()
    received: list[str] = []

    def fail(_: str) -> None:
        raise RuntimeError("HOSTILE_ON_NEXT_MARKER")

    subject.subscribe(fail)
    subject.subscribe(received.append)

    subject.on_next("changed")

    assert received == ["changed"]
    assert "HOSTILE_ON_NEXT_MARKER" not in caplog.text


def test_observer_safe_subject_isolates_on_completed_and_preserves_terminal_semantics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    subject = ObserverSafeSubject[str]()
    completed: list[str] = []

    def fail() -> None:
        raise RuntimeError("HOSTILE_ON_COMPLETED_MARKER")

    subject.subscribe(on_completed=fail)
    subject.subscribe(on_completed=lambda: completed.append("existing"))

    subject.on_completed()
    subject.subscribe(on_completed=lambda: completed.append("late"))
    subject.on_next("ignored")

    assert completed == ["existing", "late"]
    assert "HOSTILE_ON_COMPLETED_MARKER" not in caplog.text


@pytest.mark.parametrize("position", ["first", "middle"])
def test_observer_safe_subject_isolates_cancelled_completion_observers(
    caplog: pytest.LogCaptureFixture,
    position: str,
) -> None:
    subject = ObserverSafeSubject[str]()
    completed: list[str] = []
    marker = "HOSTILE_CANCELLED_COMPLETION_MARKER"

    def cancel() -> None:
        raise asyncio.CancelledError(marker)

    def fail() -> None:
        raise RuntimeError("HOSTILE_ORDINARY_COMPLETION_MARKER")

    callbacks = (
        (cancel, fail, lambda: completed.append("remaining"))
        if position == "first"
        else (fail, cancel, lambda: completed.append("remaining"))
    )
    for callback in callbacks:
        subject.subscribe(on_completed=callback)

    subject.on_completed()
    subject.subscribe(on_completed=lambda: completed.append("late"))

    assert completed == ["remaining", "late"]
    assert marker not in caplog.text
    assert "HOSTILE_ORDINARY_COMPLETION_MARKER" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_observer_safe_subject_preserves_ambient_task_cancellation() -> None:
    subject = ObserverSafeSubject[str]()
    task = asyncio.current_task()
    assert task is not None

    def cancel() -> None:
        task.cancel()
        raise asyncio.CancelledError

    subject.subscribe(on_completed=cancel)
    subject.on_completed()

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.sleep(0)
    finally:
        while task.cancelling():
            task.uncancel()


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_observer_safe_subject_does_not_swallow_process_control_exceptions(
    exception_type: type[BaseException],
) -> None:
    subject = ObserverSafeSubject[str]()

    def interrupt() -> None:
        raise exception_type

    subject.subscribe(on_completed=interrupt)

    with pytest.raises(exception_type):
        subject.on_completed()


def test_observer_safe_subject_isolates_on_error_and_preserves_terminal_semantics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    subject = ObserverSafeSubject[str]()
    observed: list[tuple[str, str]] = []

    def fail(_: Exception) -> None:
        raise RuntimeError("HOSTILE_ON_ERROR_MARKER")

    subject.subscribe(on_error=fail)
    subject.subscribe(on_error=lambda error: observed.append(("existing", str(error))))

    subject.on_error(ValueError("safe failure"))
    subject.subscribe(on_error=lambda error: observed.append(("late", str(error))))
    subject.on_next("ignored")

    assert observed == [
        ("existing", "safe failure"),
        ("late", "safe failure"),
    ]
    assert "HOSTILE_ON_ERROR_MARKER" not in caplog.text
