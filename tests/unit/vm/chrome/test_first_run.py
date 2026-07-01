"""Tests for the FirstRunVM facade."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.first_run_vm import FirstRunAction, FirstRunVM, S3CompatForm


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build() -> FirstRunVM:
    vm = FirstRunVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def test_initial_state() -> None:
    vm = _build()
    assert vm.is_open is False
    vm.dispose()


async def test_add_aws_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.add_aws_command.execute()
    assert await task is FirstRunAction.ADD_AWS
    vm.dispose()


async def test_add_s3_compat_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.add_s3_compat_command.execute()
    assert await task is FirstRunAction.ADD_S3_COMPAT
    vm.dispose()


async def test_skip_resolves() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.skip_command.execute()
    assert await task is FirstRunAction.SKIP
    vm.dispose()


async def test_ask_while_open_raises() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        await vm.ask()
    vm.skip_command.execute()
    await task
    vm.dispose()


async def test_dispose_while_open_resolves_skip() -> None:
    vm = _build()
    task = asyncio.create_task(vm.ask())
    await asyncio.sleep(0)
    vm.dispose()
    assert await task is FirstRunAction.SKIP


def test_form_is_valid_when_all_fields_set() -> None:
    f = S3CompatForm(
        name="minio",
        endpoint_url="https://user:pass@example.com/bucket?X-Amz-Signature=sig",
        region="us-east-1",
        access_key_id="AKID",
        secret_access_key="SECRET",
    )
    assert f.is_valid() is True


def test_form_is_invalid_when_field_missing() -> None:
    f = S3CompatForm(
        name="minio",
        endpoint_url="",
        region="us-east-1",
        access_key_id="AKID",
        secret_access_key="SECRET",
    )
    assert f.is_valid() is False


def test_form_repr_masks_credentials() -> None:
    f = S3CompatForm(
        name="minio",
        endpoint_url="https://user:pass@example.com/bucket?X-Amz-Signature=sig",
        region="us-east-1",
        access_key_id="AKID",
        secret_access_key="SECRET",
        session_token="TOKEN",
    )

    rendered = repr(f)

    assert "minio" in rendered
    assert "AKID" not in rendered
    assert "SECRET" not in rendered
    assert "TOKEN" not in rendered
    assert "user" not in rendered
    assert "pass" not in rendered
    assert "X-Amz-Signature" not in rendered
    assert "sig" not in rendered
    assert "endpoint_url='example.com/bucket'" in rendered
    assert "access_key_id='***'" in rendered
    assert "secret_access_key='***'" in rendered
    assert "session_token='***'" in rendered
