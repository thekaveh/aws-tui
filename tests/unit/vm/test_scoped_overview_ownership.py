import asyncio
from datetime import UTC, datetime

import pytest

from aws_tui.domain.scoped_overview import OverviewEntry, OverviewPage, WatchScope
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.scoped_overview_vm import ScopedOverviewVM

CONNECTION = Connection("dev", "aws", "eu-west-1", "config", profile="dev-profile")
SCOPE = WatchScope("athena", "dev", "eu-west-1", "chosen")


async def current():
    return True


@pytest.mark.asyncio
async def test_ready_row_ownership_is_exact_and_retired_watch_never_regains_it():
    from dataclasses import replace

    shared = page(SCOPE)

    async def read(scope):
        return shared

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    try:
        vm.add(SCOPE)
        await vm.refresh()
        row = vm.sections[0].page.entries[0]
        token = vm.entry_owner(row)
        assert token is not None
        assert vm.entry_owner(replace(row)) is None
        vm.remove(SCOPE)
        assert vm.entry_owner(row) is None
        vm.add(SCOPE)
        await vm.refresh()
        assert vm.entry_owner(row) is not token
        assert vm.entry_owner(row) is not None
    finally:
        await vm.shutdown()


@pytest.mark.asyncio
async def test_retired_and_new_source_share_actual_reader_concurrency():
    slots = asyncio.Semaphore(2)
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def read(scope):
        calls.append(scope)
        if len(calls) == 2:
            started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return page(scope)

    old = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current, slots=slots)
    new = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current, slots=slots)
    tasks = []
    try:
        old.add(SCOPE)
        old.add(WatchScope("glue-job", "dev", "eu-west-1", "job"))
        tasks.append(asyncio.create_task(old.refresh()))
        await asyncio.wait_for(started.wait(), 1)
        old.close()
        new.add(SCOPE)
        tasks.append(asyncio.create_task(new.refresh()))
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(calls) == 2
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(calls) == 3
        assert new.sections[0].state == "ready"
        assert old.sections[0].last_success is None
    finally:
        release.set()
        await old.shutdown()
        await new.shutdown()
        await asyncio.gather(*tasks, return_exceptions=True)


def page(scope, identity="row"):
    return OverviewPage((OverviewEntry(scope, identity, "RUNNING"),))


async def settle(vm, tasks, *events):
    for event in events:
        event.set()
    await vm.shutdown()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_remove_readd_refresh_reads_new_incarnation_after_old_drain():
    started, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def read(scope):
        number = len(calls) + 1
        calls.append(number)
        if number == 1:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                canceled.set()
                await release.wait()
        return page(scope, str(number))

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    tasks = []
    try:
        vm.add(SCOPE)
        tasks.append(asyncio.create_task(vm.refresh(SCOPE)))
        await asyncio.wait_for(started.wait(), 1)
        vm.remove(SCOPE)
        await asyncio.wait_for(canceled.wait(), 1)
        assert vm.add(SCOPE)
        tasks.append(asyncio.create_task(vm.refresh(SCOPE)))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert calls == [1, 2], "fresh explicit refresh was coalesced into removed incarnation"
        assert vm.sections[0].state == "ready"
        assert vm.sections[0].page.entries[0].identifier == "2"
    finally:
        await settle(vm, tasks, release)


@pytest.mark.asyncio
async def test_provider_native_cancel_does_not_leave_section_loading_after_busy_ends():
    async def read(scope):
        raise asyncio.CancelledError

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(SCOPE)
    try:
        result = await asyncio.gather(vm.refresh(SCOPE), return_exceptions=True)
        await asyncio.sleep(0)
        assert not vm.busy
        assert vm.sections[0].state == "canceled", (result, vm.sections)
        assert vm.sections[0].last_success is None
    finally:
        await vm.shutdown()


@pytest.mark.asyncio
async def test_repeated_public_cancel_and_shutdown_do_not_recancel_uncancelled_cleanup():
    started, cleanup_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleanup_completed = False

    async def read(scope):
        nonlocal cleanup_completed
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
            cleanup_entered.set()
            await release.wait()
            cleanup_completed = True
        return page(scope)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    tasks = []
    try:
        vm.add(SCOPE)
        tasks.append(asyncio.create_task(vm.refresh(SCOPE)))
        await asyncio.wait_for(started.wait(), 1)
        vm.cancel_refresh()
        await asyncio.wait_for(cleanup_entered.wait(), 1)
        tasks.append(asyncio.create_task(vm.shutdown()))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not tasks[-1].done(), "shutdown returned while read cleanup was still gated"
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert cleanup_completed, "repeat cancellation interrupted already-owned cleanup"
        assert vm.sections[0].last_success is None
    finally:
        await settle(vm, tasks, release)


@pytest.mark.asyncio
async def test_canceling_coalesced_noncreator_does_not_cancel_shared_read():
    started, release = asyncio.Event(), asyncio.Event()
    canceled = False
    calls = 0

    async def read(scope):
        nonlocal canceled, calls
        calls += 1
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled = True
            raise
        return page(scope)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    tasks = []
    try:
        vm.add(SCOPE)
        tasks.append(asyncio.create_task(vm.refresh(SCOPE)))
        await asyncio.wait_for(started.wait(), 1)
        tasks.append(asyncio.create_task(vm.refresh(SCOPE)))
        await asyncio.sleep(0)
        tasks[1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        assert not canceled
        assert calls == 1
        assert vm.busy
        release.set()
        await tasks[0]
        assert vm.sections[0].state == "ready"
    finally:
        await settle(vm, tasks, release)


@pytest.mark.asyncio
async def test_reentrant_cancel_on_loading_prevents_reader_dispatch():
    calls = []

    async def read(scope):
        calls.append(scope)
        return page(scope)

    vm = ScopedOverviewVM(connection=CONNECTION, read=read, source_is_current=current)
    vm.add(SCOPE)
    fired = False

    def changed(_):
        nonlocal fired
        if not fired and vm.sections[0].state == "loading":
            fired = True
            vm.cancel_refresh()

    sub = vm.on_property_changed.subscribe(changed)
    try:
        await asyncio.gather(vm.refresh(), return_exceptions=True)
        assert fired
        assert calls == []
        assert vm.sections[0].state == "canceled"
    finally:
        sub.dispose()
        await vm.shutdown()


@pytest.mark.asyncio
async def test_failed_retry_keeps_last_success_and_exact_prior_empty_page():
    now = datetime(2026, 1, 2, tzinfo=UTC)
    fail = False

    async def read(scope):
        if fail:
            raise RuntimeError("SECRET marker must not enter error state")
        return OverviewPage(())

    vm = ScopedOverviewVM(
        connection=CONNECTION, read=read, source_is_current=current, clock=lambda: now
    )
    vm.add(SCOPE)
    try:
        await vm.refresh()
        fail = True
        await vm.refresh(SCOPE)
        section = vm.sections[0]
        assert section.state == "error"
        assert section.stale
        assert section.page == OverviewPage(())
        assert section.last_success == now
        assert "SECRET" not in section.error_text
    finally:
        await vm.shutdown()
