import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button
from vmx import MessageHub

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from tests.helpers import focus_and_settle, wait_until


@pytest.mark.asyncio
async def test_settings_policy_control_persists_and_can_be_reversed(tmp_path):
    from aws_tui.ui.widgets.settings.connection_protection_panel import ConnectionProtectionPanel
    from aws_tui.vm.settings.connection_protection_vm import ConnectionProtectionVM

    store = ConfigStore(path=tmp_path / "config.toml")
    store.add_connection(ConnectionEntry(name="synthetic", kind="aws", region="us-east-1"))
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=tmp_path / "absent",
        aws_credentials_path=tmp_path / "absent",
    )
    vm = ConnectionProtectionVM(config=store, resolver=resolver, hub=MessageHub())
    panel = ConnectionProtectionPanel(vm=vm)

    class Host(App):
        def compose(self) -> ComposeResult:
            yield panel

    async with Host().run_test() as pilot:
        await wait_until(lambda: len(panel.query(Button)) == 1, what="policy row")
        button = panel.query_one(Button)
        await wait_until(lambda: button.is_mounted, what="policy control mounted")
        await focus_and_settle(button, require_focusable=True)
        await pilot.press("enter")
        await wait_until(
            lambda: store.load().connections["synthetic"].protected, what="policy persisted"
        )
        await wait_until(lambda: not panel.disabled, what="policy save complete")
        assert panel.query_one(Button) is button
        await wait_until(
            lambda: not button.has_class("-active"), what="policy button ready for second press"
        )
        await pilot.pause()
        assert await pilot.click("#connection-protection-0")
        await wait_until(
            lambda: not store.load().connections["synthetic"].protected, what="policy disabled"
        )
