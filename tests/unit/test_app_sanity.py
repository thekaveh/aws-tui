"""Package-level sanity smoke tests — the package imports, version is
set, and the app class is exposed. The bare-minimum CI must keep
green; per-layer behavioral coverage lives in the tier suites under
unit/, integration/, snapshot/, and e2e/.
"""

from __future__ import annotations

import asyncio
import json
import re
import runpy
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui import __version__
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM


def test_package_imports() -> None:
    """Importing the top-level package shouldn't raise."""
    import aws_tui  # noqa: F401


def test_version_is_set() -> None:
    """``__version__`` is exposed at the package root and follows semver.

    The literal value is *not* pinned here so a version bump doesn't
    cascade into a test failure — release plumbing is the right place
    for that gate.
    """
    from aws_tui import __version__

    assert isinstance(__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+([.-].+)?$", __version__), __version__


def test_app_class_is_exposed() -> None:
    """``AwsTuiApp`` is importable from the top-level package."""
    from aws_tui import AwsTuiApp, __version__

    assert AwsTuiApp.__name__ == "AwsTuiApp"
    assert AwsTuiApp.TITLE == "aws-tui"
    assert f"v{__version__}" == AwsTuiApp.SUB_TITLE


def test_cli_help_prints_without_launching_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aws_tui import app as app_module

    def fail_build_context(*_args: object, **_kwargs: object) -> None:
        pytest.fail("--help should not construct the Textual app context")

    monkeypatch.setattr(sys, "argv", ["aws-tui", "--help"])
    monkeypatch.setattr(app_module, "build_app_context", fail_build_context)

    with pytest.raises(SystemExit) as exc_info:
        app_module.main()

    assert exc_info.value.code == 0
    assert "--demo" in capsys.readouterr().out


def test_cli_unknown_flag_fails_without_launching_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aws_tui import app as app_module

    def fail_build_context(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unknown CLI flags should not construct the Textual app context")

    monkeypatch.setattr(sys, "argv", ["aws-tui", "--wat"])
    monkeypatch.setattr(app_module, "build_app_context", fail_build_context)

    with pytest.raises(SystemExit) as exc_info:
        app_module.main()

    assert exc_info.value.code == 2
    assert "unrecognized arguments: --wat" in capsys.readouterr().err


def test_cli_version_prints_without_launching_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aws_tui import app as app_module

    def fail_build_context(*_args: object, **_kwargs: object) -> None:
        pytest.fail("--version should not construct the Textual app context")

    monkeypatch.setattr(sys, "argv", ["aws-tui", "--version"])
    monkeypatch.setattr(app_module, "build_app_context", fail_build_context)

    app_module.main()

    assert capsys.readouterr().out == f"aws-tui {__version__} (demo: disabled)\n"


def test_cli_version_reports_demo_flag_without_launching_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aws_tui import app as app_module

    def fail_build_context(*_args: object, **_kwargs: object) -> None:
        pytest.fail("--version should not construct the Textual app context")

    monkeypatch.setattr(sys, "argv", ["aws-tui", "--demo", "--version"])
    monkeypatch.setattr(app_module, "build_app_context", fail_build_context)

    app_module.main()

    assert capsys.readouterr().out == f"aws-tui {__version__} (demo: enabled)\n"


def test_cli_version_reports_env_demo_without_launching_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aws_tui import app as app_module

    def fail_build_context(*_args: object, **_kwargs: object) -> None:
        pytest.fail("--version should not construct the Textual app context")

    monkeypatch.setattr(sys, "argv", ["aws-tui", "--version"])
    monkeypatch.setenv("AWS_TUI_DEMO", "yes")
    monkeypatch.setattr(app_module, "build_app_context", fail_build_context)

    app_module.main()

    assert capsys.readouterr().out == f"aws-tui {__version__} (demo: enabled)\n"


def test_python_module_entrypoint_invokes_main(monkeypatch: pytest.MonkeyPatch) -> None:
    import aws_tui.app as app_module

    calls: list[str] = []

    def fake_main() -> None:
        calls.append("main")

    monkeypatch.setattr(app_module, "main", fake_main)

    runpy.run_module("aws_tui.__main__", run_name="__main__")

    assert calls == ["main"]


def test_cli_demo_flag_reaches_app_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui import app as app_module

    contexts: list[object] = []
    demos: list[bool] = []

    def fake_build_app_context(*, demo: bool) -> object:
        demos.append(demo)
        return object()

    class FakeApp:
        crash_report = None

        def __init__(self, *, context: object) -> None:
            contexts.append(context)

        def run(self) -> None:
            return None

    monkeypatch.setattr(sys, "argv", ["aws-tui", "--demo"])
    monkeypatch.delenv("AWS_TUI_DEMO", raising=False)
    monkeypatch.setattr(app_module, "build_app_context", fake_build_app_context)
    monkeypatch.setattr(app_module, "AwsTuiApp", FakeApp)

    app_module.main()

    assert demos == [True]
    assert len(contexts) == 1


def test_cli_env_demo_reaches_app_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui import app as app_module

    demos: list[bool] = []

    def fake_build_app_context(*, demo: bool) -> object:
        demos.append(demo)
        return object()

    class FakeApp:
        crash_report = None

        def __init__(self, *, context: object) -> None:
            pass

        def run(self) -> None:
            return None

    monkeypatch.setattr(sys, "argv", ["aws-tui"])
    monkeypatch.setenv("AWS_TUI_DEMO", "yes")
    monkeypatch.setattr(app_module, "build_app_context", fake_build_app_context)
    monkeypatch.setattr(app_module, "AwsTuiApp", FakeApp)

    app_module.main()

    assert demos == [True]


def test_cli_returns_failure_when_textual_swallows_fatal_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui import app as app_module

    report = SimpleNamespace(
        exception_type="RuntimeError",
        exception_message="boom",
        dump_path=Path("/tmp/crash.txt"),
    )

    class FakeApp:
        crash_report = report

        def __init__(self, *, context: object) -> None:
            pass

        def run(self) -> None:
            return None

    monkeypatch.setattr(sys, "argv", ["aws-tui"])
    monkeypatch.setattr(app_module, "build_app_context", lambda *, demo: object())
    monkeypatch.setattr(app_module, "AwsTuiApp", FakeApp)

    with pytest.raises(SystemExit) as exc_info:
        app_module.main()

    assert exc_info.value.code == 1


def test_cli_reports_redacted_startup_failure_before_app_construction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aws_tui import app as app_module

    def fail_build_context(*, demo: bool) -> object:
        del demo
        raise RuntimeError("secret_access_key=SUPERSECRET")

    monkeypatch.setattr(sys, "argv", ["aws-tui"])
    monkeypatch.setattr(app_module, "build_app_context", fail_build_context)

    with pytest.raises(SystemExit) as exc_info:
        app_module.main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "aws-tui failed to start" in stderr
    assert "RuntimeError" in stderr
    assert "[REDACTED]" in stderr
    assert "SUPERSECRET" not in stderr


def test_bound_action_records_action_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui import app as app_module

    app = object.__new__(app_module.AwsTuiApp)
    app._action_ring = deque(maxlen=100)  # type: ignore[attr-defined]
    app._last_action_id = None  # type: ignore[attr-defined]
    app._current_mode = "default"  # type: ignore[attr-defined]
    app._screen_stacks = {"default": []}  # type: ignore[attr-defined]
    monkeypatch.setattr(app, "_cycle_focus", lambda *, reverse: None)

    app.action_switch_focus()

    assert app.last_action_id == "pane.switch_focus"
    assert str(app._action_ring[-1]).endswith(" pane.switch_focus")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_app_shutdown_awaits_hosted_vm_shutdown_before_root_dispose() -> None:
    from aws_tui import app as app_module

    events: list[str] = []

    class _ContentHost:
        async def shutdown(self) -> None:
            events.append("content.shutdown")

    class _RootVM:
        content_host = _ContentHost()

        def dispose(self) -> None:
            events.append("root.dispose")

    class _Disposable:
        def dispose(self) -> None:
            pass

    async def close_clients() -> None:
        events.append("clients.close")

    app = object.__new__(app_module.AwsTuiApp)
    app._workers = SimpleNamespace(cancel_group=lambda *_args: None)  # type: ignore[attr-defined]
    app._pane_state_sub = None  # type: ignore[attr-defined]
    app._connection_list_sub = None  # type: ignore[attr-defined]
    app._nav_selection_sub = None  # type: ignore[attr-defined]
    app._cursor_sub = None  # type: ignore[attr-defined]
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        transfers_vm=SimpleNamespace(
            cancel_all_command=SimpleNamespace(execute=lambda: None), dispose=lambda: None
        ),
        aws_session=SimpleNamespace(aclose_all_clients=close_clients),
        log_sink=SimpleNamespace(
            flush=lambda: events.append("logs.flush"),
            close=lambda: events.append("logs.close"),
        ),
        s3_connections_vm=_Disposable(),
        command_palette_vm=_Disposable(),
        quick_look_vm=_Disposable(),
        confirm_vm=_Disposable(),
        table_clipboard_vm=SimpleNamespace(dispose=lambda: events.append("clipboard.dispose")),
        root_vm=_RootVM(),
        focus_coordinator=_Disposable(),
        demo_emr=None,
    )

    await app._aws_tui_shutdown()

    assert events == [
        "content.shutdown",
        "clients.close",
        "clipboard.dispose",
        "root.dispose",
        "logs.flush",
        "logs.close",
    ]


@pytest.mark.asyncio
async def test_app_shutdown_records_failures_continues_and_closes_logging_last() -> None:
    from aws_tui import app as app_module

    events: list[str] = []

    class _ContentHost:
        async def shutdown(self) -> None:
            events.append("content.shutdown")

    class _Disposable:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self._name = name
            self._fail = fail

        def dispose(self) -> None:
            events.append(f"{self._name}.dispose")
            if self._fail:
                raise RuntimeError(f"{self._name} failed")

    class _Root(_Disposable):
        content_host = _ContentHost()

    class _Log:
        def error(self, event: str, **fields: object) -> None:
            events.append(f"log.error:{event}:{fields['step']}")

        def flush(self) -> None:
            events.append("logs.flush")

        def close(self) -> None:
            events.append("logs.close")

    async def close_clients() -> None:
        events.append("clients.close")
        raise RuntimeError("clients failed")

    async def close_demo() -> None:
        events.append("demo.close")

    app = object.__new__(app_module.AwsTuiApp)
    app._workers = SimpleNamespace(cancel_group=lambda *_args: None)  # type: ignore[attr-defined]
    app._pane_state_sub = None  # type: ignore[attr-defined]
    app._connection_list_sub = None  # type: ignore[attr-defined]
    app._nav_selection_sub = None  # type: ignore[attr-defined]
    app._cursor_sub = None  # type: ignore[attr-defined]
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        transfers_vm=SimpleNamespace(
            cancel_all_command=SimpleNamespace(execute=lambda: None),
            dispose=lambda: events.append("transfers.dispose"),
        ),
        aws_session=SimpleNamespace(aclose_all_clients=close_clients),
        log_sink=_Log(),
        s3_connections_vm=_Disposable("connections", fail=True),
        command_palette_vm=SimpleNamespace(
            shutdown=_complete,
            dispose=lambda: events.append("palette.dispose"),
        ),
        quick_look_vm=_Disposable("quick-look"),
        confirm_vm=_Disposable("confirm"),
        table_clipboard_vm=_Disposable("clipboard"),
        root_vm=_Root("root"),
        focus_coordinator=_Disposable("focus"),
        demo_emr=SimpleNamespace(aclose=close_demo),
    )

    await app._aws_tui_shutdown()

    assert events.index("root.dispose") < events.index("logs.flush")
    assert events.index("focus.dispose") < events.index("logs.flush")
    assert events.index("demo.close") < events.index("logs.flush")
    assert "log.error:app.shutdown.cleanup_failed:aws_session.aclose_all_clients" in events
    assert "log.error:app.shutdown.cleanup_failed:s3_connections_vm.dispose" in events
    assert events[-1] == "logs.close"
    assert app._shutdown_errors == (  # type: ignore[attr-defined]
        ("aws_session.aclose_all_clients", "RuntimeError"),
        ("s3_connections_vm.dispose", "RuntimeError"),
    )


@pytest.mark.asyncio
async def test_app_shutdown_waits_for_host_after_cancellation() -> None:
    from aws_tui import app as app_module

    events: list[str] = []
    shutdown_started = asyncio.Event()
    finish_shutdown = asyncio.Event()

    class _ContentHost:
        async def shutdown(self) -> None:
            events.append("content.shutdown.started")
            shutdown_started.set()
            await finish_shutdown.wait()
            events.append("content.shutdown.finished")

    class _RootVM:
        content_host = _ContentHost()

        def dispose(self) -> None:
            events.append("root.dispose")

    class _Disposable:
        def dispose(self) -> None:
            pass

    app = object.__new__(app_module.AwsTuiApp)
    app._workers = SimpleNamespace(cancel_group=lambda *_args: None)  # type: ignore[attr-defined]
    app._pane_state_sub = None  # type: ignore[attr-defined]
    app._connection_list_sub = None  # type: ignore[attr-defined]
    app._nav_selection_sub = None  # type: ignore[attr-defined]
    app._cursor_sub = None  # type: ignore[attr-defined]
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        transfers_vm=SimpleNamespace(
            cancel_all_command=SimpleNamespace(execute=lambda: None), dispose=lambda: None
        ),
        aws_session=SimpleNamespace(aclose_all_clients=lambda: _complete()),
        log_sink=SimpleNamespace(flush=lambda: None, close=lambda: None),
        s3_connections_vm=_Disposable(),
        command_palette_vm=_Disposable(),
        quick_look_vm=_Disposable(),
        confirm_vm=_Disposable(),
        table_clipboard_vm=_Disposable(),
        root_vm=_RootVM(),
        focus_coordinator=_Disposable(),
        demo_emr=None,
    )

    shutdown_task = asyncio.create_task(app._aws_tui_shutdown())
    await asyncio.wait_for(shutdown_started.wait(), timeout=1.0)
    shutdown_task.cancel()
    await asyncio.sleep(0)

    assert events == ["content.shutdown.started"]
    assert not shutdown_task.done()

    finish_shutdown.set()
    await shutdown_task

    assert events == ["content.shutdown.started", "content.shutdown.finished", "root.dispose"]


async def _complete() -> None:
    return None


def test_build_crash_report_writes_crash_log_event(tmp_path: Path) -> None:
    from aws_tui import app as app_module

    log_sink = LogSink(base_dir=tmp_path / "log")
    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(log_sink=log_sink)  # type: ignore[attr-defined]
    app._action_ring = deque(maxlen=100)  # type: ignore[attr-defined]
    app._last_action_id = None  # type: ignore[attr-defined]

    try:
        report = app._build_crash_report(RuntimeError("boom"))
    finally:
        log_sink.close()

    lines = (tmp_path / "log" / "aws-tui.log").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    crash_records = [record for record in records if record["event"] == "crash.captured"]
    assert len(crash_records) == 1
    assert crash_records[0]["exception_type"] == "RuntimeError"
    assert crash_records[0]["dump_path"] == str(report.dump_path)


def test_build_crash_report_redacts_modal_and_stderr_fields(tmp_path: Path) -> None:
    from aws_tui import app as app_module

    log_sink = LogSink(base_dir=tmp_path / "log")
    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(log_sink=log_sink)  # type: ignore[attr-defined]
    app._action_ring = deque(maxlen=100)  # type: ignore[attr-defined]
    app._last_action_id = None  # type: ignore[attr-defined]

    try:
        try:
            raise RuntimeError(
                "failed https://user:pass@example.com/bucket?token=tok123 secret_access_key=SECRET"
            )
        except RuntimeError as exc:
            report = app._build_crash_report(exc)
    finally:
        log_sink.close()

    rendered = f"{report.exception_message}\n{report.traceback_short}"
    for leaked in ["user:pass", "tok123", "SECRET"]:
        assert leaked not in rendered
    assert "example.com" in rendered
    assert "[REDACTED]" in rendered


def test_build_crash_report_writes_redacted_fallback_when_dump_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui import app as app_module

    real_short_traceback = app_module.CrashDump.short_traceback

    class BrokenCrashDump:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def write(self, **_kwargs: object) -> Path:
            raise OSError("disk full private_key=SECRETPRIVATE")

        @staticmethod
        def short_traceback(exc: BaseException, *, max_lines: int = 5) -> str:
            return real_short_traceback(exc, max_lines=max_lines)

    monkeypatch.setattr(app_module, "CrashDump", BrokenCrashDump)
    log_sink = LogSink(base_dir=tmp_path / "log")
    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(log_sink=log_sink)  # type: ignore[attr-defined]
    app._action_ring = deque(maxlen=100)  # type: ignore[attr-defined]
    app._last_action_id = None  # type: ignore[attr-defined]

    try:
        report = app._build_crash_report(RuntimeError("boom api_key=SECRETAPI"))
    finally:
        log_sink.close()

    assert report.dump_path.parent == tmp_path / "log"
    assert report.dump_path.name.startswith("crash-fallback-")
    fallback = report.dump_path.read_text(encoding="utf-8")
    assert "crash dump unavailable" in fallback
    for leaked in ["SECRETAPI", "SECRETPRIVATE"]:
        assert leaked not in fallback
    assert "[REDACTED]" in fallback
    records = [
        json.loads(line)
        for line in (tmp_path / "log" / "aws-tui.log").read_text(encoding="utf-8").splitlines()
    ]
    crash_records = [record for record in records if record["event"] == "crash.captured"]
    assert len(crash_records) == 1
    assert crash_records[0]["dump_path"] == str(report.dump_path)


def _config_risk_ctx(tmp_path: Path, toml_text: str) -> object:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text(toml_text, encoding="utf-8")
    return SimpleNamespace(
        config_store=ConfigStore(path=config_path),
        log_sink=LogSink(base_dir=tmp_path / "log"),
        root_vm=SimpleNamespace(
            chrome=SimpleNamespace(
                toast_stack=ToastStackVM(
                    hub=MessageHub(),
                    dispatcher=NULL_DISPATCHER,
                )
            )
        ),
    )


def test_config_risk_toasts_ignore_safe_credentials_and_aws_entries(tmp_path: Path) -> None:
    from aws_tui import app as app_module

    ctx = _config_risk_ctx(
        tmp_path,
        "[connections.aws]\n"
        'kind = "aws"\n'
        'profile = "dev"\n'
        "\n"
        "[connections.keychain]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "https://example.com"\n'
        'region = "us-east-1"\n'
        'credentials = "keychain:minio"\n'
        "verify_tls = true\n"
        "\n"
        "[connections.env]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "https://example.net"\n'
        'region = "us-east-1"\n'
        'credentials = "env:R2_"\n'
        "verify_tls = true\n",
    )
    try:
        app_module._raise_config_risk_toasts(ctx)  # type: ignore[arg-type]
        assert ctx.root_vm.chrome.toast_stack.toasts == ()
    finally:
        ctx.log_sink.close()


def test_config_risk_scan_logs_and_continues_on_invalid_config(tmp_path: Path) -> None:
    from aws_tui import app as app_module

    ctx = _config_risk_ctx(tmp_path, "[connections.bad]\nkind = ")
    try:
        app_module._raise_config_risk_toasts(ctx)  # type: ignore[arg-type]
        assert ctx.root_vm.chrome.toast_stack.toasts == ()
        ctx.log_sink.flush()
    finally:
        ctx.log_sink.close()

    raw = (tmp_path / "log" / "aws-tui.log").read_text(encoding="utf-8")
    assert "app.config_risk_scan.failed" in raw
    assert "ConfigError" in raw


@pytest.mark.asyncio
async def test_rebind_pane_threads_verify_tls_to_s3fs(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui import app as app_module
    from aws_tui.infra.connection_resolver import Connection

    providers: list[object] = []

    class RecordingS3FS:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakePane:
        async def swap_provider(self, provider: object, **_kwargs: object) -> None:
            providers.append(provider)

    monkeypatch.setattr("aws_tui.domain.s3_fs.S3FS", RecordingS3FS)
    monkeypatch.setattr("aws_tui.services.s3.service._aioboto3_session_for", lambda _conn: object())
    app = object.__new__(app_module.AwsTuiApp)
    conn = Connection(
        name="minio",
        kind="s3-compatible",
        region="us-east-1",
        source="explicit",
        endpoint_url="https://minio.local",
        force_path_style=True,
        verify_tls=False,
    )

    await app._rebind_pane_to_connection(FakePane(), conn)

    [provider] = providers
    assert isinstance(provider, RecordingS3FS)
    assert provider.kwargs["verify_tls"] is False


@pytest.mark.asyncio
async def test_rebind_pane_to_local_preserves_s3_service_local_root(tmp_path: Path) -> None:
    from aws_tui import app as app_module
    from aws_tui.domain.local_fs import LocalFS

    providers: list[object] = []

    class FakePane:
        async def swap_provider(self, provider: object, **_kwargs: object) -> None:
            providers.append(provider)

    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        registry=SimpleNamespace(
            get=lambda _service_id: SimpleNamespace(
                build_local_provider=lambda: LocalFS(root=tmp_path)
            )
        ),
    )

    await app._rebind_pane_to_local(FakePane())

    [provider] = providers
    assert isinstance(provider, LocalFS)
    assert provider._root == tmp_path.resolve()  # type: ignore[attr-defined]


def test_open_settings_records_registered_action_id() -> None:
    from collections import deque
    from types import SimpleNamespace

    from aws_tui import app as app_module

    executed: list[str] = []
    app = object.__new__(app_module.AwsTuiApp)
    app._action_ring = deque(maxlen=100)  # type: ignore[attr-defined]
    app._last_action_id = None  # type: ignore[attr-defined]
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        root_vm=SimpleNamespace(
            services_menu=SimpleNamespace(
                switch_service_command=SimpleNamespace(execute=executed.append)
            )
        )
    )

    app.action_open_settings()

    assert app.last_action_id == "app.open_settings"
    assert executed == ["settings"]


def test_move_cursor_routes_emr_through_public_page_api_only() -> None:
    from aws_tui import app as app_module

    class FakeEmrPage:
        def __init__(self) -> None:
            self.moves: list[int] = []

        @property
        def _picker(self) -> object:
            raise AssertionError("app must not inspect the EMR picker")

        @property
        def left_pane(self) -> object:
            raise AssertionError("app must not inspect EMR panes")

        def move_focused(self, delta: int) -> bool:
            self.moves.append(delta)
            return False

    page = FakeEmrPage()
    app = object.__new__(app_module.AwsTuiApp)
    app._nav_has_focus = lambda: False  # type: ignore[method-assign]
    app._glue_page = lambda: None  # type: ignore[method-assign]
    app._athena_page = lambda: None  # type: ignore[method-assign]
    app._emr_page = lambda: page  # type: ignore[method-assign]

    app._move_cursor(1)

    assert page.moves == [1]


@pytest.mark.asyncio
async def test_descend_routes_emr_through_public_page_api_only() -> None:
    from aws_tui import app as app_module

    class FakeEmrPage:
        def __init__(self) -> None:
            self.activations = 0

        @property
        def _picker(self) -> object:
            raise AssertionError("app must not inspect the EMR picker")

        def activate_focused(self) -> bool:
            self.activations += 1
            return True

    page = FakeEmrPage()
    app = object.__new__(app_module.AwsTuiApp)
    app.record_action = lambda _action_id: None  # type: ignore[method-assign]
    app._current_mode = "default"  # type: ignore[attr-defined]
    app._screen_stacks = {"default": []}  # type: ignore[attr-defined]
    app._nav_has_focus = lambda: False  # type: ignore[method-assign]
    app._glue_page = lambda: None  # type: ignore[method-assign]
    app._athena_page = lambda: None  # type: ignore[method-assign]
    app._emr_page = lambda: page  # type: ignore[method-assign]

    await app.action_descend()

    assert page.activations == 1


@pytest.mark.asyncio
async def test_settings_reload_ignores_same_named_non_s3_pane() -> None:
    from aws_tui import app as app_module

    calls: list[tuple[str, str] | None] = []

    class FakePane:
        def __init__(self, key: tuple[str, str] | None) -> None:
            self.current_connection_key = key

    aws_pane = FakePane(("aws", "shared"))
    s3_pane = FakePane(("s3-compatible", "shared"))
    app = object.__new__(app_module.AwsTuiApp)
    app._dual_pane = lambda: SimpleNamespace(left=aws_pane, right=s3_pane)  # type: ignore[method-assign]

    async def fake_local(pane: object) -> None:
        calls.append(pane.current_connection_key)  # type: ignore[attr-defined]

    app._rebind_pane_to_local = fake_local  # type: ignore[method-assign]

    await app._reload_panes_for(("shared",), deleted=True)

    assert calls == [("s3-compatible", "shared")]


@pytest.mark.asyncio
async def test_initial_service_mount_awaits_content_host_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aws_tui import app as app_module

    events: list[str] = []

    class FakeHost:
        pass

    async def replace(_host: object, widget: object) -> None:
        events.append(f"replace:{widget!r}")

    class FakeLogSink:
        def error(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("mount helper should not log an error")

    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        root_vm=SimpleNamespace(
            content_host=SimpleNamespace(current=object(), current_id="s3"),
            chrome=SimpleNamespace(toast_stack=object()),
        ),
        hub=object(),
        focus_coordinator=object(),
        registry=SimpleNamespace(
            get=lambda _service_id: SimpleNamespace(supports=lambda _connection: True)
        ),
        connection_resolver=SimpleNamespace(list=lambda: ()),
        unreachable_connections=set(),
        log_sink=FakeLogSink(),
    )

    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: FakeHost())
    monkeypatch.setattr(app, "_replace_content_widget", replace)
    monkeypatch.setattr(app_module, "DualPane", lambda *_args, **_kwargs: "dual-pane")

    await app._mount_initial_service_view()

    assert events == ["replace:'dual-pane'"]


@pytest.mark.asyncio
async def test_no_connection_placeholder_mount_awaits_content_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aws_tui import app as app_module

    mounted: list[object] = []
    config_path = tmp_path / "platform-config" / "config.toml"

    class FakeHost:
        pass

    async def replace(_host: object, widget: object) -> None:
        mounted.append(widget)

    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(config_store=SimpleNamespace(path=config_path))
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: FakeHost())
    monkeypatch.setattr(app, "_replace_content_widget", replace)

    await app._mount_no_connection_placeholder()

    assert len(mounted) == 1
    assert str(config_path) in str(getattr(mounted[0], "content", ""))


def test_app_uses_public_service_page_operations() -> None:
    app_source = (Path(__file__).parents[2] / "src" / "aws_tui" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "page._project_focus_slot" not in app_source
    assert "emr_page._project_focus_slot" not in app_source
    assert "emr_page._picker" not in app_source
