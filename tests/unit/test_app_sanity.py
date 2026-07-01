"""Package-level sanity smoke tests — the package imports, version is
set, and the app class is exposed. The bare-minimum CI must keep
green; per-layer behavioral coverage lives in the tier suites under
unit/, integration/, snapshot/, and e2e/.
"""

from __future__ import annotations

import json
import re
import runpy
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from aws_tui import __version__
from aws_tui.infra.log_sink import LogSink


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


def test_bound_action_records_action_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from aws_tui import app as app_module

    app = object.__new__(app_module.AwsTuiApp)
    app._action_ring = deque(maxlen=100)  # type: ignore[attr-defined]
    app._last_action_id = None  # type: ignore[attr-defined]
    monkeypatch.setattr(app, "_cycle_focus", lambda *, reverse: None)

    app.action_switch_focus()

    assert app.last_action_id == "pane.switch_focus"
    assert str(app._action_ring[-1]).endswith(" pane.switch_focus")  # type: ignore[attr-defined]


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
        registry=SimpleNamespace(get=lambda _service_id: SimpleNamespace(_local_root=tmp_path)),
    )

    await app._rebind_pane_to_local(FakePane())

    [provider] = providers
    assert isinstance(provider, LocalFS)
    assert provider._root == tmp_path.resolve()  # type: ignore[attr-defined]


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
        async def remove_children(self) -> None:
            events.append("remove")

        async def mount(self, widget: object) -> None:
            events.append(f"mount:{widget!r}")

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
        log_sink=FakeLogSink(),
    )

    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: FakeHost())
    monkeypatch.setattr(app_module, "DualPane", lambda *_args, **_kwargs: "dual-pane")

    await app._mount_initial_service_view()

    assert events == ["remove", "mount:'dual-pane'"]


@pytest.mark.asyncio
async def test_no_connection_placeholder_mount_awaits_content_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aws_tui import app as app_module

    mounted: list[object] = []
    config_path = tmp_path / "platform-config" / "config.toml"

    class FakeHost:
        async def remove_children(self) -> None:
            mounted.append("remove")

        async def mount(self, widget: object) -> None:
            mounted.append(widget)

    app = object.__new__(app_module.AwsTuiApp)
    app._app_ctx = SimpleNamespace(config_store=SimpleNamespace(path=config_path))
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: FakeHost())

    await app._mount_no_connection_placeholder()

    assert len(mounted) == 2
    assert mounted[0] == "remove"
    assert str(config_path) in str(getattr(mounted[1], "content", ""))
