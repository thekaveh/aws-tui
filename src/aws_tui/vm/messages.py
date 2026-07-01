"""Custom hub message envelopes for the aws-tui VM layer.

VMx's :class:`vmx.messages.protocols.Message` is a structural Protocol — any
class that exposes ``sender_name: str`` and ``sender_object: object`` satisfies
it. These envelopes are immutable, slot-backed dataclasses that publish on the
shared :class:`vmx.MessageHub` so reactive subscribers (retained status VM,
chrome, toasts, hint legend) can react without hard references between VMs.

The envelopes carry plain infra types (:class:`Connection`, :class:`TokenState`)
to keep the VM layer free of Textual / boto3 imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection

#: Reason values for ``AuthExpiredMessage``.
AuthExpiredReason = Literal["expired", "missing", "load_error"]


class TransferState(StrEnum):
    """State machine values per spec §7.5.

    ``PAUSED`` is reachable via the network-failure recovery flow (spec §7.5):
    ``RUNNING -> PAUSED -> RUNNING (recovered)``. The connectivity watcher that
    transitions to PAUSED on sustained network failure is not yet wired in
    v0.7.x; the state remains in the enum so the eventual wiring is additive.
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ConnectionChangedMessage:
    """Published by ``RootVM`` after a successful connection switch.

    Subscribers: :class:`NavMenuVM`, :class:`StatusBarVM`, every service
    content VM, the active :class:`ContentHostVM` swap orchestrator.
    """

    connection: Connection
    auth_state: TokenState
    sender_name: str = "root"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class ThemeChangedMessage:
    """Published by ``RootVM`` when the active theme changes.

    Subscribers: the view layer (re-applies ``.tcss``).
    """

    name: str
    sender_name: str = "root"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class AuthExpiredMessage:
    """Published by ``infra.AwsSession`` when a 401-equivalent or stale SSO
    token is detected.

    Subscribers: :class:`ToastStackVM` (soft toast "press a to sso-login"),
    the failing pane (renders "auth needed" placeholder).
    """

    connection_name: str
    reason: AuthExpiredReason
    sender_name: str = "aws_session"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class TransferProgressMessage:
    """Published by ``domain.CrossFsCopy`` / ``CrossFsMove`` workers.

    Subscribers: :class:`TransferVM` (per-transfer detail), retained
    :class:`StatusBarVM` (aggregate counter).

    ``source_label`` / ``destination_label`` are optional — included so
    that the first message for a given transfer can carry enough info
    for :class:`TransfersVM` to auto-register a usefully-labeled
    placeholder. Subsequent progress messages for the same transfer
    may omit them (the placeholder already has the labels).
    """

    transfer_id: str
    bytes_transferred: int
    bytes_total: int | None
    state: TransferState
    source_label: str = ""
    destination_label: str = ""
    sender_name: str = "transfers"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class TransferCancelRequestedMessage:
    """Published by :class:`TransferVM` when the user clicks the cancel
    chip (or otherwise fires ``cancel_command``).

    Subscribers: :class:`DualPaneVM`, which owns the in-flight
    ``CrossFsCopy.copy(...)`` task and races it against a per-transfer
    ``asyncio.Event``. When this message arrives the event is set, the
    copy task is cancelled, the journal is marked aborted, and the
    batch loop continues to the next queued transfer.

    The VM-side state transition to ``CANCELLED`` happens
    independently (immediately on user click) so the overlay gives
    instant feedback; this message is the asynchronous "actually
    interrupt the copy" signal.
    """

    transfer_id: str
    sender_name: str = "transfer"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class ConnectionListChangedMessage:
    """Published by :class:`S3ConnectionsVM` after each successful CRUD
    on the s3-compatible connection list.

    Subscribers (verified in production wiring):

    - :class:`NavMenuVM` — re-derives the service filter so a newly
      added connection becomes a candidate (or a removed one is
      dropped) on the next ``Shift+S`` cycle.
    - :class:`AwsTuiApp` — on ``deleted``, drops the name from
      :attr:`AppContext.unreachable_connections` so a future
      re-addition isn't pre-filtered; on ``updated``, schedules a
      pane reload if the affected name is currently mounted.

    :class:`ConnectionResolver` does NOT subscribe — it is
    cacheless and re-reads ``ConfigStore`` on every ``list()``.
    :class:`SettingsVM` does NOT subscribe — the inline
    ``S3ConnectionsPanel`` updates its own row list directly off
    the same :class:`S3ConnectionsVM` it shares with the rest of
    the app, no message round-trip needed.
    """

    names: tuple[str, ...]
    change: Literal["added", "updated", "deleted"]
    sender_name: str = "s3_connections"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class KeymapChangedMessage:
    """Published by ``infra.KeymapStore`` after a runtime rebind.

    Subscribers: :class:`HintLegendVM` (re-derives chip labels), the view
    layer's input router.
    """

    action: str
    new_keys: tuple[str, ...]
    sender_name: str = "keymap_store"

    @property
    def sender_object(self) -> object:
        return self


@dataclass(frozen=True, slots=True)
class FocusChangedMessage:
    """Published by the view layer (via ``RootVM``) whenever focus moves to a
    different VM.

    Subscribers: :class:`HintLegendVM` (swaps the action chips).
    """

    focused_vm_id: str
    sender_name: str = "root"

    @property
    def sender_object(self) -> object:
        return self


__all__ = [
    "AuthExpiredMessage",
    "AuthExpiredReason",
    "ConnectionChangedMessage",
    "ConnectionListChangedMessage",
    "FocusChangedMessage",
    "KeymapChangedMessage",
    "ThemeChangedMessage",
    "TransferCancelRequestedMessage",
    "TransferProgressMessage",
    "TransferState",
]
