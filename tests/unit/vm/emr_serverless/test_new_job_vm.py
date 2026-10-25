"""Blank Spark submission has no source run and owns one immutable request intent."""

from __future__ import annotations

import asyncio
import json

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.emr_job_request import build_start_job_run_request
from aws_tui.domain.filesystem import ProviderUnreachableError, ValidationError
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from aws_tui.vm.service_source_vm import ServiceSourceContext


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.entered = asyncio.Event()
        self.release = None

    async def start_job_run(self, application_id, **kwargs):
        self.calls.append((application_id, kwargs))
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return "newrun001"


def make(*, current=lambda: True, application_id="application001"):
    client = RecordingClient()
    vm = JobRunCloneVM(
        None,
        application_id=application_id,
        client=client,
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        source=ServiceSourceContext(
            connection_name="sandbox", profile="sandbox-profile", region="us-east-1"
        ),
        source_is_current=current,
    )
    vm.construct()
    return vm, client


def fill(vm):
    vm.apply_field("execution_role_arn", "arn:aws:iam::123456789012:role/Synthetic")
    vm.apply_field("entry_point", "s3://synthetic/job.py")


def test_blank_mode_has_no_source_run_or_inherited_request_fields():
    vm, client = make()
    try:
        assert vm.source_detail is None
        assert vm.is_new_job
        assert vm.form_title == "New Spark job"
        assert vm.review_title == "Review new Spark job"
        assert vm.application_id == "application001"
        assert vm.execution_role_arn == vm.entry_point == ""
        assert vm.name is None
        assert vm.entry_point_arguments is None
        assert vm.spark_submit_parameters is None
        assert vm.settings == {}
        assert not vm.is_valid()[0]
        assert client.calls == []
    finally:
        vm.dispose()


@pytest.mark.parametrize("field", ["execution_role_arn", "entry_point"])
async def test_empty_required_field_prevents_submit(field):
    vm, client = make()
    fill(vm)
    vm.apply_field(field, "   ")
    try:
        with pytest.raises(ValidationError):
            await vm.submit()
        assert client.calls == []
    finally:
        vm.dispose()


async def test_blank_request_and_review_preserve_literal_arguments_and_settings():
    vm, client = make()
    fill(vm)
    arguments = ("", "a b", '"quoted"', "日本語\nline\r\nnext")
    vm.apply_field("name", "日本語 job")
    vm.apply_field("entry_point_arguments", arguments)
    vm.apply_field("spark_submit_parameters", '--conf spark.synthetic="literal value"\n')
    configuration = {
        "monitoringConfiguration": {"s3MonitoringConfiguration": {"logUri": "s3://synthetic/logs"}}
    }
    vm.apply_settings(
        {
            "configurationOverrides": configuration,
            "executionTimeoutMinutes": 0,
            "retryPolicy": {"maxAttempts": 2},
            "mode": "BATCH",
            "executionIamPolicy": {"policy": '{"Statement": []}'},
            "tags": {"purpose": "synthetic"},
        }
    )
    try:
        review = vm.review_text
        for value in (
            "sandbox",
            "sandbox-profile",
            "us-east-1",
            "application001",
            "Synthetic",
            "日本語",
        ):
            assert value in review
        assert "Source run:" not in review
        assert "Preserved:" not in review
        assert vm.client_token not in review
        assert "clientToken" in review
        assert "redacted" in review
        assert "Inherited" in review
        assert "unknown" in review
        payload = json.loads(review.split("Effective StartJobRun request:\n", 1)[1])
        expected = build_start_job_run_request(
            vm.application_id,
            execution_role_arn=vm.execution_role_arn,
            entry_point=vm.entry_point,
            entry_point_arguments=arguments,
            spark_submit_parameters=vm.spark_submit_parameters,
            client_token="<app-owned; redacted>",
            name="日本語 job",
            configuration_overrides=configuration,
            execution_timeout_minutes=0,
            retry_policy={"maxAttempts": 2},
            execution_iam_policy={"policy": '{"Statement": []}'},
            mode="BATCH",
            tags={"purpose": "synthetic"},
        )
        assert payload == expected
        assert await vm.submit() == "newrun001"
        app_id, request = client.calls[0]
        assert app_id == "application001"
        assert request["entry_point_arguments"] == arguments
        assert request["mode"] == "BATCH"
        assert request["tags"] == {"purpose": "synthetic"}
        assert request["configuration_overrides"] == configuration
        assert request["execution_timeout_minutes"] == 0
        assert request["retry_policy"] == {"maxAttempts": 2}
        assert request["execution_iam_policy"] == {"policy": '{"Statement": []}'}
    finally:
        vm.dispose()


async def test_ambiguous_retry_reuses_token_and_edits_create_new_intent():
    vm, client = make()
    fill(vm)
    client.failure = ProviderUnreachableError("synthetic lost response")
    try:
        with pytest.raises(ProviderUnreachableError):
            await vm.submit()
        first = client.calls[-1][1]["client_token"]
        vm.apply_field("entry_point", vm.entry_point)
        with pytest.raises(ProviderUnreachableError):
            await vm.submit()
        assert client.calls[-1][1]["client_token"] == first
        vm.apply_field("entry_point_arguments", ("new argument",))
        assert vm.client_token != first
        client.failure = None
        await vm.submit()
        assert client.calls[-1][1]["client_token"] != first
    finally:
        vm.dispose()


async def test_concurrent_submit_is_refused_and_old_completion_keeps_newer_token():
    vm, client = make()
    fill(vm)
    client.release = asyncio.Event()
    first = asyncio.create_task(vm.submit())
    await client.entered.wait()
    try:
        with pytest.raises(ValidationError):
            await vm.submit()
        assert len(client.calls) == 1
        vm.apply_field("entry_point_arguments", ("new intent",))
        edited_token = vm.client_token
        client.release.set()
        await first
        assert vm.client_token == edited_token
        assert client.calls[0][1]["entry_point_arguments"] is None
    finally:
        client.release.set()
        await first
        vm.dispose()


async def test_stale_application_is_rejected_without_submission():
    current = True
    vm, client = make(current=lambda: current)
    fill(vm)
    current = False
    try:
        with pytest.raises(ValidationError, match="source"):
            await vm.submit()
        assert client.calls == []
    finally:
        vm.dispose()


@pytest.mark.parametrize("invalidate", ["source", "cancel", "dispose"])
@pytest.mark.parametrize("edit", [False, True])
async def test_late_completion_does_not_publish_into_inactive_form(invalidate, edit):
    current = True
    vm, client = make(current=lambda: current)
    fill(vm)
    client.release = asyncio.Event()
    token = vm.client_token
    pending = asyncio.create_task(vm.submit())
    await client.entered.wait()
    try:
        if edit:
            vm.apply_field("entry_point_arguments", ("new intent",))
            token = vm.client_token
        if invalidate == "source":
            current = False
        else:
            getattr(vm, invalidate)()
        client.release.set()
        assert await pending == "newrun001"
        assert vm.submitted_id is None
        assert vm.client_token == token
    finally:
        client.release.set()
        await pending
        vm.dispose()


@pytest.mark.parametrize("application_id", [None, "", "   "])
async def test_blank_mode_without_application_cannot_submit(application_id):
    vm, client = make(application_id=application_id)
    fill(vm)
    try:
        with pytest.raises(ValidationError, match="application"):
            await vm.submit()
        assert client.calls == []
    finally:
        vm.dispose()


async def test_submit_snapshots_every_field_and_settings_before_await():
    vm, client = make()
    fill(vm)
    vm.apply_field("name", "original")
    vm.apply_field("entry_point_arguments", ("original argument",))
    vm.apply_field("spark_submit_parameters", "--conf original=value")
    vm.apply_settings({"tags": {"intent": "original"}, "executionTimeoutMinutes": 10})
    client.release = asyncio.Event()
    token = vm.client_token
    pending = asyncio.create_task(vm.submit())
    await client.entered.wait()
    try:
        for field, value in (
            ("name", "edited"),
            ("execution_role_arn", "edited role"),
            ("entry_point", "edited entry"),
            ("entry_point_arguments", ("edited argument",)),
            ("spark_submit_parameters", "--conf edited=value"),
        ):
            vm.apply_field(field, value)
        vm.apply_settings({"tags": {"intent": "edited"}})
        assert client.calls == [
            (
                "application001",
                {
                    "execution_role_arn": "arn:aws:iam::123456789012:role/Synthetic",
                    "entry_point": "s3://synthetic/job.py",
                    "entry_point_arguments": ("original argument",),
                    "spark_submit_parameters": "--conf original=value",
                    "client_token": token,
                    "name": "original",
                    "configuration_overrides": None,
                    "execution_timeout_minutes": 10,
                    "retry_policy": None,
                    "mode": None,
                    "execution_iam_policy": None,
                    "tags": {"intent": "original"},
                },
            )
        ]
        edited_token = vm.client_token
        client.release.set()
        await pending
        assert vm.client_token == edited_token
    finally:
        client.release.set()
        await pending
        vm.dispose()
