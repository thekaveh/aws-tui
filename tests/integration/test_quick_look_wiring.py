"""Integration: Space opens the Quick Look preview modal for the cursor file."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.pane import EntryRow
from aws_tui.ui.widgets.quick_look import QuickLook
from tests.integration.conftest import AppContextBuilder
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _inject_connection(ctx: object) -> None:
    ctx.config_store.path.write_text(  # type: ignore[attr-defined]
        '[defaults]\nconnection = "test"\n\n'
        "[connections.test]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9000"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
        'region = "us-east-1"\n'
    )


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"hello quick look"))
    return fs


@pytest.mark.asyncio
async def test_space_opens_quick_look(app_context_factory: AppContextBuilder) -> None:
    ctx = app_context_factory(fs=await _seed())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert list(app.query(EntryRow)), "pane entries did not mount"

        await pilot.press("space")
        await pilot.pause()

        assert isinstance(app.screen, QuickLook)
        content = app.screen.vm.content
        assert content is not None
        assert content.title == "alpha.txt"
        assert app._crash_report is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quick_look_noop_when_no_file(app_context_factory: AppContextBuilder) -> None:
    # Empty FS -> the pane has no cursor entry -> Space is a no-op (no modal,
    # no crash).
    ctx = app_context_factory(fs=InMemoryFS())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        app.action_quick_look()
        await pilot.pause()

        assert not isinstance(app.screen, QuickLook)
        assert app._crash_report is None  # type: ignore[attr-defined]
