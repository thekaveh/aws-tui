"""Bounded edit/review modal for faithful EMR Serverless Spark clones."""

from __future__ import annotations

import json
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, Static, TextArea
from vmx import Message, MessageHub

from aws_tui.domain.emr_job_request import validate_clone_settings
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)
from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.modal_button import ModalButton as _ModalButton
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM


def _submission_error(exc: Exception) -> str:
    # Provider/SDK exception strings may echo arbitrary arguments and policies.
    # Keep actionable categories without copying those strings into diagnostics.
    if isinstance(exc, AuthRequiredError):
        return "Authentication required. Reauthenticate the selected profile (SSO: aws sso login), then retry."
    if isinstance(exc, PermissionDeniedError):
        return "Access denied. Check the execution role and StartJobRun permission."
    if isinstance(exc, ProviderUnreachableError):
        return (
            "No response from AWS. Retry this unchanged review to reuse the same submission token."
        )
    if isinstance(exc, ThrottledError):
        return "AWS throttled the request. Wait briefly, then retry this review."
    if isinstance(exc, ValidationError):
        return "AWS rejected the job settings. Go Back to review the role, entry point and configuration."
    if isinstance(exc, ProviderError):
        return "AWS could not complete the request. Retry unchanged, or go Back to review settings."
    return (
        "An unexpected error prevented submission. Retry unchanged, or go Back to review settings."
    )


class JobRunCloneModal(DeferredWorkerMixin, ModalScreen[str | None]):
    """Edit first, then review a fixed intent before starting a new run."""

    DEFAULT_CSS: ClassVar[str] = """
    JobRunCloneModal > Container {
        width: 100;
        max-width: 90%;
        height: 90%;
        max-height: 42;
        padding: 1 2;
    }
    JobRunCloneModal .modal-title { height: 1; }
    JobRunCloneModal VerticalScroll { height: 1fr; }
    JobRunCloneModal Input, JobRunCloneModal TextArea { margin-bottom: 1; }
    JobRunCloneModal TextArea { height: 5; }
    JobRunCloneModal #clone-settings { height: 8; }
    JobRunCloneModal .modal-footer { height: 3; }
    JobRunCloneModal .modal-error {
        color: $error;
        height: auto;
        max-height: 4;
    }
    JobRunCloneModal .modal-field-label { height: auto; }
    JobRunCloneModal #clone-review { height: auto; }
    JobRunCloneModal #clone-progress { height: auto; max-height: 3; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012

    def __init__(self, vm: JobRunCloneVM, *, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub
        self._error: Static | None = None
        self._submitting = False
        self._reviewed_token: str | None = None
        self._reviewed_form: tuple[str, ...] | None = None

    @property
    def vm(self) -> JobRunCloneVM:
        return self._vm

    @property
    def reviewing(self) -> bool:
        return self._reviewed_token is not None

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Clone job run", classes="modal-title", id="clone-title")
            with VerticalScroll(id="clone-edit"):
                yield Static("Name (optional)", classes="modal-field-label")
                yield Input(
                    value=self._vm.name or "", placeholder="optional run name", id="clone-name"
                )
                yield Static("Execution role ARN", classes="modal-field-label")
                yield Input(value=self._vm.execution_role_arn, id="clone-role")
                yield Static("Entry point (s3:// URL)", classes="modal-field-label")
                yield Input(value=self._vm.entry_point, id="clone-entry")
                yield Static(
                    "Entry point arguments (JSON array of strings)", classes="modal-field-label"
                )
                yield TextArea(
                    json.dumps(self._vm.entry_point_arguments, ensure_ascii=False, indent=2),
                    id="clone-args",
                )
                yield Static(
                    "Spark submit parameters (optional, preserved exactly)",
                    classes="modal-field-label",
                )
                yield TextArea(self._vm.spark_submit_parameters or "", id="clone-spark")
                yield Static(
                    "Advanced settings (JSON object; remove a key to omit it)",
                    classes="modal-field-label",
                )
                yield TextArea(
                    json.dumps(self._vm.settings, ensure_ascii=False, indent=2), id="clone-settings"
                )
            with VerticalScroll(id="clone-review-scroll") as review:
                review.display = False
                yield Static("", id="clone-review", markup=False)
            yield Static("", id="clone-progress", markup=False)
            with Horizontal(classes="modal-footer"):
                for label, key in (
                    ("Cancel", "cancel"),
                    ("Back", "back"),
                    ("Review", "review"),
                    ("Submit", "submit"),
                ):
                    button = _ModalButton(
                        label,
                        button_id=key,
                        classes="-primary" if key in {"review", "submit"} else "",
                    )
                    button.id = "clone-review-button" if key == "review" else f"clone-{key}"
                    button.display = key in {"cancel", "review"}
                    yield button
            self._error = Static("", classes="modal-error", id="clone-error", markup=False)
            yield self._error

    def _form_values(self) -> tuple[str, ...]:
        return (
            *(self.query_one(f"#clone-{key}", Input).value for key in ("name", "role", "entry")),
            *(
                self.query_one(f"#clone-{key}", TextArea).text
                for key in ("args", "spark", "settings")
            ),
        )

    def _sync_form_to_vm(self) -> None:
        # Parse and validate both JSON editors before changing any intent field.
        try:
            args = json.loads(self.query_one("#clone-args", TextArea).text)
        except (ValueError, TypeError):
            raise ValidationError("Arguments must be a valid JSON array of strings") from None
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValidationError("Arguments must be a JSON array containing only strings")
        try:
            settings = json.loads(self.query_one("#clone-settings", TextArea).text)
        except (ValueError, TypeError):
            raise ValidationError("Advanced settings must be a valid JSON object") from None
        if not isinstance(settings, dict):
            raise ValidationError("Advanced settings must be a JSON object")
        validate_clone_settings(settings)
        self._vm.apply_field("name", self.query_one("#clone-name", Input).value)
        self._vm.apply_field("execution_role_arn", self.query_one("#clone-role", Input).value)
        self._vm.apply_field("entry_point", self.query_one("#clone-entry", Input).value)
        self._vm.apply_field("entry_point_arguments", tuple(args))
        self._vm.apply_field(
            "spark_submit_parameters", self.query_one("#clone-spark", TextArea).text
        )
        self._vm.apply_settings(settings)

    def _set_stage(self, review: bool) -> None:
        self.query_one("#clone-edit").display = not review
        self.query_one("#clone-review-scroll").display = review
        self.query_one("#clone-review", Static).update(self._vm.review_text if review else "")
        self.query_one("#clone-title", Static).update(
            "Review clone job run" if review else "Clone job run"
        )
        self.query_one("#clone-review", Static).display = review
        self.query_one("#clone-back").display = review
        self.query_one("#clone-submit").display = review
        # The footer review button and the comparison Static have distinct IDs.
        self.query_one("#clone-review-button").display = not review
        target = self.query_one("#clone-review-scroll" if review else "#clone-name")
        target.focus()

    def action_review(self) -> None:
        if self._submitting:
            return
        try:
            self._sync_form_to_vm()
        except ValidationError as exc:
            self._show_error(str(exc))
            return
        valid, reason = self._vm.is_valid()
        if not valid:
            self._show_error(reason or "Form is invalid")
            return
        self._reviewed_token = self._vm.client_token
        self._reviewed_form = self._form_values()
        self._show_error("")
        self._set_stage(True)

    def action_back(self) -> None:
        if self._submitting:
            return
        self._reviewed_token = None
        self._reviewed_form = None
        self._show_error("")
        self._set_stage(False)

    def action_cancel(self) -> None:
        self._vm.cancel()
        self.dismiss(None)

    async def action_submit(self) -> None:
        if self._submitting:
            return
        if not self.reviewing:
            self._show_error("Review the clone before submitting")
            return
        if (
            self._reviewed_token != self._vm.client_token
            or self._reviewed_form != self._form_values()
        ):
            self.action_back()
            self._show_error("The form changed. Review it again before submitting")
            return
        valid, reason = self._vm.is_valid()
        if not valid:
            self._show_error(reason or "Form is invalid")
            return
        self._submitting = True
        submit_button = self.query_one("#clone-submit")
        back_button = self.query_one("#clone-back")
        progress = self.query_one("#clone-progress", Static)
        submit_button.disabled = True
        back_button.disabled = True
        self.query_one("#clone-cancel", Static).update("Close")
        progress.update("Submitting. Closing this form does not cancel the AWS job.")
        try:
            new_id = await self._vm.submit()
        except Exception as exc:
            self._show_error(_submission_error(exc))
            return
        finally:
            self._submitting = False
            if self.is_attached:
                submit_button.disabled = False
                back_button.disabled = False
                progress.update("")
        if self.is_attached and self.is_active and not self._vm.cancelled:
            self.dismiss(new_id)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_review()

    def on_click(self, event: Click) -> None:
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, _ModalButton):
                if node.disabled:
                    return
                if node.button_id == "submit":
                    self._run_lifecycle_worker(
                        self.action_submit, group="emr-clone-submit", exclusive=False
                    )
                elif node.button_id == "review":
                    self.action_review()
                elif node.button_id == "back":
                    self.action_back()
                else:
                    self.action_cancel()
                return
            node = getattr(node, "parent", None)

    def _show_error(self, message: str) -> None:
        if self._error is not None:
            self._error.update(message)


__all__ = ["JobRunCloneModal"]
