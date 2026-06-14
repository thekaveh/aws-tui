"""QuickLook modal screen bound to :class:`QuickLookVM`.

Streamed preview of a file's first ~64 KB. The actual byte stream comes
from :class:`QuickLookContent.chunks` (an async iterator); we consume it
on mount and append text to the body.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.vm.chrome.quick_look_vm import QuickLookVM


class QuickLook(ModalScreen[None]):
    """Quick Look modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "close", "Close"),
        ("space", "close", "Close"),
    ]

    def __init__(
        self,
        vm: QuickLookVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: QuickLookVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> QuickLookVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container():
            content = self._vm.content
            title = content.title if content else "(no preview)"
            yield Static(title, classes="quicklook-title")
            with VerticalScroll(id="quicklook-body-scroll"):
                yield Static("loading...", id="quicklook-body", classes="quicklook-body")

    async def on_mount(self) -> None:
        content = self._vm.content
        if content is None or content.chunks is None:
            return
        body = self.query_one("#quicklook-body", Static)
        buf = bytearray()
        async for chunk in content.chunks:
            buf.extend(chunk)
            if len(buf) >= 64 * 1024:
                break
        try:
            text = buf.decode("utf-8", errors="replace")
        except Exception:
            text = repr(bytes(buf))
        body.update(text)

    def action_close(self) -> None:
        self._vm.close_command.execute()
        self.dismiss(None)


__all__ = ["QuickLook"]
