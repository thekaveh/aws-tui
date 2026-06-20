"""$AWS_PROFILE env-var resolution.

If $AWS_PROFILE is exported, ``AwsTuiApp._resolve_initial_connection``
must prefer that profile over the first auto-discovered profile. This is
the SSO-recovery path: many macOS users have a no-creds ``[default]``
profile and the real profile in $AWS_PROFILE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import ServiceRegistry
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _make_app_with_two_profiles(tmp: Path, default_profile: str) -> AwsTuiApp:
    """Set up an app context with two auto-discovered profiles named
    ``default`` and ``sso-dev``. No service registered — we only care
    about the resolver's pick."""
    aws_config = tmp / "aws-config"
    aws_config.write_text(
        "[default]\nregion = us-east-1\n\n[profile sso-dev]\nregion = us-east-1\n"
    )
    aws_creds = tmp / "aws-credentials"
    aws_creds.write_text("")

    from vmx import MessageHub, RxDispatcher

    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()

    log = LogSink(base_dir=tmp / "log")
    config_store = ConfigStore(path=tmp / "config.toml")
    keymap = KeymapStore()
    theme = ThemeStore()
    aws_session = AwsSession()
    resolver = ConnectionResolver(
        config_store=config_store,
        aws_config_path=aws_config,
        aws_credentials_path=aws_creds,
    )
    registry = ServiceRegistry()
    root = RootVM(
        registry=registry,
        keymap=keymap,
        theme=theme,
        log=log,
        dispatcher=dispatcher,
        hub=hub,
    )
    # No [defaults] in config.toml — fall through to AWS_PROFILE / first auto.
    config_store.path.write_text("")

    from aws_tui.domain.transfer_journal import TransferJournal

    s3_connections_vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=config_store,
        hub=hub,
        dispatcher=dispatcher,
    )
    settings_vm = SettingsVM(
        s3=s3_connections_vm,
        hub=hub,
        dispatcher=dispatcher,
    )

    ctx = AppContext(
        root_vm=root,
        registry=registry,
        config_store=config_store,
        log_sink=log,
        keymap_store=keymap,
        theme_store=theme,
        connection_resolver=resolver,
        aws_session=aws_session,
        transfers_vm=TransfersVM(hub=hub, dispatcher=dispatcher),
        confirm_vm=ConfirmationVM(hub=hub, dispatcher=dispatcher),
        quick_look_vm=QuickLookVM(hub=hub, dispatcher=dispatcher),
        command_palette_vm=CommandPaletteVM(hub=hub, dispatcher=dispatcher),
        transfer_journal=TransferJournal(base_dir=tmp / "transfers"),
        hub=hub,
        dispatcher=dispatcher,
        initial_theme="carbon",
        s3_connections_vm=s3_connections_vm,
        settings_vm=settings_vm,
    )
    return AwsTuiApp(ctx)


def test_resolve_picks_aws_profile_env_var_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _make_app_with_two_profiles(tmp_path, default_profile="default")
    monkeypatch.setenv("AWS_PROFILE", "sso-dev")

    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    assert conn.profile == "sso-dev", (
        f"AWS_PROFILE should win over first auto, got {conn.profile!r}"
    )


def test_resolve_falls_back_to_first_auto_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _make_app_with_two_profiles(tmp_path, default_profile="default")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    # First auto: the parser lists [default] first.
    assert conn.profile == "default"


def test_resolve_ignores_empty_aws_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty/whitespace ``$AWS_PROFILE`` should not poison resolution
    (a user exporting it to '' should be treated as not-set)."""
    app = _make_app_with_two_profiles(tmp_path, default_profile="default")
    monkeypatch.setenv("AWS_PROFILE", "  ")
    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    assert conn.profile == "default"


def test_resolve_prefers_config_defaults_over_aws_profile_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The top-priority startup-resolution rule (config.toml
    ``[defaults].connection`` > ``$AWS_PROFILE`` > first auto) must be
    covered by a positive test, not only the lower-priority branches.

    Seeds a ``[defaults].connection = "auto-prod"`` plus the matching
    ``[connections.auto-prod]`` block in ``config.toml`` and exports
    ``$AWS_PROFILE=sso-dev``. The resolver must pick ``auto-prod``,
    not ``sso-dev``.
    """
    app = _make_app_with_two_profiles(tmp_path, default_profile="default")
    monkeypatch.setenv("AWS_PROFILE", "sso-dev")

    (tmp_path / "config.toml").write_text(
        "[defaults]\n"
        'connection = "auto-prod"\n'
        "\n"
        "[connections.auto-prod]\n"
        'kind = "aws"\n'
        'profile = "default"\n'
        'region = "us-east-1"\n'
    )

    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    assert conn.name == "auto-prod", (
        f"config.toml [defaults].connection must outrank $AWS_PROFILE; got {conn.name!r}"
    )


def test_resolve_returns_none_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no [defaults].connection, no $AWS_PROFILE, and no
    auto-discovered AWS profile exist, the resolver returns ``None``
    so the no-connection placeholder mounts.
    """
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    aws_config = tmp_path / "aws-config"
    aws_config.write_text("")  # no profiles
    aws_creds = tmp_path / "aws-credentials"
    aws_creds.write_text("")

    from vmx import MessageHub, RxDispatcher

    from aws_tui.domain.transfer_journal import TransferJournal

    hub: MessageHub = MessageHub()
    dispatcher = RxDispatcher.immediate()
    log = LogSink(base_dir=tmp_path / "log")
    config_store = ConfigStore(path=tmp_path / "config.toml")
    config_store.path.write_text("")
    keymap = KeymapStore()
    theme = ThemeStore()
    aws_session = AwsSession()
    resolver = ConnectionResolver(
        config_store=config_store,
        aws_config_path=aws_config,
        aws_credentials_path=aws_creds,
    )
    registry = ServiceRegistry()
    root = RootVM(
        registry=registry,
        keymap=keymap,
        theme=theme,
        log=log,
        dispatcher=dispatcher,
        hub=hub,
    )
    s3_connections_vm = S3ConnectionsVM(
        resolver=resolver,
        config_store=config_store,
        hub=hub,
        dispatcher=dispatcher,
    )
    settings_vm = SettingsVM(
        s3=s3_connections_vm,
        hub=hub,
        dispatcher=dispatcher,
    )
    ctx = AppContext(
        root_vm=root,
        registry=registry,
        config_store=config_store,
        log_sink=log,
        keymap_store=keymap,
        theme_store=theme,
        connection_resolver=resolver,
        aws_session=aws_session,
        transfers_vm=TransfersVM(hub=hub, dispatcher=dispatcher),
        confirm_vm=ConfirmationVM(hub=hub, dispatcher=dispatcher),
        quick_look_vm=QuickLookVM(hub=hub, dispatcher=dispatcher),
        command_palette_vm=CommandPaletteVM(hub=hub, dispatcher=dispatcher),
        transfer_journal=TransferJournal(base_dir=tmp_path / "transfers"),
        hub=hub,
        dispatcher=dispatcher,
        initial_theme="carbon",
        s3_connections_vm=s3_connections_vm,
        settings_vm=settings_vm,
    )
    app = AwsTuiApp(ctx)
    assert app._resolve_initial_connection() is None  # type: ignore[attr-defined]
