"""ChromeVM — aggregate of HintLegendVM, StatusBarVM, ToastStackVM.

The chrome is the persistent UI furniture that never gets disposed during a
session — only at app exit. It is composed as a facade around three
independent child VMs (we don't use VMx's :class:`AggregateVM3` here
because our child VMs are facades; AggregateVMs require real VMx VMs).
"""

from __future__ import annotations

from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM
from aws_tui.vm.chrome.status_bar_vm import StatusBarVM
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM


class ChromeVM:
    """Cross-service chrome aggregate (hint legend, status bar, toasts)."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        keymap: KeymapStore,
    ) -> None:
        self._hint_legend = HintLegendVM(hub=hub, dispatcher=dispatcher, keymap=keymap)
        self._status_bar = StatusBarVM(hub=hub, dispatcher=dispatcher)
        self._toast_stack = ToastStackVM(hub=hub, dispatcher=dispatcher)

        self._inner: ComponentVM = (
            ComponentVM.builder().name("chrome").services(hub, dispatcher).build()
        )

    # ── Children accessors ──────────────────────────────────────────────────

    @property
    def hint_legend(self) -> HintLegendVM:
        return self._hint_legend

    @property
    def status_bar(self) -> StatusBarVM:
        return self._status_bar

    @property
    def toast_stack(self) -> ToastStackVM:
        return self._toast_stack

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        self._hint_legend.construct()
        self._status_bar.construct()
        self._toast_stack.construct()

    def destruct(self) -> None:
        self._toast_stack.destruct()
        self._status_bar.destruct()
        self._hint_legend.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        self._toast_stack.dispose()
        self._status_bar.dispose()
        self._hint_legend.dispose()
        self._inner.dispose()


__all__ = ["ChromeVM"]
