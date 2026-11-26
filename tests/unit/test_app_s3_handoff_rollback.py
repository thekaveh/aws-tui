"""A failed S3 handoff rollback is logged, and the handoff still reports its failure."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aws_tui.app import AwsTuiApp


class _RecordingLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def error(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


class _Navigation:
    def __init__(self) -> None:
        self.tracked: set[object] = set()

    def track_rollback(self, task: object) -> None:
        self.tracked.add(task)

    def untrack_rollback(self, task: object) -> None:
        self.tracked.discard(task)


@pytest.mark.asyncio
async def test_failed_s3_handoff_rollback_is_logged_and_reported_as_not_restored() -> None:
    """A Glue restore that raises used to escape the handler and crash the app.

    The restore runs in the content-mount worker, which exits the application on
    an error. The failure is now logged here, and the rollback is untracked.
    """
    log = _RecordingLog()
    navigation = _Navigation()

    async def failing_restore(**_: object) -> bool:
        raise ValueError("Glue recovery table is unavailable")

    stub = SimpleNamespace(
        service_navigation=navigation,
        _app_ctx=SimpleNamespace(log_sink=log),
        _s3_handoff_was_superseded=lambda _snapshot: False,
        _restore_service_after_s3_handoff_failure=failing_restore,
    )
    snapshot = SimpleNamespace(
        connection=None,
        auth_state=None,
        service_id="glue",
        athena=None,
        athena_result_execution_id=None,
        glue=None,
    )

    restored = await AwsTuiApp._restore_s3_handoff_snapshot(stub, snapshot)

    assert restored is False
    assert [event for event, _ in log.events] == ["app.s3_handoff.rollback_failed"]
    assert log.events[0][1]["error_type"] == "ValueError"
    assert navigation.tracked == set()
