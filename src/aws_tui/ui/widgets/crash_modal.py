"""CrashModal screen bound to :class:`CrashVM`.

Centered modal showing the exception type, a short traceback preview,
and the dump-file path. Buttons:

- ``view trace``  → return :class:`CrashChoice.VIEW_TRACE`
- ``continue``   → return :class:`CrashChoice.CONTINUE` (disabled if
  the offending command was a write per spec §7.10)
- ``quit``       → return :class:`CrashChoice.QUIT`

Bindings: ``Esc`` and ``q`` quit, ``Enter`` selects the default action
(``view trace`` if continue is disabled, ``continue`` otherwise).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashVM


class CrashModal(ModalScreen[CrashChoice]):
    """Post-mortem crash modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "quit", "Quit"),
        ("q", "quit", "Quit"),
        ("v", "view_trace", "View trace"),
        ("c", "continue", "Continue"),
        ("enter", "default", "Default"),
    ]

    def __init__(
        self,
        vm: CrashVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: CrashVM = vm
        self._hub: MessageHub[Message] = hub
        self.add_class("-danger")

    @property
    def vm(self) -> CrashVM:
        return self._vm

    def compose(self) -> ComposeResult:
        report = self._vm.report
        with Container():
            yield Static("unexpected error", classes="modal-title")
            yield Static(
                f"{report.exception_type}: {report.exception_message}",
                classes="modal-body crash-exc",
            )
            yield Static(report.traceback_short, classes="modal-body crash-trace")
            yield Static(str(report.dump_path), classes="modal-body crash-dump-path")
            with Horizontal(classes="modal-footer"):
                yield Button("view trace", id="crash-view-btn")
                yield Button(
                    "continue",
                    id="crash-continue-btn",
                    disabled=not self._vm.can_continue,
                )
                yield Button("quit", id="crash-quit-btn", variant="error")

    def action_quit(self) -> None:
        self._vm.quit_command.execute()
        self.dismiss(CrashChoice.QUIT)

    def action_view_trace(self) -> None:
        self._vm.view_trace_command.execute()
        self.dismiss(CrashChoice.VIEW_TRACE)

    def action_continue(self) -> None:
        if not self._vm.can_continue:
            return
        self._vm.continue_command.execute()
        self.dismiss(CrashChoice.CONTINUE)

    def action_default(self) -> None:
        if self._vm.can_continue:
            self.action_continue()
        else:
            self.action_view_trace()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "crash-quit-btn":
            self.action_quit()
        elif event.button.id == "crash-view-btn":
            self.action_view_trace()
        elif event.button.id == "crash-continue-btn":
            self.action_continue()


__all__ = ["CrashModal"]
