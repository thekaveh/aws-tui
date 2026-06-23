"""Tests for the ContentHostVM."""

from __future__ import annotations

import asyncio
from typing import cast

from vmx import NULL_DISPATCHER, ComponentVM, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.vm.content_host_vm import ContentHostVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _component() -> ComponentVM:
    return ComponentVM.builder().name("test").with_null_services().build()


def _build() -> ContentHostVM:
    vm = ContentHostVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


async def test_set_content_constructs_new_vm() -> None:
    host = _build()
    child = _component()
    await host.set_content(child, service_id="ec2")
    assert host.current is child
    assert host.current_id == "ec2"
    assert child.is_constructed
    host.dispose()


async def test_set_content_disposes_old_vm() -> None:
    host = _build()
    first = _component()
    second = _component()
    await host.set_content(first, service_id="ec2")
    await host.set_content(second, service_id="s3")
    assert first.status == ConstructionStatus.DISPOSED
    assert second.is_constructed
    assert host.current is second
    assert host.current_id == "s3"
    host.dispose()


async def test_set_content_same_service_is_noop() -> None:
    host = _build()
    first = _component()
    second = _component()
    await host.set_content(first, service_id="ec2")
    await host.set_content(second, service_id="ec2")
    # Second call is a no-op: first stays, second is NOT constructed.
    assert host.current is first
    assert first.is_constructed
    assert second.status == ConstructionStatus.DESTRUCTED
    host.dispose()


async def test_dispose_disposes_current_content() -> None:
    host = _build()
    child = _component()
    await host.set_content(child, service_id="ec2")
    host.dispose()
    assert child.status == ConstructionStatus.DISPOSED


async def test_set_content_awaits_setup_when_vm_defines_one() -> None:
    """Regression: production-path `DualPaneVM` exposes async `setup()` that
    runs `provider.list()` on each pane. The host must await it after
    `construct()` or the panes render empty. Cf. `S3Service.build_vm`'s
    docstring requiring this contract.
    """

    class _SetupVM:
        def __init__(self) -> None:
            self.constructed = False
            self.setup_called = False
            self.is_constructed = False
            self.status = ConstructionStatus.DESTRUCTED

        def construct(self) -> None:
            self.constructed = True
            self.is_constructed = True
            self.status = ConstructionStatus.CONSTRUCTED

        async def setup(self) -> None:
            assert self.constructed, "setup must run after construct"
            self.setup_called = True

        def dispose(self) -> None:
            self.status = ConstructionStatus.DISPOSED

    host = _build()
    vm = _SetupVM()
    await host.set_content(cast("ComponentVM", vm), service_id="s3")
    # ``set_content`` returns as soon as the VM is constructed and
    # adopted — ``setup`` runs as a background asyncio task so a slow
    # listing (e.g. ``S3FS.list`` on an unreachable endpoint, blocking
    # botocore's 60-second retry budget) can't gate the view from
    # mounting the freshly-adopted VM. ``construct`` must already have
    # run synchronously inside the await; ``setup_called`` flips once
    # the background task gets event-loop time.
    assert vm.constructed
    assert host._setup_task is not None  # task was scheduled
    await host._setup_task  # wait for it to complete
    assert vm.setup_called
    host.dispose()


async def test_set_content_does_not_block_on_slow_setup() -> None:
    """Adoption + ``"current"`` message fire before ``setup`` awaits a
    slow operation — the user-perceptible regression that motivated the
    refactor was a 60-second blank screen at startup when the resolved
    s3-compatible endpoint was unreachable. Verify the await returns
    promptly even when ``setup`` would block forever.
    """

    setup_started = asyncio.Event()
    release_setup = asyncio.Event()

    class _SlowSetupVM:
        def __init__(self) -> None:
            self.is_constructed = False
            self.status = ConstructionStatus.DESTRUCTED

        def construct(self) -> None:
            self.is_constructed = True
            self.status = ConstructionStatus.CONSTRUCTED

        async def setup(self) -> None:
            setup_started.set()
            await release_setup.wait()

        def dispose(self) -> None:
            self.status = ConstructionStatus.DISPOSED

    host = _build()
    vm = _SlowSetupVM()
    # If ``set_content`` were still awaiting setup inline, this await
    # would hang forever because ``release_setup`` is never set.
    await asyncio.wait_for(
        host.set_content(cast("ComponentVM", vm), service_id="s3"),
        timeout=2.0,
    )
    assert host.current is vm
    assert host.current_id == "s3"
    # The setup task is scheduled and has started running.
    await asyncio.wait_for(setup_started.wait(), timeout=1.0)
    # Release the task so dispose cleans up cleanly.
    release_setup.set()
    if host._setup_task is not None:
        await host._setup_task
    host.dispose()


async def test_set_content_cancels_prior_setup_task_on_swap() -> None:
    """Re-entering ``set_content`` with a different service id must
    cancel the prior VM's setup task — otherwise the cancelled VM's
    setup keeps running against the now-disposed state.
    """

    release_first = asyncio.Event()
    first_was_cancelled = asyncio.Event()

    class _FirstVM:
        def __init__(self) -> None:
            self.is_constructed = False
            self.status = ConstructionStatus.DESTRUCTED

        def construct(self) -> None:
            self.is_constructed = True
            self.status = ConstructionStatus.CONSTRUCTED

        async def setup(self) -> None:
            try:
                await release_first.wait()
            except asyncio.CancelledError:
                first_was_cancelled.set()
                raise

        def dispose(self) -> None:
            self.status = ConstructionStatus.DISPOSED

    host = _build()
    first = _FirstVM()
    await host.set_content(cast("ComponentVM", first), service_id="s3")
    # Yield once so the first setup task actually starts running
    # (otherwise the cancel below pre-empts it before its first
    # ``await`` ever runs and the ``except CancelledError`` block
    # never executes).
    await asyncio.sleep(0)
    second = _component()
    await host.set_content(second, service_id="ec2")
    await asyncio.wait_for(first_was_cancelled.wait(), timeout=1.0)
    assert host.current is second
    host.dispose()


async def test_set_content_none_clears_current() -> None:
    host = _build()
    child = _component()
    await host.set_content(child, service_id="ec2")
    await host.set_content(None, service_id=None)
    assert host.current is None
    assert host.current_id is None
    assert child.status == ConstructionStatus.DISPOSED
    host.dispose()


def test_initial_state() -> None:
    host = _build()
    assert host.current is None
    assert host.current_id is None
    host.dispose()
