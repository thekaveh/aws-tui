"""Full production App first-run lifecycle; no credential or provider network access."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input, Static

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keychain import InMemoryKeychain
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.first_run import (
    INVALID_CONFIGURATION,
    NO_CONNECTIONS,
    PROBE_FAILED,
    ConnectionChoice,
    FirstRunView,
)
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from tests.helpers import drain_workers, wait_until
from tests.integration.conftest import AppContextBuilder


class RecordingFS(InMemoryFS):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[PathRef] = []

    async def list(self, path: PathRef):  # type: ignore[no-untyped-def]
        self.calls.append(path)
        return await super().list(path)


def setup_context(
    factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: bytes | None = b"",
    state: TokenState = TokenState.CONNECTED,
):  # type: ignore[no-untyped-def]
    fs = RecordingFS()
    ctx = factory(fs=fs)
    if config is None:
        ctx.config_store.path.unlink()
    else:
        ctx.config_store.path.write_bytes(config)
    keychain = InMemoryKeychain()
    aws_config = ctx.config_store.path.parent / "aws-config"
    aws_credentials = ctx.config_store.path.parent / "aws-credentials"
    # Both real owners share exactly the fixture's paths and credential backend.
    ctx.connection_resolver = ConnectionResolver(
        config_store=ctx.config_store,
        keychain=keychain,
        aws_config_path=aws_config,
        aws_credentials_path=aws_credentials,
    )
    ctx.s3_connections_vm = S3ConnectionsVM(
        resolver=ctx.connection_resolver,
        config_store=ctx.config_store,
        keychain=keychain,
        hub=ctx.hub,
        dispatcher=ctx.dispatcher,
    )
    aws_credentials.write_bytes(b"# fixture credentials remain byte-for-byte unchanged\r\n")
    probes: list[str] = []

    def probe(connection):  # type: ignore[no-untyped-def]
        probes.append(connection.name)
        return TokenProbeResult(state)

    monkeypatch.setattr(ctx.aws_session, "probe_token", probe)
    return SimpleNamespace(
        ctx=ctx,
        app=AwsTuiApp(ctx),
        fs=fs,
        probes=probes,
        aws_config=aws_config,
        aws_credentials=aws_credentials,
    )


def file_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def aws_bytes(env):  # type: ignore[no-untyped-def]
    return file_bytes(env.aws_config), file_bytes(env.aws_credentials)


def button(app, name):  # type: ignore[no-untyped-def]
    return next(b for b in app.query(ModalButton) if b.button_id == name)


async def ready(env, pilot):  # type: ignore[no-untyped-def]
    await drain_workers(env.app)
    await pilot.pause()
    assert len(env.app.query(FirstRunView)) == 1
    assert env.app.focused is button(env.app, "first-run-add")
    assert env.fs.calls == []
    assert env.probes == []


async def fill_keyboard(app, pilot, name="local"):  # type: ignore[no-untyped-def]
    await pilot.press("enter")
    await wait_until(lambda: isinstance(app.focused, Input), what="first form field")
    for value in (name, "http://localhost:9000", "us-east-1", "ACCESS", "SECRET"):
        await pilot.press(*value, "tab")
    await pilot.press("tab", "tab")
    assert app.focused is button(app, "form-save-btn")


@pytest.mark.asyncio
async def test_empty_actions_invalid_form_and_cancel_preserve_files(
    app_context_factory, monkeypatch
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    before = aws_bytes(env), file_bytes(env.ctx.config_store.path)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        assert str(env.app.query_one("#first-run-status", Static).content) == NO_CONNECTIONS
        guide = next(
            widget for widget in env.app.query(Static) if "Connection guide:" in str(widget.content)
        )
        assert "https://thekaveh.github.io/aws-tui/connections/" in str(guide.content)
        assert guide.region.bottom <= env.app.query_one(FirstRunView).content_region.bottom
        for id_ in ("first-run-add", "first-run-aws", "first-run-retry"):
            assert env.app.focused is button(env.app, id_)
            await pilot.press("tab")
        button(env.app, "first-run-aws").focus()
        await pilot.press("enter")
        assert env.app.query_one("#first-run-aws-guidance").display
        assert env.fs.calls == env.probes == []
        button(env.app, "first-run-add").focus()
        await pilot.press("enter")
        form = env.app.query_one(ConnectionFormInline)
        await wait_until(lambda: isinstance(env.app.focused, Input), what="form focus")
        assert form.has_class("-open")
        assert button(env.app, "form-save-btn").disabled
        await pilot.press(*"bad name", "tab", *"not-a-url")
        assert button(env.app, "form-save-btn").disabled
        await pilot.press("escape")
        assert not form.has_class("-open")
        assert env.app.focused is button(env.app, "first-run-add")
        assert (aws_bytes(env), file_bytes(env.ctx.config_store.path)) == before
        assert env.fs.calls == env.probes == []


@pytest.mark.asyncio
async def test_keyboard_save_opens_service_in_session(app_context_factory, monkeypatch):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    before = aws_bytes(env)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        await fill_keyboard(env.app, pilot)
        await pilot.press("enter", "enter")
        await drain_workers(env.app)
        await wait_until(lambda: len(env.app.query(DualPane)) == 1, what="saved service mounted")
        assert env.ctx.root_vm.active_connection.name == "local"
        assert [c.name for c in env.ctx.connection_resolver.list()] == ["local"]
        assert env.fs.calls
        assert env.probes == ["local"]
        assert "SECRET" not in env.ctx.config_store.path.read_text()
        assert aws_bytes(env) == before
        assert env.app.query_one(NavMenu).outer_size.width == 12


@pytest.mark.asyncio
async def test_retry_rediscovery_rail_explicit_selection_and_removed_identity(
    app_context_factory, monkeypatch
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        env.aws_config.write_text("[profile added]\nregion = eu-west-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        rows = list(env.app.query(ConnectionChoice))
        assert [r.connection_name for r in rows] == ["added"]
        assert "auto-aws-profile" in rows[0].render().plain
        assert [c.name for c in env.ctx.connection_resolver.list()] == ["added"]
        assert env.fs.calls == env.probes == []
        assert env.app.query_one(NavMenu).outer_size.width == 28
        assert aws_bytes(env) == before
        rows[0].focus()
        env.aws_config.write_text("")
        before = aws_bytes(env)
        await pilot.press("enter")
        await drain_workers(env.app)
        assert not env.app.query(ConnectionChoice)
        assert env.probes == env.fs.calls == []
        assert str(env.app.query_one("#first-run-status", Static).content) == NO_CONNECTIONS
        assert aws_bytes(env) == before
        env.aws_config.write_text("[profile added]\nregion = ap-south-1\n")
        before = aws_bytes(env)
        button(env.app, "first-run-retry").focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("down", "up")
        assert env.probes == env.fs.calls == []
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        assert env.ctx.root_vm.active_connection.region == "ap-south-1"
        assert env.probes == ["added"]
        assert env.fs.calls
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_source", ["app", "aws"])
async def test_invalid_configuration_repairs_via_retry(
    app_context_factory, monkeypatch, bad_source
):  # type: ignore[no-untyped-def]
    env = setup_context(
        app_context_factory, monkeypatch, config=b"[broken" if bad_source == "app" else b""
    )
    if bad_source == "aws":
        env.aws_config.write_bytes(b"[broken")
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        assert str(env.app.query_one("#first-run-status", Static).content) == INVALID_CONFIGURATION
        path = env.ctx.config_store.path if bad_source == "app" else env.aws_config
        path.write_text("")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        assert str(env.app.query_one("#first-run-status", Static).content) == NO_CONNECTIONS
        assert env.fs.calls == env.probes == []
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [TokenState.MISSING, TokenState.EXPIRED, "throws"])
async def test_failed_explicit_probe_never_creates_provider(
    app_context_factory, monkeypatch, state
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch, state=state)
    if state == "throws":

        def probe(connection):  # type: ignore[no-untyped-def]
            env.probes.append(connection.name)
            raise RuntimeError("private-endpoint-secret")

        monkeypatch.setattr(env.ctx.aws_session, "probe_token", probe)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        assert str(env.app.query_one("#first-run-status", Static).content) == PROBE_FAILED
        assert env.probes == ["added"]
        assert env.fs.calls == []
        assert env.ctx.root_vm.active_connection is None
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("absent", [False, True])
async def test_cancel_preserves_app_config_absence(app_context_factory, monkeypatch, absent):  # type: ignore[no-untyped-def]
    env = setup_context(
        app_context_factory, monkeypatch, config=None if absent else b"# preserve this exact file\n"
    )
    before = file_bytes(env.ctx.config_store.path), aws_bytes(env)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        await pilot.press("enter")
        await wait_until(lambda: isinstance(env.app.focused, Input), what="form focus")
        await pilot.press("l", "escape")
        assert (file_bytes(env.ctx.config_store.path), aws_bytes(env)) == before
        assert env.fs.calls == env.probes == []


@pytest.mark.asyncio
async def test_settings_then_connection_setup_preserves_navigation(
    app_context_factory, monkeypatch
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    before = aws_bytes(env)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        await pilot.press("comma")
        await drain_workers(env.app)
        assert len(env.app.query(SettingsView)) == 1
        button(env.app, "first-run-setup").focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        assert len(env.app.query(FirstRunView)) == 1
        assert not env.app.query(SettingsView)
        assert env.fs.calls == env.probes == []
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["settings", "shutdown"])
async def test_slow_probe_superseded(app_context_factory, monkeypatch, destination):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    started, release = threading.Event(), threading.Event()

    def probe(connection):  # type: ignore[no-untyped-def]
        started.set()
        assert release.wait(10)
        env.probes.append(connection.name)
        return TokenProbeResult(TokenState.CONNECTED)

    monkeypatch.setattr(env.ctx.aws_session, "probe_token", probe)
    try:
        async with env.app.run_test(size=(120, 40)) as pilot:
            await ready(env, pilot)
            env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
            before = aws_bytes(env)
            await pilot.press("tab", "tab", "enter")
            await drain_workers(env.app)
            env.app.query_one(ConnectionChoice).focus()
            await pilot.press("enter")
            await wait_until(started.is_set, what="off-loop probe started")
            shutdown = None
            if destination == "settings":
                await pilot.press("comma")
            else:
                shutdown = asyncio.create_task(env.app.action_quit())
                await wait_until(
                    lambda: env.app._service_navigation_closed, what="shutdown closed navigation"
                )
            release.set()
            if shutdown is not None:
                await shutdown
            else:
                await drain_workers(env.app)
                assert len(env.app.query(SettingsView)) == 1
            assert env.ctx.root_vm.active_connection is None
            assert env.fs.calls == []
            assert aws_bytes(env) == before
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate", "save", "build", "mount"])
async def test_save_failure_recovers_form_or_actionable_setup(
    app_context_factory, monkeypatch, failure
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    before = aws_bytes(env)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        await fill_keyboard(env.app, pilot)
        if failure == "duplicate":
            env.aws_config.write_text("[profile local]\nregion=us-east-1\n")
            before = aws_bytes(env)
        elif failure == "save":

            def fail_write(*args):  # type: ignore[no-untyped-def]
                raise OSError("private-endpoint-secret")

            monkeypatch.setattr(env.ctx.config_store, "add_connection", fail_write)
        elif failure == "build":

            def fail_build(*args):  # type: ignore[no-untyped-def]
                raise RuntimeError("private-endpoint-secret")

            monkeypatch.setattr(env.ctx.registry.get("s3"), "build_vm", fail_build)
        else:
            from aws_tui import app as app_module

            def fail_view(*args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("private-endpoint-secret")

            monkeypatch.setattr(app_module, "build_service_view", fail_view)
        await pilot.press("enter")
        await drain_workers(env.app)
        view = env.app.query_one(FirstRunView)
        status = str(view.query_one("#first-run-status", Static).content)
        assert "private-endpoint-secret" not in status
        assert not button(view, "first-run-add").is_disabled
        if failure in {"duplicate", "save"}:
            form = env.app.query_one(ConnectionFormInline)
            assert form.has_class("-open")
            assert form.query_one("#form-name", Input).value == "local"
            assert form.query_one("#form-secret_access_key", Input).value == "SECRET"
            assert not button(form, "form-cancel-btn").is_disabled
            assert env.fs.calls == env.probes == []
            assert env.ctx.config_store.load().connections == {}
        else:
            assert "local" in env.ctx.config_store.load().connections
        assert aws_bytes(env) == before


@pytest.mark.asyncio
async def test_adoption_cancelled_by_settings_does_not_publish_connection(
    app_context_factory, monkeypatch
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    host = env.ctx.root_vm.content_host
    original = host._cancel_and_drain_setup
    first = True

    async def barrier():
        nonlocal first
        if first:
            first = False
            started.set()
            await release.wait()
        await original()

    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        monkeypatch.setattr(host, "_cancel_and_drain_setup", barrier)
        env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("enter")
        await asyncio.wait_for(started.wait(), 5)
        await pilot.press("comma")
        release.set()
        await drain_workers(env.app)
        assert len(env.app.query(SettingsView)) == 1
        assert env.ctx.root_vm.active_connection is None
        assert env.fs.calls == []
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["settings", "shutdown"])
async def test_slow_discovery_superseded(app_context_factory, monkeypatch, destination):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    started, release = threading.Event(), threading.Event()
    discover = env.ctx.connection_resolver.discover

    def barrier():
        snapshot = discover()
        started.set()
        assert release.wait(10)
        return snapshot

    try:
        async with env.app.run_test(size=(120, 40)) as pilot:
            await ready(env, pilot)
            before = aws_bytes(env)
            monkeypatch.setattr(env.ctx.connection_resolver, "discover", barrier)
            await pilot.press("tab", "tab", "enter")
            await wait_until(started.is_set, what="off-loop discovery started")
            if destination == "settings":
                await pilot.press("comma")
            else:
                shutdown = asyncio.create_task(env.app.action_quit())
                await wait_until(
                    lambda: env.app._service_navigation_closed, what="shutdown closed navigation"
                )
            release.set()
            if destination == "settings":
                await drain_workers(env.app)
                assert len(env.app.query(SettingsView)) == 1
            else:
                await shutdown
                assert env.app._shutdown_complete
            assert env.ctx.root_vm.active_connection is None
            assert env.fs.calls == env.probes == []
            assert aws_bytes(env) == before
    finally:
        release.set()


@pytest.mark.asyncio
async def test_setup_deferred_focus_preserves_modal(app_context_factory, monkeypatch):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        await env.app.action_help()
        await pilot.pause()
        focused = env.app.focused
        await pilot.press("tab", "shift+tab", "enter")
        assert len(env.app.screen_stack) > 1
        assert env.app.focused is focused
        assert env.fs.calls == env.probes == []
        await pilot.press("escape")
        await pilot.pause()
        assert env.app.focused is button(env.app, "first-run-add")


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["stay", "settings"])
async def test_committed_save_rejects_duplicate_and_cancel_and_survives_navigation(
    app_context_factory, monkeypatch, destination
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    started, release = threading.Event(), threading.Event()
    writes = []
    original = env.ctx.config_store.add_connection

    def barrier(entry):  # type: ignore[no-untyped-def]
        original(entry)
        writes.append(entry.name)
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(env.ctx.config_store, "add_connection", barrier)
    try:
        async with env.app.run_test(size=(120, 40)) as pilot:
            await ready(env, pilot)
            before = aws_bytes(env)
            await fill_keyboard(env.app, pilot)
            await pilot.press("enter")
            await wait_until(started.is_set, what="durable save completed before publish")
            form = env.app.query_one(ConnectionFormInline)
            form.action_cancel()
            await pilot.press("escape", "enter")
            assert form.has_class("-open")
            assert all(i.is_disabled for i in form.query(Input))
            assert "local" in env.ctx.config_store.load().connections
            if destination == "settings":
                await pilot.press("comma")
                await wait_until(
                    lambda: len(env.app.query(SettingsView)) == 1,
                    what="Settings replaces pending save page",
                )
            release.set()
            await drain_workers(env.app)
            assert writes == ["local"]
            assert "local" in env.ctx.config_store.load().connections
            if destination == "settings":
                assert len(env.app.query(SettingsView)) == 1
                assert env.probes == env.fs.calls == []
                assert env.ctx.root_vm.active_connection is None
            else:
                assert len(env.app.query(DualPane)) == 1
                assert env.probes == ["local"]
            assert aws_bytes(env) == before
    finally:
        release.set()


@pytest.mark.asyncio
async def test_settings_save_is_not_handled_by_setup_owner(app_context_factory, monkeypatch):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    writes = []
    original = env.ctx.config_store.add_connection

    def record(entry):  # type: ignore[no-untyped-def]
        writes.append(entry.name)
        return original(entry)

    monkeypatch.setattr(env.ctx.config_store, "add_connection", record)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        before = aws_bytes(env)
        await pilot.press("comma")
        await drain_workers(env.app)
        form = env.app.query_one(ConnectionFormInline)
        form.open_for_add()
        for field, value in (
            ("name", "settings"),
            ("endpoint_url", "http://localhost:9000"),
            ("region", "us-east-1"),
            ("access_key_id", "KEY"),
            ("secret_access_key", "SECRET"),
        ):
            form.query_one(f"#form-{field}", Input).value = value
        await pilot.pause()
        button(form, "form-save-btn").focus()
        await pilot.press("enter")
        await wait_until(lambda: not form.has_class("-open"), what="Settings save handler finished")
        await drain_workers(env.app)
        assert writes == ["settings"]
        assert len(env.app.query(SettingsView)) == 1
        assert env.ctx.root_vm.active_connection is None
        assert env.fs.calls == env.probes == []
        assert aws_bytes(env) == before


@pytest.mark.asyncio
async def test_changed_identity_during_probe_is_not_adopted(app_context_factory, monkeypatch):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    started, release = threading.Event(), threading.Event()

    def probe(connection):  # type: ignore[no-untyped-def]
        env.probes.append(connection.region)
        started.set()
        assert release.wait(10)
        return TokenProbeResult(TokenState.CONNECTED)

    monkeypatch.setattr(env.ctx.aws_session, "probe_token", probe)
    try:
        async with env.app.run_test(size=(120, 40)) as pilot:
            await ready(env, pilot)
            env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
            await pilot.press("tab", "tab", "enter")
            await drain_workers(env.app)
            env.app.query_one(ConnectionChoice).focus()
            await pilot.press("enter")
            await wait_until(started.is_set, what="identity probe in flight")
            env.aws_config.write_text("[profile added]\nregion=eu-west-1\n")
            before = aws_bytes(env)
            release.set()
            await drain_workers(env.app)
            assert env.ctx.root_vm.active_connection is None
            assert env.probes == ["us-east-1"]
            assert env.fs.calls == []
            assert [r.connection_name for r in env.app.query(ConnectionChoice)] == ["added"]
            assert aws_bytes(env) == before
    finally:
        release.set()


@pytest.mark.asyncio
async def test_mount_lifecycle_failure_recovers_actionable_setup(app_context_factory, monkeypatch):  # type: ignore[no-untyped-def]
    from textual.widget import Widget

    from aws_tui import app as app_module

    env = setup_context(app_context_factory, monkeypatch)

    class BrokenView(Widget):
        def on_mount(self):
            raise RuntimeError("private-endpoint-secret")

    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        monkeypatch.setattr(app_module, "build_service_view", lambda *args, **kwargs: BrokenView())
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        await pilot.pause()
        assert len(env.app.query(FirstRunView)) == 1
        assert "Unable to open connection" in str(
            env.app.query_one("#first-run-status", Static).content
        )
        assert not button(env.app, "first-run-add").is_disabled
        assert env.app.crash_report is None
        assert aws_bytes(env) == before


@pytest.mark.asyncio
async def test_invalid_aws_source_does_not_autoopen_other_usable_profiles(
    app_context_factory, monkeypatch
):  # type: ignore[no-untyped-def]
    env = setup_context(app_context_factory, monkeypatch)
    env.aws_config.write_text("[profile usable]\nregion=us-east-1\n")
    env.aws_credentials.write_bytes(b"[broken")
    before = aws_bytes(env)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        assert str(env.app.query_one("#first-run-status", Static).content) == INVALID_CONFIGURATION
        assert [r.connection_name for r in env.app.query(ConnectionChoice)] == ["usable"]
        assert env.ctx.root_vm.active_connection is None
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("hold_descendants", [False, True])
async def test_mount_recovery_discovery_cannot_update_new_setup_generation(
    app_context_factory, monkeypatch, hold_descendants
):  # type: ignore[no-untyped-def]
    from textual.widget import Widget

    from aws_tui import app as app_module
    from aws_tui.ui.widgets.first_run import FirstRunConnectionList

    env = setup_context(app_context_factory, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    partial_mount_started, descendants_release = asyncio.Event(), asyncio.Event()
    original_mount_composed = FirstRunView.mount_composed_widgets
    original_recover = env.app._recover_content_mount_lifecycle
    original_refresh = env.app._refresh_first_run_discovery
    recovering = False
    paused = False

    class BrokenView(Widget):
        def on_mount(self):
            raise RuntimeError("private-endpoint-secret")

    async def recover(*args):
        nonlocal recovering
        recovering = True
        try:
            await original_recover(*args)
        finally:
            recovering = False

    async def refresh(generation):
        nonlocal paused
        snapshot = await original_refresh(generation)
        if recovering and not paused:
            paused = True
            started.set()
            await release.wait()
        return snapshot

    async def mount_composed(view, widgets):
        # Textual registers the view before composing and mounting its descendants.
        partial_mount_started.set()
        await descendants_release.wait()
        await original_mount_composed(view, widgets)

    def new_setup_ready():
        try:
            views = env.app.query(FirstRunView)
            return len(views) == 1 and any(
                b.button_id == "first-run-add" and not b.is_disabled
                for b in views[0].query(ModalButton)
            )
        finally:
            # Let real descendant mounting continue only after readiness was polled.
            descendants_release.set()

    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        monkeypatch.setattr(env.app, "_recover_content_mount_lifecycle", recover)
        monkeypatch.setattr(env.app, "_refresh_first_run_discovery", refresh)
        monkeypatch.setattr(app_module, "build_service_view", lambda *args, **kwargs: BrokenView())
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("enter")
        await asyncio.wait_for(started.wait(), 5)
        await pilot.press("comma")
        if hold_descendants:
            monkeypatch.setattr(FirstRunView, "mount_composed_widgets", mount_composed)
        # Recovery holds the mount lock; intent advances before the winner mounts.
        env.app.on_first_run_connection_list_setup_requested(
            FirstRunConnectionList.SetupRequested()
        )
        release.set()
        if hold_descendants:
            await asyncio.wait_for(partial_mount_started.wait(), 5)
            assert len(env.app.query(FirstRunView)) == 1
            assert not any(b.button_id == "first-run-add" for b in env.app.query(ModalButton))
        await wait_until(
            new_setup_ready,
            what="new setup generation ready",
        )
        release.set()
        await drain_workers(env.app)
        assert "Unable to open connection" not in str(
            env.app.query_one("#first-run-status", Static).content
        )
        assert env.app.crash_report is None
        assert aws_bytes(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "reset", "remove"])
@pytest.mark.parametrize("destination", ["settings", "setup", "shutdown"])
async def test_failed_mount_recovery_retains_original_navigation_owner(
    app_context_factory, monkeypatch, phase, destination
):  # type: ignore[no-untyped-def]
    from textual.widget import Widget

    from aws_tui import app as app_module
    from aws_tui.ui.widgets.first_run import FirstRunConnectionList

    env = setup_context(app_context_factory, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    winner_mount_started = asyncio.Event()
    original_recover = env.app._recover_content_mount_lifecycle
    original_reset = env.app._reset_content_host
    original_settings_mount = env.app._mount_settings_view
    original_setup_mount = env.app._mount_no_connection_placeholder
    paused = False
    recovering = False

    class BrokenView(Widget):
        def on_mount(self):
            raise RuntimeError("old mount failure")

    async def recover(*args):
        nonlocal recovering
        if phase == "queued":
            started.set()
            await release.wait()
        recovering = True
        try:
            await original_recover(*args)
        finally:
            recovering = False

    async def reset(host):
        nonlocal paused
        if phase == "remove" and recovering and not paused:
            paused = True
            original_remove = host.remove

            async def remove():
                result = await original_remove()
                started.set()
                await release.wait()
                return result

            monkeypatch.setattr(host, "remove", remove)
        result = await original_reset(host)
        if phase == "reset" and recovering and not paused:
            paused = True
            started.set()
            await release.wait()
        return result

    async def settings_mount():
        winner_mount_started.set()
        await original_settings_mount()

    async def setup_mount():
        winner_mount_started.set()
        await original_setup_mount()

    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        monkeypatch.setattr(env.app, "_recover_content_mount_lifecycle", recover)
        monkeypatch.setattr(env.app, "_reset_content_host", reset)
        monkeypatch.setattr(app_module, "build_service_view", lambda *args, **kwargs: BrokenView())
        env.app.query_one(ConnectionChoice).focus()
        await pilot.pause()
        await env.app.action_descend()
        await asyncio.wait_for(started.wait(), 5)
        try:
            if destination == "shutdown":
                monkeypatch.setattr(env.app, "_mount_settings_view", settings_mount)
                monkeypatch.setattr(env.app, "_mount_no_connection_placeholder", setup_mount)
                quit_task = asyncio.create_task(env.app.action_quit())
                await wait_until(
                    lambda: env.app._service_navigation_closed, what="shutdown closes intake"
                )
                release.set()
                await quit_task
                assert not winner_mount_started.is_set()
            else:
                if phase == "remove":
                    monkeypatch.setattr(env.app, "_mount_settings_view", settings_mount)
                    monkeypatch.setattr(env.app, "_mount_no_connection_placeholder", setup_mount)
                env.app.action_open_settings()
                if phase == "queued":
                    await wait_until(
                        lambda: len(env.app.query(SettingsView)) == 1,
                        what="Settings wins queued recovery",
                    )
                if destination == "setup":
                    env.app.on_first_run_connection_list_setup_requested(
                        FirstRunConnectionList.SetupRequested()
                    )
                # The failed host has been reset or removed; flush healthy chrome
                # while the winner waits at the navigation boundary.
                await pilot.pause()
                if phase == "remove":
                    assert not winner_mount_started.is_set()
                release.set()
                await drain_workers(env.app)
                await pilot.pause()
                assert len(env.app.query("#content-host")) == 1
                assert not env.app.query("#content-mount-error")
                if destination == "settings":
                    assert len(env.app.query(SettingsView)) == 1
                    assert not env.app.query(FirstRunView)
                else:
                    assert len(env.app.query(FirstRunView)) == 1
                    assert "Unable to open connection" not in str(
                        env.app.query_one("#first-run-status", Static).content
                    )
                    await pilot.press("enter", *"unsaved-new-form")
                    expected_view = env.app.query_one(FirstRunView)
                    expected_form = env.app.query_one(ConnectionFormInline)
                    await drain_workers(env.app)
                    assert env.app.query_one(FirstRunView) is expected_view
                    assert env.app.query_one(ConnectionFormInline) is expected_form
                    assert env.app.query_one(Input).value == "unsaved-new-form"
            assert env.app.crash_report is None
            assert aws_bytes(env) == before
        finally:
            release.set()


@pytest.mark.asyncio
async def test_public_quit_drains_pending_first_run_rail_prune(app_context_factory, monkeypatch):  # type: ignore[no-untyped-def]
    from aws_tui.ui.widgets.first_run import FirstRunConnectionList

    env = setup_context(app_context_factory, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    prune_cancelled = asyncio.Event()

    async def unmount(_section):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            prune_cancelled.set()
            raise

    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        env.aws_config.write_text("[profile added]\nregion=us-east-1\n")
        before = aws_bytes(env)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        nav = env.app.query_one(NavMenu)
        width_before = nav.styles.width
        spacer = nav.query_one("#menu-spacer")
        assert not spacer.display
        monkeypatch.setattr(FirstRunConnectionList, "on_unmount", unmount, raising=False)
        env.app.query_one(ConnectionChoice).focus()
        await pilot.pause()
        await env.app.action_descend()
        await asyncio.wait_for(started.wait(), 5)
        try:
            quit_task = asyncio.create_task(env.app.action_quit())
            await wait_until(
                lambda: env.app._service_navigation_closed, what="public quit closes intake"
            )
            release.set()
            await quit_task
            # This is observed before run_test's context performs its cleanup.
            print("rail message pump cancelled during public quit:", prune_cancelled.is_set())
            assert not prune_cancelled.is_set()
            assert nav.styles.width == width_before
            assert not spacer.display
            assert env.app._shutdown_complete
            assert env.app.crash_report is None
            assert aws_bytes(env) == before
        finally:
            release.set()
