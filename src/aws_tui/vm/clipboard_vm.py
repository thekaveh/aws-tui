"""ClipboardVM — owns the OS clipboard port and classifies what a write did.

The App keeps two things and hands the rest here: the OSC 52 write, which
is Textual's own ``App.copy_to_clipboard`` and cannot leave the view layer
(``vm/`` may not import textual), and the outcome → toast mapping, which is
where every other three-outcome report in this repo already lives.

What moved off the View is the part that was never view work: holding the
:class:`~aws_tui.infra.clipboard.ClipboardPort`, keeping its blocking spawn
off the event loop, and deciding *which* of the four outcomes a write
produced. ``S3ConnectionsVM.add_async`` is the precedent for a VM owning an
infra port and performing the identical ``anyio.to_thread.run_sync``
offload itself; :class:`~aws_tui.vm.table_clipboard_vm.TableClipboardVM` is
the precedent for the VMx composition — a ``ComponentVMOf`` holding the
last outcome as its model, so the classification is published on
``property_changed`` rather than existing only as a local variable inside
an App coroutine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import partial

import anyio
import reactivex as rx
from vmx import ComponentVMOf, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.clipboard import NO_MECHANISM, ClipboardPort, ClipboardResult


class ClipboardChannel(Enum):
    """Which channel a copy actually reached.

    Four, not three: the terminal leg can fail too. A path decoded with
    ``surrogateescape`` cannot be UTF-8 encoded for OSC 52, so "no native
    helper" and "no native helper AND the terminal refused" are different
    things to tell the user, and only the second one means nothing at all
    was copied.
    """

    #: The platform helper took the text. The only outcome that justifies
    #: the word "copied".
    NATIVE = "native"
    #: A helper was present and refused the payload — a fault worth a log
    #: line, with :attr:`ClipboardWrite.mechanism` naming which one.
    HELPER_FAILED = "helper-failed"
    #: No helper exists (ssh, tmux, a headless CI shell), so OSC 52 was the
    #: only channel used. Not a fault; unacknowledgeable, so not a success.
    TERMINAL_ONLY = "terminal-only"
    #: No helper, and the OSC 52 write raised. Nothing was copied.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ClipboardWrite:
    """The classified result of one copy.

    ``error_type`` is an exception class name and never the payload, the
    same contract :class:`~aws_tui.infra.clipboard.ClipboardResult` keeps:
    a copied path is on occasion a credential, and this value is reported
    to a log sink.
    """

    channel: ClipboardChannel
    mechanism: str
    error_type: str | None = None


class ClipboardVM:
    """Writes text to the OS clipboard and says what the write did."""

    def __init__(
        self,
        *,
        clipboard: ClipboardPort,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._clipboard: ClipboardPort = clipboard
        self._inner: ComponentVMOf[ClipboardWrite | None] = (
            ComponentVMOf[ClipboardWrite | None]
            .builder()
            .name("clipboard")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )

    # ── VMx lifecycle accessors ─────────────────────────────────────────────

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._inner.property_changed

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── State ───────────────────────────────────────────────────────────────

    @property
    def last_write(self) -> ClipboardWrite | None:
        """The most recent classified outcome, or ``None`` before any copy."""
        return self._inner.model

    # ── Behaviour ───────────────────────────────────────────────────────────

    async def write_async(self, text: str, *, terminal_written: bool) -> ClipboardWrite:
        """Write ``text`` through the port and classify the result.

        ``terminal_written`` is the caller's report of the OSC 52 leg, which
        only the App can perform. It changes nothing about the native write
        and everything about what an unsuccessful one means.

        ``pbcopy`` is ~2 ms, but an ``xclip`` with a wedged X server is not,
        and the port lets it run to its own timeout. The offload keeps that
        off the event loop; the caller's worker keeps it off the App's
        message pump. Neither substitutes for the other, and both are still
        required. anyio, never ``asyncio.to_thread`` — the repo runs its
        blocking work through anyio's limiter.
        """
        result = await anyio.to_thread.run_sync(partial(self._clipboard.write, text))
        write = _classify(result, terminal_written=terminal_written)
        # Assigning the model is what publishes ``property_changed``; the
        # outcome is VM state a binder can read, not a local in a coroutine.
        self._inner.model = write
        return write


def _classify(result: ClipboardResult, *, terminal_written: bool) -> ClipboardWrite:
    """Map a port result plus the terminal leg onto one channel.

    ``NO_MECHANISM`` is compared here and nowhere else: telling "the helper
    failed" (reportable, loggable) from "there was never a helper" (not a
    fault) is a decision about what happened, so it belongs on this side of
    the boundary rather than in the widget that renders the toast.
    """
    if result.ok:
        return ClipboardWrite(channel=ClipboardChannel.NATIVE, mechanism=result.mechanism)
    if result.mechanism != NO_MECHANISM:
        return ClipboardWrite(
            channel=ClipboardChannel.HELPER_FAILED,
            mechanism=result.mechanism,
            error_type=result.error_type,
        )
    if terminal_written:
        return ClipboardWrite(channel=ClipboardChannel.TERMINAL_ONLY, mechanism=result.mechanism)
    return ClipboardWrite(channel=ClipboardChannel.NONE, mechanism=result.mechanism)


__all__ = ["ClipboardChannel", "ClipboardVM", "ClipboardWrite"]
