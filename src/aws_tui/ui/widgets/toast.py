"""Toast + ToastStack widgets bound to :class:`ToastStackVM`.

The stack is a vertical container floating in the top-right; each
:class:`Toast` widget renders one :class:`ToastVM` model. We subscribe to
``CollectionChangedEvent`` (via the composite's ``on_collection_changed``
observable) so additions/removals trigger a remount of children.
"""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastVM


class Toast(Widget):
    """Render one toast.

    A single line: ``<text>`` plus an optional ``[<action_label>]`` tail.
    The level class is applied to the widget so themes can tint the
    border accordingly.
    """

    DEFAULT_CSS = """
    Toast {
        height: auto;
        padding: 0 1;
    }
    """

    LEVEL_CLASS: ClassVar[dict[ToastLevel, str]] = {
        ToastLevel.INFO: "-info",
        ToastLevel.SUCCESS: "-success",
        ToastLevel.WARNING: "-warning",
        ToastLevel.ERROR: "-error",
    }

    def __init__(
        self,
        toast_vm: ToastVM,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        # Always include the level class so theme rules pick it up.
        level_class = self.LEVEL_CLASS.get(toast_vm.model.level, "-info")
        merged = " ".join(c for c in (classes, level_class) if c)
        super().__init__(id=id, classes=merged)
        self._toast_vm = toast_vm

    @property
    def toast_vm(self) -> ToastVM:
        return self._toast_vm

    def render(self) -> Text:
        text = Text(self._toast_vm.model.text)
        action_label = self._toast_vm.model.action_label
        if action_label:
            text.append("  [")
            text.append(action_label, style="bold")
            text.append("]")
        return text


class ToastStack(Widget):
    """Vertical container of currently-visible toasts."""

    DEFAULT_CSS = """
    ToastStack {
        layer: notifications;
        dock: right;
        offset: 0 8;
        width: 50;
        height: auto;
        align: right top;
    }
    ToastStack > #toast-stack-inner {
        width: auto;
        height: auto;
    }
    """

    def __init__(
        self,
        vm: ToastStackVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: ToastStackVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    @property
    def vm(self) -> ToastStackVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Vertical(id="toast-stack-inner")

    def on_mount(self) -> None:
        # Initial render of any toasts present when we mounted.
        self._rebuild_toasts()
        # Listen for collection mutations.
        self._sub = self._vm.on_collection_changed.subscribe(on_next=self._on_collection_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_collection_changed(self, _event: object) -> None:
        # Marshal to Textual's event loop via call_after_refresh — the hub
        # could deliver from any thread, but in our app it's the asyncio loop.
        self.call_after_refresh(self._rebuild_toasts)

    def _rebuild_toasts(self) -> None:
        try:
            container = self.query_one("#toast-stack-inner", Vertical)
        except NoMatches:
            return
        # Remove existing children + remount fresh ones.
        for child in list(container.children):
            child.remove()
        for toast_vm in self._vm.toasts:
            container.mount(Toast(toast_vm))


__all__ = ["Toast", "ToastStack"]
