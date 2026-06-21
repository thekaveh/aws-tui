"""In-process integration tests for the nav-routed Settings flow."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.config_store import ConfigStore

_MINIO_LOCAL_TOML = (
    "[connections.minio-local]\n"
    'kind = "s3-compatible"\n'
    'endpoint_url = "http://127.0.0.1:1"\n'  # unreachable on purpose
    'region = "us-east-1"\n'
    'access_key_id = "AKIATEST"\n'
    'secret_access_key = "SECRETTEST"\n'
    "force_path_style = true\n"
    "verify_tls = false\n"
)


def _prep(tmp_path: Path, toml_text: str = "") -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


def _dispose(ctx: object) -> None:
    """Standard teardown — mirrors the pattern in build_app_context order."""
    for attr in [
        "s3_connections_vm",
        "transfers_vm",
        "confirm_vm",
        "quick_look_vm",
        "command_palette_vm",
    ]:
        v = getattr(ctx, attr, None)
        if v is not None and hasattr(v, "dispose"):
            with contextlib.suppress(Exception):
                v.dispose()
    root = getattr(ctx, "root_vm", None)
    if root is not None and hasattr(root, "dispose"):
        with contextlib.suppress(Exception):
            root.dispose()


@pytest.mark.asyncio
async def test_toggle_settings_s3_settings_does_not_crash(tmp_path: Path) -> None:
    """Regression: clicking Settings → S3 → Settings used to crash with
    ``StatusTransitionError('Cannot construct from state Disposed.')``.

    Root cause: ``ctx.settings_vm`` was a singleton; ``ContentHostVM``
    calls ``vm.dispose()`` on swap-out and ``vm.construct()`` on
    swap-in, so the second Settings click tried to re-construct an
    already-Disposed VM. Fix: build a fresh ``SettingsVM`` per mount.
    """
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    (config_dir / "config.toml").write_text(
        _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            menu = ctx.root_vm.services_menu
            # 1st: settings
            menu.switch_service_command.execute("settings")
            await pilot.pause()
            # 2nd: back to s3
            menu.switch_service_command.execute("s3")
            await pilot.pause()
            # 3rd: settings again — this was the crash path.
            menu.switch_service_command.execute("settings")
            await pilot.pause()

            from aws_tui.vm.settings.settings_vm import SettingsVM

            assert isinstance(ctx.root_vm.content_host.current, SettingsVM), (
                "Settings VM should be re-mounted; if the singleton was reused "
                "it would have been Disposed after the s3 swap and the second "
                "Settings switch would have crashed in ContentHostVM.set_content "
                "while calling vm.construct()."
            )
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_comma_selects_settings_and_swaps_main_area(tmp_path: Path) -> None:
    """Press comma → SettingsView becomes the ContentHost's current content."""
    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()
            # NavMenuVM should now have settings selected.
            # ctx.root_vm.services_menu is the canonical accessor (NavMenuVM).
            assert ctx.root_vm.services_menu.selected_id == "settings"
            # ContentHost's current VM should be the SettingsVM.
            from aws_tui.vm.settings.settings_vm import SettingsVM

            assert isinstance(ctx.root_vm.content_host.current, SettingsVM)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_add_inline_form_persists_to_toml(tmp_path: Path) -> None:
    """Open Settings → expand inline form → fill + Save → TOML round-trip."""
    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()

            # The inline form widget should be mounted (hidden) within
            # the SettingsView's Connections panel. Open it.
            from aws_tui.ui.widgets.settings.connection_form import (
                ConnectionFormInline,
                ConnectionFormSubmitted,
            )
            from aws_tui.vm.chrome.first_run_vm import S3CompatForm

            form = pilot.app.query_one(ConnectionFormInline)
            form.open_for_add()
            await pilot.pause()

            # Fill programmatically (pilot.press char-by-char is flaky
            # under the textual test harness for Input widgets).
            from textual.widgets import Input

            pilot.app.query_one("#form-name", Input).value = "minio-test"
            pilot.app.query_one("#form-endpoint_url", Input).value = "http://localhost:9000"
            pilot.app.query_one("#form-region", Input).value = "us-east-1"
            pilot.app.query_one("#form-access_key_id", Input).value = "AKIATEST"
            pilot.app.query_one("#form-secret_access_key", Input).value = "SECRETTEST"
            await pilot.pause()

            # Post the submission event the form would emit on Save click.
            form_obj = S3CompatForm(
                name="minio-test",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="AKIATEST",
                secret_access_key="SECRETTEST",
                force_path_style=True,
                verify_tls=True,
            )
            form.post_message(
                ConnectionFormSubmitted(form=form_obj, mode="add", original_name=None)
            )
            await pilot.pause()
    finally:
        _dispose(ctx)

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-test" in cfg.connections
    entry = cfg.connections["minio-test"]
    assert entry.endpoint_url == "http://localhost:9000"


@pytest.mark.asyncio
async def test_delete_via_confirm_removes_from_toml(tmp_path: Path) -> None:
    """Seed a connection → open Settings → click delete chip → confirm → TOML removed."""
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("comma")
            await pilot.pause()
            await pilot.click("#delete-minio-local")
            await pilot.pause()
            # ConfirmModal opens; danger defaults focus to Cancel — press
            # Right then Enter to confirm.
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
    finally:
        _dispose(ctx)

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-local" not in cfg.connections


@pytest.mark.asyncio
async def test_dual_pane_mounts_at_startup_without_blank_screen(tmp_path: Path) -> None:
    """Regression: startup with an S3 connection MUST render the DualPane in
    the content host. Without the ``_boot_in_flight`` guard on
    ``_on_nav_selection_changed``, the seed selected_id change that
    ``switch_service("s3")`` fires during ``on_mount`` would spawn a
    ``_mount_service_view`` worker that races against on_mount's own
    ``_mount_initial_service_view``. Both call ``host.remove_children()`` +
    ``host.mount()`` on the same container; whichever runs second silently
    clobbers the other and the user sees a blank screen at startup.

    This test asserts the DualPane is actually mounted (not just that the
    VM is set), which is the user-visible outcome.
    """
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    # Mark the seeded connection as the default so on_mount picks it up.
    (config_dir / "config.toml").write_text(
        _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # Boot guard must release after on_mount completes.
            assert app._boot_in_flight is False
            # DualPane must be mounted in #content-host.
            from textual.containers import Container

            from aws_tui.ui.widgets.dual_pane import DualPane

            host = pilot.app.query_one("#content-host", Container)
            dual_panes = host.query(DualPane)
            assert len(dual_panes) == 1, (
                f"expected exactly one DualPane in #content-host but got "
                f"{len(dual_panes)} — the seed selected_id change may be "
                f"racing _mount_initial_service_view again"
            )
            # Content host MUST have non-zero rendered width.  Without
            # the AwsTuiApp.CSS rule giving ``#content-host`` an
            # explicit ``width: 1fr``, the Horizontal layout doesn't
            # allocate the remaining space (NavMenu starts collapsed
            # at width 0), so the content host renders at zero width
            # and the DualPane mounted inside is invisible — the
            # user-reported "blank screen at launch" symptom.
            assert host.region.width > 0, (
                "#content-host rendered at zero width — DualPane is "
                "mounted but invisible. AwsTuiApp.CSS must give "
                "#content-host an explicit width:1fr so it takes the "
                "space the collapsed (display:none) NavMenu leaves."
            )
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_expired_sso_does_not_call_switch_service_at_startup(tmp_path: Path) -> None:
    """Regression: when probe_token returns EXPIRED for the resolved AWS
    connection, on_mount MUST NOT call switch_service("s3") — because
    building the DualPane drives S3FS.list which asks boto3 to refresh
    the SSO access token over the network, and an expired refresh
    token / unreachable SSO OIDC endpoint hangs the whole startup.

    The fix is to detect EXPIRED/MISSING from probe_token (offline,
    cheap) BEFORE switch_service runs, and mount an auth-required
    placeholder instead.
    """
    import contextlib as _contextlib

    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.infra.connection_resolver import Connection

    config_dir = _prep(tmp_path)  # empty config — we'll seed via monkey-patch
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")

    # Force a single AWS-kind connection to be the resolved initial,
    # and force probe_token to return EXPIRED for it.
    fake_conn = Connection(
        name="dev-sso",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev-sso",
    )
    ctx.connection_resolver.list = lambda: [fake_conn]  # type: ignore[assignment,method-assign]
    ctx.aws_session.probe_token = lambda _c: TokenProbeResult(  # type: ignore[assignment,method-assign]
        state=TokenState.EXPIRED
    )

    # Track whether switch_service was called — that's the hang path.
    called: list[str] = []
    original_switch = ctx.root_vm.switch_service

    async def _watching_switch(service_id: str) -> None:
        called.append(service_id)
        await original_switch(service_id)

    ctx.root_vm.switch_service = _watching_switch  # type: ignore[assignment,method-assign]

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert called == [], (
                f"switch_service must NOT be called when probe_token returns "
                f"EXPIRED — the build path drives S3FS.list → boto3 SSO token "
                f"refresh → network hang. Got call(s): {called}"
            )
            # Placeholder content must be visible to the user.
            host = pilot.app.query_one("#content-host")
            placeholder = host.query("#content-placeholder")
            assert len(placeholder) == 1, (
                f"expected #content-placeholder mounted in content-host but got {len(placeholder)}"
            )
    finally:
        with _contextlib.suppress(Exception):
            _dispose(ctx)
