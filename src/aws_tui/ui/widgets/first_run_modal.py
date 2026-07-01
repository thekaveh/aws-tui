"""FirstRunModal — welcome screen for sessions with no known connections.

The modal offers three buttons mapping to :class:`FirstRunAction`:

- ``add aws``        — composition root shells out to ``aws configure sso``
- ``add s3-compat``  — opens :class:`~aws_tui.ui.widgets.settings.connection_form.ConnectionFormInline`
                       embedded in the modal; on save the connection is written
                       to config and the modal dismisses with ADD_S3_COMPAT.
- ``skip``           — proceed to main screen
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui import notifications
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormCancelled,
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
from aws_tui.vm.chrome.first_run_vm import FirstRunAction, FirstRunVM

if TYPE_CHECKING:
    from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM


def _toast_stack_from_app_ctx(ctx: object) -> ToastStackVM | None:
    root_vm = getattr(ctx, "root_vm", None)
    chrome = getattr(root_vm, "chrome", None)
    toast_stack = getattr(chrome, "toast_stack", None)
    return cast("ToastStackVM | None", toast_stack)


class FirstRunModal(ModalScreen[FirstRunAction]):
    """The empty-config welcome modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "skip", "Skip"),
        ("a", "add_aws", "Add AWS"),
        ("s", "add_s3_compat", "Add S3-compat"),
        ("k", "skip", "Skip"),
    ]

    def __init__(
        self,
        vm: FirstRunVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: FirstRunVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> FirstRunVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("welcome to aws-tui", classes="modal-title")
            yield Static(
                "no AWS or S3-compatible connections configured.",
                classes="modal-body",
            )
            yield Static(
                "choose how to get started:",
                classes="modal-body",
            )
            yield Static(
                "  add aws profile  (runs 'aws configure sso' in your terminal)",
                classes="modal-body first-run-bullet",
            )
            yield Static(
                "  add s3-compatible (in-TUI form for MinIO, R2, etc.)",
                classes="modal-body first-run-bullet",
            )
            yield Static(
                "  skip for now (you can add later via : connection add)",
                classes="modal-body first-run-bullet",
            )
            yield ConnectionFormInline(hub=self._hub)
            with Horizontal(classes="modal-footer"):
                yield ModalButton("add aws", button_id="first-run-aws-btn", classes="-primary")
                yield ModalButton("add s3-compatible", button_id="first-run-s3-btn")
                yield ModalButton("skip", button_id="first-run-skip-btn")

    def action_add_aws(self) -> None:
        self._vm.add_aws_command.execute()
        self.dismiss(FirstRunAction.ADD_AWS)

    def action_add_s3_compat(self) -> None:
        self.query_one(ConnectionFormInline).open_for_add()

    def action_skip(self) -> None:
        self._vm.skip_command.execute()
        self.dismiss(FirstRunAction.SKIP)

    def on_click(self, event: Click) -> None:
        target = event.widget
        button_id = getattr(target, "button_id", None)
        if button_id == "first-run-aws-btn":
            self.action_add_aws()
        elif button_id == "first-run-s3-btn":
            self.action_add_s3_compat()
        elif button_id == "first-run-skip-btn":
            self.action_skip()

    @on(ConnectionFormSubmitted)
    def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
        """Persist the new connection then dismiss the modal.

        On persistence failure, the form stays open and an error toast is
        surfaced — the modal is NOT dismissed.
        """
        from aws_tui.composition import add_s3_compat_connection

        try:
            # Reach in via ``_app_ctx`` (not the ``app_ctx`` property) so
            # the unit-test harness can mount this modal under a vanilla
            # Textual ``App`` patched with ``_app_ctx = SimpleNamespace(...)``.
            # ``hasattr`` gating would skip the persist step in tests;
            # the test harness explicitly sets the private name to drive
            # the persist path.
            ctx = self.app._app_ctx  # type: ignore[attr-defined]
            add_s3_compat_connection(config_store=ctx.config_store, form=event.form)
        except Exception as exc:
            # ``clear_submitting()`` re-arms the form's re-entrancy flag so the
            # user can retry after fixing the underlying problem (e.g. freeing
            # disk space). Without this the Submit button would silently no-op
            # after the first failure and the user would be stuck.
            self.query_one(ConnectionFormInline).clear_submitting()
            message = f"Couldn't save connection: {exc}"
            toast_stack = _toast_stack_from_app_ctx(ctx)
            if toast_stack is not None:
                notifications.error(
                    toast_stack,
                    subject="Settings",
                    message=message,
                    action="Fix the config path or permissions, then retry.",
                )
            else:
                # Harness fallback: production uses the unified toast helper,
                # while vanilla Textual test apps still need a visible error.
                self.notify(message, severity="error", timeout=8)
            return
        self.query_one(ConnectionFormInline).close()
        self._vm.add_s3_compat_command.execute()
        self.dismiss(FirstRunAction.ADD_S3_COMPAT)

    @on(ConnectionFormCancelled)
    def on_connection_form_cancelled(self, event: ConnectionFormCancelled) -> None:
        """Form was cancelled — just hide it, stay on the welcome modal."""
        return


__all__ = ["FirstRunModal"]
