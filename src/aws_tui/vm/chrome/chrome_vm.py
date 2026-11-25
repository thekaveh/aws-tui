"""ChromeVM — aggregate of HintLegendVM and ToastStackVM.

The chrome is the persistent UI furniture that never gets disposed during a
session — only at app exit. The class is an isolated component that owns its
two child VMs as lifecycle members: construct and destruct drive them in
order, and dispose releases them newest first.
"""

from __future__ import annotations

from vmx import Dispatcher, Message, MessageHub

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.vm._component import IsolatedComponentVM
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM


class ChromeVM(IsolatedComponentVM):
    """Cross-service chrome aggregate (hint legend and toasts)."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        keymap: KeymapStore,
    ) -> None:
        super().__init__(name="chrome", hub=hub, dispatcher=dispatcher)
        self._hint_legend = HintLegendVM(hub=hub, dispatcher=dispatcher, keymap=keymap)
        self._own(self._hint_legend)
        self._toast_stack = ToastStackVM(hub=hub, dispatcher=dispatcher)
        self._own(self._toast_stack)

    # ── Children accessors ──────────────────────────────────────────────────

    @property
    def hint_legend(self) -> HintLegendVM:
        return self._hint_legend

    @property
    def toast_stack(self) -> ToastStackVM:
        return self._toast_stack

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _on_construct(self) -> None:
        super()._on_construct()
        self._complete_lifecycle_hook_after(
            self._transition_children((self._hint_legend, self._toast_stack), construct=True)
        )

    def _on_destruct(self) -> None:
        super()._on_destruct()
        self._complete_lifecycle_hook_after(
            self._transition_children((self._toast_stack, self._hint_legend), construct=False)
        )


__all__ = ["ChromeVM"]
