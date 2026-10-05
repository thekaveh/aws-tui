"""Tests for SettingsView."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def test_settings_view_does_not_import_textual_private_widgets() -> None:
    source = Path("src/aws_tui/ui/widgets/settings_view.py").read_text(encoding="utf-8")

    assert "textual.widgets._collapsible" not in source


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3


def test_settings_view_can_be_constructed(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    try:
        view = SettingsView(vm=vm, hub=_hub())
        # The widget exposes its VM via the public ``vm`` property
        # (used by inline-form submitters to read s3 state). Locking
        # the identity here means a refactor that swaps the VM
        # reference would surface immediately, not only when the
        # downstream submit path breaks.
        assert view.vm is vm
    finally:
        vm.dispose()
        s3.dispose()


@pytest.mark.asyncio
async def test_settings_view_shows_connections_section_expanded_by_default(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)

    class _Host(App[None]):
        def __init__(self, w: SettingsView) -> None:
            super().__init__()
            self._w = w

        def compose(self) -> ComposeResult:
            yield self._w

    view = SettingsView(vm=vm, hub=_hub())
    app = _Host(view)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Collapsible

            conn_section = view.query_one("#section-connections", Collapsible)
            assert conn_section.collapsed is False
            themes_section = view.query_one("#section-themes", Collapsible)
            assert themes_section.collapsed is True
            assert themes_section.disabled is True
    finally:
        vm.dispose()
        s3.dispose()


async def test_drafts_section_keyboard_reentry_and_real_path(tmp_path):
    from textual.widgets import Button, Collapsible, Static

    from tests.athena_drafts_helpers import runtime_at
    from tests.helpers import drain_workers, focus_and_settle

    vm, s3 = _make_vm(tmp_path)
    drafts, _ = runtime_at(tmp_path, enabled=False)
    vm._athena_drafts = drafts

    class Host(App[None]):
        def compose(self):
            yield SettingsView(vm=vm, hub=_hub())

    app = Host()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SettingsView)
            section = view.query_one("#section-athena-drafts", Collapsible)
            assert not section.collapsed
            assert view.query_one("#athena-drafts-path", Static).content == str(drafts.directory)
            title = next(
                w
                for w in section.walk_children()
                if callable(getattr(w, "action_toggle_collapsible", None))
            )
            assert title in view._focus_controls()
            await focus_and_settle(title)
            await pilot.press("enter")
            await pilot.pause()
            assert section.collapsed
            assert view.query_one("#athena-drafts-toggle", Button) not in view._focus_controls()
            await focus_and_settle(title)
            await pilot.press("enter")
            await pilot.pause()
            assert not section.collapsed
            assert view.cycle_focus(reverse=False)
            await pilot.pause()
            assert app.focused.id == "athena-drafts-toggle"
            await pilot.press("enter")
            await drain_workers(app)
            assert drafts.enabled
            assert (
                str(view.query_one("#athena-drafts-toggle", Button).label)
                == "Disable and delete drafts"
            )
    finally:
        vm.dispose()
        s3.dispose()
        await drafts.shutdown()
        drafts.dispose()


@pytest.mark.parametrize("read_only", [False, True])
async def test_drafts_initial_mount_hides_cleanup_and_disables_demo(tmp_path, read_only):
    from textual.widgets import Button, Static

    from tests.athena_drafts_helpers import runtime_at

    vm, s3 = _make_vm(tmp_path)
    drafts, _ = runtime_at(tmp_path, enabled=False)
    drafts._read_only = read_only
    vm._athena_drafts = drafts

    class Host(App[None]):
        def compose(self):
            yield SettingsView(vm=vm, hub=_hub())

    app = Host()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app.query_one("#athena-drafts-cleanup", Button).display
            assert app.query_one("#athena-drafts-toggle", Button).disabled is read_only
            assert app.query_one("#athena-drafts-setting-status", Static).content == (
                "Unavailable in demo mode" if read_only else ""
            )
    finally:
        vm.dispose()
        s3.dispose()
        await drafts.shutdown()
        drafts.dispose()


async def test_disabled_cleanup_failure_offers_retry_without_enable(tmp_path, monkeypatch):
    from textual.widgets import Button

    from aws_tui.infra.athena_draft_store import DraftStoreResult
    from tests.athena_drafts_helpers import runtime_at
    from tests.helpers import drain_workers, focus_and_settle

    vm, s3 = _make_vm(tmp_path)
    drafts, store = runtime_at(tmp_path)
    original = store.set_enabled
    monkeypatch.setattr(
        store, "set_enabled", lambda enabled: DraftStoreResult(code="io", enabled=False)
    )
    assert not await drafts.set_enabled(False)
    assert not drafts.enabled
    assert drafts.cleanup_required
    vm._athena_drafts = drafts

    class Host(App[None]):
        def compose(self):
            yield SettingsView(vm=vm, hub=_hub())

    app = Host()
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            cleanup = app.query_one("#athena-drafts-cleanup", Button)
            assert cleanup.display
            monkeypatch.setattr(store, "set_enabled", original)
            await focus_and_settle(cleanup)
            await pilot.press("enter")
            await drain_workers(app)
            await pilot.pause()
            assert not drafts.enabled
            assert not drafts.cleanup_required
            assert not cleanup.display
    finally:
        vm.dispose()
        s3.dispose()
        await drafts.shutdown()
        drafts.dispose()
