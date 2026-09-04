"""Runtime smoke coverage for declared direct dependency floors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aioboto3
import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy
from aws_tui.infra.config_store import Config, ConfigStore, Defaults, Keybindings


@pytest.mark.asyncio
async def test_local_filesystem_floor_reads_and_writes(tmp_path: Path) -> None:
    async def source() -> AsyncIterator[bytes]:
        yield b"floor-compatible"

    fs = LocalFS(root=tmp_path)
    path = PathRef.from_posix("/payload.txt")

    await fs.write_stream(path, source(), total_size=16)

    stream = await fs.read_stream(path)
    chunks = [chunk async for chunk in stream]
    assert b"".join(chunks) == b"floor-compatible"
    assert [entry.name for entry in await fs.list(PathRef.from_posix("/"))] == ["payload.txt"]


def test_sqlglot_floor_enforces_read_only_policy() -> None:
    policy = ReadOnlySqlPolicy()

    assert policy.validate("SELECT * FROM analytics.events") == ("SELECT * FROM analytics.events")
    with pytest.raises(QueryRejectedError):
        policy.validate("DELETE FROM analytics.events")


@pytest.mark.asyncio
async def test_aioboto3_floor_constructs_public_s3_client() -> None:
    session = aioboto3.Session()

    async with session.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="floor-test",
        aws_secret_access_key="floor-test",
        endpoint_url="http://127.0.0.1:1",
    ) as client:
        assert client.meta.service_model.service_name == "s3"


@pytest.mark.asyncio
async def test_textual_and_tomli_w_floors_construct_and_round_trip(tmp_path: Path) -> None:
    store = ConfigStore(path=tmp_path / "config.toml")
    expected = Config(
        connections={},
        defaults=Defaults(theme="carbon"),
        keybindings=Keybindings(),
    )
    store.save(expected)

    ctx = build_app_context(
        config_dir=tmp_path / "app-config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)

    try:
        assert store.load() == expected
        assert app._app_ctx is ctx
    finally:
        await app._aws_tui_shutdown()
