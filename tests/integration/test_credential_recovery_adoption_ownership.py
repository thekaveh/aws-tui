"""Fence live editor ownership before recovery drains and retires its page."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from textual.widgets import TextArea

from aws_tui.vm.athena.page_vm import AthenaPageVM
from tests.helpers import wait_until
from tests.integration.test_credential_recovery import _mount_athena_for_recovery
from tests.integration.test_glue_page import open_service
from tests.unit.vm.athena.test_page_vm import PageClient


@asynccontextmanager
async def mounted(tmp_path, release, tasks, prepare=None):
    app, ctx, service, initial = _mount_athena_for_recovery(tmp_path)
    if prepare is not None:
        prepare(initial)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            try:
                yield app, ctx, service, initial, pilot
            finally:
                # Release before run_test's exit starts its own shutdown.
                release.set()
                for task in tasks:
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
    finally:
        release.set()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(ctx.root_vm.content_host.shutdown(), 5)
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
        ctx.log_sink.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during_drain", [False, True])
async def test_recovery_retains_editor_input_during_outgoing_setup_drain(
    tmp_path: Path, cancel_during_drain: bool
):
    entered, draining, release = (asyncio.Event() for _ in range(3))
    tasks = []

    def prepare(initial):
        original_get = initial.get_workgroup

        async def held_get(name):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                draining.set()
                await release.wait()
            return await original_get(name)

        initial.get_workgroup = held_get

    async with mounted(tmp_path, release, tasks, prepare) as (app, ctx, service, _, pilot):
        await app.workers.wait_for_complete(list(app.workers._workers))
        await pilot.pause()
        ctx.root_vm.services_menu.switch_service_command.execute("athena")
        await asyncio.wait_for(entered.wait(), 3)
        await wait_until(
            lambda: (
                isinstance(ctx.root_vm.content_host.current, AthenaPageVM)
                and bool(app.query("#athena-editor"))
            ),
            what="mounted editor while original setup is held",
        )
        live = ctx.root_vm.content_host.current
        live.query.set_sql("SELECT old_text")
        await pilot.pause()
        service._client_factory = lambda _: PageClient(
            connection_name="demo-dev", region="us-east-1"
        )
        candidates = []
        original_build = ctx.root_vm.build_recovery_service_vm

        def record_candidate(*args):
            prepared = original_build(*args)
            candidates.append(prepared.vm)
            return prepared

        ctx.root_vm.build_recovery_service_vm = record_candidate
        recovery = asyncio.create_task(app._recover_active_source())
        tasks.append(recovery)
        await asyncio.wait_for(draining.wait(), 5)
        assert ctx.root_vm.content_host.current is live
        assert not recovery.done()
        if cancel_during_drain:
            recovery.cancel()
        editor = app.query_one("#athena-editor", TextArea)
        editor.focus()
        await pilot.pause()
        await pilot.press("end", "space", "x")
        await wait_until(
            lambda: live.query.sql == "SELECT old_text x", what="SQL accepted during setup drain"
        )
        release.set()
        if cancel_during_drain:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(recovery, 5)
        else:
            await asyncio.wait_for(recovery, 5)
        assert ctx.root_vm.content_host.current is live
        assert live.query.sql == "SELECT old_text x"
        assert not live._shutdown_started
        assert not editor.read_only
        assert candidates[0]._disposed
        if not cancel_during_drain:
            assert candidates[0]._shutdown_complete
        assert app._athena_page().vm is live
        assert not app._recovery_mount_errors
        await app._recover_active_source()
        assert ctx.root_vm.content_host.current is candidates[1]
        assert candidates[1].query.sql == "SELECT old_text x"
        assert ctx.root_vm.services_menu.selected_id == "athena"
        assert app._athena_page().vm is candidates[1]


@pytest.mark.asyncio
async def test_recovery_closes_editor_intake_before_outgoing_shutdown(tmp_path: Path):
    entered, release = asyncio.Event(), asyncio.Event()
    tasks = []
    async with mounted(tmp_path, release, tasks) as (app, ctx, service, initial, pilot):
        await open_service(ctx, pilot, "athena")
        live = ctx.root_vm.content_host.current
        live.query.set_sql("SELECT 1")
        await pilot.pause()
        assert live.query.execute_command.can_execute()
        original_flush = live.query._draft_session.flush

        async def held_flush(*, deadline):
            entered.set()
            await release.wait()
            await original_flush(deadline=deadline)

        live.query._draft_session.flush = held_flush
        claimed = []
        if hasattr(live, "close_input_admission"):
            original_claim = live.close_input_admission

            def claim():
                original_claim()
                # No yield: queued Run must be rejected before shutdown starts.
                assert not live._shutdown_started
                assert not live.query.execute_command.can_execute()
                claimed.append(True)

            live.close_input_admission = claim
        service._client_factory = lambda _: PageClient(
            connection_name="demo-dev", region="us-east-1"
        )
        recovery = asyncio.create_task(app._recover_active_source())
        tasks.append(recovery)
        await asyncio.wait_for(entered.wait(), 5)
        editor = app.query_one("#athena-editor", TextArea)
        editor.focus()
        await pilot.pause()
        await pilot.press("end", "space", "x")
        await pilot.pause()
        assert editor.read_only
        assert app._athena_page().disabled
        assert editor.text == "SELECT 1"
        assert live.query.sql == "SELECT 1"
        assert claimed == [True]
        live.query.set_sql("SELECT queued_edit")
        await live.query.execute()
        context_before = live.context
        await live.select_workgroup("analysts")
        assert live.query.sql == "SELECT 1"
        assert live.context == context_before
        assert initial.start_calls == []
        release.set()
        await asyncio.wait_for(recovery, 5)
        fresh = ctx.root_vm.content_host.current
        assert fresh is not live
        assert fresh.query.sql == "SELECT 1"
        assert not app._athena_page().disabled
        assert not app._athena_page().query_one("#athena-editor", TextArea).read_only


@pytest.mark.asyncio
async def test_recovery_rejects_visible_editor_change_before_queued_vm_acknowledgement(
    tmp_path: Path,
):
    release = asyncio.Event()
    async with mounted(tmp_path, release, []) as (app, ctx, service, _, pilot):
        await open_service(ctx, pilot, "athena")
        live = ctx.root_vm.content_host.current
        live.query.set_sql("SELECT old_text")
        await pilot.pause()
        editor = app.query_one("#athena-editor", TextArea)
        original_adopt = ctx.root_vm.adopt_prepared_service_vm
        injected = False

        async def inject_visible_change(*args, **kwargs):
            original_guard = kwargs["ownership_is_current"]

            def guard():
                nonlocal injected
                current = original_guard()
                if current and not injected:
                    injected = True
                    # Public edit posts Changed later; VM ownership is still old.
                    editor.insert(" x", location=(0, len(editor.text)))
                    assert editor.text == "SELECT old_text x"
                    assert live.query.sql == "SELECT old_text"
                return current

            kwargs["ownership_is_current"] = guard
            return await original_adopt(*args, **kwargs)

        ctx.root_vm.adopt_prepared_service_vm = inject_visible_change
        service._client_factory = lambda _: PageClient(
            connection_name="demo-dev", region="us-east-1"
        )
        await app._recover_active_source()
        await wait_until(
            lambda: live.query.sql == "SELECT old_text x",
            what="pending visible edit acknowledgement",
        )
        assert injected
        assert ctx.root_vm.content_host.current is live
        assert not live._shutdown_started
        assert not editor.read_only
        assert not app._athena_page().disabled
        editor.focus()
        await pilot.pause()
        await pilot.press("end", "y")
        await wait_until(
            lambda: live.query.sql == "SELECT old_text xy", what="retained editor stays editable"
        )
