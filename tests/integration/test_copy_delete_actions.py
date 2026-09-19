"""Integration coverage for action_copy + action_delete.

Regression guard: both commands previously escalated to the crash modal
during the launch flow. Exercise the full UI path (focus pane → press
key → confirm modal → run async op) against in-memory providers and
assert the user-visible filesystem mutation, not just "no crash".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef
from aws_tui.ui.widgets.pane import EntryRow, Pane
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from tests.integration.conftest import AppContextBuilder


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_left() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha-content"))
    await fs.write_stream(PathRef(("beta.txt",)), _stream(b"beta"))
    return fs


class _BlockingReadFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = asyncio.Event()
        self.read_cancelled = asyncio.Event()

    async def read_stream(
        self,
        path: PathRef,
        *,
        chunk_size: int = 8 * 1024 * 1024,
    ) -> AsyncIterator[bytes]:
        self.read_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.read_cancelled.set()
            raise

    async def stat(self, path: PathRef) -> FileEntry:
        if path.segments and path.segments[-1] == "alpha.txt":
            return FileEntry(
                name="alpha.txt",
                kind=EntryKind.FILE,
                size=10_000_000,
                modified=None,
            )
        return await super().stat(path)


def _use_injected_s3_connection(ctx: object) -> None:
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


async def _wait_until_names(
    pane_provider: object,
    *,
    contains: str | None = None,
    omits: str | None = None,
) -> list[str]:
    for _ in range(60):
        entries = await pane_provider.list(PathRef(()))  # type: ignore[attr-defined]
        names = [entry.name for entry in entries]
        if (contains is None or contains in names) and (omits is None or omits not in names):
            return names
        await asyncio.sleep(0.05)
    return names


async def _wait_until_entry_rows(app: AwsTuiApp) -> list[EntryRow]:
    for _ in range(100):
        rows = list(app.query(EntryRow))
        if rows:
            return rows
        await asyncio.sleep(0.01)
    return rows


@pytest.mark.asyncio
async def test_copy_action_with_confirm_does_not_crash(
    app_context_factory: AppContextBuilder,
) -> None:
    """Press 'c', confirm, and verify the destination pane receives the file."""
    fs = await _seed_left()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
        await pilot.pause()

        # Make sure the panes mounted with entries.
        panes = list(app.query(Pane))
        assert len(panes) == 2
        rows = await _wait_until_entry_rows(app)
        assert len(rows) > 0

        # Press 'c' — this opens ConfirmModal.
        await pilot.press("c")
        await pilot.pause()
        # Confirm with Enter.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        dual = ctx.root_vm.content_host.current
        assert isinstance(dual, DualPaneVM)
        names = await _wait_until_names(dual.right.provider, contains="alpha.txt")

        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"Copy command crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )
        assert "alpha.txt" in names


@pytest.mark.asyncio
async def test_delete_action_with_confirm_does_not_crash(
    app_context_factory: AppContextBuilder,
) -> None:
    """Press 'd', confirm, and verify the focused source file is deleted."""
    fs = await _seed_left()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        names = await _wait_until_names(fs, omits="alpha.txt")

        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"Delete command crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )
        assert "alpha.txt" not in names
        assert "beta.txt" in names


@pytest.mark.asyncio
async def test_mark_keys_do_not_change_delete_target_behind_confirm_modal(
    app_context_factory: AppContextBuilder,
) -> None:
    fs = await _seed_left()
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        # App-level priority bindings used to let Shift+Down mutate
        # the underlying pane while the confirm modal was open. Two
        # presses would mark both alpha and beta, so confirming the
        # "Delete alpha.txt?" modal deleted beta too.
        await pilot.press("shift+down")
        await pilot.press("shift+down")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        names = await _wait_until_names(fs, omits="alpha.txt")

        assert "alpha.txt" not in names
        assert "beta.txt" in names


@pytest.mark.asyncio
async def test_switching_to_settings_cancels_active_copy_worker(
    app_context_factory: AppContextBuilder,
) -> None:
    """Page swaps must stop in-flight copy bytes before disposing DualPaneVM."""
    fs = _BlockingReadFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha-content"))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()
        await pilot.press("enter")
        await asyncio.wait_for(fs.read_started.wait(), timeout=2.0)

        await pilot.press("comma")
        await pilot.pause()

        await asyncio.wait_for(fs.read_cancelled.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_delete_worker_does_not_cancel_an_in_flight_copy(
    app_context_factory: AppContextBuilder,
) -> None:
    """Copy and delete must not share an exclusive worker group.

    ``exclusive=True`` cancels the group's prior worker, and the copy worker
    awaits the entire byte-streaming batch — so confirming a delete killed a
    running transfer mid-batch, leaving a partial object at the destination.
    ``_run_copy`` catches only ``Exception``, so the resulting ``CancelledError``
    produced no toast and no log line either.
    """
    from aws_tui.app import (
        _TRANSFER_COPY_GROUP,
        _TRANSFER_DELETE_GROUP,
        _TRANSFER_WORKER_GROUPS,
    )

    assert _TRANSFER_COPY_GROUP != _TRANSFER_DELETE_GROUP
    # The content-swap cancellation must still reach both.
    assert set(_TRANSFER_WORKER_GROUPS) == {_TRANSFER_COPY_GROUP, _TRANSFER_DELETE_GROUP}

    cancelled: list[str] = []

    async def _long(name: str) -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    app = AwsTuiApp(app_context_factory(fs=await _seed_left()))
    async with app.run_test(size=(120, 40)):
        app.run_worker(_long("copy"), exclusive=True, group=_TRANSFER_COPY_GROUP)
        await asyncio.sleep(0)
        app.run_worker(_long("delete"), exclusive=True, group=_TRANSFER_DELETE_GROUP)
        await asyncio.sleep(0.05)

        assert cancelled == [], "a delete must not cancel an in-flight copy"

        # Exclusivity within a single group is retained.
        app.run_worker(_long("copy-2"), exclusive=True, group=_TRANSFER_COPY_GROUP)
        await asyncio.sleep(0.05)
        assert cancelled == ["copy"]


@pytest.mark.asyncio
async def test_cursor_fallback_copy_marks_the_row_it_is_acting_on(
    app_context_factory: AppContextBuilder,
) -> None:
    """A copy with nothing marked must show which row it is transferring.

    With no multi-selection the copy falls back to the cursor row, and the
    worker marks that row for the duration of the transfer — the only
    on-screen confirmation of what is moving. Two consequences are asserted,
    and only together do they pin the route the flash has to take:

    * The row repaints. It does that off its own
      ``EntryVM.on_property_changed`` binding, so a direct
      ``entry.set_marked`` from ``app.py`` would satisfy this half on its
      own. Asserted on the mounted widget's classes and glyph, not on
      ``entry_vm.is_marked``: the model flag stayed correct throughout the
      regression this pins, while the screen showed nothing.
    * The pane's footer Static repaints with the marked count. Nothing
      refreshes that widget except ``Pane._refresh_chrome``, and nothing
      calls it except the pane-level ``"viewmodel"`` notify — which only
      ``PaneVM.set_marked_entries`` emits. A bypass straight to
      ``EntryVM.set_marked`` repaints the row and leaves the footer on
      screen stating the wrong count, which is exactly the failure
      ``PaneVM.set_marked_entries``'s own docstring describes. Asserted on
      the rendered Static and NOT on ``pane.viewmodel.summary``: that
      property is derived on every read, so it reports the new count
      whether or not anything was ever notified.

    ``_BlockingReadFS`` holds the transfer open so the flash can be observed
    mid-flight; the app is torn down with the worker still running, exactly
    as ``test_switching_to_settings_cancels_active_copy_worker`` does.
    """
    fs = _BlockingReadFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha-content"))
    ctx = app_context_factory(fs=fs)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        rows = await _wait_until_entry_rows(app)
        target = next(row for row in rows if row.entry_vm.name == "alpha.txt")
        assert "-marked" not in target.classes, "precondition: nothing is marked yet"

        dual = ctx.root_vm.content_host.current
        assert isinstance(dual, DualPaneVM)
        src_pane = dual.focused_pane
        src_widget = next(pane for pane in app.query(Pane) if pane.vm is src_pane)
        footer = src_widget.query_one(".pane-footer", Static)
        footer_before = str(footer.render_line(0).text)
        assert "marked" not in footer_before, "precondition: the footer counts no marks"

        await pilot.press("c")
        await pilot.pause()
        await pilot.press("enter")
        await asyncio.wait_for(fs.read_started.wait(), timeout=2.0)
        await pilot.pause()
        await pilot.pause()

        assert "-marked" in target.classes, (
            "the cursor-fallback row is not painted as the copy's target"
        )
        assert "*" in target.render_line(0).text
        # The half only the owning view model can satisfy. ``set_marked_entries``
        # emits the pane-level ``"viewmodel"`` notify that drives
        # ``Pane._refresh_chrome``; the rows' own bindings do not, so a bypass
        # to ``EntryVM.set_marked`` paints the row above and leaves this
        # Static showing the pre-transfer line.
        footer_during = str(footer.render_line(0).text)
        assert footer_during != footer_before
        assert "1 marked" in footer_during, footer_during

        # Cancel the in-flight worker so teardown is not racing it.
        await pilot.press("comma")
        await pilot.pause()
        await asyncio.wait_for(fs.read_cancelled.wait(), timeout=2.0)

        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"the mark flash crashed the app: {app._crash_report}"  # type: ignore[attr-defined]
        )
