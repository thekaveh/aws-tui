from __future__ import annotations

import asyncio

import pytest

from aws_tui.vm.glue._lifecycle import GlueOperationOwner


@pytest.mark.asyncio
async def test_provider_self_cancellation_is_not_reported_as_superseded() -> None:
    owner = GlueOperationOwner()

    async def self_cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await owner.run(self_cancel)
