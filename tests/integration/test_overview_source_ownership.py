"""Source-check cancellation retains actual resolver ownership and retryability."""

import asyncio
from threading import Event

import pytest

from tests.helpers import drain_workers
from tests.integration.test_workspace_manager import make_workspace_app


def current_watch(app, vm):
    watch = app._overview_current_scope(vm.connection)
    assert watch is not None
    return watch


@pytest.mark.asyncio
async def test_cancel_overview_refresh_drains_its_source_resolver_thread(tmp_path, monkeypatch):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    entered, release, drained = Event(), Event(), Event()
    task = None
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            try:
                await drain_workers(app)
                resolve = ctx.connection_resolver.resolve_selected

                def held(name):
                    entered.set()
                    try:
                        assert release.wait(5)
                        return resolve(name)
                    finally:
                        drained.set()

                monkeypatch.setattr(ctx.connection_resolver, "resolve_selected", held)
                app.action_overview()
                await pilot.pause()
                vm = app.screen.vm
                vm.add(current_watch(app, vm))
                task = asyncio.create_task(vm.refresh())
                assert await asyncio.to_thread(entered.wait, 3)
                vm.cancel_refresh()
                for _ in range(20):
                    await asyncio.sleep(0)
                assert not task.done(), (
                    "overview refresh returned while its source resolver thread was still running"
                )
                assert not drained.is_set()
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
    finally:
        release.set()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_cancel_initial_source_check_then_retry_same_overview_is_usable(
    tmp_path, monkeypatch
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    entered, release, drained = Event(), Event(), Event()
    task = None
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            try:
                await drain_workers(app)
                resolve = ctx.connection_resolver.resolve_selected
                first = True

                def held_once(name):
                    nonlocal first
                    if first:
                        first = False
                        entered.set()
                        try:
                            assert release.wait(5)
                        finally:
                            drained.set()
                    return resolve(name)

                monkeypatch.setattr(ctx.connection_resolver, "resolve_selected", held_once)
                app.action_overview()
                await pilot.pause()
                vm = app.screen.vm
                vm.add(current_watch(app, vm))
                task = asyncio.create_task(vm.refresh())
                assert await asyncio.to_thread(entered.wait, 3)
                vm.cancel_refresh()
                for _ in range(20):
                    await asyncio.sleep(0)
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                assert await asyncio.to_thread(drained.wait, 3)
                await pilot.press("escape")
                app.action_overview()
                await pilot.pause()
                assert app.screen.vm is vm
                await vm.refresh()
                assert vm.sections[0].state == "ready", (
                    "canceled initial source check permanently poisoned same-source overview retry"
                )
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
    finally:
        release.set()
        ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
async def test_transient_initial_source_lookup_failure_can_retry_after_same_source_reopen(
    tmp_path, monkeypatch
):
    app, ctx = make_workspace_app(tmp_path, monkeypatch)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await drain_workers(app)
            resolve = ctx.connection_resolver.resolve_selected
            first = True

            def fail_once(name):
                nonlocal first
                if first:
                    first = False
                    raise OSError("controlled transient config lookup failure")
                return resolve(name)

            monkeypatch.setattr(ctx.connection_resolver, "resolve_selected", fail_once)
            app.action_overview()
            await pilot.pause()
            vm = app.screen.vm
            vm.add(current_watch(app, vm))
            await vm.refresh()
            assert vm.sections[0].state == "changed"
            await pilot.press("escape")
            app.action_overview()
            await pilot.pause()
            current = app.screen.vm
            if not current.sections:
                current.add(current_watch(app, current))
            await current.refresh()
            assert current.sections[0].state == "ready", (
                "transient initial lookup failure remains permanent after requested reopen"
            )
    finally:
        ctx.root_vm.dispose()
        ctx.log_sink.close()
