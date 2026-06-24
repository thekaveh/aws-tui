r"""ConfirmModal screen bound to :class:`ConfirmationVM`.

Custom-styled, bound-box modal with title + body lines + confirm/cancel
"buttons". The buttons are Static widgets (not :class:`Button`) because
the Textual default Button ships with heavy built-in CSS (ansi colors,
``\$border-blurred``, etc.) that fights theme overrides and makes the
labels overflow on narrow widths. Static + a small CSS class gives us
predictable layout + clean theme adoption.

Enter accepts (confirm), Esc cancels. The App-level priority bindings
for arrows + enter are forwarded to ``action_confirm`` / ``action_cancel``
from ``AwsTuiApp`` (see ``_forward_to_modal``).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton as _ModalButton
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest


class ConfirmModal(ModalScreen[bool]):
    """Yes/no modal driven by the VM's RelayCommands.

    Arrow-key nav: ``Left`` / ``Right`` (also ``Tab`` / ``Shift+Tab``)
    swap the focused button between Cancel and Confirm. ``Enter``
    commits whichever side is currently focused; ``Escape`` always
    cancels. The focused button gets the ``-focused`` CSS class so
    themes can highlight it (default: bold-reverse).
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
        ("enter", "commit_focused", "Confirm"),
        ("left,shift+tab", "focus_prev", "←"),
        ("right,tab", "focus_next", "→"),
    ]

    def __init__(
        self,
        vm: ConfirmationVM,
        request: ConfirmRequest,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: ConfirmationVM = vm
        self._request: ConfirmRequest = request
        self._hub: MessageHub[Message] = hub
        # Default focus = the primary (confirm) button — matches the
        # most common case where the user is about to hit Enter to
        # accept. For danger dialogs we start on Cancel instead so a
        # reflex Enter doesn't nuke data.
        #
        # The class that lights up the button visually (``-focused``)
        # is NOT applied on mount — both buttons start looking
        # deselected. The user must hover (CSS ``:hover``) or press
        # an arrow / Tab to make the focus visible. The logical
        # default is still kept here so a reflex Enter still routes
        # to the safe side without any navigation, but it doesn't
        # paint a "this button is selected" border at mount time
        # which the user found confusing because two buttons read
        # as "both selected" at a glance.
        self._focused_button_id: str = "cancel" if request.danger else "confirm"
        self._focus_visible: bool = False
        if request.danger:
            self.add_class("-danger")

    @property
    def vm(self) -> ConfirmationVM:
        return self._vm

    @property
    def request(self) -> ConfirmRequest:
        return self._request

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(self._request.title, classes="modal-title")
            for path_entry in self._request.paths:
                yield Static(path_entry.label, classes="modal-path-label")
                yield Static(path_entry.path, classes="modal-path-value")
            for line in self._request.body_lines:
                yield Static(line, classes="modal-body")
            footer_classes = "modal-footer -danger" if self._request.danger else "modal-footer"
            with Horizontal(classes=footer_classes):
                yield _ModalButton(
                    self._request.cancel_label,
                    button_id="cancel",
                )
                primary_classes = "-danger" if self._request.danger else "-primary"
                yield _ModalButton(
                    self._request.confirm_label,
                    button_id="confirm",
                    classes=primary_classes,
                )

    def on_mount(self) -> None:
        # No focus class on mount — both buttons start neutral.
        # The first arrow / Tab press flips ``_focus_visible`` True
        # and lights up the default-focused button.
        return

    def action_cancel(self) -> None:
        self._vm.cancel_command.execute()
        self.dismiss(False)

    def action_confirm(self) -> None:
        self._vm.confirm_command.execute()
        self.dismiss(True)

    def action_commit_focused(self) -> None:
        """Enter commits whichever button currently holds focus."""
        if self._focused_button_id == "confirm":
            self.action_confirm()
        else:
            self.action_cancel()

    def action_focus_next(self) -> None:
        # right / tab is directional — first press lands on the
        # RIGHT button (confirm) regardless of which side was the
        # logical default. The user pressing ``right`` clearly
        # wants the right button. Subsequent presses toggle.
        if not self._focus_visible:
            self._focus_visible = True
            self._focused_button_id = "confirm"
        else:
            self._focused_button_id = "confirm" if self._focused_button_id == "cancel" else "cancel"
        self._apply_focus_class()

    def action_focus_prev(self) -> None:
        # left / shift+tab — first press lands on the LEFT button
        # (cancel). Same toggle behaviour on subsequent presses.
        if not self._focus_visible:
            self._focus_visible = True
            self._focused_button_id = "cancel"
        else:
            self._focused_button_id = "confirm" if self._focused_button_id == "cancel" else "cancel"
        self._apply_focus_class()

    def on_click(self, event: Click) -> None:
        # Walk up from the click target to find the button (if any).
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, _ModalButton):
                if node.button_id == "confirm":
                    self.action_confirm()
                else:
                    self.action_cancel()
                return
            node = getattr(node, "parent", None)

    def _apply_focus_class(self) -> None:
        # When focus isn't visible yet (mount + before any arrow
        # press) we strip the class from every button — both look
        # deselected. Once the user navigates, the focused button
        # gets the class.
        for btn in self.query(_ModalButton):
            if self._focus_visible and btn.button_id == self._focused_button_id:
                btn.add_class("-focused")
            else:
                btn.remove_class("-focused")


__all__ = ["ConfirmModal"]
