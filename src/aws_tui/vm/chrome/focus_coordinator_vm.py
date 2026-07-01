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
from reactivex.subject import Subject
from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
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
        self._focused_slot: FocusSlot = initial
        # Non-modal slot saved when a modal opens; restored on close.
        self._saved_slot: FocusSlot | None = None
        self._on_changed: Subject[FocusSlot] = Subject()
        self._disposed = False
        self._inner: ComponentVM = (
            ComponentVM.builder().name("focus_coordinator").services(hub, dispatcher).build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def focused_slot(self) -> FocusSlot:
        return self._focused_slot

    @property
    def is_modal(self) -> bool:
        """True while a modal has been opened via :meth:`modal_open`
        and not yet closed."""
        return self._focused_slot is FocusSlot.MODAL

    @property
    def on_focused_slot_changed(self) -> rx.Observable[FocusSlot]:
        """Hot Observable. Fires every time ``focused_slot`` changes;
        the payload is the NEW slot."""
        return self._on_changed

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
        if self._focused_slot is FocusSlot.MODAL:
            # Implicit close — the caller is overriding the saved slot.
            self._saved_slot = None
        if self._focused_slot is slot:
            return
        self._focused_slot = slot
        self._emit_changed()

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
        if self._focused_slot is FocusSlot.MODAL:
            return
        self._saved_slot = self._focused_slot
        self._focused_slot = FocusSlot.MODAL
        self._emit_changed()

    def modal_close(self) -> None:
        """Pop the MODAL precedence slot. Restores the prior
        non-modal slot. No-op when no modal is open."""
        if self._focused_slot is not FocusSlot.MODAL:
            return
        restored = self._saved_slot if self._saved_slot is not None else FocusSlot.NAV_MENU
        self._saved_slot = None
        self._focused_slot = restored
        self._emit_changed()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._on_changed.on_completed()
        self._on_changed.dispose()
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _emit_changed(self) -> None:
        self._on_changed.on_next(self._focused_slot)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "focused_slot"))

    def _cycle(self, order: tuple[FocusSlot, ...]) -> None:
        try:
            idx = order.index(self._focused_slot)
        except ValueError:
            self.set_focused_slot(order[0])
            return
        self.set_focused_slot(order[(idx + 1) % len(order)])


__all__ = ["FocusCoordinatorVM", "FocusSlot"]
