"""$AWS_PROFILE env-var resolution — pass-5 fix.

If $AWS_PROFILE is exported, ``AwsTuiApp._resolve_initial_connection``
must prefer that profile over the first auto-discovered profile. This is
the SSO-recovery path: many macOS users have a no-creds ``[default]``
profile and the real profile in $AWS_PROFILE.
"""

from __future__ import annotations

import tempfile
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
    )
    return AwsTuiApp(ctx)


def test_resolve_picks_aws_profile_env_var_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-prof-"))
    app = _make_app_with_two_profiles(tmp, default_profile="default")
    monkeypatch.setenv("AWS_PROFILE", "sso-dev")

    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    assert conn.profile == "sso-dev", (
        f"AWS_PROFILE should win over first auto, got {conn.profile!r}"
    )


def test_resolve_falls_back_to_first_auto_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-prof-"))
    app = _make_app_with_two_profiles(tmp, default_profile="default")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    # First auto: the parser lists [default] first.
    assert conn.profile == "default"


def test_resolve_ignores_empty_aws_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/whitespace ``$AWS_PROFILE`` should not poison resolution
    (a user exporting it to '' should be treated as not-set)."""
    tmp = Path(tempfile.mkdtemp(prefix="aws-tui-prof-"))
    app = _make_app_with_two_profiles(tmp, default_profile="default")
    monkeypatch.setenv("AWS_PROFILE", "  ")
    conn = app._resolve_initial_connection()  # type: ignore[attr-defined]
    assert conn is not None
    assert conn.profile == "default"
