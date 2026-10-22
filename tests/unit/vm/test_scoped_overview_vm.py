import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from aws_tui.domain.filesystem import PermissionDeniedError, ThrottledError
from aws_tui.domain.scoped_overview import OverviewEntry, OverviewPage, WatchScope
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.scoped_overview_vm import ScopedOverviewVM

CONNECTION = Connection("dev", "aws", "eu-west-1", "config", profile="dev-profile")
NOW = datetime(2026, 1, 2, tzinfo=UTC)


def scope(resource: str = "chosen") -> WatchScope:
    return WatchScope("athena", "dev", "eu-west-1", resource)


def page(watch: WatchScope) -> OverviewPage:
    return OverviewPage((OverviewEntry(watch, "run-a", "RUNNING"),))


async def current() -> bool:
    return True


@pytest.mark.asyncio
async def test_no_scope_or_request_until_explicit_add_and_manual_refresh() -> None:
    calls = []

    async def read(watch: WatchScope) -> OverviewPage:
        calls.append(watch)
        return page(watch)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    assert vm.sections == ()
    await vm.refresh()
    assert calls == []
    assert vm.add(scope())
    assert not vm.add(scope())
    await asyncio.sleep(0)
    assert calls == []
    await vm.refresh()
    assert calls == [scope()]
    assert vm.sections[0].page == page(scope())
    await vm.shutdown()


@pytest.mark.asyncio
async def test_explicit_scope_count_and_identity_are_bounded_before_reads() -> None:
    async def read(watch: WatchScope) -> OverviewPage:
        pytest.fail("No automatic read is allowed")

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    for index in range(8):
        vm.add(scope(str(index)))
    with pytest.raises(ValueError, match="eight"):
        vm.add(scope("ninth"))
    with pytest.raises(ValueError, match="source"):
        vm.add(WatchScope("athena", "other", "eu-west-1", "chosen"))
    assert len(vm.sections) == 8
    await vm.shutdown()


@pytest.mark.asyncio
async def test_fake_clock_advancing_does_not_poll_and_records_only_success() -> None:
    now = NOW
    calls = 0

    async def read(watch: WatchScope) -> OverviewPage:
        nonlocal calls
        calls += 1
        return page(watch)

    vm = ScopedOverviewVM(
        connection=CONNECTION, read=read, source_is_current=current, clock=lambda: now
    )
    vm.add(scope())
    now += timedelta(days=1)
    await asyncio.sleep(0)
    assert calls == 0
    await vm.refresh()
    assert vm.sections[0].last_success == now
    now += timedelta(days=1)
    await asyncio.sleep(0)
    assert calls == 1
    assert vm.sections[0].last_success != now
    await vm.shutdown()


@pytest.mark.asyncio
async def test_one_failed_source_does_not_hide_siblings_and_retry_is_scoped() -> None:
    calls: Counter[WatchScope] = Counter()
    fail = True

    async def read(watch: WatchScope) -> OverviewPage:
        calls[watch] += 1
        if fail and watch.resource == "denied":
            raise PermissionDeniedError("raw token=do-not-show")
        return page(watch)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(scope("denied"))
    vm.add(scope("healthy"))
    await vm.refresh()
    denied, healthy = vm.sections
    assert denied.state == "denied"
    assert "do-not-show" not in (denied.error_text or "")
    assert healthy.state == "ready"
    assert healthy.page == page(scope("healthy"))
    fail = False
    await vm.refresh(scope("denied"))
    assert calls == Counter({scope("denied"): 2, scope("healthy"): 1})
    assert all(section.state == "ready" for section in vm.sections)
    await vm.shutdown()


@pytest.mark.asyncio
async def test_failed_refresh_retains_explicitly_stale_rows_and_last_success() -> None:
    fail = False

    async def read(watch: WatchScope) -> OverviewPage:
        if fail:
            raise ThrottledError("provider is throttled")
        return page(watch)

    vm = ScopedOverviewVM(
        connection=CONNECTION, read=read, source_is_current=current, clock=lambda: NOW
    )
    vm.add(scope())
    await vm.refresh()
    fail = True
    await vm.refresh()
    section = vm.sections[0]
    assert section.state == "throttled"
    assert section.stale
    assert section.page == page(scope())
    assert section.last_success == NOW
    await vm.shutdown()


@pytest.mark.asyncio
async def test_eight_watches_share_two_reader_slots_and_duplicate_refresh_coalesces() -> None:
    release, two_started = asyncio.Event(), asyncio.Event()
    active = maximum = 0
    calls: Counter[WatchScope] = Counter()

    async def read(watch: WatchScope) -> OverviewPage:
        nonlocal active, maximum
        calls[watch] += 1
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            two_started.set()
        try:
            await release.wait()
            return page(watch)
        finally:
            active -= 1

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    for index in range(8):
        vm.add(scope(str(index)))
    first = asyncio.create_task(vm.refresh())
    await asyncio.wait_for(two_started.wait(), 1)
    duplicate = asyncio.create_task(vm.refresh())
    await asyncio.sleep(0)
    assert sum(calls.values()) == 2
    release.set()
    await asyncio.gather(first, duplicate)
    assert maximum == 2
    assert sum(calls.values()) == 8
    assert all(count == 1 for count in calls.values())
    await vm.shutdown()


@pytest.mark.asyncio
async def test_source_change_after_read_rejects_late_success() -> None:
    valid = True

    async def source_current() -> bool:
        return valid

    async def read(watch: WatchScope) -> OverviewPage:
        nonlocal valid
        valid = False
        return page(watch)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=source_current)
    vm.add(scope())
    await vm.refresh()
    assert vm.sections[0].state == "changed"
    assert vm.sections[0].last_success is None
    assert vm.sections[0].page.entries == ()
    await vm.shutdown()


@pytest.mark.asyncio
async def test_cancellation_drains_actual_read_through_repeated_caller_cancellation() -> None:
    started, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def read(watch: WatchScope) -> OverviewPage:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled.set()
            await release.wait()
        return page(watch)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(scope())
    owner = asyncio.create_task(vm.refresh())
    await asyncio.wait_for(started.wait(), 1)
    owner.cancel()
    await asyncio.wait_for(canceled.wait(), 1)
    owner.cancel()
    await asyncio.sleep(0)
    assert not owner.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert not vm.busy
    assert vm.sections[0].last_success is None
    assert vm.sections[0].page.entries == ()
    await vm.shutdown()


@pytest.mark.asyncio
async def test_removing_one_inflight_scope_does_not_cancel_its_healthy_sibling() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def read(watch: WatchScope) -> OverviewPage:
        started.set()
        await release.wait()
        return page(watch)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(scope("removed"))
    vm.add(scope("healthy"))
    owner = asyncio.create_task(vm.refresh())
    await asyncio.wait_for(started.wait(), 1)
    vm.remove(scope("removed"))
    release.set()
    await owner
    assert len(vm.sections) == 1
    assert vm.sections[0].scope == scope("healthy")
    assert vm.sections[0].state == "ready"
    assert vm.sections[0].page == page(scope("healthy"))
    await vm.shutdown()


@pytest.mark.asyncio
async def test_shutdown_drains_cancellation_consuming_read_without_publishing() -> None:
    started, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def read(watch: WatchScope) -> OverviewPage:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled.set()
            await release.wait()
        return page(watch)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(scope())
    owner = asyncio.create_task(vm.refresh())
    await asyncio.wait_for(started.wait(), 1)
    shutdown = asyncio.create_task(vm.shutdown())
    await asyncio.wait_for(canceled.wait(), 1)
    assert not shutdown.done()
    release.set()
    await asyncio.gather(owner, shutdown)
    assert not vm.busy
    assert vm.sections[0].last_success is None
    assert vm.sections[0].page.entries == ()
    await vm.refresh()


@pytest.mark.asyncio
async def test_remove_readd_same_scope_refreshes_new_incarnation_while_old_read_drains() -> None:
    started, canceled, release, replacement = (asyncio.Event() for _ in range(4))
    calls = 0

    async def read(watch: WatchScope) -> OverviewPage:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                canceled.set()
                await release.wait()
            return OverviewPage((OverviewEntry(watch, "old-run", "FAILED"),))
        replacement.set()
        return OverviewPage((OverviewEntry(watch, "new-run", "RUNNING"),))

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(scope())
    old = asyncio.create_task(vm.refresh())
    await asyncio.wait_for(started.wait(), 1)
    vm.remove(scope())
    await asyncio.wait_for(canceled.wait(), 1)
    vm.add(scope())
    new = asyncio.create_task(vm.refresh())
    try:
        await asyncio.wait_for(replacement.wait(), 1)
        await new
        assert vm.sections[0].page.entries[0].identifier == "new-run"
        assert vm.busy  # The old, canceled actual read is still owned.
    finally:
        release.set()
        await asyncio.gather(old, new, return_exceptions=True)
        await vm.shutdown()
    assert vm.sections[0].page.entries[0].identifier == "new-run"
