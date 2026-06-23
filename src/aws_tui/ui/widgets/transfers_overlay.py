"""TransfersOverlay — top-right floating box listing in-progress transfers.

Each :class:`TransferVM` in :class:`TransfersVM` gets one
:class:`TransferRowWidget`: a card with a state-colored left bar
(``$accent`` running / ``$success`` done / ``$danger`` failed),
title row, destination row, custom 10-cell progress bar + cancel chip,
and a meta row (bytes done/total + speed + eta).

Wiring: the overlay docks on the ``notifications`` layer (same one
ToastStack uses) so it floats above the dual-pane without taking flow
space. It listens for ``transfers`` PropertyChanged on the hub and
re-mounts children on each batch update.
"""

from __future__ import annotations

import os

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.resume_vm import humanize_bytes
from aws_tui.vm.file_manager.transfer_vm import TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM

# Seconds a completed/failed/cancelled transfer stays visible before it
# fades out. Long enough that the user notices completion; short enough
# that the box doesn't accumulate cruft. Override with $AWS_TUI_TRANSFER_LINGER
# (used by tests so they don't have to sleep).
_LINGER_SECONDS: float = float(os.environ.get("AWS_TUI_TRANSFER_LINGER", "3.0"))

#: Width of the custom progress bar in cells.
_BAR_CELLS: int = 10
_BAR_FILLED: str = "▰"  # ▰
_BAR_EMPTY: str = "▱"  # ▱


def _last_segment(uri: str) -> str:
    """Shorten a label to just the trailing path segment for the overlay."""
    cleaned = uri.rstrip("/")
    if not cleaned or "/" not in cleaned:
        return cleaned or "?"
    return cleaned.rsplit("/", 1)[-1]


def _format_eta(seconds: float | None) -> str:
    """Human-readable mm:ss / h:mm:ss for the ETA cell."""
    if seconds is None:
        return "--:--"
    total = int(seconds)
    if total < 0:
        return "--:--"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _state_class(state: TransferState) -> str:
    """CSS modifier class corresponding to a transfer state."""
    return {
        TransferState.PENDING: "-pending",
        TransferState.RUNNING: "-running",
        TransferState.PAUSED: "-paused",
        TransferState.COMPLETED: "-done",
        TransferState.FAILED: "-failed",
        TransferState.CANCELLED: "-cancelled",
    }.get(state, "-pending")


class TransferRowWidget(HubSubscriberMixin, Widget):
    """One card-style row inside the overlay — bound to a :class:`TransferVM`.

    Subscribes to the transfer's own ``state`` PropertyChanged so the
    bar / meta line / state class refresh without rebuilding the row."""

    DEFAULT_CSS = """
    TransferRowWidget {
        height: 5;
        width: 100%;
        padding: 0 1;
        border-left: thick transparent;
    }
    TransferRowWidget > .transfer-title-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    TransferRowWidget > .transfer-dest-row {
        height: 1;
        width: 100%;
    }
    TransferRowWidget > .transfer-bar-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    TransferRowWidget > .transfer-meta-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    TransferRowWidget .transfer-name { width: 1fr; }
    TransferRowWidget .transfer-state-word { width: auto; text-align: right; }
    TransferRowWidget .transfer-bar { width: 1fr; }
    TransferRowWidget .transfer-cancel {
        width: 5;
        height: 1;
        text-align: center;
        margin: 0 0 0 1;
    }
    TransferRowWidget .transfer-bytes { width: 1fr; }
    TransferRowWidget .transfer-rate { width: auto; text-align: right; }
    """

    def __init__(self, transfer_vm: TransferVM, *, hub: MessageHub[Message]) -> None:
        super().__init__(classes=f"transfer-row {_state_class(transfer_vm.state)}")
        self._vm: TransferVM = transfer_vm
        self._hub: MessageHub[Message] = hub

    @property
    def transfer_vm(self) -> TransferVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Horizontal(classes="transfer-title-row"):
            yield Static(self._name_text(), classes="transfer-name", markup=False)
            yield Static(self._state_word(), classes="transfer-state-word", markup=False)
        yield Static(self._dest_text(), classes="transfer-dest-row", markup=False)
        with Horizontal(classes="transfer-bar-row"):
            yield Static(self._bar_text(), classes="transfer-bar", markup=False)
            yield Static("[✕]", id="cancel-btn", classes="transfer-cancel", markup=False)
        with Horizontal(classes="transfer-meta-row"):
            yield Static(self._bytes_text(), classes="transfer-bytes", markup=False)
            yield Static(self._rate_text(), classes="transfer-rate", markup=False)

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def on_click(self, event: Click) -> None:
        # Bubble: react when the click landed on our Cancel Static.
        # Cancelled / completed / failed rows ignore clicks (the chip is dim).
        if self._vm.is_finished:
            return
        target = event.widget if hasattr(event, "widget") else None
        node: object | None = target
        while node is not None:
            if isinstance(node, Static) and getattr(node, "id", None) == "cancel-btn":
                self._vm.cancel_command.execute()
                return
            node = getattr(node, "parent", None)

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name != "state":
            return
        # Refresh state-class modifier and the four lines.
        self._sync_state_class()
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        try:
            self.query_one(".transfer-name", Static).update(self._name_text())
            self.query_one(".transfer-state-word", Static).update(self._state_word())
            self.query_one(".transfer-dest-row", Static).update(self._dest_text())
            self.query_one(".transfer-bar", Static).update(self._bar_text())
            self.query_one(".transfer-bytes", Static).update(self._bytes_text())
            self.query_one(".transfer-rate", Static).update(self._rate_text())
        except NoMatches:
            return

    def _sync_state_class(self) -> None:
        for cls in ("-pending", "-running", "-paused", "-done", "-failed", "-cancelled"):
            self.remove_class(cls)
        self.add_class(_state_class(self._vm.state))

    def _name_text(self) -> str:
        return _last_segment(self._vm.model.source_label)

    def _dest_text(self) -> str:
        # Show the FULL destination URL (with scheme) rather than just
        # the trailing segment. The trailing segment is usually
        # identical to the source name, making the row ambiguous —
        # showing "s3://bucket/path/Snowpiercer" is unambiguous about
        # where the file is going. Long URLs are truncated with
        # ellipsis by the per-theme CSS (`text-wrap: nowrap;
        # text-overflow: ellipsis` on `.transfer-dest-row`).
        return f"→ {self._vm.model.destination_label}"

    def _state_word(self) -> str:
        state = self._vm.state
        pct = self._percentage()
        if state is TransferState.RUNNING:
            return f"↑ {pct}%" if pct is not None else "↑ ..."
        if state is TransferState.PAUSED:
            return f"⏸ {pct}%" if pct is not None else "⏸ ..."
        if state is TransferState.COMPLETED:
            return "✓ done"
        if state is TransferState.FAILED:
            return "✗ failed"
        if state is TransferState.CANCELLED:
            return "⊘ cancelled"
        return "..."

    def _bar_text(self) -> str:
        # Terminal states drive the bar directly so we never show an
        # empty bar on a done/failed/cancelled transfer just because
        # the underlying entry had no bytes_total (e.g. a directory
        # copy where LocalFS doesn't populate dir size).
        state = self._vm.state
        if state is TransferState.COMPLETED:
            return _BAR_FILLED * _BAR_CELLS
        pct = self._percentage()
        if pct is None:
            return _BAR_EMPTY * _BAR_CELLS
        filled = round(pct / 100.0 * _BAR_CELLS)
        filled = max(0, min(filled, _BAR_CELLS))
        return (_BAR_FILLED * filled) + (_BAR_EMPTY * (_BAR_CELLS - filled))

    def _bytes_text(self) -> str:
        done = self._vm.model.bytes_done
        total = self._vm.model.bytes_total
        state = self._vm.state
        # Terminal-state messaging is honest about what we know:
        # COMPLETED with no total → "✓ done"; FAILED / CANCELLED with
        # no progress → just the state word (the bar + left bar already
        # convey what happened).
        if state is TransferState.COMPLETED and (total is None or total <= 0):
            return "✓ done"
        if state in (TransferState.FAILED, TransferState.CANCELLED) and (
            total is None or total <= 0
        ):
            return ""
        if total is None or total <= 0:
            return f"{humanize_bytes(done)} · streaming…"
        return f"{humanize_bytes(done)} / {humanize_bytes(total)}"

    def _rate_text(self) -> str:
        if self._vm.is_finished:
            return ""
        speed = self._vm.current_speed
        eta = self._vm.current_eta
        if speed is None:
            return ""
        speed_str = f"{humanize_bytes(int(speed))}/s"
        eta_str = _format_eta(eta)
        return f"{speed_str} · {eta_str}"

    def _percentage(self) -> int | None:
        total = self._vm.model.bytes_total
        if total is None or total <= 0:
            return None
        return int(self._vm.model.bytes_done / total * 100)


class TransfersOverlay(Widget):
    """Top-right floating box that aggregates active + recently-finished
    transfers."""

    DEFAULT_CSS = """
    TransfersOverlay {
        layer: notifications;
        dock: right;
        offset: 0 2;
        width: 44;
        height: auto;
        max-height: 60%;
        padding: 1 0;
    }
    TransfersOverlay.-hidden { display: none; }
    TransfersOverlay #transfers-overlay-inner {
        width: 100%;
        height: auto;
    }
    TransfersOverlay #transfers-overlay-title {
        height: 1;
        width: 100%;
        padding: 0 2 1 2;
        text-style: bold;
    }
    """

    def __init__(
        self,
        vm: TransfersVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: TransfersVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None
        self._expired_ids: set[str] = set()
        # Tracks transfer ids whose linger timer is currently armed
        # but hasn't fired yet. Without this, every ``_rebuild`` call
        # would re-arm a fresh ``set_timer`` for the same id (the
        # early-return on ``_expired_ids`` only catches state AFTER
        # the timer has fired). A rapid sequence of rebuilds would
        # spawn a fan of independent timers all expiring at staggered
        # offsets.
        self._pending_linger_ids: set[str] = set()

    @property
    def vm(self) -> TransfersVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("▌ TRANSFERS", id="transfers-overlay-title", markup=False)
        yield Vertical(id="transfers-overlay-inner")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)
        self._rebuild()

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.sender_object is not self._vm:
            return
        if msg.property_name != "transfers":
            return
        self.call_after_refresh(self._rebuild)

    def _rebuild(self) -> None:
        try:
            container = self.query_one("#transfers-overlay-inner", Vertical)
        except NoMatches:
            return

        visible: list[TransferVM] = []
        for t in self._vm.transfers:
            if t.id in self._expired_ids:
                continue
            if t.is_active or t.state is TransferState.PENDING:
                visible.append(t)
            elif t.is_finished:
                visible.append(t)
                self._arm_linger(t.id)

        new_ids = {t.id for t in visible}
        for row in list(container.query(TransferRowWidget)):
            if row.transfer_vm.id not in new_ids:
                row.remove()

        currently_mounted = {row.transfer_vm.id for row in container.query(TransferRowWidget)}
        for t in visible:
            if t.id not in currently_mounted:
                container.mount(TransferRowWidget(t, hub=self._hub))

        if visible:
            self.remove_class("-hidden")
        else:
            self.add_class("-hidden")

    def _arm_linger(self, transfer_id: str) -> None:
        """Schedule ``transfer_id`` to expire from the overlay after the
        configured linger interval. Idempotent on repeat calls."""
        if transfer_id in self._expired_ids:
            return
        if transfer_id in self._pending_linger_ids:
            # A previous ``_rebuild`` already armed a timer for this id
            # — let it ride. Without this guard the docstring's
            # "idempotent" claim was a lie: every ``_rebuild`` for a
            # finished transfer queued a fresh timer, so a fan of
            # them all fired at staggered offsets.
            return
        self._pending_linger_ids.add(transfer_id)

        def _expire() -> None:
            self._pending_linger_ids.discard(transfer_id)
            self._expired_ids.add(transfer_id)
            self.call_after_refresh(self._rebuild)

        self.set_timer(_LINGER_SECONDS, _expire)


__all__ = ["TransferRowWidget", "TransfersOverlay"]
