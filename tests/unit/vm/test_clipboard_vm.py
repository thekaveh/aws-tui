"""``ClipboardVM`` owns the port, the offload and the classification.

These four outcomes used to be decided inside an ``AwsTuiApp`` coroutine,
which meant the App imported an infra sentinel (``NO_MECHANISM``) to tell a
failed helper from an absent one and hand-rolled the thread offload around
a port it held itself. The integration tier
(``tests/integration/test_clipboard_reporting.py``) still pins the toast
each outcome produces; this tier pins the decision itself, with no Textual
app anywhere in the path.
"""

from __future__ import annotations

import threading

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.infra.clipboard import ClipboardResult, InMemoryClipboard
from aws_tui.vm.clipboard_vm import ClipboardChannel, ClipboardVM, ClipboardWrite


def _make_vm(port: object) -> ClipboardVM:
    vm = ClipboardVM(
        clipboard=port,  # type: ignore[arg-type]
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    return vm


class _ThreadRecordingClipboard:
    """Records which thread the blocking ``write`` actually ran on."""

    def __init__(self, result: ClipboardResult) -> None:
        self._result = result
        self.writes: list[str] = []
        self.threads: list[int] = []

    def write(self, text: str) -> ClipboardResult:
        self.writes.append(text)
        self.threads.append(threading.get_ident())
        return self._result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "terminal_written", "expected"),
    [
        pytest.param(
            ClipboardResult(ok=True, mechanism="pbcopy"),
            True,
            ClipboardWrite(channel=ClipboardChannel.NATIVE, mechanism="pbcopy"),
            id="helper-took-it",
        ),
        pytest.param(
            ClipboardResult(ok=False, mechanism="xclip", error_type="TimeoutExpired"),
            True,
            ClipboardWrite(
                channel=ClipboardChannel.HELPER_FAILED,
                mechanism="xclip",
                error_type="TimeoutExpired",
            ),
            id="helper-failed",
        ),
        pytest.param(
            ClipboardResult(ok=False, mechanism="none"),
            True,
            ClipboardWrite(channel=ClipboardChannel.TERMINAL_ONLY, mechanism="none"),
            id="no-helper-terminal-took-it",
        ),
        pytest.param(
            ClipboardResult(ok=False, mechanism="none"),
            False,
            ClipboardWrite(channel=ClipboardChannel.NONE, mechanism="none"),
            id="no-helper-and-the-terminal-refused",
        ),
    ],
)
async def test_write_async_classifies_every_outcome(
    result: ClipboardResult,
    terminal_written: bool,
    expected: ClipboardWrite,
) -> None:
    """The four-way decision is the VM's, and ``terminal_written`` is an input.

    Only the last two share a port result: "no helper" means something
    different depending on whether the terminal leg the App performed
    actually went out, and that distinction is what separates a usable
    OSC-52-only copy from nothing having been copied at all.
    """
    port = _ThreadRecordingClipboard(result)
    vm = _make_vm(port)
    try:
        write = await vm.write_async("s3://bucket/key", terminal_written=terminal_written)

        assert write == expected
        assert port.writes == ["s3://bucket/key"], "the port is written on every outcome"
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_the_blocking_port_call_is_offloaded_off_the_event_loop() -> None:
    """``xclip`` against a wedged X server runs to the port's own timeout.

    The offload is the whole reason this is a coroutine. Asserted on the
    thread identity rather than on a timing, so it cannot go quietly green
    on a fast machine if the ``run_sync`` is ever dropped.
    """
    port = _ThreadRecordingClipboard(ClipboardResult(ok=True, mechanism="pbcopy"))
    vm = _make_vm(port)
    try:
        await vm.write_async("value", terminal_written=True)

        assert port.threads, "precondition: the port was actually called"
        assert port.threads[0] != threading.get_ident()
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_the_outcome_is_published_as_view_model_state() -> None:
    """The classification is VM state a binder can read, not a local.

    ``ComponentVMOf`` publishes ``property_changed`` on the model
    assignment, which is what makes the outcome observable at all — the
    App's ``await`` is one reader of it, not the only possible one.
    """
    port = InMemoryClipboard(ok=False, mechanism="none")
    vm = _make_vm(port)
    changed: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=changed.append)
    try:
        assert vm.last_write is None, "precondition: nothing has been copied yet"

        returned = await vm.write_async("value", terminal_written=True)

        assert vm.last_write == returned
        assert vm.last_write is not None
        assert vm.last_write.channel is ClipboardChannel.TERMINAL_ONLY
        assert changed, "the model assignment must publish property_changed"
    finally:
        subscription.dispose()
        vm.dispose()
