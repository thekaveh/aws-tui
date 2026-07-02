"""FocusCoordinatorVM — single source of truth for the app-wide focus slot.

Round-3 directive (spec §9.bis.11 / §4.3 / §9.bis.9): a bespoke
``ComponentVM``-composing VM with a typed slot discriminator. Replaces
the dual-state fragmentation §3.2.bis enumerated (10 parallel sources
of focus / selection state).

Public surface:

- ``focused_slot`` — the live discriminator (a :class:`FocusSlot`).
- ``on_focused_slot_changed`` — Observable that fires on slot change.
- ``set_focused_slot(slot)`` — explicit set (used by ENTER on
  NavMenu, swap_provider focus chains, modal dismissal).

Subscriptions (wired by the composition root):

- Each VM's selection-changed event projects to this coordinator. The
  default mapping is "if the NavMenu's current changes, the focus
  follows the new active service's default slot"; the composition
  root can register additional projections.

The coordinator does NOT take over Textual's ``app.focused`` — that
stays the runtime's authoritative state. The View bridge reads
``focused_slot`` to drive its CSS class / ``app.set_focus(...)``
calls. The bridge is intentionally out of scope for this VM (it lives
in the View layer); this VM exposes the data the bridge needs.
"""

from __future__ import annotations

from enum import StrEnum

import reactivex as rx
from reactivex.abc import DisposableBase
from vmx import ComponentVM, DiscriminatorVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


class FocusSlot(StrEnum):
    """The app-wide focus slot discriminator.

    Per spec §4.3. The set is closed — adding a slot requires a spec
    update so the bridge knows what to project it to.

    ``MODAL`` is the precedence slot used while any modal overlay is
    open; the coordinator freezes the prior non-modal slot on
    ``modal_open()`` and restores it on ``modal_close()``.
    """

    NAV_MENU = "nav_menu"
    S3_LEFT = "s3.left"
    S3_RIGHT = "s3.right"
    EMR_RUNS = "emr.runs"
    EMR_DETAIL = "emr.detail"
    EMR_LOGS = "emr.logs"
    SETTINGS = "settings"
    MODAL = "modal"


class FocusCoordinatorVM:
    """Single source of truth for the app-wide focus slot."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        initial: FocusSlot = FocusSlot.NAV_MENU,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._focus_discriminator: DiscriminatorVM[FocusSlot] = DiscriminatorVM(initial)
        self._modal_restore_stack: list[FocusSlot] = []
        self._focus_sub: DisposableBase = self._focus_discriminator.active_changed.subscribe(
            on_next=self._emit_changed
        )
        self._disposed = False
        self._inner: ComponentVM = (
            ComponentVM.builder().name("focus_coordinator").services(hub, dispatcher).build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def focused_slot(self) -> FocusSlot:
        return self._focus_discriminator.active_key

    @property
    def is_modal(self) -> bool:
        """True while a modal has been opened via :meth:`modal_open`
        and not yet closed."""
        return self._focus_discriminator.is_active(FocusSlot.MODAL)

    @property
    def on_focused_slot_changed(self) -> rx.Observable[FocusSlot]:
        """Hot Observable. Fires every time ``focused_slot`` changes;
        the payload is the NEW slot."""
        return self._focus_discriminator.active_changed

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Mutation ────────────────────────────────────────────────────────────

    def set_focused_slot(self, slot: FocusSlot) -> None:
        """Set the active focus slot. No-op if the slot is unchanged.

        Special handling: calling ``set_focused_slot(MODAL)`` is
        equivalent to :meth:`modal_open` (saves the prior slot).
        Calling ``set_focused_slot(<non-MODAL>)`` while currently
        MODAL is equivalent to :meth:`modal_close` with an explicit
        target slot.
        """
        if slot is FocusSlot.MODAL:
            self.modal_open()
            return
        if self.focused_slot is FocusSlot.MODAL:
            # Implicit close — the caller is overriding the saved slot.
            # The facade owns restore semantics so VMx internals remain
            # replaceable across compatible VMx releases.
            self._modal_restore_stack.clear()
        if self.focused_slot is slot:
            return
        self._focus_discriminator.set_active_key(slot)

    def cycle_s3_focus(self, *, reverse: bool = False) -> None:
        """Rotate the S3 focus ring.

        Forward: LEFT -> RIGHT -> NAV -> LEFT.
        Reverse: LEFT -> NAV -> RIGHT -> LEFT.
        """
        if reverse:
            order = (FocusSlot.S3_LEFT, FocusSlot.NAV_MENU, FocusSlot.S3_RIGHT)
        else:
            order = (FocusSlot.S3_LEFT, FocusSlot.S3_RIGHT, FocusSlot.NAV_MENU)
        self._cycle(order)

    def cycle_settings_focus(self, *, reverse: bool = False) -> None:
        """Rotate the two-slot Settings focus ring.

        ``reverse`` is accepted for API symmetry with service rings; a
        two-slot ring has the same destination in both directions.
        """
        _ = reverse
        self._cycle((FocusSlot.SETTINGS, FocusSlot.NAV_MENU))

    def modal_open(self) -> None:
        """Push the MODAL precedence slot. Saves the prior non-modal
        slot so :meth:`modal_close` can restore it."""
        if self.focused_slot is FocusSlot.MODAL:
            return
        self._modal_restore_stack.append(self.focused_slot)
        self._focus_discriminator.set_active_key(FocusSlot.MODAL)

    def modal_close(self) -> None:
        """Pop the MODAL precedence slot. Restores the prior
        non-modal slot. No-op when no modal is open."""
        if self.focused_slot is not FocusSlot.MODAL:
            return
        restore = (
            self._modal_restore_stack.pop() if self._modal_restore_stack else FocusSlot.NAV_MENU
        )
        self._focus_discriminator.set_active_key(restore)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._focus_sub.dispose()
        self._modal_restore_stack.clear()
        self._focus_discriminator.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _emit_changed(self, _slot: FocusSlot) -> None:
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "focused_slot"))

    def _cycle(self, order: tuple[FocusSlot, ...]) -> None:
        try:
            idx = order.index(self.focused_slot)
        except ValueError:
            self.set_focused_slot(order[0])
            return
        self.set_focused_slot(order[(idx + 1) % len(order)])


__all__ = ["FocusCoordinatorVM", "FocusSlot"]
