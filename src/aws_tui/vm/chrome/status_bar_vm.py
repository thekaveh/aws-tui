"""StatusBarVM — top-row status strip viewmodel.

The status bar exposes four derived strings: ``connection_label``,
``region``, ``auth_indicator``, ``transfers_summary``. They are recomputed
on every change to the underlying sources (connection, auth state, transfer
aggregate). The VM subscribes to :class:`ConnectionChangedMessage` and
:class:`TransferProgressMessage` on the hub so that external publishers
(RootVM, transfer workers) drive the bar without holding direct references.

The display strings are deterministic and lowercase per the Carbon-theme
discipline — semantic colors are added by the view layer, not the VM.
"""

from __future__ import annotations

from reactivex.abc import DisposableBase
from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.messages import ConnectionChangedMessage, TransferProgressMessage

# Transfer states the bar treats as "in progress" for the active counter.
_ACTIVE_STATES: frozenset[str] = frozenset({"pending", "running", "paused"})


def _humanize_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    units = [
        (1_000_000_000_000, "T"),
        (1_000_000_000, "G"),
        (1_000_000, "M"),
        (1_000, "k"),
    ]
    for threshold, suffix in units:
        if n >= threshold:
            return f"{n / threshold:.1f} {suffix}"
    return f"{n} B"


class StatusBarVM:
    """Reactive status-strip viewmodel.

    The four derived strings are plain attributes recomputed in setter helpers;
    we emit ``PropertyChangedMessage`` on the hub when they change so the view
    layer can re-render selectively. We use plain string fields (not
    ``DerivedProperty``) because the recompute logic is trivial and tying it
    to ``BehaviorSubject`` sources would add ceremony without value.
    """

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._connection: Connection | None = None
        self._auth_state: TokenState | None = None
        self._active_count: int = 0
        self._bytes_done: int = 0
        self._bytes_total: int | None = None

        self._connection_label: str = "no connection"
        self._region: str = ""
        self._auth_indicator: str = "no session"
        self._transfers_summary: str = "transfers idle"

        self._inner: ComponentVM = (
            ComponentVM.builder().name("status_bar").services(hub, dispatcher).build()
        )
        self._sub: DisposableBase | None = None
        # Tracks which transfer ids we've already counted as "active" so a
        # repeat RUNNING event doesn't double-increment. Populated in
        # __init__ rather than lazily on first message so the attribute
        # set is fully declared up-front.
        self._seen_ids: set[str] = set()

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def connection_label(self) -> str:
        return self._connection_label

    @property
    def region(self) -> str:
        return self._region

    @property
    def auth_indicator(self) -> str:
        return self._auth_indicator

    @property
    def transfers_summary(self) -> str:
        return self._transfers_summary

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        if self._sub is None:
            self._sub = self._hub.messages.subscribe(on_next=self._on_message)

    def destruct(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.destruct()

    def dispose(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.dispose()

    # ── Public update API ──────────────────────────────────────────────────

    def update_connection(self, conn: Connection, auth_state: TokenState) -> None:
        self._connection = conn
        self._auth_state = auth_state
        self._recompute_connection_strings()

    def update_transfers(self, active_count: int, bytes_done: int, bytes_total: int | None) -> None:
        self._active_count = active_count
        self._bytes_done = bytes_done
        self._bytes_total = bytes_total
        self._recompute_transfers_summary()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_message(self, msg: object) -> None:
        if isinstance(msg, ConnectionChangedMessage):
            self.update_connection(msg.connection, msg.auth_state)
        elif isinstance(msg, TransferProgressMessage):
            self._apply_transfer_event(msg)

    def _apply_transfer_event(self, msg: TransferProgressMessage) -> None:
        # We don't track per-id state — only the aggregate. Counter +1 on the
        # first "running" we see for an id, -1 on any terminal state. Since
        # the StatusBarVM is a denormalized projection of a small space,
        # this simple counter is enough for the §4.1 status strip.
        is_active = msg.state in _ACTIVE_STATES
        was_active = msg.transfer_id in self._seen_ids
        if is_active and not was_active:
            self._seen_ids.add(msg.transfer_id)
            self._active_count += 1
        elif not is_active and was_active:
            self._seen_ids.discard(msg.transfer_id)
            self._active_count -= 1
        # Track aggregate bytes (running totals; reset to zero when idle).
        if self._active_count == 0:
            self._bytes_done = 0
            self._bytes_total = 0
        else:
            self._bytes_done = msg.bytes_transferred
            self._bytes_total = msg.bytes_total
        self._recompute_transfers_summary()

    def _recompute_connection_strings(self) -> None:
        conn = self._connection
        if conn is None:
            new_label, new_region, new_auth = "no connection", "", "no session"
        else:
            new_label = f"{conn.name} ({conn.kind})"
            new_region = conn.region
            new_auth = self._auth_indicator_for(conn, self._auth_state)
        self._set("_connection_label", "connection_label", new_label)
        self._set("_region", "region", new_region)
        self._set("_auth_indicator", "auth_indicator", new_auth)

    def _recompute_transfers_summary(self) -> None:
        if self._active_count == 0:
            new_summary = "transfers idle"
        else:
            done = _humanize_bytes(self._bytes_done)
            total = _humanize_bytes(self._bytes_total)
            new_summary = f"{self._active_count} active . {done} / {total}"
        self._set("_transfers_summary", "transfers_summary", new_summary)

    def _auth_indicator_for(self, conn: Connection, auth: TokenState | None) -> str:
        if conn.kind == "s3-compatible":
            return "keys"
        # aws
        if auth is None:
            return "no session"
        match auth:
            case TokenState.CONNECTED:
                return "sso ok"
            case TokenState.EXPIRED:
                return "login needed"
            case TokenState.MISSING:
                return "no session"
        # `match` is exhaustive over the StrEnum but mypy doesn't know that.
        return "no session"

    def _set(self, attr: str, prop_name: str, new_value: str) -> None:
        old_value = getattr(self, attr)
        if old_value == new_value:
            return
        setattr(self, attr, new_value)
        self._hub.send(PropertyChangedMessage.create(self, self.name, prop_name))


__all__ = ["StatusBarVM"]
