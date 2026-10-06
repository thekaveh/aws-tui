"""Session launch CLI over synthetic local sources; no clients or UI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aws_tui import app as app_module
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver


def forbidden(*args, **kwargs):
    pytest.fail("launch preflight must not compose, run UI, or build a client")


@pytest.fixture
def sources(tmp_path, monkeypatch):
    from aws_tui.infra import paths

    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text(
        "[defaults]\nconnection = 'other'\n"
        "[connections.selected]\nkind = 'aws'\nprofile = 'analytics'\nregion = 'us-east-1'\n"
        "[connections.other]\nkind = 'aws'\nprofile = 'other'\n"
        "[connections.analytics]\nkind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\ncredentials = 'env:TEST_S3'\n"
    )
    aws = tmp_path / "aws"
    aws.mkdir()
    (aws / "config").write_text(
        "[profile analytics]\nregion = eu-west-1\n[profile other]\nregion = us-west-2\n"
    )
    (aws / "credentials").write_text("")
    for key in tuple(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(aws / "credentials"))
    monkeypatch.setattr(paths, "config_home", lambda: config)
    monkeypatch.setattr("aws_tui.infra.config_store.config_home", lambda: config)
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    monkeypatch.setattr(app_module, "prefer_sigwinch_resize", forbidden)
    monkeypatch.setattr(app_module.AwsTuiApp, "run", forbidden)
    return config


@pytest.mark.parametrize(
    "flag", ["--connection", "--profile", "--region", "--service", "--location"]
)
def test_help_lists_launch_selectors(monkeypatch, capsys, flag):
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--help"])
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    monkeypatch.setattr(ConfigStore, "load", forbidden)
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 0
    assert flag in capsys.readouterr().out


def test_help_documents_values_and_precedence(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--help"])
    with pytest.raises(SystemExit):
        app_module.main()
    text = capsys.readouterr().out
    for value in (
        "[defaults].connection",
        "AWS_PROFILE",
        "s3://",
        "directory",
        "eu-west-1",
        "session",
    ):
        assert value in text


def test_connection_and_profile_are_mutually_exclusive(monkeypatch, capsys, sources):
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--connection", "named", "--profile", "profile"])
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("service", ["settings", "bogus", ""])
def test_unknown_service_is_parser_error(monkeypatch, capsys, sources, service):
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--service", service])
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--connection", "missing"],
        ["--profile", "missing"],
        ["--connection", "analytics", "--service", "athena"],
        ["--location", "s3:///prefix"],
        ["--location", "https://example.com/path"],
        ["--location", "arn:aws:s3:::bucket"],
        ["--location", "absent-dir"],
        ["--location", "config.toml"],
        ["--location", "s3://bucket", "--service", "glue"],
        ["--connection", "bad\nname"],
        ["--location", "s3://bucket/a\x00b"],
        ["--region", ""],
        ["--region", "eu_west_1"],
        ["--region", "eu-west-1\n"],
    ],
)
def test_semantic_error_is_one_safe_line_before_composition(monkeypatch, capsys, sources, args):
    monkeypatch.chdir(sources)
    monkeypatch.setattr(sys, "argv", ["aws-tui", *args])
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    error = capsys.readouterr().err
    assert len(error.splitlines()) == 1
    assert "Traceback" not in error
    assert "\x00" not in error


@pytest.mark.parametrize(
    "flag", ["--connection", "--profile", "--region", "--service", "--location"]
)
@pytest.mark.parametrize("mode", ["demo-flag", "demo-env", "doctor"])
def test_real_selectors_conflict_before_discovery(monkeypatch, capsys, flag, mode):
    monkeypatch.setattr(ConfigStore, "load", forbidden)
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    args = [flag, "s3" if flag == "--service" else "value"]
    if mode == "demo-flag":
        args.insert(0, "--demo")
    elif mode == "demo-env":
        monkeypatch.setenv("AWS_TUI_DEMO", "1")
    else:
        args.append("doctor")
    monkeypatch.setattr(sys, "argv", ["aws-tui", *args])
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_version_never_discovers_even_with_selector(monkeypatch, capsys):
    monkeypatch.setattr(ConfigStore, "load", forbidden)
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--version", "--connection", "unknown"])
    app_module.main()
    assert "aws-tui" in capsys.readouterr().out


def resolve(sources, **kwargs):
    from aws_tui.launch import LaunchRequest, resolve_launch

    store = ConfigStore(path=sources / "config.toml", read_only=True)
    return resolve_launch(
        LaunchRequest(**kwargs),
        config_store=store,
        resolver=ConnectionResolver(config_store=store, read_credentials=False),
    )


@pytest.mark.parametrize("service", ["s3", "athena", "glue", "emr-serverless"])
def test_registered_service_acceptance_and_real_registry_agreement(sources, service):
    from aws_tui.composition import build_app_context
    from aws_tui.launch import service_definitions

    launch = resolve(sources, connection="selected", service=service)
    ctx = build_app_context(config_dir=sources, cache_dir=sources / "cache", demo=True)
    try:
        definitions = service_definitions()
        assert {s.descriptor.id for s in definitions} == {
            s.descriptor.id for s in ctx.registry.all()
        }
        for definition in definitions:
            for connection in ConnectionResolver(
                config_store=ConfigStore(path=sources / "config.toml", read_only=True)
            ).list():
                assert definition.supports(connection) == ctx.registry.get(
                    definition.descriptor.id
                ).supports(connection)
        assert launch.service_id == service
        assert launch.connection.name == "selected"
    finally:
        ctx.close_unstarted()


def test_exact_profile_ignores_alias_and_nonaws_collision(sources):
    launch = resolve(sources, profile="analytics", region="ap-southeast-2")
    assert launch.underlying.name == "analytics"
    assert launch.underlying.kind == "aws"
    assert launch.connection.profile == "analytics"
    assert launch.connection.region == "ap-southeast-2"
    assert launch.underlying.region == "eu-west-1"


@pytest.fixture(params=["configured", "profile", "s3"])
def retained_session(request, sources, monkeypatch):
    from aws_tui.composition import _LaunchConnectionResolver

    source = sources / "config.toml"
    if request.param == "s3":
        source.write_text(
            source.read_text().replace(
                "kind = 'aws'\nprofile = 'analytics'\nregion = 'us-east-1'",
                "kind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\n"
                "region = 'us-east-1'\ncredentials = 'env:FIRST_'",
            )
        )
        for prefix in ("FIRST_", "SECOND_"):
            monkeypatch.setenv(prefix + "ACCESS_KEY_ID", prefix + "synthetic-access")
            monkeypatch.setenv(prefix + "SECRET_ACCESS_KEY", prefix + "synthetic-secret")
    selectors = (
        {"profile": "analytics"} if request.param == "profile" else {"connection": "selected"}
    )
    launch = resolve(sources, region="ap-southeast-2", **selectors)
    store = ConfigStore(path=sources / "config.toml", read_only=True)
    resolver = _LaunchConnectionResolver(config_store=store, keychain=None, launch=launch)
    if request.param == "profile":
        source = Path(os.environ["AWS_CONFIG_FILE"])

    def edit():
        source.write_text(
            source.read_text()
            .replace(launch.underlying.region, launch.connection.region)
            .replace("env:FIRST_", "env:SECOND_")
        )

    return store, resolver, launch, edit


@pytest.mark.parametrize("method", ["resolve", "resolve_selected"])
def test_retained_source_edit_to_override_region_is_refused(retained_session, method):
    from aws_tui.infra.connection_resolver import ConnectionNotFound

    _, resolver, launch, edit = retained_session
    edit()
    with pytest.raises(ConnectionNotFound):
        getattr(resolver, method)(launch.connection.name)


@pytest.mark.parametrize("initialized", [False, True])
async def test_retained_source_edit_invalidates_first_and_established_guards(
    retained_session, initialized
):
    from aws_tui.composition import make_source_check_factory

    store, resolver, launch, edit = retained_session
    check = make_source_check_factory(store, resolver)(launch.connection)
    if initialized:
        assert await check()
    edit()
    assert not await check()


def test_retained_session_metadata_and_other_sources_keep_ordinary_resolution(retained_session):
    store, resolver, launch, edit = retained_session
    ordinary = ConnectionResolver(config_store=store, read_credentials=False)
    other = ordinary.resolve_selected("other")
    assert resolver.resolve_selected("other") == other
    selected = next(item for item in resolver.list() if item.name == launch.connection.name)
    assert selected == launch.connection
    assert selected.access_key_id is None
    assert selected.secret_access_key is None
    if launch.request.profile is not None:
        # The configured same-name S3 source is hidden by the exact profile,
        # while the differently named AWS alias remains an ordinary source.
        assert selected.kind == "aws"
        assert selected.source == "auto-aws-profile"
        assert resolver.resolve_selected("selected") == ordinary.resolve_selected("selected")
    edit()
    assert resolver.resolve_selected("other") == other
    metadata = next(item for item in resolver.list() if item.name == launch.connection.name)
    assert metadata.region == launch.connection.region
    assert metadata.access_key_id is None
    assert metadata.secret_access_key is None
    assert next(item for item in resolver.list() if item.name == "other") == other


def test_explicit_connection_and_default_precedence(sources, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "analytics")
    assert resolve(sources, region="eu-west-1").connection.name == "other"
    assert resolve(sources, connection="selected").connection.name == "selected"
    (sources / "config.toml").write_text("")
    assert resolve(sources, region="eu-west-1").connection.profile == "analytics"


def test_s3_prefix_is_literal(sources):
    launch = resolve(sources, connection="selected", location="s3://bucket/a//b%2F?#雪/")
    # The provider adds the directory delimiter; all key components survive.
    assert launch.location.path.as_posix() == "/bucket/a//b%2F?#雪"
    assert launch.location.path.join("report.csv").as_posix() == "/bucket/a//b%2F?#雪/report.csv"
    assert launch.location.scheme == "s3"


@pytest.mark.parametrize("form", ["absolute", "relative", "home"])
def test_local_native_path_and_absent_config(sources, monkeypatch, form):
    directory = sources / "dir space 雪"
    directory.mkdir()
    (sources / "config.toml").unlink()
    Path(os.environ["AWS_CONFIG_FILE"]).write_text("")
    monkeypatch.chdir(sources)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sources))
    monkeypatch.setenv("HOME", str(sources))
    value = (
        str(directory)
        if form == "absolute"
        else ("./dir space 雪" if form == "relative" else "~/dir space 雪")
    )
    launch = resolve(sources, location=value)
    assert launch.connection is None
    assert launch.location.native_path == directory.resolve()
    assert not (sources / "config.toml").exists()


def test_default_resolution_continues_after_missing_default_and_uses_profile_alias(
    sources, monkeypatch
):
    config = sources / "config.toml"
    config.write_text(config.read_text().replace("connection = 'other'", "connection = 'missing'"))
    monkeypatch.setenv("AWS_PROFILE", "analytics")
    assert resolve(sources, region="eu-west-1").connection.name == "selected"


def test_selected_keychain_preflight_does_not_read_secrets(sources, monkeypatch):
    from aws_tui.infra.keychain import Keyring

    (sources / "config.toml").write_text(
        "[connections.secret]\nkind = 's3-compatible'\nendpoint_url = 'http://localhost:9000'\ncredentials = 'keychain:selected-keychain'\n"
    )
    monkeypatch.setattr(Keyring, "get", forbidden)
    launch = resolve(sources, connection="secret")
    assert launch.connection.name == "secret"
    assert launch.connection.access_key_id is None


@pytest.mark.parametrize(
    "mode", ["ordinary", "demo-flag", "demo-env", "selector", "runtime-error", "constructor-error"]
)
def test_main_dispatch_and_explicit_error_cleanup(sources, monkeypatch, capsys, mode):
    from types import SimpleNamespace

    calls = []
    closed = []
    context = SimpleNamespace(
        athena_drafts_shutdown_warning=None, close_unstarted=lambda: closed.append(True)
    )

    def build(**kwargs):
        calls.append(kwargs)
        return context

    class App:
        crash_report = None
        launch_error = (
            "explicit launch failed for the selected source or location"
            if mode == "runtime-error"
            else None
        )

        def __init__(self, **kwargs):
            assert kwargs == {"context": context}
            if mode == "constructor-error":
                raise ValueError("secret\ndetail")

        def run(self):
            calls.append("run")

    monkeypatch.setattr(app_module, "build_app_context", build)
    monkeypatch.setattr(app_module, "AwsTuiApp", App)
    monkeypatch.setattr(app_module, "prefer_sigwinch_resize", lambda: calls.append("resize"))
    args = (
        ["--connection", "selected"]
        if mode in {"selector", "runtime-error", "constructor-error"}
        else (["--demo"] if mode == "demo-flag" else [])
    )
    if mode == "demo-env":
        monkeypatch.setenv("AWS_TUI_DEMO", "1")
    monkeypatch.setattr(sys, "argv", ["aws-tui", *args])
    if mode in {"runtime-error", "constructor-error"}:
        with pytest.raises(SystemExit) as result:
            app_module.main()
        assert result.value.code == 1
        error = capsys.readouterr().err
        assert len(error.splitlines()) == 1
        assert "Traceback" not in error
        assert "secret" not in error
    else:
        app_module.main()
    if args and args[0] == "--connection":
        assert calls[0]["launch"].connection.name == "selected"
        assert calls[0]["demo"] is False
    else:
        assert calls[0] == {"demo": mode in {"demo-flag", "demo-env"}}
    if mode == "constructor-error":
        assert closed == [True]
        assert len(calls) == 1
    else:
        assert calls[1:] == ["resize", "run"]
        assert closed == []


@pytest.mark.parametrize("service", ["s3", "athena", "glue", "emr-serverless"])
def test_no_sources_reject_remote_service_early(sources, monkeypatch, capsys, service):
    (sources / "config.toml").unlink()
    Path(os.environ["AWS_CONFIG_FILE"]).write_text("")
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--service", service])
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.parametrize(
    ("native", "parts", "root"),
    [
        ("C:/Users/me/downloads", ("C:", "Users", "me", "downloads"), None),
        ("//server/share/downloads space/雪", ("downloads space", "雪"), "\\\\server\\share\\"),
    ],
)
def test_native_windows_path_representation_preserves_drive_or_share_root(native, parts, root):
    from pathlib import PureWindowsPath

    from aws_tui.launch import _local_path_ref

    path, native_root = _local_path_ref(PureWindowsPath(native))
    assert path.segments == parts
    assert native_root == root


def test_unreadable_profile_source_still_reports_one_cli_line(sources, monkeypatch, capsys):
    import logging

    logger = logging.getLogger("aws_tui.infra.connection_resolver")
    monkeypatch.setattr(logger, "handlers", [logging.StreamHandler(sys.stderr)])
    monkeypatch.setattr(logger, "propagate", False)
    Path(os.environ["AWS_CONFIG_FILE"]).write_text("[profile broken\n")
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--profile", "missing"])
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    assert len(capsys.readouterr().err.splitlines()) == 1
