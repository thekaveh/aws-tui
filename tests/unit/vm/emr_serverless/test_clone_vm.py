"""JobRunCloneVM tests — pin form pre-population, apply_field,
submit success, and the typed-error fallthrough.

The submit-failure path matters because the modal relies on it to
keep itself open when AWS returns ``ValidationException`` — without
re-raising the typed :class:`ProviderError` the modal would
silently dismiss and the user would assume the run was submitted."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import ValidationError
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _detail() -> JobRunDetail:
    return JobRunDetail(
        application_id="00abc",
        job_run_id="r-001",
        name="nightly",
        state=JobRunState.SUCCESS,
        created_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 12, 4, tzinfo=UTC),
        entry_point="s3://b/job.py",
        entry_point_arguments=("--in", "s3://b/in/"),
        spark_submit_parameters="--conf spark.executor.instances=4",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
        s3_monitoring_log_uri=None,
    )


def _make(client: object | None = None) -> tuple[JobRunCloneVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunCloneVM(
        _detail(),
        client=client or fake,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm, fake


def test_construct_prepopulates_form_from_detail() -> None:
    vm, _ = _make()
    assert vm.application_id == "00abc"
    assert vm.name == "nightly"
    assert vm.execution_role_arn == "arn:aws:iam::123456789012:role/EmrJobRole"
    assert vm.entry_point == "s3://b/job.py"
    assert vm.entry_point_arguments == ("--in", "s3://b/in/")
    assert vm.spark_submit_parameters == "--conf spark.executor.instances=4"
    vm.dispose()


def test_apply_field_updates_str_fields_and_clears_optional() -> None:
    vm, _ = _make()
    vm.apply_field("name", "")
    assert vm.name is None
    vm.apply_field("name", "renamed")
    assert vm.name == "renamed"
    vm.apply_field("execution_role_arn", "arn:aws:iam::999::role/Other")
    assert vm.execution_role_arn == "arn:aws:iam::999::role/Other"
    vm.apply_field("entry_point", "s3://b/new.py")
    assert vm.entry_point == "s3://b/new.py"
    vm.apply_field("spark_submit_parameters", "")
    assert vm.spark_submit_parameters is None
    vm.dispose()


def test_apply_field_accepts_tuple_for_arguments() -> None:
    vm, _ = _make()
    vm.apply_field("entry_point_arguments", ("--in", "s3://b/in", "--out", "s3://b/out"))
    assert vm.entry_point_arguments == ("--in", "s3://b/in", "--out", "s3://b/out")
    vm.dispose()


def test_apply_field_rejects_unknown_field() -> None:
    vm, _ = _make()
    with pytest.raises(KeyError):
        vm.apply_field("does_not_exist", "value")
    vm.dispose()


def test_apply_field_rejects_type_mismatch() -> None:
    vm, _ = _make()
    with pytest.raises(TypeError):
        vm.apply_field("entry_point_arguments", "not-a-tuple")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        vm.apply_field("execution_role_arn", ("not", "a", "str"))  # type: ignore[arg-type]
    vm.dispose()


def test_is_valid_blocks_when_required_fields_blank() -> None:
    vm, _ = _make()
    vm.apply_field("execution_role_arn", "")
    ok, reason = vm.is_valid()
    assert ok is False
    assert reason is not None
    assert "execution role" in reason.lower()
    # Restore role, blank the entry point.
    vm.apply_field("execution_role_arn", "arn:aws:iam::123456789012:role/EmrJobRole")
    vm.apply_field("entry_point", "")
    ok, reason = vm.is_valid()
    assert ok is False
    assert reason is not None
    assert "entry point" in reason.lower()
    vm.dispose()


@pytest.mark.asyncio
async def test_submit_calls_client_and_returns_new_job_run_id() -> None:
    vm, fake = _make()
    new_id = await vm.submit()
    assert new_id.startswith("r-clone-")
    # The call was recorded with the form values.
    submit_calls = [c for c in fake.calls if c[0] == "start_job_run"]
    assert len(submit_calls) == 1
    args = submit_calls[0][1]
    assert args[0] == "00abc"
    assert args[1] == "arn:aws:iam::123456789012:role/EmrJobRole"
    assert args[2] == "s3://b/job.py"
    assert args[3] == ("--in", "s3://b/in/")
    assert args[4] == "--conf spark.executor.instances=4"
    assert args[5] == "nightly"
    vm.dispose()


@pytest.mark.asyncio
async def test_submit_propagates_provider_error_without_swallowing() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    fake.start_job_run_exc = ValidationError("entryPoint must be an s3:// URL")
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunCloneVM(
        _detail(),
        client=fake,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    try:
        with pytest.raises(ValidationError) as exc_info:
            await vm.submit()
        assert "entryPoint" in str(exc_info.value)
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_submit_records_submitted_id_property() -> None:
    """Tests that probe the VM in isolation (no modal widget on top)
    rely on :attr:`submitted_id` to assert the post-submit state."""
    vm, _ = _make()
    assert vm.submitted_id is None
    new_id = await vm.submit()
    assert vm.submitted_id == new_id
    vm.dispose()


def test_cancel_sets_cancelled_flag() -> None:
    """The page widget reads the modal's dismiss value; the
    :attr:`cancelled` flag exists for unit tests + symmetry with
    Confirm / Resume modal VMs."""
    vm, _ = _make()
    assert vm.cancelled is False
    vm.cancel()
    assert vm.cancelled is True
    vm.dispose()


def test_dispose_is_idempotent() -> None:
    vm, _ = _make()
    vm.dispose()
    vm.dispose()  # Must not raise.
