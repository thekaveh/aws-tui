import pytest
from textual.app import App
from textual.widgets import OptionList, Static

from aws_tui.domain.scoped_overview import OverviewEntry, OverviewPage, WatchScope
from aws_tui.infra.connection_resolver import Connection
from aws_tui.ui.widgets.scoped_overview_modal import ScopedOverviewModal
from aws_tui.vm.scoped_overview_vm import ScopedOverviewVM
from tests.helpers import wait_until


class OverviewApp(App):
    def __init__(self, vm, watch):
        super().__init__()
        self.vm, self.current_watch, self.selected = vm, watch, None

    def on_mount(self):
        self.push_screen(ScopedOverviewModal(self.vm, add_current=self.current_watch), self.chosen)

    def chosen(self, entry):
        self.selected = entry


async def focus(app, pilot, identity):
    for _ in range(15):
        if app.focused is not None and app.focused.id == identity:
            return
        await pilot.press("tab")
    raise AssertionError(f"Could not reach {identity}")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_keyboard_explicit_add_manual_refresh_and_exact_open(size):
    calls = []
    watch = WatchScope("athena", "dev", "eu-west-1", "chosen")
    entry = OverviewEntry(watch, "run-1", "FAILED", error_summary="native structured failure")

    async def read(scope):
        calls.append(scope)
        return OverviewPage((entry,), "First page only; more available")

    async def current():
        return True

    vm = ScopedOverviewVM(
        connection=Connection("dev", "aws", "eu-west-1", "config", profile="dev"),
        read=read,
        source_is_current=current,
    )
    app = OverviewApp(vm, watch)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert calls == []
            assert "No watched scopes" in str(
                app.screen.query_one("#overview-status", Static).render()
            )
            await focus(app, pilot, "overview-add")
            await pilot.press("enter")
            assert len(vm.sections) == 1
            assert calls == []
            await focus(app, pilot, "overview-refresh")
            await pilot.press("enter")
            await wait_until(lambda: vm.sections[0].state == "ready", what="manual refresh")
            assert calls == [watch]
            await focus(app, pilot, "overview-list")
            await pilot.press("down")
            listing = app.screen.query_one(OptionList)
            assert listing.highlighted == 1
            assert "native structured failure" in str(
                app.screen.query_one("#overview-details", Static).render()
            )
            await pilot.press("enter")
            await wait_until(lambda: app.selected is entry, what="exact overview entry selected")
            assert calls == [watch]
    finally:
        await vm.shutdown()


@pytest.mark.parametrize("kind", ["local", "s3-compatible"])
async def test_non_aws_session_does_not_claim_default_aws_authentication(kind):
    async def read(scope):
        return OverviewPage(())

    async def current():
        return True

    vm = ScopedOverviewVM(
        connection=Connection("session", kind, "local", "session"),
        read=read,
        source_is_current=current,
    )
    app = OverviewApp(vm, WatchScope("transfers", "session", "local", ""))
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            label = str(app.screen.query_one("#overview-identity", Static).render())
            assert "Profile: not applicable" in label
            assert "AWS chain" not in label
    finally:
        await vm.shutdown()
