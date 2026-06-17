"""TransfersOverlay — top-right floating box listing in-progress transfers.

Each :class:`TransferVM` in :class:`TransfersVM` gets one
:class:`TransferRowWidget`: a label, a progress bar, and a cancel
button. Finished transfers linger briefly (so the user can see they
landed) then quietly disappear, leaving the next ones to take their
place.

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
from textual.widgets import ProgressBar, Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.file_manager.transfer_vm import TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM

# Seconds a completed/failed/cancelled transfer stays visible before it
# fades out. Long enough that the user notices completion; short enough
# that the box doesn't accumulate cruft. Override with $AWS_TUI_TRANSFER_LINGER
# (used by tests so they don't have to sleep).
_LINGER_SECONDS: float = float(os.environ.get("AWS_TUI_TRANSFER_LINGER", "3.0"))


def _last_segment(uri: str) -> str:
    """Shorten a label to just the trailing path segment for the overlay."""
    cleaned = uri.rstrip("/")
    if not cleaned or "/" not in cleaned:
        return cleaned or "?"
    return cleaned.rsplit("/", 1)[-1]


class TransferRowWidget(HubSubscriberMixin, Widget):
    """One row inside the overlay — bound to a :class:`TransferVM`.

    Subscribes to the transfer's own ``model`` PropertyChanged so the
    progress bar and label refresh without rebuilding the row."""

    # The Cancel "button" is a themable Static instead of
    # ``textual.widgets.Button`` because Button ships with its own
    # heavy DEFAULT_CSS (ansi colors etc) that doesn't follow our
    # theme tokens.
    DEFAULT_CSS = """
    TransferRowWidget {
        height: 3;
        width: 100%;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    TransferRowWidget > .transfer-label {
        height: 1;
        width: 100%;
    }
    TransferRowWidget > .transfer-row {
        height: 1;
        width: 100%;
    }
    TransferRowWidget ProgressBar {
        width: 1fr;
        height: 1;
    }
    TransferRowWidget .transfer-cancel {
        width: 10;
        height: 1;
        margin: 0 0 0 1;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, transfer_vm: TransferVM, *, hub: MessageHub[Message]) -> None:
        super().__init__(classes="transfer-row-host")
        self._vm: TransferVM = transfer_vm
        self._hub: MessageHub[Message] = hub

    @property
    def transfer_vm(self) -> TransferVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static(self._label_text(), classes="transfer-label", markup=True)
        with Horizontal(classes="transfer-row"):
            yield ProgressBar(total=100, show_eta=False, show_percentage=True)
            yield Static("Cancel", id="cancel-btn", classes="transfer-cancel")

    def on_mount(self) -> None:
        self._refresh_progress()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def on_click(self, event: Click) -> None:
        # Bubble: react when the click landed on our Cancel Static.
        target = event.widget if hasattr(event, "widget") else None
        node: object | None = target
        while node is not None:
            if isinstance(node, Static) and getattr(node, "id", None) == "cancel-btn":
                self._vm.cancel_command.execute()
                return
            node = getattr(node, "parent", None)

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "model":
            self._refresh_progress()
            try:
                label = self.query_one(".transfer-label", Static)
            except NoMatches:
                return
            label.update(self._label_text())

    def _label_text(self) -> str:
        model = self._vm.model
        src = _last_segment(model.source_label)
        dst = _last_segment(model.destination_label)
        state_tag = self._state_tag()
        return f"[b]{src}[/]  →  {dst}  {state_tag}"

    def _state_tag(self) -> str:
        state = self._vm.state
        if state is TransferState.RUNNING:
            return "[dim]running[/]"
        if state is TransferState.PAUSED:
            return "[yellow]paused[/]"
        if state is TransferState.COMPLETED:
            return "[green]done[/]"
        if state is TransferState.FAILED:
            return "[red]failed[/]"
        if state is TransferState.CANCELLED:
            return "[dim]cancelled[/]"
        return f"[dim]{state}[/]"

    def _refresh_progress(self) -> None:
        try:
            bar = self.query_one(ProgressBar)
        except Exception:
            return
        model = self._vm.model
        total = model.bytes_total
        done = model.bytes_done
        if total and total > 0:
            bar.update(total=total, progress=min(done, total))
        else:
            # Indeterminate — leave at 0 so the bar isn't misleading.
            bar.update(total=100, progress=0)


class TransfersOverlay(Widget):
    """Top-right floating box that aggregates active + recently-finished
    transfers. New transfers stack below older ones; finished entries
    linger briefly then disappear."""

    # Structural CSS only — colors/border come from the theme overlay
    # (each .tcss defines TransfersOverlay with $bg-elev background and
    # $rule-dim border so they swap on theme change).
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
    TransfersOverlay.-hidden {
        display: none;
    }
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
        # Transfer ids whose row should drop on the next rebuild because
        # the linger timer has elapsed.
        self._expired_ids: set[str] = set()

    @property
    def vm(self) -> TransfersVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("Transfers", id="transfers-overlay-title")
        yield Vertical(id="transfers-overlay-inner")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)
        self._rebuild()

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Internal ────────────────────────────────────────────────────────────

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
        except Exception:
            return

        # Pick the set of rows to render: every active transfer plus any
        # recently-finished ones we haven't yet expired.
        visible: list[TransferVM] = []
        for t in self._vm.transfers:
            if t.id in self._expired_ids:
                continue
            if t.is_active or t.state is TransferState.PENDING:
                visible.append(t)
            elif t.is_finished:
                # Newly finished — keep visible while the linger timer
                # ticks. ``_arm_linger`` is already idempotent (it
                # short-circuits on `transfer_id in self._expired_ids`)
                # so we don't need to filter on "is this the first
                # time" — the second call is a no-op.
                visible.append(t)
                self._arm_linger(t.id)

        new_ids = {t.id for t in visible}

        # Remove rows no longer in the visible set.
        for row in list(container.query(TransferRowWidget)):
            if row.transfer_vm.id not in new_ids:
                row.remove()

        # Add fresh rows for transfers not yet mounted.
        currently_mounted = {row.transfer_vm.id for row in container.query(TransferRowWidget)}
        for t in visible:
            if t.id not in currently_mounted:
                container.mount(TransferRowWidget(t, hub=self._hub))

        # Hide the whole overlay when there's nothing to show — keeps the
        # corner of the screen free during idle moments.
        if visible:
            self.remove_class("-hidden")
        else:
            self.add_class("-hidden")

    def _arm_linger(self, transfer_id: str) -> None:
        """Schedule ``transfer_id`` to expire from the overlay after the
        configured linger interval. Idempotent on repeat calls."""
        if transfer_id in self._expired_ids:
            return

        def _expire() -> None:
            self._expired_ids.add(transfer_id)
            self.call_after_refresh(self._rebuild)

        # Don't fire if already expired between scheduling + run.
        self.set_timer(_LINGER_SECONDS, _expire)


__all__ = ["TransferRowWidget", "TransfersOverlay"]
