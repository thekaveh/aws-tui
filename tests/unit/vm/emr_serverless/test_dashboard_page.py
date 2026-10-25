"""The page owns dashboard selection revisions and teardown."""

from __future__ import annotations

import pytest

from tests.unit.vm.emr_serverless.test_page_vm import _make


async def test_failed_dashboard_adapter_never_repeats_original_error(tmp_path):
    import traceback

    from aws_tui.domain.filesystem import AuthRequiredError
    from aws_tui.infra.crash_dump import CrashDump
    from aws_tui.services.emr_serverless.service import _FailedEmrClient

    private = "https://private-host.amazonaws.com/private-path?private-query#private-fragment"
    client = _FailedEmrClient(AuthRequiredError(private))
    with pytest.raises(AuthRequiredError) as caught:
        await client.get_dashboard_for_job_run("application001", "run001")
    rendered = "".join(traceback.format_exception(caught.value))
    dump = CrashDump(base_dir=tmp_path).write(exc=caught.value).read_text()
    for secret in (private, "private-host", "private-path", "private-query", "private-fragment"):
        assert secret not in str(caught.value)
        assert secret not in rendered
        assert secret not in dump


async def test_dashboard_review_invalidated_by_selection_away_and_back():
    page, fake = _make()
    fake.add_application(app_id="a1", name="Spark")
    for run_id in ("r1", "r2"):
        fake.add_job_run(application_id="a1", job_run_id=run_id)
    try:
        await page.setup()
        await page.select_job_run("r1")
        original = page.dashboard_target()
        assert original is not None
        assert page.dashboards.available(original)
        page.job_runs.select("r2")
        page.job_runs.select("r1")
        assert page.dashboard_target().revision > original.revision
        assert not page.dashboards.available(original)
        latest = page.dashboard_target()
        await page.shutdown()
        assert not page.dashboards.available(latest)
        assert page.dashboard_target() is None
    finally:
        page.dispose()
        fake.dispose()


@pytest.mark.parametrize("kind", ["SPARK", "HIVE", "unknown"])
async def test_dashboard_requires_matching_supported_run(kind):
    page, fake = _make()
    fake.add_application(app_id="a1", name="application", app_type=kind)
    try:
        await page.setup()
        assert page.dashboard_target() is None
        fake.add_job_run(application_id="a1", job_run_id="r1")
        await page.refresh_job_runs(application_id="a1")
        await page.select_job_run("r1")
        assert (page.dashboard_target() is not None) is (kind in {"SPARK", "HIVE"})
        page.job_runs.set_application("other")
        assert page.dashboard_target() is None
    finally:
        page.dispose()
        fake.dispose()
