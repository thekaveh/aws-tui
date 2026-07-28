from __future__ import annotations

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
