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

import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Input, Static, TextArea
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import AuthRequiredError
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.helpers import focus_and_settle, wait_until

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
        job_driver={"sparkSubmit": {"entryPoint": "s3://b/jobs/etl.py"}},
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
        modal.action_review()
        modal._show_error = MagicMock(side_effect=captured.append)  # type: ignore[method-assign]
        await modal.action_submit()
        # Submission was awaited; drain dismissal events before asserting the error kept the modal open.
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
        modal.action_review()
        modal._show_error = MagicMock(side_effect=captured.append)  # type: ignore[method-assign]
        await modal.action_submit()
        # Submission was awaited; drain dismissal events before asserting the unexpected error kept the modal open.
        await pilot.pause()
        # Modal still active — defensive clause kept the app alive.
        assert isinstance(pilot.app.screen, JobRunCloneModal)
        assert len(captured) == 1
        assert "unexpected error" in captured[0]
        assert "bug in submit" not in captured[0]


async def test_enter_in_clone_input_opens_review_before_submission() -> None:
    vm = _make_vm()
    vm.submit = AsyncMock(return_value="r-new")  # type: ignore[method-assign]
    hub: MessageHub[Message] = MessageHub()

    async with _CloneModalHostApp(vm, hub).run_test() as pilot:
        modal = pilot.app.screen
        assert isinstance(modal, JobRunCloneModal)
        await focus_and_settle(modal.query_one("#clone-name", Input))
        await pilot.press("enter")
        assert isinstance(pilot.app.screen, JobRunCloneModal)
        vm.submit.assert_not_awaited()
        assert modal.reviewing
        await focus_and_settle(modal.query_one("#clone-submit"))
        await pilot.press("enter")
        await wait_until(
            lambda: not isinstance(pilot.app.screen, JobRunCloneModal),
            what="clone form submission dismissed the modal",
        )

        vm.submit.assert_awaited_once()
        assert not isinstance(pilot.app.screen, JobRunCloneModal)


async def test_second_submit_while_the_first_is_in_flight_launches_one_job() -> None:
    """The re-entrancy guard is billing-critical and was untested.

    ``vm.submit()`` awaits a multi-hundred-millisecond ``start_job_run``
    round-trip. A second activation while the first is in flight would start a
    SECOND EMR job for one user intent. The app-owned ``clientToken``
    (``JobRunCloneVM.client_token``) covers a *retry* of one intent, not two
    concurrent activations: both in-flight submits would carry the same
    token, and AWS would collapse them, but only after two round trips. The
    guard keeps it to one.
    """
    import asyncio

    vm = _make_vm()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _slow_submit() -> str:
        nonlocal calls
        calls += 1
        if calls > 1:
            # Return immediately so a missing guard fails on the assertion
            # below rather than deadlocking the test on ``release``.
            return "r-003"
        started.set()
        await release.wait()
        return "r-002"

    vm.submit = _slow_submit  # type: ignore[method-assign]
    hub: MessageHub[Message] = MessageHub()
    app = _CloneModalHostApp(vm, hub)

    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, JobRunCloneModal)

        modal.action_review()
        first = asyncio.ensure_future(modal.action_submit())
        await asyncio.wait_for(started.wait(), timeout=5)

        # Second activation arrives while the first round-trip is open.
        await modal.action_submit()
        assert calls == 1, "a second submit started another EMR job run"

        release.set()
        await asyncio.wait_for(first, timeout=5)

    assert calls == 1


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_review_shows_identity_changes_and_keeps_actions_reachable(
    size: tuple[int, int],
) -> None:
    hub = MessageHub()
    vm = JobRunCloneVM(
        _detail(),
        client=_InMemoryEmr(),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        source=ServiceSourceContext("analytics", "prod", "us-east-1"),
    )
    vm.submit = AsyncMock(return_value="r-new")
    try:
        async with _CloneModalHostApp(vm, hub).run_test(size=size) as pilot:
            modal = pilot.app.screen
            modal.query_one("#clone-name", Input).value = "changed-job"
            modal.query_one("#clone-settings", TextArea).load_text(
                '{"mode":"STREAMING","executionTimeoutMinutes":0}'
            )
            await pilot.pause()
            modal.action_review()
            await pilot.pause()
            assert modal.reviewing
            vm.submit.assert_not_awaited()
            review = str(modal.query_one("#clone-review", Static).content)
            for expected in [
                "r-001",
                "prod",
                "us-east-1",
                "a1",
                "EmrJobRole",
                "nightly",
                "changed-job",
                "STREAMING",
                "Inherited",
                "unknown",
            ]:
                assert expected in review
            for selector in ["#clone-back", "#clone-submit", "#clone-cancel"]:
                button = modal.query_one(selector)
                assert button.region.height > 0
                assert button.region.bottom <= size[1]
                assert button.region.right <= size[0]
            modal.action_back()
            await pilot.pause()
            assert not modal.reviewing
            assert modal.query_one("#clone-name", Input).value == "changed-job"
            await modal.action_submit()
            vm.submit.assert_not_awaited()
            modal.query_one("#clone-name", Input).value = "second-edit"
            modal.action_review()
            assert "second-edit" in str(modal.query_one("#clone-review", Static).content)
            modal.action_cancel()
            await pilot.pause()
            vm.submit.assert_not_awaited()
    finally:
        vm.dispose()


async def test_review_preserves_empty_newline_arguments_and_spark_whitespace() -> None:
    detail = replace(
        _detail(),
        entry_point_arguments=("", "line1\nline2", "  padded  "),
        spark_submit_parameters="  --conf key=value\n",
    )
    fake = _InMemoryEmr()
    hub = MessageHub()
    vm = JobRunCloneVM(detail, client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    try:
        async with _CloneModalHostApp(vm, hub).run_test() as pilot:
            modal = pilot.app.screen
            assert json.loads(modal.query_one("#clone-args", TextArea).text) == list(
                detail.entry_point_arguments
            )
            modal.action_review()
            await modal.action_submit()
            calls = [c[1] for c in fake.calls if c[0] == "start_job_run"]
            assert len(calls) == 1
            assert calls[0][3] == detail.entry_point_arguments
            assert calls[0][4] == detail.spark_submit_parameters
    finally:
        vm.dispose()
        fake.dispose()


@pytest.mark.parametrize(
    ("selector", "text"),
    [
        ("#clone-args", "[secret-argument"),
        ("#clone-spark", "secret-unquoted-parameter"),
        ("#clone-spark", '["secret-wrong-shape"]'),
        ("#clone-spark", '""'),
        ("#clone-args", '["valid", 123]'),
        ("#clone-settings", '{"mode":"secret-mode"}'),
        ("#clone-settings", '["secret-setting"]'),
    ],
)
async def test_invalid_form_never_reviews_or_leaks_values(selector: str, text: str) -> None:
    vm = _make_vm()
    vm.submit = AsyncMock(return_value="r-new")
    hub = MessageHub()
    try:
        async with _CloneModalHostApp(vm, hub).run_test() as pilot:
            modal = pilot.app.screen
            token = vm.client_token
            modal.query_one(selector, TextArea).load_text(text)
            modal.action_review()
            await pilot.pause()
            assert not modal.reviewing
            vm.submit.assert_not_awaited()
            assert vm.client_token == token
            error = str(modal.query_one("#clone-error", Static).content)
            assert error
            assert "secret-" not in error
    finally:
        vm.dispose()


async def test_external_intent_change_invalidates_review() -> None:
    vm = _make_vm()
    vm.submit = AsyncMock(return_value="r-new")
    hub = MessageHub()
    try:
        async with _CloneModalHostApp(vm, hub).run_test() as pilot:
            modal = pilot.app.screen
            modal.action_review()
            vm.apply_settings({"mode": "STREAMING"})
            await modal.action_submit()
            vm.submit.assert_not_awaited()
            assert not modal.reviewing
            assert "review" in str(modal.query_one("#clone-error", Static).content).lower()
    finally:
        vm.dispose()


async def test_modal_ambiguous_retry_keeps_exact_reviewed_intent() -> None:
    from aws_tui.domain.filesystem import ProviderUnreachableError

    fake = _InMemoryEmr()
    hub = MessageHub()
    vm = JobRunCloneVM(_detail(), client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    try:
        async with _CloneModalHostApp(vm, hub).run_test() as pilot:
            modal = pilot.app.screen
            modal.query_one("#clone-settings", TextArea).load_text('{"mode":"STREAMING"}')
            modal.action_review()
            token = vm.client_token
            fake.start_job_run_exc = ProviderUnreachableError("PRIVATE_ARG_IN_EXCEPTION_238")
            await modal.action_submit()
            assert modal.reviewing
            assert vm.client_token == token
            assert "PRIVATE_ARG_IN_EXCEPTION_238" not in str(
                modal.query_one("#clone-error", Static).content
            )
            fake.start_job_run_exc = None
            await modal.action_submit()
            calls = [call[1] for call in fake.calls if call[0] == "start_job_run"]
            assert len(calls) == 2
            assert calls[0][6] == calls[1][6] == token
            assert calls[0][7] == calls[1][7]
    finally:
        vm.dispose()
        fake.dispose()


async def test_cancel_during_submit_ignores_late_result() -> None:
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()
    vm = _make_vm()

    async def submit() -> str:
        started.set()
        await release.wait()
        return "r-already-submitted"

    vm.submit = submit
    try:
        async with _CloneModalHostApp(vm, MessageHub()).run_test() as pilot:
            modal = pilot.app.screen
            modal.action_review()
            task = asyncio.create_task(modal.action_submit())
            await asyncio.wait_for(started.wait(), timeout=5)
            assert str(modal.query_one("#clone-cancel", Static).content) == "Close"
            assert "does not cancel" in str(modal.query_one("#clone-progress", Static).content)
            modal.action_cancel()
            await pilot.pause()
            release.set()
            await asyncio.wait_for(task, timeout=5)
            assert vm.cancelled
            assert not isinstance(pilot.app.screen, JobRunCloneModal)
    finally:
        release.set()
        vm.dispose()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_keyboard_reaches_advanced_editor_review_and_scroll(size: tuple[int, int]) -> None:
    vm = _make_vm()
    vm.submit = AsyncMock(return_value="r-keyboard")
    try:
        async with _CloneModalHostApp(vm, MessageHub()).run_test(size=size) as pilot:
            modal = pilot.app.screen
            await focus_and_settle(modal.query_one("#clone-name", Input))
            reached = set()
            for _ in range(15):
                focused = pilot.app.focused
                if focused is not None:
                    reached.add(focused.id)
                if focused is modal.query_one("#clone-review-button"):
                    break
                await pilot.press("tab")
            assert {
                "clone-role",
                "clone-entry",
                "clone-args",
                "clone-spark",
                "clone-settings",
                "clone-review-button",
            } <= reached
            await pilot.press("enter")
            assert modal.reviewing
            scroll = modal.query_one("#clone-review-scroll")
            await wait_until(lambda: scroll.has_focus, what="review scroll focus")
            await pilot.press("end")
            await wait_until(lambda: scroll.scroll_y > 0, what="keyboard review scroll")
            for _ in range(5):
                if pilot.app.focused is modal.query_one("#clone-submit"):
                    break
                await pilot.press("tab")
            assert pilot.app.focused is modal.query_one("#clone-submit")
            await pilot.press("enter")
            await wait_until(
                lambda: not isinstance(pilot.app.screen, JobRunCloneModal),
                what="keyboard clone submit",
            )
            vm.submit.assert_awaited_once()
    finally:
        vm.dispose()


async def test_escape_remains_responsive_during_submission() -> None:
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()
    vm = _make_vm()

    async def submit() -> str:
        started.set()
        await release.wait()
        return "r-slow"

    vm.submit = submit
    try:
        async with _CloneModalHostApp(vm, MessageHub()).run_test() as pilot:
            modal = pilot.app.screen
            modal.action_review()
            await pilot.pause()
            try:
                modal.query_one("#clone-submit").press()
                await asyncio.wait_for(started.wait(), timeout=5)
                await asyncio.wait_for(pilot.press("escape"), timeout=5)
                assert not release.is_set()
                assert not isinstance(pilot.app.screen, JobRunCloneModal)
                assert vm.cancelled
            finally:
                release.set()
    finally:
        vm.dispose()


@pytest.mark.parametrize("kind", ["arguments", "spark", "settings"])
async def test_review_preserves_unicode_separators_and_mixed_line_endings(kind: str) -> None:
    special = "first\u2028second\u2029third\r\nfourth\nfifth"
    detail = replace(
        _detail(),
        entry_point_arguments=(special,) if kind == "arguments" else ("ordinary",),
        spark_submit_parameters=special if kind == "spark" else "--conf k=v",
        configuration_overrides={
            "applicationConfiguration": [
                {"classification": "spark-defaults", "properties": {"spark.example": special}}
            ]
        }
        if kind == "settings"
        else None,
    )
    fake = _InMemoryEmr()
    hub = MessageHub()
    vm = JobRunCloneVM(detail, client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    token = vm.client_token
    try:
        async with _CloneModalHostApp(vm, hub).run_test() as pilot:
            modal = pilot.app.screen
            modal.action_review()
            assert modal.reviewing
            assert vm.entry_point_arguments == detail.entry_point_arguments
            assert vm.spark_submit_parameters == detail.spark_submit_parameters
            assert vm.settings.get("configurationOverrides") == detail.configuration_overrides
            assert vm.client_token == token
            await modal.action_submit()
            request = next(c[1][7] for c in fake.calls if c[0] == "start_job_run")
            assert request["jobDriver"]["sparkSubmit"]["entryPointArguments"] == list(
                detail.entry_point_arguments
            )
            assert (
                request["jobDriver"]["sparkSubmit"]["sparkSubmitParameters"]
                == detail.spark_submit_parameters
            )
            assert request.get("configurationOverrides") == detail.configuration_overrides
    finally:
        vm.dispose()
        fake.dispose()


async def test_argument_null_review_omission_and_empty_edit_rotate_token() -> None:
    fake = _InMemoryEmr()
    hub = MessageHub()
    vm = JobRunCloneVM(
        replace(_detail(), entry_point_arguments=None),
        client=fake,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    try:
        async with _CloneModalHostApp(vm, hub).run_test() as pilot:
            modal = pilot.app.screen
            token = vm.client_token
            modal.action_review()
            assert modal.reviewing
            assert vm.client_token == token
            assert "Arguments: unknown / not supplied; omitted" in vm.review_text
            modal.action_back()
            modal.query_one("#clone-args", TextArea).load_text("[]")
            modal.action_review()
            assert vm.client_token != token
            assert vm.entry_point_arguments == ()
            assert "Arguments — Changed\nSource: null\nProposed: []" in vm.review_text
            empty_token = vm.client_token
            modal.action_back()
            modal.query_one("#clone-args", TextArea).load_text("null")
            modal.action_review()
            assert vm.client_token != empty_token
            assert vm.entry_point_arguments is None
            await modal.action_submit()
            request = next(c[1][7] for c in fake.calls if c[0] == "start_job_run")
            assert "entryPointArguments" not in request["jobDriver"]["sparkSubmit"]
    finally:
        vm.dispose()
        fake.dispose()
