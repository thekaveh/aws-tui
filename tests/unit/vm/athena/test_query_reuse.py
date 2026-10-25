from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.filesystem import ProviderError, ValidationError
from aws_tui.domain.query import QueryState
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.vm.athena.query_vm import AthenaQueryVM
from tests.unit.vm.athena.test_query_vm import (
    _CONTEXT,
    InMemoryAthena,
    _detail,
    _no_sleep,
)


class RecordingAthena(InMemoryAthena):
    def __init__(self):
        super().__init__(executions=([_detail("q-app-1", QueryState.SUCCEEDED)],))
        self.requests = []

    async def start_query(self, sql, context, *, request_token, result_reuse_max_age_minutes=None):
        self.requests.append((sql, context, request_token, result_reuse_max_age_minutes))
        return await super().start_query(sql, context, request_token=request_token)


def make_vm(fake, reason=lambda: None):
    vm = AthenaQueryVM(
        client=fake,
        context=_CONTEXT,
        policy=ReadOnlySqlPolicy(),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        sleep=_no_sleep,
        reuse_unavailable_reason=reason,
    )
    vm.construct()
    vm.set_sql("SELECT 1")
    return vm


@pytest.mark.parametrize("age", [0, 60, 10080])
async def test_enabled_reuse_reaches_provider_with_exact_age(age):
    fake = RecordingAthena()
    vm = make_vm(fake)
    try:
        assert not vm.reuse_enabled
        assert vm.reuse_max_age_minutes == 60
        vm.set_reuse_options(enabled=True, max_age_minutes=age)
        await vm.execute()
        assert fake.requests[0][3] == age
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize("age", [-1, 10081, True, 1.5, "60", None])
async def test_invalid_age_cannot_change_settings_or_dispatch(age):
    fake = RecordingAthena()
    vm = make_vm(fake)
    try:
        with pytest.raises(ValidationError):
            vm.set_reuse_options(enabled=True, max_age_minutes=age)
        assert not vm.reuse_enabled
        assert fake.requests == []
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_unchanged_failed_retry_keeps_token_and_edited_options_rotate_it():
    fake = RecordingAthena()
    fake.start_error = ProviderError("ambiguous response")
    vm = make_vm(fake)
    try:
        vm.set_reuse_options(enabled=True, max_age_minutes=0)
        await vm.execute()
        await vm.execute()
        assert len(fake.requests) == 2
        assert fake.requests[0][2:] == fake.requests[1][2:]
        vm.set_reuse_options(enabled=True, max_age_minutes=10080)
        await vm.execute()
        assert fake.requests[-1][2] != fake.requests[0][2]
        assert fake.requests[-1][3] == 10080
        previous = fake.requests[-1][2]
        vm.set_sql("SELECT 2")
        vm.set_sql("SELECT 1")
        await vm.execute()
        assert fake.requests[-1][2] != previous
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_context_change_clears_reuse_and_failed_request_identity():
    fake = RecordingAthena()
    fake.start_error = ProviderError("ambiguous response")
    vm = make_vm(fake)
    try:
        vm.set_reuse_options(enabled=True, max_age_minutes=42)
        await vm.execute()
        await vm.set_context(replace(_CONTEXT, workgroup="other"))
        assert not vm.reuse_enabled
        assert vm.reuse_max_age_minutes == 60
        await vm.execute()
        assert fake.requests[-1][3] is None
        assert fake.requests[-1][2] != fake.requests[0][2]
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_unavailable_metadata_clears_enabled_reuse_without_replay():
    fake = RecordingAthena()
    state = {"reason": None}
    vm = make_vm(fake, lambda: state["reason"])
    try:
        vm.set_reuse_options(enabled=True, max_age_minutes=60)
        state["reason"] = "Managed results do not support result reuse"
        vm.refresh_execution_availability()
        assert not vm.reuse_enabled
        assert vm.reuse_unavailable_reason == state["reason"]
        with pytest.raises(ValidationError, match="Managed results"):
            vm.set_reuse_options(enabled=True, max_age_minutes=60)
        state["reason"] = None
        vm.refresh_execution_availability()
        assert not vm.reuse_enabled
        assert fake.requests == []
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_explicit_cancel_of_pending_submission_retires_accepted_request_token():
    fake = RecordingAthena()
    fake.executions.append([_detail("q-app-2", QueryState.SUCCEEDED)])
    fake.block_start = True
    fake.ignore_start_cancellation = True
    vm = make_vm(fake)
    execute = asyncio.create_task(vm.execute())
    cancel = None
    try:
        await asyncio.wait_for(fake.start_started.wait(), 1)
        cancel = asyncio.create_task(vm.cancel())
        await asyncio.sleep(0)
        fake.release_start.set()
        await asyncio.wait_for(asyncio.gather(execute, cancel), 2)
        assert fake.stop_calls == ["q-app-1"]
        fake.block_start = False
        await vm.execute()
        assert len(fake.requests) == 2
        assert fake.requests[0][2] != fake.requests[1][2]
    finally:
        fake.release_start.set()
        await asyncio.gather(execute, *([cancel] if cancel else []), return_exceptions=True)
        await vm.shutdown()
        vm.dispose()


async def test_edit_during_pending_submission_keeps_new_options_after_old_completion():
    fake = RecordingAthena()
    fake.executions.append([_detail("q-app-2", QueryState.SUCCEEDED)])
    fake.block_start = True
    vm = make_vm(fake)
    vm.set_reuse_options(enabled=True, max_age_minutes=0)
    execute = asyncio.create_task(vm.execute())
    try:
        await asyncio.wait_for(fake.start_started.wait(), 1)
        vm.set_reuse_options(enabled=True, max_age_minutes=10080)
        fake.release_start.set()
        await asyncio.wait_for(execute, 2)
        assert vm.reuse_max_age_minutes == 10080
        assert fake.requests[0][3] == 0
        fake.block_start = False
        await vm.execute()
        assert fake.requests[1][3] == 10080
        assert fake.requests[1][2] != fake.requests[0][2]
    finally:
        fake.release_start.set()
        await asyncio.gather(execute, return_exceptions=True)
        await vm.shutdown()
        vm.dispose()


async def test_metadata_invalidated_after_capture_prevents_provider_dispatch():
    fake = RecordingAthena()
    state = {"reason": None}
    vm = make_vm(fake, lambda: state["reason"])
    vm.set_reuse_options(enabled=True, max_age_minutes=60)
    retire = vm._retire_owned_query_before_run

    async def retire_then_invalidate(generation):
        result = await retire(generation)
        state["reason"] = "Managed results do not support result reuse"
        vm.refresh_execution_availability()
        return result

    vm._retire_owned_query_before_run = retire_then_invalidate
    try:
        await vm.execute()
        assert fake.requests == []
        assert not vm.reuse_enabled
    finally:
        await vm.shutdown()
        vm.dispose()


@pytest.mark.parametrize(
    "changed", [{"workgroup": "other"}, {"connection_name": "other"}, {"region": "us-east-2"}]
)
async def test_context_switch_during_enabled_submission_cleans_old_ref(changed):
    fake = RecordingAthena()
    fake.block_start = True
    fake.ignore_start_cancellation = True
    vm = make_vm(fake)
    vm.set_reuse_options(enabled=True, max_age_minutes=60)
    execute = asyncio.create_task(vm.execute())
    change = None
    try:
        await asyncio.wait_for(fake.start_started.wait(), 1)
        new_context = replace(_CONTEXT, **changed)
        change = asyncio.create_task(vm.set_context(new_context))
        await asyncio.sleep(0)
        fake.release_start.set()
        await asyncio.wait_for(asyncio.gather(execute, change), 2)
        assert vm.context == new_context
        assert vm.execution_ref is None
        assert fake.stop_calls == ["q-app-1"]
        assert not vm.reuse_enabled
    finally:
        fake.release_start.set()
        await asyncio.gather(execute, *([change] if change else []), return_exceptions=True)
        await vm.shutdown()
        vm.dispose()


async def test_restoring_query_snapshot_does_not_restore_transient_reuse():
    fake = RecordingAthena()
    vm = make_vm(fake)
    try:
        snapshot = vm.export_snapshot()
        vm.set_reuse_options(enabled=True, max_age_minutes=42)
        await vm.restore_snapshot(snapshot)
        assert not vm.reuse_enabled
        assert vm.reuse_max_age_minutes == 60
        assert fake.requests == []
    finally:
        await vm.shutdown()
        vm.dispose()
