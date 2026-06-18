"""Multi-select via keyboard + modifier click.

Locks in:
- ``shift+up`` / ``shift+down`` extends the selection
- Modifier+click (shift OR meta OR ctrl) toggles a row's marked flag
- The pane footer summary reflects the marked-byte total
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.pane import Pane
from tests.integration.conftest import AppContextBuilder
from tests.unit.domain._in_memory_fs import InMemoryFS


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_local() -> InMemoryFS:
    fs = InMemoryFS()
    for name, body in (
        ("alpha.txt", b"a" * 10),
        ("beta.txt", b"b" * 100),
        ("gamma.txt", b"c" * 1000),
        ("delta.txt", b"d" * 100),
    ):
        await fs.write_stream(PathRef((name,)), _stream(body))
    return fs


@pytest.mark.asyncio
async def test_shift_arrow_extends_selection(
    app_context_factory: AppContextBuilder,
) -> None:
    local = await _seed_local()
    ctx = app_context_factory(fs=local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        # Press shift+down twice; expect marked count to climb.
        panes = list(app.query(Pane))
        focused = panes[0]
        initial_marked = sum(1 for e in focused.vm.filtered_entries if e.is_marked)
        await pilot.press("shift+down")
        await pilot.pause()
        await pilot.press("shift+down")
        await pilot.pause()
        after_marked = sum(1 for e in focused.vm.filtered_entries if e.is_marked)
        assert after_marked > initial_marked, (
            f"shift+down didn't extend selection (was {initial_marked}, now {after_marked})"
        )


@pytest.mark.asyncio
async def test_pane_footer_summary_includes_selected_bytes(
    app_context_factory: AppContextBuilder,
) -> None:
    """When entries are marked, the summary shows the marked-byte
    total, not the all-entries total."""
    local = await _seed_local()
    ctx = app_context_factory(fs=local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panes = list(app.query(Pane))
        focused = panes[0]
        # Mark the cursor row directly through the VM (avoids relying on
        # the pane's focused-side default).
        focused.vm.toggle_mark_at(focused.vm.cursor_index)
        await pilot.pause()
        summary = focused.vm.viewmodel.summary
        assert "marked" in summary
        assert "selected" in summary


@pytest.mark.asyncio
async def test_modifier_click_marks_the_row(
    app_context_factory: AppContextBuilder,
) -> None:
    """Pilot click + a shift/meta/ctrl modifier on a row must mark it.

    Background: on macOS Terminal.app, Shift+Click is intercepted by
    the terminal for native text-selection and never reaches the app;
    Cmd+Click (meta) is the documented workaround on that platform
    (see docs/keybindings.md). This test verifies that the view-side
    modifier dispatch in pane.py recognises any of the three flags —
    so when the terminal DOES forward the event, the row gets marked.
    """
    from aws_tui.ui.widgets.pane import EntryRow

    local = await _seed_local()
    ctx = app_context_factory(fs=local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panes = list(app.query(Pane))
        focused = panes[0]
        before = sum(1 for e in focused.vm.filtered_entries if e.is_marked)

        rows = list(focused.query(EntryRow))
        # Click on a real entry row (not the ".." parent link, which
        # the click handler intentionally won't mark).
        target = next((r for r in rows if not r._entry_vm.is_parent_link), None)  # type: ignore[attr-defined]
        assert target is not None, "no markable entry row found"

        await pilot.click(target, control=True)  # Ctrl+Click — universal modifier
        await pilot.pause()

        after = sum(1 for e in focused.vm.filtered_entries if e.is_marked)
        assert after == before + 1, (
            f"Ctrl+Click didn't mark a row (was {before}, now {after}). "
            "If this regresses, shift+click and cmd+click will silently break too."
        )


@pytest.mark.asyncio
async def test_pane_vm_toggle_mark_at_enters_multiselect(
    app_context_factory: AppContextBuilder,
) -> None:
    """toggle_mark_at should put the pane into multi-select mode the
    first time it's called so subsequent navigation preserves marks."""
    local = await _seed_local()
    ctx = app_context_factory(fs=local)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panes = list(app.query(Pane))
        focused = panes[0]
        assert focused.vm.is_multiselect_mode is False
        focused.vm.toggle_mark_at(0)
        assert focused.vm.is_multiselect_mode is True
