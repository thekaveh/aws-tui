"""JobRunCloneModal action_submit error-handling tests.

Pre-fix the modal only caught :class:`ProviderError`; any other
exception raised from ``vm.submit()`` (a botocore parameter-validation
error escaping the facade, a programmer error in clone_vm, a future
regression that adds a new exception type) propagated to Textual's
default error handler and crashed the EMR page. These tests pin
the defensive ``Exception`` clause: the modal stays open, the inline
error label shows the message, and the page does not crash.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from textual.app import App, ComposeResult
from textual.containers import Container
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import AuthRequiredError
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr

_FIXED_TS = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)


def _detail() -> JobRunDetail:
    return JobRunDetail(
        application_id="a1",
        job_run_id="r-001",
        name="nightly",
        state=JobRunState.SUCCESS,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
        entry_point="s3://b/jobs/etl.py",
        entry_point_arguments=("--input", "s3://b/raw/"),
        spark_submit_parameters="--conf k=v",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
        s3_monitoring_log_uri=None,
    )


class _CloneModalHostApp(App[None]):
    """Vanilla host that pushes a single :class:`JobRunCloneModal`
    so the modal can be exercised via Textual's pilot harness."""

    def __init__(self, vm: JobRunCloneVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        await self.push_screen(JobRunCloneModal(self._vm, hub=self._hub))


def _make_vm() -> JobRunCloneVM:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunCloneVM(_detail(), client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


async def test_action_submit_provider_error_shows_inline_keeps_modal_open() -> None:
    """The pre-existing :class:`ProviderError` path: error message
    surfaces in the inline ``#clone-error`` label and the modal
    stays open."""
    vm = _make_vm()
    vm.submit = AsyncMock(side_effect=AuthRequiredError("aws sso login --profile <X>"))  # type: ignore[method-assign]
    hub: MessageHub[Message] = MessageHub()
    async with _CloneModalHostApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, JobRunCloneModal)
        # Spy on the inline-error helper to capture the message
        # without depending on Textual's Static renderable API.
        captured: list[str] = []
        modal._show_error = MagicMock(side_effect=captured.append)  # type: ignore[method-assign]
        await modal.action_submit()
        await pilot.pause()
        # Modal still active — not dismissed.
        assert isinstance(pilot.app.screen, JobRunCloneModal)
        # _show_error fired once with the AuthRequiredError text.
        assert len(captured) == 1
        assert "aws sso login" in captured[0]


async def test_action_submit_unexpected_exception_caught_keeps_modal_open() -> None:
    """New defensive clause: a non-:class:`ProviderError` raise
    (e.g. botocore parameter-validation, programmer error) is
    caught and surfaced in the inline error label instead of
    crashing through Textual's default error handler.
    """
    vm = _make_vm()
    vm.submit = AsyncMock(side_effect=RuntimeError("bug in submit"))  # type: ignore[method-assign]
    hub: MessageHub[Message] = MessageHub()
    async with _CloneModalHostApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, JobRunCloneModal)
        captured: list[str] = []
        modal._show_error = MagicMock(side_effect=captured.append)  # type: ignore[method-assign]
        await modal.action_submit()
        await pilot.pause()
        # Modal still active — defensive clause kept the app alive.
        assert isinstance(pilot.app.screen, JobRunCloneModal)
        assert len(captured) == 1
        assert "unexpected error" in captured[0]
        assert "bug in submit" in captured[0]
