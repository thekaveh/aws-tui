"""Tests for the ResumeVM facade."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.transfer_journal import TransferJournalEntry
from aws_tui.vm.chrome.resume_vm import (
    ResumeAction,
    ResumeVM,
    entry_summary,
    humanize_bytes,
)


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _entry(
    *, transfer_id: str = "abc", bytes_total: int | None = 1_000_000
) -> TransferJournalEntry:
    return TransferJournalEntry(
        transfer_id=transfer_id,
        source_uri=f"local:///tmp/{transfer_id}.bin",
        destination_uri=f"s3://bucket/uploads/{transfer_id}.bin",
        upload_id=f"mpu-{transfer_id}",
        bytes_total=bytes_total,
        started_at=datetime(2026, 6, 13, tzinfo=UTC),
        last_progress=datetime(2026, 6, 13, tzinfo=UTC),
        completed_parts=(1, 2),
        completed_etags=("e1", "e2"),
    )


def _build(*entries: TransferJournalEntry) -> ResumeVM:
    vm = ResumeVM(entries or (_entry(),), hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_initial_state() -> None:
    vm = _build(_entry(transfer_id="a"), _entry(transfer_id="b"))
    assert vm.is_open is False
    assert vm.count == 2
    assert vm.entries[0].transfer_id == "a"
    vm.dispose()


async def test_resume_all_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.resume_all_command.execute()
    assert await task is ResumeAction.RESUME_ALL
    vm.dispose()


async def test_abort_all_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.abort_all_command.execute()
    assert await task is ResumeAction.ABORT_ALL
    vm.dispose()


async def test_decide_each_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.decide_each_command.execute()
    assert await task is ResumeAction.DECIDE_EACH
    vm.dispose()


async def test_keep_for_later_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.keep_for_later_command.execute()
    assert await task is ResumeAction.KEEP_FOR_LATER
    vm.dispose()


async def test_ask_while_open_raises() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        await vm.ask()
    vm.keep_for_later_command.execute()
    await task
    vm.dispose()


async def test_dispose_while_open_resolves_keep() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.dispose()
    assert await task is ResumeAction.KEEP_FOR_LATER


def test_humanize_bytes_units() -> None:
    assert humanize_bytes(None) == "?"
    assert humanize_bytes(512) == "512 B"
    assert "kB" in humanize_bytes(2048)
    assert "MB" in humanize_bytes(5_000_000)


def test_entry_summary_includes_basename_and_total() -> None:
    e = _entry(transfer_id="alpha", bytes_total=4_000_000)
    summary = entry_summary(e)
    assert "alpha.bin" in summary
    assert "%" in summary


def test_entry_summary_unknown_total() -> None:
    e = _entry(transfer_id="beta", bytes_total=None)
    summary = entry_summary(e)
    assert "parts" in summary
