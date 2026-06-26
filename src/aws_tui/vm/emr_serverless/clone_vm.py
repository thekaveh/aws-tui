"""JobRunCloneVM — backs the EMR clone-job-run modal.

Pre-populates from a :class:`JobRunDetail`, lets the view bind the
five editable fields, and (via :meth:`submit`) calls
``client.start_job_run`` to fire the re-run. On failure a
:class:`ProviderError` is re-raised so the modal can surface a
typed inline error without dismissing.

Lifecycle mirrors the other EMR VMs: a :class:`ComponentVM` inner
gives the construct/dispose plumbing; the public surface is
plain Python attributes + ``apply_field`` / ``submit`` / ``cancel``."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunDetail

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
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._application_id: str = detail.application_id
        # Pre-populated form state. Tuple for arguments (immutable
        # snapshot the view can render row-per-line); str / None for
        # the rest.
        self._name: str | None = detail.name
        self._execution_role_arn: str = detail.execution_role_arn
        self._entry_point: str = detail.entry_point or ""
        self._entry_point_arguments: tuple[str, ...] = detail.entry_point_arguments
        self._spark_submit_parameters: str | None = detail.spark_submit_parameters
        # Caller may call :meth:`cancel` for symmetry with the other
        # modal VMs (Confirm / Resume / FirstRun); the page widget
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
        self._hub.send(PropertyChangedMessage.create(self, self.vm_name, field_name))

    def is_valid(self) -> tuple[bool, str | None]:
        """Cheap inline validation used by the modal before
        :meth:`submit` is awaited.

        Returns ``(True, None)`` when the form is submittable; otherwise
        ``(False, reason)`` with a short user-facing string. We only
        block on the two AWS-required fields (``executionRoleArn`` and
        ``jobDriver.sparkSubmit.entryPoint``) — deeper validation is
        deferred to the AWS API itself, which will reply with a typed
        ``ValidationError`` that the modal also surfaces inline.
        """
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
        new_id: str = await self._client.start_job_run(
            self._application_id,
            execution_role_arn=self._execution_role_arn,
            entry_point=self._entry_point,
            entry_point_arguments=self._entry_point_arguments,
            spark_submit_parameters=self._spark_submit_parameters,
            name=self._name,
        )
        self._submitted_id = new_id
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

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._inner.dispose()


__all__ = ["JobRunCloneVM"]
