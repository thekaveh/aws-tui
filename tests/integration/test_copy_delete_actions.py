"""Smoke: action_copy + action_delete must NOT crash the running app.

User-reported pass-9 regression: both commands escalate to the crash
modal during the launch flow. We can't reproduce the real S3 backend
here, but we can exercise the full UI path (focus pane → mark → press
key → confirm modal → run async op) against in-memory providers and
ensure no exception escapes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.pane import EntryRow, Pane
from tests.integration.conftest import AppContextBuilder
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_left() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha-content"))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"beta"))
    return fs


@pytest.mark.asyncio
async def test_copy_action_with_confirm_does_not_crash(
    app_context_factory: AppContextBuilder,
) -> None:
    """Press 'c', confirm in the modal, verify no exception bubbles."""
    fs = await _seed_left()
    ctx = app_context_factory(fs=fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Make sure the panes mounted with entries.
        panes = list(app.query(Pane))
        assert len(panes) == 2
        rows = list(app.query(EntryRow))
        assert len(rows) > 0

        # Press 'c' — this opens ConfirmModal.
        await pilot.press("c")
        await pilot.pause()
        # Confirm with Enter.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        # Verify the app is still alive — no crash modal pushed, no
        # unhandled exception captured in the AwsTuiApp.crash_report.
        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"Copy command crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_delete_action_with_confirm_does_not_crash(
    app_context_factory: AppContextBuilder,
) -> None:
    """Press 'd', confirm in the modal, verify no exception bubbles."""
    fs = await _seed_left()
    ctx = app_context_factory(fs=fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"Delete command crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )
