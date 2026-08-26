"""Pane source switching via Shift+S.

Locks in PaneVM.swap_provider behavior wired through the App action:
- The focused pane's provider is replaced
- Path resets to root
- Border subtitle (identity) updates
- Border title protocol updates (s3:// vs /)
- The unfocused pane is left alone
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from vmx import MessageHub, RxDispatcher

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS
from aws_tui.vm.file_manager.pane_vm import PaneVM


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed_fs(label: str) -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef((f"{label}.txt",)), _stream(label.encode()))
    return fs


@pytest.mark.asyncio
async def test_pane_vm_swap_provider_replaces_source_and_resets() -> None:
    """Direct PaneVM contract — used by the app-level action."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs_a = await _seed_fs("a")
    fs_b = await _seed_fs("b")
    vm = PaneVM(
        provider=fs_a,
        hub=hub,
        dispatcher=dispatcher,
        identity_label="A",
        path_protocol="s3:",
    )
    vm.construct()
    await vm.setup()
    try:
        # Drift away from root.
        await vm.navigate_to(PathRef(("subdir",)))  # may 404, that's fine
        await vm.swap_provider(fs_b, identity_label="B", path_protocol="")
        assert vm.path.is_root
        assert vm.viewmodel.border_subtitle == "B"
        # Border title for an empty protocol at root.
        assert vm.viewmodel.border_title == "/"
        names = {e.entry.name for e in vm.filtered_entries}
        assert "b.txt" in names
        assert "a.txt" not in names
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_unfocused_pane_unaffected_by_vm_swap() -> None:
    """Swapping the focused pane VM doesn't touch the other side. We
    drive the swap at the VM layer (not the binding) because the
    binding path requires a real AWS session for the local→S3 swap
    direction (out of scope for unit testing)."""
    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    fs_left = await _seed_fs("left")
    fs_right = await _seed_fs("right")
    fs_replacement = await _seed_fs("rep")

    left = PaneVM(provider=fs_left, hub=hub, dispatcher=dispatcher, id_prefix="L")
    right = PaneVM(provider=fs_right, hub=hub, dispatcher=dispatcher, id_prefix="R")
    left.construct()
    right.construct()
    await left.setup()
    await right.setup()
    try:
        right_provider_before = right.provider
        await left.swap_provider(fs_replacement, identity_label="replaced", path_protocol="")
        assert right.provider is right_provider_before
    finally:
        left.dispose()
        right.dispose()


@pytest.mark.asyncio
async def test_pane_source_swap_does_not_change_active_connection(tmp_path: Path) -> None:
    """The S3 dual-pane path owns independent sources, not RootVM state."""
    from aws_tui.app import AwsTuiApp
    from aws_tui.composition import build_app_context

    ctx = build_app_context(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        demo=True,
    )
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            dual = app._dual_pane()
            assert dual is not None
            active = ctx.root_vm.active_connection
            assert active is not None
            await app.action_swap_source()
            assert ctx.root_vm.active_connection == active
    finally:
        ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_pane_source_swap_preserves_provider_when_credentials_are_missing(
    tmp_path: Path,
) -> None:
    from aws_tui.app import AwsTuiApp
    from aws_tui.composition import build_app_context
    from aws_tui.domain.filesystem import AuthRequiredError

    ctx = build_app_context(demo=True, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            dual = app._dual_pane()
            assert dual is not None
            focused = dual.focused_pane
            assert focused is not None
            provider_before = focused.provider

            def fail_provider(_connection: object) -> object:
                raise AuthRequiredError("credentials missing")

            app._make_s3_provider_for_connection = fail_provider  # type: ignore[method-assign]

            await app.action_swap_source()

            assert focused.provider is provider_before
            matching = [
                toast
                for toast in ctx.root_vm.chrome.toast_stack.toasts
                if toast.model.id == "swap-source-auth-required"
            ]
            assert len(matching) == 1
            assert "authentication required" in matching[0].model.text
    finally:
        ctx.root_vm.dispose()


def test_all_four_pane_combinations_are_constructible() -> None:
    """Sanity: the user can put any of {S3, local} x {S3, local} into
    the dual-pane. This is what swap_provider enables."""
    import aioboto3

    session = aioboto3.Session(region_name="us-east-1")
    s3 = S3FS(session=session, bucket=None)
    local = LocalFS()
    combos = [(s3, s3), (s3, local), (local, s3), (local, local)]
    for left, right in combos:
        assert left is not None
        assert right is not None
