"""Pass-12: Enter in a ConfirmModal must call action_confirm even
though the App declares ``Binding('enter', 'descend', priority=True)``
to navigate the dual-pane. ``_forward_to_modal`` routes Enter to
``ModalScreen.action_confirm`` when a modal is on top of the stack.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from tests.integration.conftest import AppContextBuilder
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"a" * 10))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"b" * 100))
    return fs


@pytest.mark.asyncio
async def test_enter_on_copy_confirm_modal_runs_copy(
    app_context_factory: AppContextBuilder,
) -> None:
    """Press c to open the modal, then Enter to confirm. Without the
    forward, Enter would descend into the cursor row instead."""
    fs = await _seed()
    ctx = app_context_factory(fs=fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        # Modal should be on the stack.
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Modal closed — Enter forwarded to action_confirm.
        assert not isinstance(app.screen, ConfirmModal), "Enter didn't close the confirm modal"
        assert app._crash_report is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_escape_on_delete_modal_cancels(
    app_context_factory: AppContextBuilder,
) -> None:
    fs = await _seed()
    ctx = app_context_factory(fs=fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        # No crash.
        assert app._crash_report is None  # type: ignore[attr-defined]
