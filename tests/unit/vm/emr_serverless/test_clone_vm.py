"""JobRunCloneVM tests — pin form pre-population, apply_field,
submit success, and the typed-error fallthrough.

The submit-failure path matters because the modal relies on it to
keep itself open when AWS returns ``ValidationException`` — without
re-raising the typed :class:`ProviderError` the modal would
silently dismiss and the user would assume the run was submitted."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import ProviderUnreachableError, ValidationError
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from aws_tui.vm.service_source_vm import ServiceSourceContext


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
        job_driver={"sparkSubmit": {"entryPoint": "s3://b/job.py"}},
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


@pytest.mark.asyncio
async def test_submit_passes_the_vm_client_token_to_the_client() -> None:
    vm, fake = _make()
    token = vm.client_token
    await vm.submit()
    submit_calls = [c for c in fake.calls if c[0] == "start_job_run"]
    assert submit_calls[0][1][6] == token
    vm.dispose()


@pytest.mark.asyncio
async def test_retry_after_an_ambiguous_failure_reuses_the_same_client_token() -> None:
    """A timeout after AWS accepted the request must not mint a second job.

    The first attempt raises; the user presses submit again; AWS sees the same
    ``clientToken`` and returns the run it already created.
    """
    vm, fake = _make()
    fake.start_job_run_exc = ProviderUnreachableError("read timeout")
    with pytest.raises(ProviderUnreachableError):
        await vm.submit()
    fake.start_job_run_exc = None
    await vm.submit()

    tokens = [c[1][6] for c in fake.calls if c[0] == "start_job_run"]
    assert len(tokens) == 2
    assert tokens[0] == tokens[1]
    vm.dispose()


def test_editing_a_field_rotates_the_client_token() -> None:
    vm, _fake = _make()
    before = vm.client_token
    vm.apply_field("name", "nightly")  # unchanged value: same intent
    assert vm.client_token == before
    vm.apply_field("name", "nightly-rerun")
    assert vm.client_token != before
    vm.dispose()


@pytest.mark.asyncio
async def test_successful_submit_rotates_the_client_token() -> None:
    vm, _fake = _make()
    before = vm.client_token
    await vm.submit()
    assert vm.client_token != before
    vm.dispose()


_SETTINGS = [
    (
        "configurationOverrides",
        {
            "applicationConfiguration": [
                {"classification": "spark-defaults", "properties": {"k": "one"}}
            ]
        },
        {
            "applicationConfiguration": [
                {"classification": "spark-defaults", "properties": {"k": "two"}}
            ]
        },
    ),
    ("executionTimeoutMinutes", 0, 60),
    ("retryPolicy", {"maxAttempts": 2}, {"maxAttempts": 3}),
    ("mode", "STREAMING", "BATCH"),
    ("executionIamPolicy", {"policy": '{"Statement": []}'}, {}),
    ("tags", {"team": "analytics"}, {"team": "platform"}),
]


@pytest.mark.parametrize(("key", "value", "edited"), _SETTINGS)
async def test_retry_and_edit_intent_include_each_new_setting(
    key: str, value: object, edited: object
) -> None:
    vm, fake = _make()
    try:
        before = vm.client_token
        vm.apply_settings({key: value})
        assert vm.client_token != before
        intent = vm.client_token
        vm.apply_settings({key: value})
        assert vm.client_token == intent
        fake.start_job_run_exc = ProviderUnreachableError("ambiguous")
        with pytest.raises(ProviderUnreachableError):
            await vm.submit()
        assert vm.client_token == intent
        fake.start_job_run_exc = None
        await vm.submit()
        calls = [c[1] for c in fake.calls if c[0] == "start_job_run"]
        assert len(calls) == 2
        assert calls[0][6] == calls[1][6] == intent
        assert calls[0][7] == calls[1][7]
        assert calls[0][7][key] == value
        assert vm.client_token != intent
        current = vm.client_token
        vm.apply_settings({key: edited})
        assert vm.client_token != current
        current = vm.client_token
        vm.apply_settings({})
        assert vm.client_token != current
    finally:
        vm.dispose()
        fake.dispose()


def test_settings_and_source_are_defensive_snapshots() -> None:
    source = replace(
        _detail(),
        mode="STREAMING",
        tags={"team": "source"},
        configuration_overrides={
            "monitoringConfiguration": {"s3MonitoringConfiguration": {"logUri": "s3://logs/source"}}
        },
    )
    fake = _InMemoryEmr()
    vm = JobRunCloneVM(source, client=fake, hub=MessageHub(), dispatcher=NULL_DISPATCHER)
    try:
        assert vm.settings["mode"] == "STREAMING"
        source.tags["team"] = "mutated"
        assert vm.settings["tags"] == {"team": "source"}
        assert vm.source_detail.tags == {"team": "source"}
        exposed_source = vm.source_detail
        exposed_source.tags["team"] = "also mutated"
        assert vm.source_detail.tags == {"team": "source"}
        settings = {"tags": {"team": "new"}}
        vm.apply_settings(settings)
        token = vm.client_token
        settings["tags"]["team"] = "caller mutation"
        exposed = vm.settings
        exposed["tags"]["team"] = "getter mutation"
        assert vm.settings == {"tags": {"team": "new"}}
        assert vm.client_token == token
    finally:
        vm.dispose()


@pytest.mark.parametrize(
    "driver",
    [
        None,
        {},
        {"hive": {"query": "s3://b/q.hql"}},
        {"future": {}},
        {"sparkSubmit": {"entryPoint": "s3://b/job.py"}, "hive": {}},
        {"sparkSubmit": {"entryPoint": "s3://b/job.py", "unsupported": "secret-value"}},
    ],
)
async def test_unsupported_driver_cannot_submit_even_after_spark_field_edits(
    driver: dict | None,
) -> None:
    fake = _InMemoryEmr()
    vm = JobRunCloneVM(
        replace(_detail(), job_driver=driver),
        client=fake,
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
    )
    try:
        vm.apply_field("entry_point", "s3://b/new.py")
        valid, reason = vm.is_valid()
        assert not valid
        assert "driver" in reason.lower()
        assert "secret-value" not in reason
        with pytest.raises(ValidationError, match="driver"):
            await vm.submit()
        assert not fake.calls
    finally:
        vm.dispose()


@pytest.mark.parametrize("action", ["cancel", "dispose"])
async def test_inactive_clone_cannot_submit(action: str) -> None:
    vm, fake = _make()
    getattr(vm, action)()
    with pytest.raises(ValidationError):
        await vm.submit()
    assert not fake.calls
    vm.dispose()


def test_invalid_settings_do_not_change_current_intent() -> None:
    vm, _ = _make()
    token = vm.client_token
    try:
        with pytest.raises(ValidationError):
            vm.apply_settings({"mode": "secret-future-mode"})
        assert vm.settings == {}
        assert vm.client_token == token
    finally:
        vm.dispose()


def test_review_names_identity_differences_and_unknown_inheritance() -> None:
    detail = replace(
        _detail(),
        mode="BATCH",
        execution_timeout_minutes=30,
        source_application_settings={
            "releaseLabel": "emr-7.10.0",
            "networkConfiguration": {"subnetIds": ["subnet-source"]},
        },
    )
    vm = JobRunCloneVM(
        detail,
        client=_InMemoryEmr(),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        source=ServiceSourceContext("analytics", "production", "us-east-1"),
    )
    try:
        vm.apply_field("name", "edited-job")
        vm.apply_settings({"mode": "STREAMING", "executionTimeoutMinutes": 0})
        review = vm.review_text
        for value in [
            "r-001",
            "analytics",
            "production",
            "us-east-1",
            "00abc",
            detail.execution_role_arn,
            "nightly",
            "edited-job",
            "BATCH",
            "STREAMING",
            "30",
            "0",
            "Changed",
            "Inherited",
            "unknown",
            "emr-7.10.0",
            "subnet-source",
        ]:
            assert value in review
        assert "imageConfiguration" in review
        assert "workerTypeSpecifications" in review
        assert "executionIamPolicy" in review
    finally:
        vm.dispose()
