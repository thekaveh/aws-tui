"""Slow S3 listings must leave the actual keyboard quit path responsive."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from textual.containers import VerticalScroll

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import EntryKind, FileEntry, PathRef, ProviderError
from aws_tui.ui.widgets.pane import Pane
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import focus_and_settle, wait_until
from tests.integration.conftest import AppContextBuilder


class _GatedListingFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.block_listing = False
        self.list_started = asyncio.Event()
        self.release_listing = asyncio.Event()
        self.list_cancelled = asyncio.Event()

    async def list(self, path: PathRef) -> list[FileEntry]:
        if self.block_listing:
            self.list_started.set()
            try:
                await self.release_listing.wait()
            except asyncio.CancelledError:
                self.list_cancelled.set()
                raise
        if path.is_root:
            return [FileEntry("folder", EntryKind.DIRECTORY, None, None)]
        return []


class _QuitObservedApp(AwsTuiApp):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        self.quit_requested = asyncio.Event()

    async def action_quit(self) -> None:
        self.quit_requested.set()
        await super().action_quit()


class _LateListingFS(_GatedListingFS):
    async def list(self, path: PathRef) -> list[FileEntry]:
        if self.block_listing:
            self.block_listing = False
            self.list_started.set()
            try:
                await self.release_listing.wait()
            except asyncio.CancelledError:
                self.list_cancelled.set()
                # Model a provider that finishes cleanup and returns late.
                await self.release_listing.wait()
            return [FileEntry("old.txt", EntryKind.FILE, 1, None)]
        if self.list_started.is_set():
            return [FileEntry("new.txt", EntryKind.FILE, 1, None)]
        return await super().list(path)


class _FailingListingFS(_GatedListingFS):
    fail_next = False

    async def list(self, path: PathRef) -> list[FileEntry]:
        if self.fail_next:
            self.fail_next = False
            raise ProviderError("synthetic listing failure")
        return await super().list(path)


def _isolate_runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import aws_tui.infra.paths as runtime_paths

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(runtime_paths, "config_home", lambda: tmp_path / "config")
    monkeypatch.setattr(runtime_paths, "cache_home", lambda: tmp_path / "cache")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path.resolve()))


@pytest.mark.parametrize("listing_key", ["r", "enter", "backspace", "S"])
async def test_quit_is_handled_while_s3_listing_is_blocked(
    listing_key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    # Default theme/cache/SSO lookups also remain inside this owned temp tree.
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _GatedListingFS()
    app = _QuitObservedApp(app_context_factory(fs=provider))

    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: bool(app.query(Pane)) and bool(app.query(Pane).first().vm.entries),
            what="initial synthetic S3 listing",
        )
        pane = app.query(Pane).first()
        if listing_key == "S":
            pane = app.query(Pane).last()
            dual = app.app_ctx.root_vm.content_host.current
            await pilot.press("tab")
            await wait_until(lambda: dual.focused_pane is pane.vm, what="local right pane focused")
            assert pane.vm.provider is not provider
        if listing_key == "backspace":
            await pane.vm.navigate_to(PathRef(("folder",)))
        await focus_and_settle(pane.query_one("#pane-body", VerticalScroll), timeout=2.0)
        provider.block_listing = True
        listing_press = asyncio.create_task(pilot.press(listing_key))
        quit_press: asyncio.Task[None] | None = None
        quit_before_release = False
        try:
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            quit_press = asyncio.create_task(pilot.press("q"))
            try:
                await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
                quit_before_release = True
                await asyncio.wait_for(provider.list_cancelled.wait(), timeout=2.0)
            except TimeoutError:
                pass
        finally:
            # Release even on failure so the red regression never wedges teardown.
            provider.release_listing.set()
            presses = [listing_press]
            if quit_press is not None:
                presses.append(quit_press)
            await asyncio.wait_for(asyncio.gather(*presses, return_exceptions=True), timeout=5.0)

        await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
        assert quit_before_release, (
            f"q was not handled while S3 {listing_key!r} awaited its listing; "
            "input dispatch remained blocked until the provider gate was released"
        )


async def test_superseded_refresh_cannot_publish_cancellation_resistant_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _LateListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: bool(app.query(Pane)) and bool(app.query(Pane).first().vm.entries),
            what="initial synthetic S3 listing",
        )
        pane = app.query(Pane).first()
        await focus_and_settle(pane.query_one("#pane-body", VerticalScroll), timeout=2.0)
        dual = app.app_ctx.root_vm.content_host.current
        provider.block_listing = True
        try:
            await pilot.press("r")
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            await pilot.press("r")
            await asyncio.wait_for(provider.list_cancelled.wait(), timeout=2.0)
            await wait_until(
                lambda: [entry.entry.name for entry in pane.vm.entries] == ["new.txt"],
                what="latest refresh result",
                timeout=2.0,
            )
        finally:
            provider.release_listing.set()
        await wait_until(lambda: not dual._refresh_tasks, what="all superseded reads drained")
        assert [entry.entry.name for entry in pane.vm.entries] == ["new.txt"]


async def test_deferred_listing_error_settles_and_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _FailingListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: bool(app.query(Pane)) and bool(app.query(Pane).first().vm.entries),
            what="initial synthetic S3 listing",
        )
        pane = app.query(Pane).first()
        await focus_and_settle(pane.query_one("#pane-body", VerticalScroll), timeout=2.0)
        dual = app.app_ctx.root_vm.content_host.current
        provider.fail_next = True
        await pilot.press("r")
        await wait_until(lambda: pane.vm.state is PaneState.ERROR, what="listing error published")
        await wait_until(lambda: not dual._refresh_tasks, what="failed listing task settled")
        assert pane.vm.viewmodel.error_text == "synthetic listing failure"
        await pilot.press("r")
        await wait_until(lambda: pane.vm.state is PaneState.IDLE, what="listing retry recovered")
        await wait_until(lambda: not dual._refresh_tasks, what="retry task settled")
        assert pane.vm.viewmodel.error_text is None
        assert [entry.entry.name for entry in pane.vm.entries] == ["folder"]


async def test_repeated_enter_during_listing_cannot_activate_an_old_invisible_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _GatedListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: bool(app.query(Pane)) and bool(app.query(Pane).first().vm.entries),
            what="initial synthetic S3 listing",
        )
        pane = app.query(Pane).first()
        await focus_and_settle(pane.query_one("#pane-body", VerticalScroll), timeout=2.0)
        provider.block_listing = True
        try:
            await pilot.press("enter")
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            assert pane.vm.path == PathRef(("folder",))
            assert pane.vm.state is PaneState.LOADING
            await pilot.press("enter")
            await pilot.pause()
            assert pane.vm.path == PathRef(("folder",))
            assert not provider.list_cancelled.is_set()
        finally:
            provider.release_listing.set()
        await wait_until(lambda: pane.vm.state is PaneState.IDLE, what="intended listing settles")


async def test_source_swap_preserves_opposite_pane_inflight_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _GatedListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: len(app.query(Pane)) == 2 and all(p.vm.entries for p in app.query(Pane)),
            what="both synthetic S3 panes ready",
        )
        left, right = app.query(Pane)
        await pilot.pause()
        dual = app.app_ctx.root_vm.content_host.current
        await pilot.press("tab")
        await wait_until(lambda: dual.focused_pane is right.vm, what="right pane selected by Tab")
        await pilot.press("S")
        await wait_until(
            lambda: right.vm.provider is provider and right.vm.state is PaneState.IDLE,
            what="right pane switched from local to synthetic S3",
        )
        provider.block_listing = True
        try:
            await pilot.press("r")
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            assert right.vm.state is PaneState.LOADING
            await pilot.press("shift+tab")
            await wait_until(
                lambda: dual.focused_pane is left.vm, what="left pane selected by Shift+Tab"
            )
            await pilot.press("S")
            await wait_until(lambda: left.vm.provider is not provider, what="left source switched")
            assert right.vm.provider is provider
            assert not provider.list_cancelled.is_set()
        finally:
            provider.release_listing.set()
        await wait_until(
            lambda: right.vm.state is PaneState.IDLE,
            what="unchanged right source completes its original refresh",
        )


async def test_queued_source_swap_cannot_move_to_a_newly_focused_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _GatedListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: len(app.query(Pane)) == 2 and all(p.vm.entries for p in app.query(Pane)),
            what="both panes ready before queued source change",
        )
        left, right = app.query(Pane)
        dual = app.app_ctx.root_vm.content_host.current
        await focus_and_settle(left.query_one("#pane-body", VerticalScroll), timeout=2.0)
        sources = (left.vm.provider, right.vm.provider)
        generation = app._table_navigation_generation
        await app._service_navigation_lock.acquire()
        try:
            await pilot.press("S")
            await wait_until(
                lambda: app._table_navigation_generation > generation,
                what="source action waiting for navigation ownership",
            )
            await pilot.press("tab")
            await wait_until(lambda: dual.focused_pane is right.vm, what="new right pane focus")
        finally:
            app._service_navigation_lock.release()
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5.0)
        assert (left.vm.provider, right.vm.provider) == sources


async def test_repeat_source_switch_cancels_the_loading_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _GatedListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: len(app.query(Pane)) == 2 and all(p.vm.entries for p in app.query(Pane)),
            what="initial sources ready",
        )
        right = app.query(Pane).last()
        dual = app.app_ctx.root_vm.content_host.current
        await pilot.press("tab")
        await wait_until(lambda: dual.focused_pane is right.vm, what="local right pane focused")
        provider.block_listing = True
        try:
            await pilot.press("S")
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            assert right.vm.provider is provider
            await pilot.press("S")
            await asyncio.wait_for(provider.list_cancelled.wait(), timeout=2.0)
            await wait_until(
                lambda: right.vm.provider is not provider and right.vm.state is PaneState.IDLE,
                what="second source intent completes without the old listing",
            )
        finally:
            provider.release_listing.set()


@pytest.mark.parametrize("switch_key", ["S", ","])
async def test_source_or_service_swap_drains_interactive_listing_cleanup(
    switch_key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _LateListingFS()
    app = AwsTuiApp(app_context_factory(fs=provider))
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            lambda: bool(app.query(Pane)) and bool(app.query(Pane).first().vm.entries),
            what="initial synthetic S3 listing",
        )
        pane = app.query(Pane).first()
        await focus_and_settle(pane.query_one("#pane-body", VerticalScroll), timeout=2.0)
        dual = app.app_ctx.root_vm.content_host.current
        provider.block_listing = True
        switch_press: asyncio.Task[None] | None = None
        try:
            await pilot.press("r")
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            switch_press = asyncio.create_task(pilot.press(switch_key))
            await asyncio.wait_for(provider.list_cancelled.wait(), timeout=2.0)
            assert pane.vm.provider is provider
            assert not dual._disposed
            assert dual._refresh_tasks
        finally:
            provider.release_listing.set()
            if switch_press is not None:
                await asyncio.wait_for(switch_press, timeout=5.0)
        await wait_until(lambda: not dual._refresh_tasks, what="outgoing listing cleanup drained")
        if switch_key == "S":
            assert pane.vm.provider is not provider
        else:
            await wait_until(lambda: dual._disposed, what="old S3 view disposed")
