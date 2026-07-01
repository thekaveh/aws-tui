"""ResumeModal screen bound to :class:`ResumeVM`.

Scaffold for the future startup scan that will show unfinished
``TransferJournal.find_unfinished()`` entries. Automatic startup wiring is
not live in v0.8.x, so the modal is currently test/scaffold reachable and
surfaces the journal actions planned for that path: abort all, decide each,
keep journal for later.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.resume_vm import ResumeAction, ResumeVM, entry_summary


class ResumeModal(ModalScreen[ResumeAction]):
    """Resume-after-crash modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "keep_for_later", "Keep for later"),
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
                    # ``markup=False`` — entry_summary includes the
                    # transfer's source / destination URIs, which can
                    # be real filenames or S3 keys containing
                    # ``[…]`` (``releases[2025].tar.gz``). Without
                    # the guard the resume modal would crash on the
                    # first such entry — and resume is the path
                    # users hit after a CRASH, so a second crash
                    # here is especially user-hostile.
                    yield Static(
                        f"  - {entry_summary(entry)}",
                        classes="modal-body resume-entry",
                        markup=False,
                    )
            with Horizontal(classes="modal-footer"):
                yield ModalButton("abort all", button_id="resume-abort-btn", classes="-danger")
                yield ModalButton("decide each", button_id="resume-decide-btn")
                yield ModalButton("keep for later", button_id="resume-keep-btn", classes="-primary")

    def action_abort_all(self) -> None:
        self._vm.abort_all_command.execute()
        self.dismiss(ResumeAction.ABORT_ALL)

    def action_decide_each(self) -> None:
        self._vm.decide_each_command.execute()
        self.dismiss(ResumeAction.DECIDE_EACH)

    def action_keep_for_later(self) -> None:
        self._vm.keep_for_later_command.execute()
        self.dismiss(ResumeAction.KEEP_FOR_LATER)

    def on_click(self, event: Click) -> None:
        # ModalButton bubbles its click up; we read the tagged button_id.
        target = event.widget
        button_id = getattr(target, "button_id", None)
        if button_id == "resume-abort-btn":
            self.action_abort_all()
        elif button_id == "resume-decide-btn":
            self.action_decide_each()
        elif button_id == "resume-keep-btn":
            self.action_keep_for_later()


__all__ = ["ResumeModal"]
