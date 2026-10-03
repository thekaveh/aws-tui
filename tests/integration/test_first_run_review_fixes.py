"""Full-App regression evidence for discovery errors and superseded save outcomes."""

from __future__ import annotations

import asyncio
import threading

import pytest
from keyring.errors import KeyringLocked
from textual.widget import Widget
from textual.widgets import Input, Static

from aws_tui.infra.config_store import ConnectionEntry
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keychain import InMemoryKeychain
from aws_tui.ui.widgets.first_run import ConnectionChoice, FirstRunView
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.ui.widgets.settings_view import SettingsView
from aws_tui.ui.widgets.toast import Toast
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from tests.helpers import drain_workers, wait_until
from tests.integration.test_first_run_flow import (
    aws_bytes,
    button,
    file_bytes,
    fill_keyboard,
    ready,
    setup_context,
)


class ControlledKeychain(InMemoryKeychain):
    locked = True

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.pause_failure = False

    def get(self, service, key):
        if self.locked:
            if self.pause_failure:
                self.pause_failure = False
                self.started.set()
                assert self.release.wait(10)
            raise KeyringLocked("private-endpoint-secret")
        return super().get(service, key)


def install_backend(env, backend):
    ctx = env.ctx
    ctx.connection_resolver = ConnectionResolver(
        config_store=ctx.config_store,
        keychain=backend,
        aws_config_path=env.aws_config,
        aws_credentials_path=env.aws_credentials,
    )
    ctx.s3_connections_vm = S3ConnectionsVM(
        resolver=ctx.connection_resolver,
        config_store=ctx.config_store,
        keychain=backend,
        hub=ctx.hub,
        dispatcher=ctx.dispatcher,
    )


def add_keychain_connection(env):
    env.ctx.config_store.add_connection(
        ConnectionEntry(
            name="local",
            kind="s3-compatible",
            endpoint_url="http://localhost:9000",
            credentials="keychain:local",
        )
    )


def assert_actionable_error(env):
    text = str(env.app.query_one("#first-run-status", Static).content)
    assert "keychain" in text.lower()
    assert "Retry" in text
    assert "private-endpoint-secret" not in text
    assert not button(env.app, "first-run-retry").is_disabled
    assert not button(env.app, "first-run-add").is_disabled
    assert env.app.crash_report is None


@pytest.mark.asyncio
async def test_locked_backend_startup_retry_and_external_repair(app_context_factory, monkeypatch):
    env = setup_context(app_context_factory, monkeypatch)
    backend = ControlledKeychain()
    install_backend(env, backend)
    add_keychain_connection(env)
    before = aws_bytes(env), file_bytes(env.ctx.config_store.path)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await drain_workers(env.app)
        await pilot.pause()
        assert_actionable_error(env)
        assert env.ctx.root_vm.active_connection is None
        assert env.fs.calls == []
        button(env.app, "first-run-retry").focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        assert_actionable_error(env)
        backend.locked = False
        button(env.app, "first-run-retry").focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        assert [row.connection_name for row in env.app.query(ConnectionChoice)] == ["local"]
        assert "keychain" not in str(env.app.query_one("#first-run-status", Static).content)
        assert env.probes == env.fs.calls == []
        assert (aws_bytes(env), file_bytes(env.ctx.config_store.path)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["selection", "post-probe", "recovery", "mount-recovery"])
async def test_selection_backend_failure_is_contained(app_context_factory, monkeypatch, phase):
    env = setup_context(app_context_factory, monkeypatch)
    backend = ControlledKeychain()
    backend.locked = False
    install_backend(env, backend)
    async with env.app.run_test(size=(120, 40)) as pilot:
        await ready(env, pilot)
        add_keychain_connection(env)
        before = aws_bytes(env), file_bytes(env.ctx.config_store.path)
        await pilot.press("tab", "tab", "enter")
        await drain_workers(env.app)
        if phase == "selection":
            backend.locked = True
        elif phase == "post-probe":
            original_probe = env.ctx.aws_session.probe_token

            def lock_after_probe(connection):
                result = original_probe(connection)
                backend.locked = True
                return result

            monkeypatch.setattr(env.ctx.aws_session, "probe_token", lock_after_probe)
        elif phase == "recovery":

            def fail_build(*args):
                backend.locked = True
                raise RuntimeError("private-endpoint-secret")

            monkeypatch.setattr(env.ctx.registry.get("s3"), "build_vm", fail_build)
        else:
            from aws_tui import app as app_module

            class BrokenView(Widget):
                def on_mount(self):
                    backend.locked = True
                    raise RuntimeError("private-endpoint-secret")

            monkeypatch.setattr(
                app_module, "build_service_view", lambda *args, **kwargs: BrokenView()
            )
        env.app.query_one(ConnectionChoice).focus()
        await pilot.press("enter")
        await drain_workers(env.app)
        assert_actionable_error(env)
        if phase != "mount-recovery":
            assert env.ctx.root_vm.active_connection is None
            assert env.fs.calls == []
        else:
            # Explicit selection already adopted the connection before the view failed.
            assert env.ctx.root_vm.active_connection.name == "local"
        assert env.probes == ([] if phase == "selection" else ["local"])
        assert (aws_bytes(env), file_bytes(env.ctx.config_store.path)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["settings", "shutdown"])
async def test_backend_failure_after_supersession_cannot_update_ui(
    app_context_factory, monkeypatch, destination
):
    env = setup_context(app_context_factory, monkeypatch)
    backend = ControlledKeychain()
    backend.locked = False
    install_backend(env, backend)
    try:
        async with env.app.run_test(size=(120, 40)) as pilot:
            await ready(env, pilot)
            add_keychain_connection(env)
            before = aws_bytes(env), file_bytes(env.ctx.config_store.path)
            backend.locked = backend.pause_failure = True
            await pilot.press("tab", "tab", "enter")
            await wait_until(backend.started.is_set, what="backend discovery failure barrier")
            shutdown = None
            if destination == "settings":
                await pilot.press("comma")
                await wait_until(
                    lambda: len(env.app.query(SettingsView)) == 1, what="Settings mounted"
                )
                page = env.app.query_one(SettingsView)
                form = page.query_one(ConnectionFormInline)
                form.open_for_add()
                field = form.query_one("#form-name", Input)
                field.value = "keep"
                field.focus()
                await pilot.pause()
            else:
                shutdown = asyncio.create_task(env.app.action_quit())
                await wait_until(
                    lambda: env.app._service_navigation_closed, what="closed navigation"
                )
            backend.release.set()
            if shutdown is not None:
                await shutdown
            else:
                await drain_workers(env.app)
                await pilot.pause()
                assert env.app.query_one(SettingsView) is page
                assert env.app.focused is field
                assert field.value == "keep"
                assert not env.app.query(FirstRunView)
            assert env.app.crash_report is None
            assert env.ctx.root_vm.active_connection is None
            assert env.probes == env.fs.calls == []
            assert (aws_bytes(env), file_bytes(env.ctx.config_store.path)) == before
    finally:
        backend.release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("destination", ["settings", "shutdown"])
async def test_save_precommit_outcome_survives_navigation_without_activation(
    app_context_factory, monkeypatch, failure, destination
):
    env = setup_context(app_context_factory, monkeypatch)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    attempts = []
    durable_bytes = []
    original = env.ctx.config_store.add_connection

    def before_commit(entry):
        attempts.append(entry.name)
        started.set()
        assert release.wait(10)
        try:
            if failure:
                raise OSError("private-endpoint-secret")
            original(entry)
            durable_bytes.append(file_bytes(env.ctx.config_store.path))
        finally:
            finished.set()

    monkeypatch.setattr(env.ctx.config_store, "add_connection", before_commit)
    notices = []
    stack = env.ctx.root_vm.chrome.toast_stack
    original_notice = stack.raise_toast

    def record_notice(model):
        notices.append(model)
        return original_notice(model)

    monkeypatch.setattr(stack, "raise_toast", record_notice)
    try:
        async with env.app.run_test(size=(120, 40)) as pilot:
            await ready(env, pilot)
            before = aws_bytes(env), file_bytes(env.ctx.config_store.path)
            await fill_keyboard(env.app, pilot)
            await pilot.press("enter")
            await wait_until(started.is_set, what="precommit barrier before durable write")
            assert file_bytes(env.ctx.config_store.path) == before[1]
            shutdown = None
            if destination == "settings":
                await pilot.press("comma")
                await wait_until(
                    lambda: len(env.app.query(SettingsView)) == 1, what="Settings before commit"
                )
                page = env.app.query_one(SettingsView)
                assert "No S3-compatible connections" in " ".join(
                    str(s.content) for s in page.query(Static)
                )
                form = page.query_one(ConnectionFormInline)
                form.open_for_add()
                field = form.query_one("#form-name", Input)
                field.value = "keep"
                field.focus()
                await pilot.pause()
            else:
                shutdown = asyncio.create_task(env.app.action_quit())
                await wait_until(
                    lambda: env.app._service_navigation_closed, what="closed navigation"
                )
            notices.clear()
            release.set()
            if shutdown is not None:
                await shutdown
                await wait_until(finished.is_set, what="shutdown persistence thread finished")
                assert not notices
            else:
                await drain_workers(env.app)
                await pilot.pause()
                assert env.app.query_one(SettingsView) is page
                assert page.query_one(ConnectionFormInline) is form
                assert env.app.focused is field
                assert field.value == "keep"
                assert form.has_class("-open")
                expected = "Unable to save connection" if failure else "Connection saved"
                visible = [toast.render().plain for toast in env.app.query(Toast) if toast.visible]
                assert any(expected in text for text in visible)
                assert all("private-endpoint-secret" not in text for text in visible)
                assert len([model for model in notices if expected in model.text]) == 1
            assert attempts == ["local"]
            if failure:
                assert file_bytes(env.ctx.config_store.path) == before[1]
                assert env.ctx.config_store.load().connections == {}
            else:
                persisted = env.ctx.config_store.load().connections["local"]
                assert persisted.kind == "s3-compatible"
                assert persisted.region == "us-east-1"
                assert persisted.endpoint_url == "http://localhost:9000"
                assert persisted.credentials == "keychain:aws-tui:connections/local"
                assert persisted.access_key_id is persisted.secret_access_key is None
                assert file_bytes(env.ctx.config_store.path) == durable_bytes[0]
                assert file_bytes(env.ctx.config_store.path) != before[1]
                assert "SECRET" not in env.ctx.config_store.path.read_text()
            assert env.probes == env.fs.calls == []
            assert env.ctx.root_vm.active_connection is None
            assert aws_bytes(env) == before[0]
            assert env.app.crash_report is None
    finally:
        release.set()
