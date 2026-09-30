"""JobRunCloneVM — backs the EMR clone-job-run modal.

Pre-populates from a :class:`JobRunDetail`, lets the view bind the
five Spark fields plus advanced request settings, and (via :meth:`submit`) calls
``client.start_job_run`` to fire the re-run. On failure a
:class:`ProviderError` is re-raised so the modal can surface a
typed inline error without dismissing.

Lifecycle mirrors the other EMR VMs: a :class:`ComponentVM` inner
gives the construct/dispose plumbing; the public surface is
plain Python attributes + ``apply_field`` / ``submit`` / ``cancel``."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any
from uuid import uuid4

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_job_request import validate_clone_settings, validate_spark_driver
from aws_tui.domain.emr_serverless import EmrServerlessClientProtocol, JobRunDetail
from aws_tui.domain.filesystem import ValidationError
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.service_source_vm import ServiceSourceContext

# The five editable fields on the modal — kept as a tuple so
# ``apply_field`` rejects typos up front and the view can iterate
# them without re-stating the names.
_FIELDS: tuple[str, ...] = (
    "name",
    "execution_role_arn",
    "entry_point",
    "entry_point_arguments",
    "spark_submit_parameters",
)


class JobRunCloneVM:
    """Form-state + submit/cancel for the clone-job-run modal.

    The instance is single-shot: construct with the source detail,
    push the modal, await :meth:`submit` (or :meth:`cancel`), then
    dispose. Reusing across modals would require resetting the
    future and the form snapshot — the orchestrator builds a fresh
    VM per invocation instead, matching :class:`ConfirmationVM`'s
    contract.
    """

    def __init__(
        self,
        detail: JobRunDetail,
        *,
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
        self._settings: dict[str, Any] = {
            key: deepcopy(value)
            for key, value in {
                "configurationOverrides": detail.configuration_overrides,
                "executionTimeoutMinutes": detail.execution_timeout_minutes,
                "retryPolicy": detail.retry_policy,
                "mode": detail.mode,
                "executionIamPolicy": detail.execution_iam_policy,
                "tags": detail.tags,
            }.items()
            if value is not None
        }
        self._application_id: str = detail.application_id
        # Pre-populated form state. Tuple for arguments (immutable
        # snapshot the view can render row-per-line); str / None for
        # the rest.
        self._name: str | None = detail.name
        self._execution_role_arn: str = detail.execution_role_arn
        self._entry_point: str = detail.entry_point or ""
        self._entry_point_arguments: tuple[str, ...] = detail.entry_point_arguments
        self._spark_submit_parameters: str | None = detail.spark_submit_parameters
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
        self._disposed: bool = False
        self._inner: ComponentVM = (
            ComponentVM.builder().name("emr.job_run_clone").services(hub, dispatcher).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def application_id(self) -> str:
        return self._application_id

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def execution_role_arn(self) -> str:
        return self._execution_role_arn

    @property
    def entry_point(self) -> str:
        return self._entry_point

    @property
    def entry_point_arguments(self) -> tuple[str, ...]:
        return self._entry_point_arguments

    @property
    def spark_submit_parameters(self) -> str | None:
        return self._spark_submit_parameters

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def vm_name(self) -> str:
        return self._inner.name

    @property
    def source_detail(self) -> JobRunDetail:
        return deepcopy(self._source_detail)

    @property
    def settings(self) -> dict[str, Any]:
        return deepcopy(self._settings)

    @property
    def unsupported_driver_reason(self) -> str | None:
        try:
            validate_spark_driver(self._source_detail.job_driver)
        except ValidationError as exc:
            return str(exc)
        return None

    def apply_settings(self, settings: dict[str, Any]) -> None:
        candidate = deepcopy(settings)
        validate_clone_settings(candidate)
        if candidate != self._settings:
            self._settings = candidate
            self._client_token = uuid4().hex
        send_value_free(self._hub, PropertyChangedMessage.create(self, self.vm_name, "settings"))

    @property
    def review_text(self) -> str:
        """Complete user-facing comparison; never sent through diagnostics."""
        detail = self._source_detail
        source = self._source
        identity = (
            f"Connection: {source.connection_name}\n"
            f"Profile: {source.profile or 'default credential chain (profile unknown)'}\n"
            f"Region: {source.region}"
            if source is not None
            else "Connection / profile / region: unknown"
        )
        lines = [
            f"Source run: {detail.job_run_id}",
            identity,
            f"Application: {detail.application_id}",
            f"Source execution role: {detail.execution_role_arn}",
            "",
        ]
        pairs = {
            "Name": (detail.name, self._name),
            "Execution role": (detail.execution_role_arn, self._execution_role_arn),
            "Entry point": (detail.entry_point, self._entry_point),
            "Arguments": (detail.entry_point_arguments, self._entry_point_arguments),
            "Spark parameters": (detail.spark_submit_parameters, self._spark_submit_parameters),
            "configurationOverrides": (
                detail.configuration_overrides,
                self._settings.get("configurationOverrides"),
            ),
            "executionTimeoutMinutes": (
                detail.execution_timeout_minutes,
                self._settings.get("executionTimeoutMinutes"),
            ),
            "retryPolicy": (detail.retry_policy, self._settings.get("retryPolicy")),
            "mode": (detail.mode, self._settings.get("mode")),
            "executionIamPolicy": (
                detail.execution_iam_policy,
                self._settings.get("executionIamPolicy"),
            ),
            "tags": (detail.tags, self._settings.get("tags")),
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

    def apply_field(self, field_name: str, value: str | tuple[str, ...]) -> None:
        """Update ``field_name`` to ``value``.

        ``entry_point_arguments`` accepts ``tuple[str, ...]`` only;
        all other fields accept ``str``. ``name`` and
        ``spark_submit_parameters`` are optional — an empty string
        normalises to ``None`` so the boto3 call omits them.

        Raises ``KeyError`` for an unknown field name. Type
        mismatches raise ``TypeError`` — the caller is the view,
        which is type-checked.
        """
        if field_name not in _FIELDS:
            raise KeyError(f"unknown field {field_name!r}; valid: {_FIELDS}")
        before = self._intent()
        if field_name == "entry_point_arguments":
            if not isinstance(value, tuple):
                raise TypeError("entry_point_arguments must be a tuple[str, ...]")
            self._entry_point_arguments = value
        else:
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a str")
            if field_name == "name":
                self._name = value or None
            elif field_name == "execution_role_arn":
                self._execution_role_arn = value
            elif field_name == "entry_point":
                self._entry_point = value
            else:  # spark_submit_parameters
                self._spark_submit_parameters = value or None
        if self._intent() != before:
            self._client_token = uuid4().hex
        send_value_free(self._hub, PropertyChangedMessage.create(self, self.vm_name, field_name))

    def is_valid(self) -> tuple[bool, str | None]:
        """Cheap inline validation used by the modal before
        :meth:`submit` is awaited.

        Returns ``(True, None)`` when the form is submittable; otherwise
        ``(False, reason)`` with a value-free user-facing string. Require
        an active form, a supported source driver, valid advanced settings,
        an execution role and an entry point. AWS performs authorization
        and remaining service-side validation.
        """
        if self._disposed or self._cancelled:
            return False, "clone is no longer active"
        if self._source_is_current is not None and not self._source_is_current():
            return False, "The source changed. Reopen the clone from the current source"
        unsupported = self.unsupported_driver_reason
        if unsupported is not None:
            return False, unsupported
        try:
            validate_clone_settings(self._settings)
        except ValidationError as exc:
            return False, str(exc)
        if not self._execution_role_arn.strip():
            return False, "execution role ARN is required"
        if not self._entry_point.strip():
            return False, "entry point is required"
        return True, None

    # ── Async API ──────────────────────────────────────────────────────────

    async def submit(self) -> str:
        """Fire ``client.start_job_run`` with the current form state.

        Returns the new ``job_run_id`` on success. Re-raises any
        :class:`ProviderError` so the modal can render the error
        inline (without dismissing)."""
        valid, reason = self.is_valid()
        if not valid:
            raise ValidationError(reason or "clone form is invalid")
        settings = self.settings
        new_id: str = await self._client.start_job_run(
            self._application_id,
            execution_role_arn=self._execution_role_arn,
            entry_point=self._entry_point,
            entry_point_arguments=self._entry_point_arguments,
            spark_submit_parameters=self._spark_submit_parameters,
            client_token=self._client_token,
            name=self._name,
            configuration_overrides=settings.get("configurationOverrides"),
            execution_timeout_minutes=settings.get("executionTimeoutMinutes"),
            retry_policy=settings.get("retryPolicy"),
            mode=settings.get("mode"),
            execution_iam_policy=settings.get("executionIamPolicy"),
            tags=settings.get("tags"),
        )
        self._submitted_id = new_id
        self._client_token = uuid4().hex
        return new_id

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

    def _intent(self) -> tuple[object, ...]:
        return (
            self._name,
            self._execution_role_arn,
            self._entry_point,
            self._entry_point_arguments,
            self._spark_submit_parameters,
            deepcopy(self._settings),
        )

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._inner.dispose()


__all__ = ["JobRunCloneVM"]
