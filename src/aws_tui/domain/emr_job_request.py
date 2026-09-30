"""Explicit, value-free validation of EMR Serverless clone requests."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

from botocore.exceptions import ParamValidationError
from botocore.model import Shape
from botocore.session import get_session
from botocore.validate import validate_parameters

from aws_tui.domain.filesystem import ValidationError

CLONE_SETTING_KEYS = frozenset(
    {
        "configurationOverrides",
        "executionTimeoutMinutes",
        "retryPolicy",
        "mode",
        "executionIamPolicy",
        "tags",
    }
)


@lru_cache(maxsize=1)
def _request_shape() -> Shape:
    return (
        get_session().get_service_model("emr-serverless").operation_model("StartJobRun").input_shape
    )


def _reject_bool_numbers(value: Any, shape: Shape) -> None:
    # Python bool is an int subclass, but JSON true is not an AWS duration
    # or retry count. Botocore's integer validator accepts that subclass.
    if shape.type_name in {"integer", "long"} and isinstance(value, bool):
        raise ValueError
    if shape.type_name == "structure" and isinstance(value, dict):
        for key, item in value.items():
            if key in shape.members:
                _reject_bool_numbers(item, shape.members[key])
    elif shape.type_name == "list" and isinstance(value, (list, tuple)):
        for item in value:
            _reject_bool_numbers(item, shape.member)
    elif shape.type_name == "map" and isinstance(value, dict):
        for item in value.values():
            _reject_bool_numbers(item, shape.value)


def validate_clone_settings(settings: dict[str, Any]) -> None:
    """Reject unsupported settings without including any supplied values.

    Use the installed SDK's complete nested shapes, rather than filtering out
    unrecognized keys. A newer AWS field unsupported by this SDK must block.
    """
    if settings.keys() - CLONE_SETTING_KEYS:
        raise ValidationError("Unsupported advanced setting; use only StartJobRun clone settings")
    if "mode" in settings and settings["mode"] not in ("BATCH", "STREAMING"):
        raise ValidationError("Unsupported job mode; only BATCH and STREAMING can be cloned")
    shape = _request_shape()
    for key, value in settings.items():
        try:
            member = shape.members[key]
            _reject_bool_numbers(value, member)
            validate_parameters(value, member)
        except (ParamValidationError, ValueError, TypeError, KeyError):
            # SDK validation messages include rejected values. Never expose
            # them through logs, diagnostics, observable state or exception chains.
            raise ValidationError(
                f"Unsupported or invalid {key} setting for the installed SDK"
            ) from None


def build_start_job_run_request(
    application_id: str,
    *,
    execution_role_arn: str,
    entry_point: str,
    entry_point_arguments: tuple[str, ...],
    spark_submit_parameters: str | None,
    client_token: str,
    name: str | None = None,
    configuration_overrides: dict[str, Any] | None = None,
    execution_timeout_minutes: int | None = None,
    retry_policy: dict[str, Any] | None = None,
    mode: str | None = None,
    execution_iam_policy: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    settings = {
        key: deepcopy(value)
        for key, value in {
            "configurationOverrides": configuration_overrides,
            "executionTimeoutMinutes": execution_timeout_minutes,
            "retryPolicy": retry_policy,
            "mode": mode,
            "executionIamPolicy": execution_iam_policy,
            "tags": tags,
        }.items()
        if value is not None
    }
    validate_clone_settings(settings)
    spark: dict[str, Any] = {
        "entryPoint": entry_point,
        "entryPointArguments": list(entry_point_arguments),
    }
    if spark_submit_parameters is not None:
        spark["sparkSubmitParameters"] = spark_submit_parameters
    request: dict[str, Any] = {
        "applicationId": application_id,
        "executionRoleArn": execution_role_arn,
        "jobDriver": {"sparkSubmit": spark},
        "clientToken": client_token,
        **settings,
    }
    if name is not None:
        request["name"] = name
    return request
