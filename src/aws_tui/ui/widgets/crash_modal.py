"""CrashModal screen bound to :class:`CrashVM`.

Centered modal showing the exception type, a short traceback preview,
and the dump-file path. Buttons:

- ``view trace``  → return :class:`CrashChoice.VIEW_TRACE`
- ``continue``   → return :class:`CrashChoice.CONTINUE` (disabled if
  the offending command was a write per spec §7.10)
- ``quit``       → return :class:`CrashChoice.QUIT`

Bindings: ``Esc`` and ``q`` quit, ``Enter`` selects the default action
(``view trace`` if continue is disabled, ``continue`` otherwise).

Buttons use :class:`ModalButton` (the themable Static-based replacement
shared with ConfirmModal / ResumeModal / FirstRunModal / ThemePickerModal)
instead of Textual's stock ``Button`` — the latter ships with ANSI color
defaults that fight ``.tcss`` palette tokens.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
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
            # ``markup=False`` everywhere a CrashReport field flows
            # through Static. Exception messages, tracebacks, and
            # dump paths routinely contain ``[…]`` (``KeyError:
            # '[foo]'``, ``IndexError: ... [0]``, repr of dicts in
            # ``__str__``) which Rich would parse as style tags and
            # crash on. The crash modal is the LAST widget that's
            # allowed to crash — its whole job is to surface
            # crashes; a second crash here is a hard app freeze.
            yield Static(
                f"{report.exception_type}: {report.exception_message}",
                classes="modal-body crash-exc",
                markup=False,
            )
            yield Static(report.traceback_short, classes="modal-body crash-trace", markup=False)
            yield Static(str(report.dump_path), classes="modal-body crash-dump-path", markup=False)
            with Horizontal(classes="modal-footer"):
                yield ModalButton("view trace", button_id="crash-view-btn")
                # ``continue`` is the safe-side primary action when the
                # offending command was a read (``can_continue=True``).
                # When unsafe (writes per spec §7.10), the ``-disabled``
                # class signals non-interactivity; ``action_continue``
                # also guards by checking ``can_continue`` directly.
                continue_classes = "-primary" if self._vm.can_continue else "-disabled"
                yield ModalButton(
                    "continue",
                    button_id="crash-continue-btn",
                    classes=continue_classes,
                )
                yield ModalButton("quit", button_id="crash-quit-btn", classes="-danger")

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

    def on_click(self, event: Click) -> None:
        # Walk up from the click target to find the ModalButton (if any).
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, ModalButton):
                if node.button_id == "crash-quit-btn":
                    self.action_quit()
                elif node.button_id == "crash-view-btn":
                    self.action_view_trace()
                elif node.button_id == "crash-continue-btn":
                    self.action_continue()
                return
            node = getattr(node, "parent", None)


__all__ = ["CrashModal"]
