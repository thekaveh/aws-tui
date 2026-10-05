from __future__ import annotations

from html import unescape

from textual.app import App
from textual.containers import VerticalScroll
from textual.widgets import Button, OptionList

from aws_tui.infra.athena_draft_store import DraftPermit
from tests.athena_drafts_helpers import record, runtime_at
from tests.helpers import drain_workers, focus_and_settle
from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm


async def test_manager_selection_never_restores_and_escape_closes(tmp_path):
    from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal

    runtime, store = runtime_at(tmp_path)
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    assert store.save(record(), permit=DraftPermit()).code is None
    app = App()
    results = []
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            modal = AthenaDraftsModal(page, hub=page._hub)
            app.push_screen(modal, results.append)
            await drain_workers(app)
            await pilot.pause()
            assert len(modal.query_one(OptionList)._options) == 1
            await pilot.press("enter", "down", "enter")
            assert page.query.sql == ""
            assert "SELECT" not in app.export_screenshot()
            await pilot.press("escape")
            await pilot.pause()
            assert results == ["closed"]
    finally:
        await page.shutdown()
        page.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_long_metadata_keyboard_scroll_renders_saved_and_error(tmp_path):
    from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal

    runtime, store = runtime_at(tmp_path)
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    long_context = (
        "analytics" + "[literal] " * 80,
        "us-west-2",
        "primary",
        "AwsDataCatalog",
        "default",
    )
    assert store.save(record(context=long_context), permit=DraftPermit()).code is None
    page._draft_recovery_error = (
        "Draft context is unavailable or changed. Select the exact original context and retry."
    )
    app = App()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            modal = AthenaDraftsModal(page, hub=page._hub)
            app.push_screen(modal)
            await drain_workers(app)
            await pilot.pause()
            detail = modal.query_one("#athena-drafts-detail-scroll", VerticalScroll)
            await focus_and_settle(detail)
            assert "Connection:" in unescape(app.export_screenshot())
            for _ in range(30):
                await pilot.press("down")
            await pilot.pause()
            assert detail.scroll_y > 0
            svg = unescape(app.export_screenshot()).replace("\xa0", " ")
            assert "Database: default" in svg
            assert "Saved:" in svg
            rows = app.screen._compositor.render_strips()
            visible = "".join(
                row.crop(detail.content_region.x, detail.content_region.right).text.rstrip()
                for row in rows[detail.content_region.y : detail.content_region.bottom]
            )
            from rich.text import Text

            warning = page.draft_recovery_error
            expected = Text(warning).wrap(app.console, detail.content_size.width)
            assert "".join(row.plain.rstrip(" ") for row in expected) in visible
            assert page.query.sql == ""
    finally:
        await page.shutdown()
        page.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_manager_loading_disables_recovery_without_blocking_keyboard(tmp_path, monkeypatch):
    from threading import Event

    from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal
    from tests.helpers import wait_until

    runtime, store = runtime_at(tmp_path)
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    gate, started = Event(), Event()
    original = store.list

    def blocked():
        started.set()
        assert gate.wait(10)
        return original()

    monkeypatch.setattr(store, "list", blocked)
    app = App()
    try:
        async with app.run_test() as pilot:
            modal = AthenaDraftsModal(page, hub=page._hub)
            app.push_screen(modal)
            await wait_until(started.is_set, what="manager list worker running")
            await pilot.pause()
            for name in ("restore", "delete", "clear"):
                assert modal.query_one(f"#athena-drafts-{name}", Button).disabled
            await pilot.press("tab", "escape")
            await pilot.pause()
            assert app.screen is not modal
            assert page.query.sql == ""
    finally:
        gate.set()
        await page.shutdown()
        page.dispose()
        await runtime.shutdown()
        runtime.dispose()


async def test_manager_nonregular_warning_and_clear_preserve_targets(tmp_path):
    from textual.widgets import Static

    from aws_tui.ui.widgets.athena.drafts_modal import AthenaDraftsModal
    from aws_tui.ui.widgets.confirm_modal import ConfirmModal
    from tests.helpers import wait_until

    runtime, _store = runtime_at(tmp_path)
    page = make_page_vm(PageClient(), drafts=runtime)
    await page.setup()
    target = tmp_path / "private-target"
    target.write_text("PRIVATE_EXTERNAL")
    owned = runtime.directory / (record().id + ".json")
    runtime.directory.mkdir(exist_ok=True)
    owned.symlink_to(target)
    app = App()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            modal = AthenaDraftsModal(page, hub=page._hub)
            app.push_screen(modal)
            await drain_workers(app)
            await pilot.pause()
            assert "1 local draft record(s)" in str(
                modal.query_one("#athena-drafts-warning", Static).content
            )
            assert "PRIVATE_EXTERNAL" not in app.export_screenshot()
            clear = modal.query_one("#athena-drafts-clear", Button)
            assert not clear.disabled
            await focus_and_settle(clear)
            await pilot.press("enter")
            await wait_until(
                lambda: isinstance(app.screen, ConfirmModal), what="safe clear confirm"
            )
            await pilot.press("tab", "enter")
            await drain_workers(app)
            assert not owned.is_symlink()
            assert target.read_text() == "PRIVATE_EXTERNAL"
            assert runtime.skipped == 0
            owned.mkdir(parents=True)
            (owned / "nested").write_text("PRIVATE_NESTED")
            await runtime.refresh()
            await pilot.pause()
            await focus_and_settle(clear)
            await pilot.press("enter")
            await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="directory confirm")
            await pilot.press("tab", "enter")
            await drain_workers(app)
            assert owned.is_dir()
            assert (owned / "nested").read_text() == "PRIVATE_NESTED"
            assert runtime.skipped == 1
            assert runtime.error_text
    finally:
        await page.shutdown()
        page.dispose()
        await runtime.shutdown()
        runtime.dispose()
