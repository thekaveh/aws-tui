"""Provider state remains independent from the retained-run filter."""

from __future__ import annotations

import asyncio

import pytest

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.vm.emr_serverless.test_job_runs_vm import _make
from tests.unit.vm.emr_serverless.test_page_vm import _make as _make_page


@pytest.mark.parametrize("state", [JobRunState.SUCCESS, JobRunState.RUNNING])
@pytest.mark.parametrize("changed", [False, True])
async def test_successful_filtered_refresh_preserves_loaded_provider_state(state, changed):
    vm, fake = _make()
    try:
        fake.add_application(app_id="filtered", name="filtered")
        fake.add_job_run(application_id="filtered", job_run_id="retained", state=state)
        vm.set_application("filtered")
        await vm.refresh()
        vm.select("retained")
        vm.toggle_state_filter(state)
        if changed:
            fake.add_job_run(
                application_id="filtered", job_run_id="retained", state=state, name="new-name"
            )
        await vm.refresh()
        assert vm.runs == ()
        assert vm.selected_id == "retained"
        assert vm.state is PaneState.IDLE
        calls = tuple(fake.calls)
        vm.toggle_state_filter(state)
        assert tuple(fake.calls) == calls
        assert [run.job_run_id for run in vm.runs] == ["retained"]
        assert vm.state is PaneState.IDLE
    finally:
        vm.dispose()


async def test_filter_changes_do_not_turn_true_empty_results_into_loaded_state():
    vm, fake = _make()
    try:
        fake.add_application(app_id="empty", name="empty")
        vm.set_application("empty")
        await vm.refresh()
        calls = tuple(fake.calls)
        vm.toggle_state_filter(JobRunState.SUCCESS)
        vm.toggle_state_filter(JobRunState.SUCCESS)
        assert vm.state is PaneState.EMPTY
        assert vm.runs == ()
        assert tuple(fake.calls) == calls
    finally:
        vm.dispose()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthRequiredError, PaneState.AUTH_REQUIRED),
        (PermissionDeniedError, PaneState.FORBIDDEN),
        (ProviderUnreachableError, PaneState.UNREACHABLE),
        (ProviderError, PaneState.ERROR),
    ],
)
async def test_filter_changes_preserve_provider_failure_state(error, expected):
    vm, fake = _make()
    try:
        vm.set_application("failed")

        async def fail(*args, **kwargs):
            raise error("controlled provider failure")

        fake.list_job_runs_page = fail
        await vm.refresh()
        assert vm.state is expected
        message = vm.error_text
        vm.toggle_state_filter(JobRunState.SUCCESS)
        vm.toggle_state_filter(JobRunState.SUCCESS)
        assert vm.state is expected
        assert vm.error_text == message
    finally:
        vm.dispose()


async def test_filter_changes_preserve_in_flight_loading_state():
    vm, fake = _make()
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        original = fake.list_job_runs_page

        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        fake.list_job_runs_page = held
        vm.set_application("held")
        task = asyncio.create_task(vm.refresh())
        await entered.wait()
        vm.toggle_state_filter(JobRunState.SUCCESS)
        vm.toggle_state_filter(JobRunState.SUCCESS)
        assert vm.state is PaneState.LOADING
    finally:
        release.set()
        if task is not None:
            await task
        vm.dispose()


@pytest.mark.parametrize("other_visible", [False, True])
async def test_filtered_refresh_keeps_retained_selection_detail_and_log_target(other_visible):
    page, fake = _make_page()
    try:
        fake.add_application(app_id="filtered", name="filtered")
        fake.add_job_run(
            application_id="filtered", job_run_id="retained", state=JobRunState.SUCCESS
        )
        fake.add_job_run_detail(
            application_id="filtered",
            job_run_id="retained",
            s3_monitoring_log_uri="s3://logs/retained",
        )
        if other_visible:
            fake.add_job_run(
                application_id="filtered", job_run_id="other", state=JobRunState.RUNNING
            )
            fake.add_job_run_detail(application_id="filtered", job_run_id="other")
        await page.setup()
        await page.select_job_run("retained")
        detail = page.job_run_detail.detail
        page.job_runs.toggle_state_filter(JobRunState.SUCCESS)
        fake.calls.clear()
        await page.refresh_job_runs()
        assert page.job_runs.selected_id == "retained"
        assert page.job_run_detail.detail is detail
        assert page.job_run_logs.job_run_id == "retained"
        assert [name for name, _ in fake.calls] == ["list_job_runs_page"]
        calls = tuple(fake.calls)
        page.job_runs.toggle_state_filter(JobRunState.SUCCESS)
        assert tuple(fake.calls) == calls
        assert page.job_run_detail.detail is detail
    finally:
        page.dispose()
        fake.dispose()


@pytest.mark.parametrize("remaining", ["empty", "hidden", "visible"])
async def test_provider_removed_selection_still_reconciles_after_filtered_refresh(remaining):
    page, fake = _make_page()
    try:
        fake.add_application(app_id="filtered", name="filtered")
        fake.add_job_run(application_id="filtered", job_run_id="removed", state=JobRunState.SUCCESS)
        fake.add_job_run_detail(application_id="filtered", job_run_id="removed")
        if remaining != "empty":
            fake.add_job_run(
                application_id="filtered",
                job_run_id="other",
                state=JobRunState.SUCCESS if remaining == "hidden" else JobRunState.RUNNING,
            )
            fake.add_job_run_detail(application_id="filtered", job_run_id="other")
        await page.setup()
        await page.select_job_run("removed")
        page.job_runs.toggle_state_filter(JobRunState.SUCCESS)
        fake._runs["filtered"].pop("removed")
        await page.refresh_job_runs()
        if remaining == "visible":
            assert page.job_runs.selected_id == "other"
            assert page.job_run_detail.detail.job_run_id == "other"
            assert page.job_run_logs.job_run_id == "other"
        else:
            assert page.job_runs.selected_id is None
            assert page.job_run_detail.detail is None
            assert page.job_run_logs.job_run_id is None
        assert page.job_runs.state is (PaneState.EMPTY if remaining == "empty" else PaneState.IDLE)
    finally:
        page.dispose()
        fake.dispose()
