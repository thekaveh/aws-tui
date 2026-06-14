"""ResumeModal screen bound to :class:`ResumeVM`.

Shown when ``TransferJournal.find_unfinished()`` returns at least one
entry on startup. Four buttons map to :class:`ResumeAction` per spec
§7.6: resume all, abort all, decide each, keep journal for later.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.vm.chrome.resume_vm import ResumeAction, ResumeVM, entry_summary


class ResumeModal(ModalScreen[ResumeAction]):
    """Resume-after-crash modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "keep_for_later", "Keep for later"),
        ("r", "resume_all", "Resume all"),
        ("a", "abort_all", "Abort all"),
        ("d", "decide_each", "Decide each"),
        ("k", "keep_for_later", "Keep for later"),
    ]

    def __init__(
        self,
        vm: ResumeVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: ResumeVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> ResumeVM:
        return self._vm

    def compose(self) -> ComposeResult:
        count = self._vm.count
        plural = "transfer" if count == 1 else "transfers"
        with Container():
            yield Static(
                f"{count} {plural} from a previous session were not finished.",
                classes="modal-title",
            )
            with VerticalScroll(id="resume-body-scroll"):
                for entry in self._vm.entries:
                    yield Static(
                        f"  - {entry_summary(entry)}",
                        classes="modal-body resume-entry",
                    )
            with Horizontal(classes="modal-footer"):
                yield Button("resume all", id="resume-resume-btn")
                yield Button("abort all", id="resume-abort-btn", variant="error")
                yield Button("decide each", id="resume-decide-btn")
                yield Button("keep for later", id="resume-keep-btn")

    def action_resume_all(self) -> None:
        self._vm.resume_all_command.execute()
        self.dismiss(ResumeAction.RESUME_ALL)

    def action_abort_all(self) -> None:
        self._vm.abort_all_command.execute()
        self.dismiss(ResumeAction.ABORT_ALL)

    def action_decide_each(self) -> None:
        self._vm.decide_each_command.execute()
        self.dismiss(ResumeAction.DECIDE_EACH)

    def action_keep_for_later(self) -> None:
        self._vm.keep_for_later_command.execute()
        self.dismiss(ResumeAction.KEEP_FOR_LATER)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume-resume-btn":
            self.action_resume_all()
        elif event.button.id == "resume-abort-btn":
            self.action_abort_all()
        elif event.button.id == "resume-decide-btn":
            self.action_decide_each()
        elif event.button.id == "resume-keep-btn":
            self.action_keep_for_later()


__all__ = ["ResumeModal"]
