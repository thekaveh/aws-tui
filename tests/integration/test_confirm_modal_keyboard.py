"""Enter in a ConfirmModal must call action_confirm even though the
App declares ``Binding('enter', 'descend', priority=True)`` to
navigate the dual-pane. ``_forward_to_modal`` routes Enter to
``ModalScreen.action_confirm`` when a modal is on top of the stack.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.widgets import TextArea

from aws_tui.app import AwsTuiApp
from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.emr_serverless.log_filter_modal import LogFilterModal
from aws_tui.vm.chrome.crash_vm import CrashChoice, CrashReport, CrashVM
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
        assert ctx.confirm_vm.is_open
        assert app.screen.vm is ctx.confirm_vm
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Modal closed — Enter forwarded to action_confirm.
        assert not isinstance(app.screen, ConfirmModal), "Enter didn't close the confirm modal"
        assert not ctx.confirm_vm.is_open
        dual = ctx.root_vm.content_host.current
        assert dual is not None
        for _ in range(40):
            names = [entry.name for entry in await dual.right.provider.list(PathRef(()))]
            if "alpha.txt" in names:
                break
            await pilot.pause()
        assert "alpha.txt" in names
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


@pytest.mark.asyncio
async def test_repeated_copy_input_does_not_open_a_second_confirmation(
    app_context_factory: AppContextBuilder,
) -> None:
    fs = await _seed()
    ctx = app_context_factory(fs=fs)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        first_modal = app.screen

        await pilot.press("c")
        await pilot.pause()

        assert app.screen is first_modal
        assert ctx.confirm_vm.is_open
        assert app._crash_report is None  # type: ignore[attr-defined]
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_enter_in_modal_text_area_inserts_newline(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        modal = LogFilterModal(DEFAULT_LOG_FILTER)
        await app.push_screen(modal)
        editor = modal.query_one("#log-patterns", TextArea)
        editor.focus()
        before = editor.text

        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is modal
        assert editor.text == "\n" + before


@pytest.mark.asyncio
async def test_enter_on_crash_modal_uses_safe_default(
    app_context_factory: AppContextBuilder,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    report = CrashReport(
        timestamp=datetime(2026, 8, 2, tzinfo=UTC),
        exception_type="RuntimeError",
        exception_message="test",
        traceback_short="trace",
        dump_path=Path("/tmp/aws-tui-test-crash.txt"),
        can_continue=True,
    )
    vm = CrashVM(report, hub=ctx.hub, dispatcher=ctx.dispatcher)
    vm.construct()
    choices: list[CrashChoice] = []
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.push_screen(CrashModal(vm, hub=ctx.hub), callback=choices.append)
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            assert choices == [CrashChoice.CONTINUE]
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_failed_service_switch_keeps_existing_view_mounted(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        host = app.query_one("#content-host")
        prior_children = tuple(host.children)

        async def fail_switch(_service_id: str) -> None:
            raise RuntimeError("service build failed")

        monkeypatch.setattr(ctx.root_vm, "switch_service", fail_switch)
        result = await app._mount_service_view("emr-serverless")

        assert result is False
        assert tuple(host.children) == prior_children


@pytest.mark.asyncio
async def test_cancelled_settings_adoption_keeps_existing_view_mounted(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = app_context_factory()
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        host = app.query_one("#content-host")
        prior_children = tuple(host.children)
        started = asyncio.Event()

        async def stalled_set_content(*args: object, **kwargs: object) -> None:
            del args, kwargs
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(ctx.root_vm.content_host, "set_content", stalled_set_content)
        task = asyncio.create_task(app._mount_settings_view())
        await started.wait()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert tuple(host.children) == prior_children
