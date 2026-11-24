"""JobRunCloneVM — backs the EMR Spark job edit/review modal.

Pre-populates from a :class:`JobRunDetail` or starts blank for a captured
application ID, lets the view bind the five Spark fields plus advanced request
settings, and (via :meth:`submit`) calls ``client.start_job_run``. On failure a
:class:`ProviderError` is re-raised so the modal can surface a
typed inline error without dismissing. Edits use :meth:`set_model`.

Lifecycle mirrors the other EMR VMs: a :class:`ComponentVM` inner
gives the construct/dispose plumbing; a private :class:`FormVM` holds
the editable request and its validation state."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from vmx import (
    ComponentVM,
    ConstructionStatus,
    Dispatcher,
    FormVM,
    FormVMBuilder,
    Message,
    MessageHub,
    PropertyChangedMessage,
)

from aws_tui.domain.emr_job_request import (
    build_start_job_run_request,
    validate_clone_settings,
    validate_spark_driver,
)
from aws_tui.domain.emr_serverless import EmrServerlessClientProtocol, JobRunDetail
from aws_tui.domain.filesystem import ValidationError
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.service_source_vm import ServiceSourceContext

_ERROR_ORDER = ("settings", "execution_role_arn", "entry_point")


@dataclass(frozen=True, slots=True, repr=False)
class JobRunCloneInput:
    """Editable Spark request fields for a new job or clone."""

    name: str | None
    execution_role_arn: str
    entry_point: str
    entry_point_arguments: tuple[str, ...] | None
    spark_submit_parameters: str | None
    settings: dict[str, Any]

    def __post_init__(self) -> None:
        if self.name is not None and not isinstance(self.name, str):
            raise TypeError("name must be a str or None")
        if not isinstance(self.execution_role_arn, str):
            raise TypeError("execution_role_arn must be a str")
        if not isinstance(self.entry_point, str):
            raise TypeError("entry_point must be a str")
        args = self.entry_point_arguments
        if args is not None and (
            not isinstance(args, tuple) or not all(isinstance(arg, str) for arg in args)
        ):
            raise TypeError("entry_point_arguments must be a tuple[str, ...] or None")
        if self.spark_submit_parameters is not None and not isinstance(
            self.spark_submit_parameters, str
        ):
            raise TypeError("spark_submit_parameters must be a str or None")
        if not isinstance(self.settings, dict):
            raise TypeError("settings must be a dict")

    def __repr__(self) -> str:
        return "JobRunCloneInput(<redacted>)"


def _initial_input(detail: JobRunDetail | None) -> JobRunCloneInput:
    if detail is None:
        return JobRunCloneInput(
            name=None,
            execution_role_arn="",
            entry_point="",
            entry_point_arguments=None,
            spark_submit_parameters=None,
            settings={},
        )
    settings = {
        "configurationOverrides": detail.configuration_overrides,
        "executionTimeoutMinutes": detail.execution_timeout_minutes,
        "retryPolicy": detail.retry_policy,
        "mode": detail.mode,
        "executionIamPolicy": detail.execution_iam_policy,
        "tags": detail.tags,
    }
    return JobRunCloneInput(
        name=detail.name,
        execution_role_arn=detail.execution_role_arn,
        entry_point=detail.entry_point or "",
        entry_point_arguments=detail.entry_point_arguments,
        spark_submit_parameters=detail.spark_submit_parameters,
        settings={key: deepcopy(value) for key, value in settings.items() if value is not None},
    )


def _settings_error(form: JobRunCloneInput) -> str | None:
    try:
        validate_clone_settings(deepcopy(form.settings))
    except ValidationError as exc:
        return str(exc)
    return None


async def _no_persist(_: JobRunCloneInput) -> None:
    """Submit sends StartJobRun directly so the controller owns the run ID and token."""


class JobRunCloneVM:
    """Form-state + submit/cancel for the Spark job edit/review modal.

    The instance is single-shot: construct with a source detail or application ID,
    push the modal, await :meth:`submit` (or :meth:`cancel`), then
    dispose. Reusing across modals would require resetting the
    future and the form snapshot — the orchestrator builds a fresh
    VM per invocation instead, matching :class:`ConfirmationVM`'s
    contract.
    """

    def __init__(
        self,
        detail: JobRunDetail | None,
        *,
        application_id: str | None = None,
        client: EmrServerlessClientProtocol,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        source: ServiceSourceContext | None = None,
        source_is_current: Callable[[], bool] | None = None,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._source_detail = deepcopy(detail)
        self._source = source
        self._source_is_current = source_is_current
        self._application_id: str = (
            detail.application_id if detail is not None else application_id or ""
        )
        # Keep the form on its default null hub. Only the facade sends edits to the page hub.
        builder: FormVMBuilder[JobRunCloneInput] = FormVM.builder()
        self._form: FormVM[JobRunCloneInput] = (
            builder.initial(_initial_input(self._source_detail))
            .persister(_no_persist)
            .validator("settings", _settings_error)
            .validator(
                "execution_role_arn",
                lambda form: (
                    None if form.execution_role_arn.strip() else "execution role ARN is required"
                ),
            )
            .validator(
                "entry_point",
                lambda form: None if form.entry_point.strip() else "entry point is required",
            )
            .build()
        )
        # One idempotency token per form intent. Reused verbatim when a
        # submit attempt raises (the ambiguous-retry case AWS's clientToken
        # exists for), rotated when a field value changes or a submit
        # succeeds, because either of those is a new intent.
        self._client_token: str = uuid4().hex
        # Caller may call :meth:`cancel` for symmetry with the other
        # modal VMs (Confirm / Crash); the page widget
        # itself reads the modal's dismiss value rather than awaiting
        # a VM-side future, so there's no Future to resolve here.
        self._cancelled: bool = False
        self._submitted_id: str | None = None
        self._submitting: bool = False
        self._disposed: bool = False
        self._inner: ComponentVM = (
            ComponentVM.builder().name("emr.job_run_clone").services(hub, dispatcher).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def application_id(self) -> str:
        return self._application_id

    @property
    def is_new_job(self) -> bool:
        return self._source_detail is None

    @property
    def form_title(self) -> str:
        return "New Spark job" if self.is_new_job else "Clone job run"

    @property
    def review_title(self) -> str:
        return "Review new Spark job" if self.is_new_job else "Review clone job run"

    @property
    def name(self) -> str | None:
        return self._form.model.name

    @property
    def execution_role_arn(self) -> str:
        return self._form.model.execution_role_arn

    @property
    def entry_point(self) -> str:
        return self._form.model.entry_point

    @property
    def entry_point_arguments(self) -> tuple[str, ...] | None:
        return self._form.model.entry_point_arguments

    @property
    def spark_submit_parameters(self) -> str | None:
        return self._form.model.spark_submit_parameters

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def vm_name(self) -> str:
        return self._inner.name

    @property
    def source_detail(self) -> JobRunDetail | None:
        return deepcopy(self._source_detail)

    @property
    def model(self) -> JobRunCloneInput:
        model = self._form.model
        return replace(model, settings=deepcopy(model.settings))

    @property
    def settings(self) -> dict[str, Any]:
        return deepcopy(self._form.model.settings)

    @property
    def errors(self) -> dict[str, str]:
        return self._form.errors

    @property
    def unsupported_driver_reason(self) -> str | None:
        if self._source_detail is None:
            return None
        try:
            validate_spark_driver(self._source_detail.job_driver)
        except ValidationError as exc:
            return str(exc)
        return None

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.set_model(replace(self._form.model, settings=settings))

    @property
    def review_text(self) -> str:
        """Complete request or source comparison; never sent through diagnostics."""
        detail = self._source_detail
        model = self._form.model
        source = self._source
        identity = (
            f"Connection: {source.connection_name}\n"
            f"Profile: {source.profile or 'default credential chain (profile unknown)'}\n"
            f"Region: {source.region}"
            if source is not None
            else "Connection / profile / region: unknown"
        )
        if detail is None:
            request = build_start_job_run_request(
                self._application_id,
                **self._request_fields(model, client_token="<app-owned; redacted>"),
            )
            return "\n".join(
                [
                    identity,
                    f"Application: {self._application_id}",
                    f"Execution role: {model.execution_role_arn}",
                    "",
                    "Application defaults: Inherited from current application; effective values unknown.",
                    "Omitted request settings: unknown; application or service defaults may apply.",
                    "clientToken: app-owned idempotency token; opaque value redacted.",
                    "",
                    "Effective StartJobRun request:",
                    json.dumps(request, ensure_ascii=False, indent=2),
                ]
            )
        lines = [
            f"Source run: {detail.job_run_id}",
            identity,
            f"Application: {detail.application_id}",
            f"Source execution role: {detail.execution_role_arn}",
            "",
        ]
        pairs = {
            "Name": (detail.name, model.name),
            "Execution role": (detail.execution_role_arn, model.execution_role_arn),
            "Entry point": (detail.entry_point, model.entry_point),
            "Arguments": (detail.entry_point_arguments, model.entry_point_arguments),
            "Spark parameters": (detail.spark_submit_parameters, model.spark_submit_parameters),
            "configurationOverrides": (
                detail.configuration_overrides,
                model.settings.get("configurationOverrides"),
            ),
            "executionTimeoutMinutes": (
                detail.execution_timeout_minutes,
                model.settings.get("executionTimeoutMinutes"),
            ),
            "retryPolicy": (detail.retry_policy, model.settings.get("retryPolicy")),
            "mode": (detail.mode, model.settings.get("mode")),
            "executionIamPolicy": (
                detail.execution_iam_policy,
                model.settings.get("executionIamPolicy"),
            ),
            "tags": (detail.tags, model.settings.get("tags")),
        }
        for label, (before, after) in pairs.items():
            if before is None and after is None:
                lines.append(f"{label}: unknown / not supplied; omitted, defaults may apply")
            elif before == after:
                lines.append(
                    f"{label} — Preserved: {json.dumps(after, ensure_ascii=False, indent=2)}"
                )
            else:
                lines.extend(
                    [
                        f"{label} — Changed",
                        f"Source: {json.dumps(before, ensure_ascii=False, indent=2)}",
                        f"Proposed: {json.dumps(after, ensure_ascii=False, indent=2)}",
                    ]
                )
                if after is None:
                    lines.append("Omitted; effective default is unknown")
            lines.append("")
        for key in (
            "releaseLabel",
            "networkConfiguration",
            "imageConfiguration",
            "workerTypeSpecifications",
        ):
            original = detail.source_application_settings.get(key)
            display = (
                json.dumps(original, ensure_ascii=False, indent=2)
                if original is not None
                else "unknown"
            )
            lines.extend(
                [
                    f"{key} — Inherited from current application; equality unknown",
                    f"Source: {display}",
                    "",
                ]
            )
        lines.extend(
            [
                "Hidden application defaults: unknown; not inferred from this source run.",
                "Run id, ARN, creator, status, timestamps, attempts and resource usage:",
                "read-only outputs, not copied; new-run values unknown until AWS returns them.",
            ]
        )
        return "\n".join(lines)

    # ── Form API ────────────────────────────────────────────────────────────

    def set_model(self, model: JobRunCloneInput) -> None:
        """Apply an edit atomically, retaining the token for the same request."""
        settings = deepcopy(model.settings)
        validate_clone_settings(settings)
        candidate = replace(
            model,
            name=model.name or None,
            spark_submit_parameters=model.spark_submit_parameters or None,
            settings=settings,
        )
        before = self._form.model
        self._form.set_model(candidate)
        if self._form.model != before:
            self._client_token = uuid4().hex
        send_value_free(self._hub, PropertyChangedMessage.create(self, self.vm_name, "model"))

    def is_valid(self) -> tuple[bool, str | None]:
        """Cheap inline validation used by the modal before
        :meth:`submit` is awaited.

        Returns ``(True, None)`` when the form is submittable; otherwise
        ``(False, reason)`` with a value-free user-facing string. Require
        an active form, a supported source driver, valid advanced settings,
        an execution role and an entry point. AWS performs authorization
        and remaining service-side validation.
        """
        reason = self._guard_reason()
        if reason is None:
            errors = self._form.errors
            reason = next((errors[key] for key in _ERROR_ORDER if key in errors), None)
        return reason is None, reason

    def _guard_reason(self) -> str | None:
        if self._disposed or self._cancelled:
            return "job form is no longer active"
        if self._source_is_current is not None and not self._source_is_current():
            return "The source changed. Reopen the job form from the current source"
        if not self._application_id.strip():
            return "application ID is required"
        return self.unsupported_driver_reason

    # ── Async API ──────────────────────────────────────────────────────────

    async def submit(self) -> str:
        """Fire ``client.start_job_run`` with the current form state.

        Returns the new ``job_run_id`` on success. Re-raises any
        :class:`ProviderError` so the modal can render the error
        inline (without dismissing)."""
        if self._submitting:
            raise ValidationError("A job submission is already in progress")
        valid, reason = self.is_valid()
        if not valid:
            raise ValidationError(reason or "job form is invalid")
        application_id = self._application_id
        model = self.model
        token = self._client_token
        fields = self._request_fields(model, client_token=token)
        self._submitting = True
        try:
            new_id: str = await self._client.start_job_run(application_id, **fields)
        finally:
            self._submitting = False
        if (
            not self._disposed
            and not self._cancelled
            and (self._source_is_current is None or self._source_is_current())
        ):
            self._submitted_id = new_id
            # An edit during the await owns a newer token. Completing the old
            # request must not replace it, including after an ambiguous retry.
            if self._client_token == token:
                self._client_token = uuid4().hex
        return new_id

    @staticmethod
    def _request_fields(model: JobRunCloneInput, *, client_token: str) -> dict[str, Any]:
        settings = model.settings
        return {
            "execution_role_arn": model.execution_role_arn,
            "entry_point": model.entry_point,
            "entry_point_arguments": model.entry_point_arguments,
            "spark_submit_parameters": model.spark_submit_parameters,
            "client_token": client_token,
            "name": model.name,
            "configuration_overrides": settings.get("configurationOverrides"),
            "execution_timeout_minutes": settings.get("executionTimeoutMinutes"),
            "retry_policy": settings.get("retryPolicy"),
            "mode": settings.get("mode"),
            "execution_iam_policy": settings.get("executionIamPolicy"),
            "tags": settings.get("tags"),
        }

    def cancel(self) -> None:
        """Mark the VM as cancelled. The modal widget's own dismiss
        path is what surfaces the ``None`` outcome — this flag is
        provided for symmetry with the other modal VMs (and is read
        by :attr:`cancelled` for tests that drive the VM in
        isolation)."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def submitted_id(self) -> str | None:
        return self._submitted_id

    @property
    def client_token(self) -> str:
        return self._client_token

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._form.dispose()
        self._inner.dispose()


__all__ = ["JobRunCloneInput", "JobRunCloneVM"]
