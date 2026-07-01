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

from aws_tui.app import AwsTuiApp
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef
from aws_tui.ui.widgets.pane import EntryRow, Pane
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from tests.integration.conftest import AppContextBuilder
from tests.unit.domain._in_memory_fs import InMemoryFS


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
