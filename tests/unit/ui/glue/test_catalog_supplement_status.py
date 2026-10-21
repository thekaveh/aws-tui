"""Actual catalog detail rows distinguish unavailable supplements from zero."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from textual.widgets import Static

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.ui.widgets.glue.page import GluePage
from tests.helpers import drain_workers, wait_until
from tests.unit.ui.glue.test_page import _build_vm, _GlueApp
from tests.unit.vm.glue._fake_glue import InMemoryGlue


class _SupplementGlue(InMemoryGlue):
    def __init__(self, *, failed):
        super().__init__()
        self.failed = failed
        self.error = PermissionDeniedError(f"{failed} permission denied")
        self.blocked = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.add_table("analytics", "events")

    async def _before(self, kind):
        if self.failed == kind:
            if self.blocked:
                self.started.set()
                await self.release.wait()
            if self.error is not None:
                raise self.error

    async def list_partitions_page(self, ref, *, start_token=None):
        await self._before("partitions")
        return await super().list_partitions_page(ref, start_token=start_token)

    async def get_column_statistics(self, ref, columns):
        await self._before("statistics")
        return await super().get_column_statistics(ref, columns)


def _detail_lines(app):
    return [str(row.render()) for row in app.query_one("#glue-table-detail-pane").query(Static)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["partitions", "statistics"])
async def test_denied_supplement_is_scoped_in_actual_mounted_detail(failed):
    vm, _ = _build_vm(_SupplementGlue(failed=failed))
    await vm.setup()
    try:
        async with _GlueApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            lines = _detail_lines(pilot.app)
            row = next(line for line in lines if line.startswith(failed.title()))
            assert "permission denied" in row
            assert row.split(maxsplit=1)[1] != "0"
            assert any("id: string" in line for line in lines)
            assert any("s3://warehouse/analytics/events/" in line for line in lines)
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["partitions", "statistics"])
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthRequiredError("expired"), "authentication required"),
        (ProviderUnreachableError("endpoint unreachable"), "endpoint unreachable"),
        (ProviderError("request failed"), "request failed"),
        (RuntimeError("unexpected fixture failure"), "unexpected error"),
    ],
)
async def test_each_resource_projects_its_own_failure_and_refresh_restores_count(
    failed, error, expected
):
    fake = _SupplementGlue(failed=failed)
    fake.error = error
    vm, _ = _build_vm(fake)
    await vm.setup()
    try:
        async with _GlueApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            lines = _detail_lines(pilot.app)
            row = next(line for line in lines if line.startswith(failed.title()))
            assert expected in row
            assert any("id: string" in line for line in lines)
            assert any("s3://warehouse/analytics/events/" in line for line in lines)
            sibling = "Statistics" if failed == "partitions" else "Partitions"
            sibling_count = "1" if failed == "partitions" else "0"
            assert (
                next(line for line in lines if line.startswith(sibling)).split(maxsplit=1)[1]
                == sibling_count
            )
            page = pilot.app.query_one(GluePage)
            fake.error = None
            await page.action_refresh_active()
            await drain_workers(pilot.app)
            await pilot.pause()
            expected_count = "0" if failed == "partitions" else "1"
            await wait_until(
                lambda: any(
                    line.split(maxsplit=1)[1] == expected_count
                    for line in _detail_lines(pilot.app)
                    if line.startswith(failed.title())
                ),
                what="successful retry count rendered",
            )
            row = next(line for line in _detail_lines(pilot.app) if line.startswith(failed.title()))
            assert row.split(maxsplit=1)[1] == expected_count
            assert pilot.app.query_one(GluePage) is page
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["partitions", "statistics"])
async def test_loading_supplement_does_not_claim_zero_and_keeps_schema(failed):
    fake = _SupplementGlue(failed=failed)
    fake.error = None
    vm, _ = _build_vm(fake)
    await vm.setup()
    task = None
    try:
        async with _GlueApp(vm).run_test(size=(120, 40)) as pilot:
            fake.blocked = True
            task = asyncio.create_task(vm.select_table("events"))
            await wait_until(fake.started.is_set, what="supplement load started")
            await wait_until(
                lambda: any(
                    "loading" in line
                    for line in _detail_lines(pilot.app)
                    if line.startswith(failed.title())
                ),
                what="scoped supplement loading indicator rendered",
            )
            assert any("id: string" in line for line in _detail_lines(pilot.app))
            fake.release.set()
            await task
    finally:
        fake.release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 2])
async def test_successful_supplements_keep_real_counts_and_partition_rows(count):
    fake = _SupplementGlue(failed="partitions")
    fake.error = None
    ref = fake.tables["analytics"][0].ref
    for n in range(count):
        fake.add_partition(ref, f"day-{n}")
    original = fake.statistics[ref][0]
    fake.statistics[ref] = (
        (original, replace(original, column_name="day", type_name="date")) if count else ()
    )
    vm, _ = _build_vm(fake)
    await vm.setup()
    try:
        async with _GlueApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            lines = _detail_lines(pilot.app)
            for label in ["Partitions", "Statistics"]:
                assert next(line for line in lines if line.startswith(label)).split(maxsplit=1)[
                    1
                ] == str(count)
            assert (
                sum(any(line.endswith(f"day-{n}") for n in range(count)) for line in lines) == count
            )
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
async def test_partition_page_failure_overrides_count_without_discarding_visible_schema_or_rows():
    fake = _SupplementGlue(failed="partitions")
    fake.error = None
    ref = fake.tables["analytics"][0].ref
    fake.add_partition(ref, "day-0")
    fake.add_partition(ref, "day-1")
    fake.partition_page_size = 1
    vm, _ = _build_vm(fake)
    await vm.setup()
    try:
        async with _GlueApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            lines = _detail_lines(pilot.app)
            assert "1 (more available)" in next(
                line for line in lines if line.startswith("Partitions")
            )
            fake.error = PermissionDeniedError("partitions permission denied")
            await vm.catalog.load_more_partitions()
            await wait_until(
                lambda: any("permission denied" in line for line in _detail_lines(pilot.app)),
                what="later partition-page error rendered",
            )
            lines = _detail_lines(pilot.app)
            assert any("id: string" in line for line in lines)
            assert any(line.endswith("day-0") for line in lines)
            assert len(vm.catalog.partitions) == 1
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["partitions", "statistics"])
async def test_supplement_errors_keep_existing_redaction_and_render_literal_markup(failed):
    fake = _SupplementGlue(failed=failed)
    fake.error = PermissionDeniedError("[bold]denied[/bold] password=synthetic-secret")
    vm, _ = _build_vm(fake)
    await vm.setup()
    try:
        async with _GlueApp(vm).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            row = next(line for line in _detail_lines(pilot.app) if line.startswith(failed.title()))
            assert "[bold]denied[/bold]" in row
            assert "[REDACTED]" in row
            assert "synthetic-secret" not in row
            static = next(
                s
                for s in pilot.app.query_one("#glue-table-detail-pane").query(Static)
                if str(s.render()).startswith(failed.title())
            )
            assert static.has_class("-warning")
    finally:
        await vm.shutdown()
        vm.dispose()
