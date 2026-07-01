"""JobRunCloneModal — bind the EMR clone form to ``JobRunCloneVM``.

Pre-populated form: five fields (name, exec-role ARN, entry point,
entry-point arguments, spark submit parameters) the user can edit
before pressing Submit. Submit calls ``vm.submit()`` and dismisses
the modal with the new ``job_run_id`` on success; a
:class:`ProviderError` keeps the modal open and surfaces the error
text inline at the bottom. Cancel always dismisses with ``None``.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, Static, TextArea
from vmx import Message, MessageHub

from aws_tui.domain.filesystem import ProviderError
from aws_tui.infra.redaction import redact_text
from aws_tui.ui.widgets.modal_button import ModalButton as _ModalButton
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM


class JobRunCloneModal(ModalScreen[str | None]):
    """Modal form bound to :class:`JobRunCloneVM`.

    Dismiss values: the new ``job_run_id`` on a successful submit,
    or ``None`` on cancel/escape. The error path keeps the modal
    open — a small red Static below the footer shows the typed
    :class:`ProviderError` message.
    """

    DEFAULT_CSS: ClassVar[str] = """
    JobRunCloneModal > Container {
        width: 80;
        max-width: 90%;
        height: auto;
        padding: 1 2;
    }
    JobRunCloneModal Input,
    JobRunCloneModal TextArea {
        margin-bottom: 1;
    }
    JobRunCloneModal TextArea {
        height: 5;
    }
    JobRunCloneModal .modal-error {
        color: $error;
        margin-top: 1;
    }
    JobRunCloneModal .modal-field-label {
        margin-top: 1;
    }
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        vm: JobRunCloneVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: JobRunCloneVM = vm
        self._hub: MessageHub[Message] = hub
        self._error: Static | None = None
        # Re-entrancy guard: ``submit`` awaits a multi-hundred-ms
        # ``start_job_run`` round-trip. Without this flag a second
        # click on the Submit button while the first is in flight
        # would launch a SECOND EMR job for one user intent —
        # billing impact, not just UI sloppiness.
        self._submitting: bool = False

    @property
    def vm(self) -> JobRunCloneVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Clone job run", classes="modal-title")

            yield Static("Name (optional)", classes="modal-field-label")
            yield Input(
                value=self._vm.name or "",
                placeholder="optional run name",
                id="clone-name",
            )

            yield Static("Execution role ARN", classes="modal-field-label")
            yield Input(
                value=self._vm.execution_role_arn,
                placeholder="arn:aws:iam::123456789012:role/EmrJobRole",
                id="clone-role",
            )

            yield Static("Entry point (s3:// URL)", classes="modal-field-label")
            yield Input(
                value=self._vm.entry_point,
                placeholder="s3://bucket/path/to/job.py",
                id="clone-entry",
            )

            yield Static("Entry point arguments (one per line)", classes="modal-field-label")
            yield TextArea(
                "\n".join(self._vm.entry_point_arguments),
                id="clone-args",
            )

            yield Static("Spark submit parameters (optional)", classes="modal-field-label")
            yield TextArea(
                self._vm.spark_submit_parameters or "",
                id="clone-spark",
            )

            with Horizontal(classes="modal-footer"):
                yield _ModalButton("Cancel", button_id="cancel")
                yield _ModalButton("Submit", button_id="submit", classes="-primary")

            # ``markup=False`` — _show_error() feeds caller exception
            # text into update(). Boto / OSError stringifications
            # contain ``[…]`` (``[ErrorCode]``, ``[Errno 13]``,
            # ``[ContainerError(...)]``) that crash Rich markup
            # parsing. Sibling JobRunDetailPane / JobRunLogsPane
            # already guard the same content; this was the asymmetric
            # holdout.
            self._error = Static("", classes="modal-error", markup=False)
            yield self._error

    # ── Form-state sync ────────────────────────────────────────────────────

    def _sync_form_to_vm(self) -> None:
        """Copy every Input / TextArea value back into the VM.

        Run right before :meth:`submit` so the VM holds the
        user's edits without needing a per-keystroke binding.
        ``entry_point_arguments`` is split on newlines and trimmed
        of trailing/leading blanks (so the trailing newline a
        TextArea inserts doesn't become a phantom empty argument).
        """
        self._vm.apply_field("name", self.query_one("#clone-name", Input).value)
        self._vm.apply_field("execution_role_arn", self.query_one("#clone-role", Input).value)
        self._vm.apply_field("entry_point", self.query_one("#clone-entry", Input).value)
        args_text = self.query_one("#clone-args", TextArea).text
        args = tuple(line for line in args_text.splitlines() if line.strip())
        self._vm.apply_field("entry_point_arguments", args)
        self._vm.apply_field(
            "spark_submit_parameters", self.query_one("#clone-spark", TextArea).text.strip()
        )

    # ── Actions ────────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        self._vm.cancel()
        self.dismiss(None)

    async def action_submit(self) -> None:
        if self._submitting:
            # Second click while the first submit's start_job_run
            # round-trip is in flight — silently drop instead of
            # double-launching the EMR job.
            return
        self._sync_form_to_vm()
        ok, reason = self._vm.is_valid()
        if not ok:
            self._show_error(reason or "form is invalid")
            return
        self._submitting = True
        try:
            try:
                new_id = await self._vm.submit()
            except ProviderError as exc:
                text = str(exc)
                self._show_error(redact_text(text) if text else type(exc).__name__)
                return
            except Exception as exc:
                # Defensive net for non-ProviderError raises (e.g. a
                # botocore parameter-validation error that escapes
                # the facade's mapping, a programmer error in
                # clone_vm, or any future regression). Stay in the
                # modal — surface the message inline so the user
                # can correct the form or cancel — instead of
                # letting the exception crash through Textual's
                # default error handler and bring down the EMR page.
                self._show_error(redact_text(f"unexpected error: {exc}"))
                return
        finally:
            # Clear the guard before dismiss so the next clone
            # opens fresh. On the success path this is moot (the
            # modal is about to unmount); on the error paths above
            # it lets the user retry after editing the form.
            self._submitting = False
        self.dismiss(new_id)

    async def on_click(self, event: Click) -> None:
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, _ModalButton):
                if node.button_id == "submit":
                    await self.action_submit()
                else:
                    self.action_cancel()
                return
            node = getattr(node, "parent", None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _show_error(self, message: str) -> None:
        if self._error is not None:
            self._error.update(message)


__all__ = ["JobRunCloneModal"]
