from __future__ import annotations

import asyncio

import pytest

from aws_tui.vm._observable import ObserverSafeSubject


def _notify(subject: ObserverSafeSubject[str], notification: str) -> None:
    if notification == "next":
        subject.on_next("changed")
    elif notification == "error":
        subject.on_error(ValueError("safe failure"))
    else:
        subject.on_completed()


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
@pytest.mark.parametrize("notification", ["next", "error", "completed"])
def test_observer_safe_subject_isolates_cancelled_callback_observers(
    caplog: pytest.LogCaptureFixture,
    position: str,
    notification: str,
) -> None:
    subject = ObserverSafeSubject[str]()
    completed: list[str] = []
    marker = "HOSTILE_CANCELLED_COMPLETION_MARKER"

    def cancel(*_: object) -> None:
        raise asyncio.CancelledError(marker)

    def fail(*_: object) -> None:
        raise RuntimeError("HOSTILE_ORDINARY_COMPLETION_MARKER")

    def record(*_: object) -> None:
        completed.append("remaining")

    callbacks = (cancel, fail, record) if position == "first" else (fail, cancel, record)
    for callback in callbacks:
        if notification == "next":
            subject.subscribe(on_next=callback)
        elif notification == "error":
            subject.subscribe(on_error=callback)
        else:
            subject.subscribe(on_completed=callback)

    _notify(subject, notification)
    if notification == "error":
        subject.subscribe(on_error=lambda _: completed.append("late"))
    else:
        subject.subscribe(on_completed=lambda: completed.append("late"))
        if notification == "next":
            subject.on_completed()

    assert completed == ["remaining", "late"]
    assert marker not in caplog.text
    assert "HOSTILE_ORDINARY_COMPLETION_MARKER" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("notification", ["next", "error", "completed"])
async def test_observer_safe_subject_preserves_ambient_task_cancellation(
    notification: str,
) -> None:
    subject = ObserverSafeSubject[str]()
    task = asyncio.current_task()
    assert task is not None

    def cancel(*_: object) -> None:
        task.cancel()
        raise asyncio.CancelledError

    if notification == "next":
        subject.subscribe(on_next=cancel)
    elif notification == "error":
        subject.subscribe(on_error=cancel)
    else:
        subject.subscribe(on_completed=cancel)
    _notify(subject, notification)

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.sleep(0)
    finally:
        while task.cancelling():
            task.uncancel()


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("notification", ["next", "error", "completed"])
def test_observer_safe_subject_does_not_swallow_process_control_exceptions(
    exception_type: type[BaseException],
    notification: str,
) -> None:
    subject = ObserverSafeSubject[str]()

    def interrupt(*_: object) -> None:
        raise exception_type

    if notification == "next":
        subject.subscribe(on_next=interrupt)
    elif notification == "error":
        subject.subscribe(on_error=interrupt)
    else:
        subject.subscribe(on_completed=interrupt)

    with pytest.raises(exception_type):
        _notify(subject, notification)


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
