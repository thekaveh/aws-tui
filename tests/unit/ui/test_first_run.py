"""First-run controls consume snapshots and publish explicit user intents."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Input
from vmx import NULL_DISPATCHER, Message, MessageHub

from aws_tui.demo.connections import DemoConnectionResolver
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionDiscovery, ConnectionResolver
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.first_run import ConnectionChoice, FirstRunConnectionList, FirstRunView
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormCancelled,
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _connection(name: str = "[bold]literal[/]", source: str = "auto-aws-profile") -> Connection:
    return Connection(name=name, kind="aws", region="us-east-1", source=source, profile=name)


class Host(App[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab", "cycle", priority=True),
        Binding("shift+tab", "reverse_cycle", priority=True),
        Binding("enter", "activate", priority=True),
        Binding("up", "up", priority=True),
        Binding("down", "down", priority=True),
    ]

    def __init__(self, view: FirstRunView, nav: NavMenu | None = None) -> None:
        super().__init__()
        self.view = view
        self.nav = nav
        self.submissions: list[ConnectionFormSubmitted] = []
        self.cancellations: list[ConnectionFormCancelled] = []
        self.retries = 0
        self.selected: list[str] = []
        self.setups = 0
        self.cycles: list[bool] = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            if self.nav:
                yield self.nav
            yield self.view

    def action_cycle(self) -> None:
        consumed = self.view.cycle_focus()
        self.cycles.append(consumed)
        if not consumed:
            self.screen.focus_next()

    def action_reverse_cycle(self) -> None:
        if not self.view.cycle_focus(reverse=True):
            self.screen.focus_previous()

    def action_up(self) -> None:
        if self.nav:
            self.nav.action_cursor_up()

    def action_down(self) -> None:
        if self.nav:
            self.nav.action_cursor_down()

    def action_activate(self) -> None:
        if self.view.activate_focused():
            return
        if self.nav:
            self.nav.activate_first_run_focused()

    def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
        self.submissions.append(event)

    def on_connection_form_cancelled(self, event: ConnectionFormCancelled) -> None:
        self.cancellations.append(event)

    def on_first_run_view_retry_requested(self, event: FirstRunView.RetryRequested) -> None:
        self.retries += 1

    def on_first_run_connection_list_connection_selected(
        self, event: FirstRunConnectionList.ConnectionSelected
    ) -> None:
        self.selected.append(event.name)

    def on_first_run_connection_list_setup_requested(
        self, event: FirstRunConnectionList.SetupRequested
    ) -> None:
        self.setups += 1


def _view(tmp_path: Path) -> FirstRunView:
    return FirstRunView(config_path=tmp_path / "config[local].toml", hub=_hub())


def _plain(widget: Widget) -> str:
    return str(widget.render())


def _focused(app: App[None]) -> Widget | None:
    return app.focused


def _screen_text(app: App[None]) -> str:
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


@pytest.mark.parametrize("source", ["config", "auto-aws-profile", "demo"])
def test_row_renders_literal_origin(source: str) -> None:
    row = ConnectionChoice(connection=_connection(source=source))
    rendered = row.render()
    assert source in rendered.plain
    assert "[bold]literal[/]" in rendered.plain
    assert not rendered.spans
    assert row.connection_name == "[bold]literal[/]"


@pytest.mark.asyncio
async def test_actions_keyboard_setup_retry_form_and_cancel(tmp_path: Path) -> None:
    view = _view(tmp_path)
    app = Host(view)
    async with app.run_test(size=(120, 40)) as pilot:
        view.show_discovery(ConnectionDiscovery(()))
        view.focus_default()
        await pilot.pause()
        buttons = [b for b in view.query(ModalButton) if not b.button_id.startswith("form-")]
        assert [_plain(b) for b in buttons] == [
            "Add S3-compatible connection",
            "AWS profile setup",
            "Retry discovery",
        ]
        assert _focused(app) is buttons[0]
        assert "No AWS profiles or S3-compatible connections found." in _screen_text(app)
        assert "config[local].toml" in _screen_text(app)
        await pilot.press("tab")
        assert _focused(app) is buttons[1]
        assert app.cycles[-1] is True
        await pilot.press("enter")
        assert "aws configure sso" in _screen_text(app)
        assert "aws configure" in _screen_text(app)
        await pilot.press("tab", "enter")
        assert _focused(app) is buttons[2]
        assert app.retries == 1
        await pilot.press("shift+tab", "shift+tab", "enter")
        form = view.query_one(ConnectionFormInline)
        assert form.has_class("-open")
        assert form.has_errors
        save = form.query_one(".form-footer").query(ModalButton).last()
        assert _plain(save) == "Save and open"
        assert save.disabled
        assert _focused(app) is form.query_one("#form-name", Input)
        await pilot.press("enter")
        assert _focused(app) is form.query_one("#form-endpoint_url", Input)
        await pilot.press("escape")
        assert not form.has_class("-open")
        assert _focused(app) is buttons[0]
        assert app.cancellations
        assert "No AWS profiles or S3-compatible connections found." in _screen_text(app)
        assert not app.submissions


@pytest.mark.asyncio
async def test_invalid_status_precedence_and_cancel_restores_error(tmp_path: Path) -> None:
    view = _view(tmp_path)
    app = Host(view)
    async with app.run_test(size=(120, 40)) as pilot:
        view.show_discovery(ConnectionDiscovery((_connection(),), ("app-config",)))
        await pilot.pause()
        assert (
            "Invalid configuration. Fix the configuration file, then Retry discovery."
            in _screen_text(app)
        )
        view.show_error(
            "Credential probe failed. Refresh credentials outside aws-tui, then select the connection again."
        )
        view.focus_default()
        await pilot.press("enter", "escape")
        assert "Credential probe failed." in _screen_text(app)
        view.show_discovery(ConnectionDiscovery((_connection(),)))
        await pilot.pause()
        assert "Select a connection" in _screen_text(app)


@pytest.mark.asyncio
async def test_busy_blocks_actions_and_pending_form_rejects_cancel_and_duplicate(
    tmp_path: Path,
) -> None:
    view = _view(tmp_path)
    app = Host(view)
    async with app.run_test(size=(120, 40)) as pilot:
        view.show_discovery(ConnectionDiscovery(()))
        view.focus_default()
        view.set_busy(True)
        assert all(
            b.disabled for b in view.query(ModalButton) if not b.button_id.startswith("form-")
        )
        assert view.activate_focused()
        assert not app.retries
        view.set_busy(False)
        view.focus_default()
        await pilot.press("enter")
        form = view.query_one(ConnectionFormInline)
        for key, value in {
            "name": "local",
            "endpoint_url": "http://localhost:9000",
            "region": "us-east-1",
            "access_key_id": "K",
            "secret_access_key": "S",
        }.items():
            form.query_one(f"#form-{key}", Input).value = value
        await pilot.pause()
        assert not form.has_errors
        view.set_busy(True)
        assert form.disabled
        assert all(inp.is_disabled for inp in form.query(Input))
        assert view.activate_focused()
        next(b for b in form.query(ModalButton) if b.button_id == "form-save-btn").press()
        form.action_cancel()
        await pilot.pause()
        assert form.has_class("-open")
        assert not app.cancellations
        assert not app.submissions
        view.set_busy(False)
        assert not form.disabled
        view.focus_default()
        save = next(b for b in form.query(ModalButton) if b.button_id == "form-save-btn")
        await pilot.press(*(["tab"] * 7))
        assert _focused(app) is save
        assert "Save and open" in _screen_text(app)
        assert save.region.overlaps(view.region)
        await pilot.press("enter", "enter", "escape")
        form.action_cancel()
        await pilot.pause()
        assert len(app.submissions) == 1
        assert form.has_class("-open")
        assert not app.cancellations
        assert all(w.disabled for w in form.query(Input))
        assert all(b.disabled for b in form.query(ModalButton))
        form.clear_submitting()
        assert all(not w.disabled for w in form.query(Input))
        assert all(not b.disabled for b in form.query(ModalButton))
        form.mark_name_invalid()
        assert form.query_one("#form-name", Input).has_class("-invalid")
        form.close()
        assert all(not w.disabled for w in form.query(Input))


@pytest.mark.asyncio
async def test_rail_width_origins_arrows_and_overflow_keyboard(tmp_path: Path) -> None:
    hub = _hub()
    vm = NavMenuVM(registry=ServiceRegistry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    nav = NavMenu(vm=vm, hub=hub)
    app = Host(_view(tmp_path), nav)
    connections = tuple(_connection(name=f"profile-{i:02}") for i in range(30))
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await nav.show_first_run_connections(ConnectionDiscovery(connections))
            await pilot.pause()
            assert nav.region.width == 28
            section = nav.query_one(FirstRunConnectionList)
            choices = list(section.query(ConnectionChoice))
            nav.focus()
            assert app.view.cycle_focus() is False
            assert app.view.activate_focused() is False
            choices[0].focus()
            await pilot.press("tab")
            assert _focused(app) is choices[1]
            assert app.cycles[-1] is True
            await pilot.press("shift+tab")
            assert _focused(app) is choices[0]
            await pilot.pause()
            assert "auto-aws-profile" in _screen_text(app)
            assert "profile-00" in _screen_text(app)
            for _ in range(29):
                await pilot.press("down")
            assert _focused(app) is choices[-1]
            assert "profile-29" in _screen_text(app)
            assert choices[-1].region.overlaps(section.region)
            assert vm.selected_id is None
            assert not app.selected
            await pilot.press("enter")
            assert app.selected == ["profile-29"]
            await pilot.press("up")
            assert _focused(app) is choices[-2]
            assert app.selected == ["profile-29"]
            setup = next(b for b in section.query(ModalButton))
            setup.focus()
            await pilot.press("enter")
            assert app.setups == 1
            await nav.show_first_run_connections(None)
            await pilot.pause()
            assert nav.region.width == 12
            assert not nav.query(FirstRunConnectionList)
            await nav.show_first_run_connections(ConnectionDiscovery(()))
            await pilot.pause()
            assert (
                _plain(nav.query_one(FirstRunConnectionList).query_one(ModalButton))
                == "Connection setup"
            )
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", ThemeStore.BUILTIN_NAMES)
async def test_real_snapshots_render_all_origins_and_literal_names_in_themes(
    tmp_path: Path, theme: str
) -> None:
    store = ConfigStore(path=tmp_path / "app.toml")
    store.add_connection(ConnectionEntry(name="[bold]literal[/]", kind="aws", profile="literal"))
    aws_config = tmp_path / "aws-config"
    aws_config.write_text("[profile discovered]\nregion = us-east-1\n", encoding="utf-8")
    resolver = ConnectionResolver(
        config_store=store, aws_config_path=aws_config, aws_credentials_path=tmp_path / "missing"
    )
    snapshot = resolver.discover()
    demo_snapshot = DemoConnectionResolver().discover()
    hub = _hub()
    vm = NavMenuVM(registry=ServiceRegistry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    nav = NavMenu(vm=vm, hub=hub)
    view = _view(tmp_path)
    app = Host(view, nav)
    app.stylesheet.add_source(ThemeStore().load(theme))
    before_config = store.path.read_bytes()
    before_aws = aws_config.read_bytes()
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            view.show_discovery(snapshot)
            await nav.show_first_run_connections(snapshot)
            await pilot.pause()
            screen = _screen_text(app)
            assert "[bold]literal[/]" in screen
            assert "auto-aws-profile" in screen
            assert "config" in screen
            assert nav.region.width == 28
            for row, connection in zip(
                nav.query(ConnectionChoice), snapshot.connections, strict=True
            ):
                assert connection.source in row.render_line(1).text
            choice = nav.query(ConnectionChoice).first()
            choice.focus()
            await pilot.press("enter")
            assert app.selected == ["[bold]literal[/]"]
            await nav.show_first_run_connections(demo_snapshot)
            await pilot.pause()
            assert "demo" in _screen_text(app)
            assert len(nav.query(ConnectionChoice)) == 4
            view.focus_default()
            await pilot.press("tab", "enter", "tab", "enter")
            assert "aws configure sso" in _screen_text(app)
            assert app.retries == 1
        assert store.path.read_bytes() == before_config
        assert aws_config.read_bytes() == before_aws
        assert not (tmp_path / "missing").exists()
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_long_wide_name_keeps_origin_visible_and_selects_exact_identity(
    tmp_path: Path,
) -> None:
    name = "[bold]" + "界" * 45 + "[/]"
    connection = _connection(name=name)
    hub = _hub()
    vm = NavMenuVM(registry=ServiceRegistry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    nav = NavMenu(vm=vm, hub=hub)
    app = Host(_view(tmp_path), nav)
    app.stylesheet.add_source(ThemeStore().load("carbon"))
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await nav.show_first_run_connections(ConnectionDiscovery((connection,)))
            await pilot.pause()
            choice = nav.query_one(ConnectionChoice)
            choice.focus()
            await pilot.pause()
            assert nav.region.width == 28
            assert choice.region.height == 2
            screen = _screen_text(app)
            assert "auto-aws-profile" in screen
            assert "[bold]" in screen
            assert "…" in screen
            assert "auto-aws-profile" in choice.render().plain
            assert "auto-aws-profile" in choice.render_line(1).text
            await pilot.press("enter")
            assert app.selected == [name]
            assert choice.connection_name == name
    finally:
        vm.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["one\ntwo", "one\r\ntwo", "one\rtwo"])
async def test_accepted_multiline_name_keeps_origin_visible_and_exact_selection(
    tmp_path: Path, name: str
) -> None:
    store = ConfigStore(path=tmp_path / "app.toml")
    store.add_connection(ConnectionEntry(name=name, kind="aws", profile="literal"))
    resolver = ConnectionResolver(
        config_store=store,
        aws_config_path=tmp_path / "missing-config",
        aws_credentials_path=tmp_path / "missing-credentials",
    )
    snapshot = resolver.discover()
    assert snapshot.connections[0].name == name
    hub = _hub()
    vm = NavMenuVM(registry=ServiceRegistry(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    nav = NavMenu(vm=vm, hub=hub)
    app = Host(_view(tmp_path), nav)
    app.stylesheet.add_source(ThemeStore().load("carbon"))
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await nav.show_first_run_connections(snapshot)
            await pilot.pause()
            choice = nav.query_one(ConnectionChoice)
            assert nav.region.width == 28
            assert choice.region.height == 2
            assert "config" in choice.render_line(1).text
            assert "config" in _screen_text(app)
            assert name.replace("\r", r"\r").replace("\n", r"\n") in choice.render_line(0).text
            choice.focus()
            await pilot.press("enter")
            assert choice.connection_name == name
            assert app.selected == [name]
    finally:
        vm.dispose()
