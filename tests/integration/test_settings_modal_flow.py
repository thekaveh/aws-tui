"""In-process integration tests for the App Settings overlay's flows."""

from __future__ import annotations

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
    'credentials = "static"\n'  # required for ConnectionResolver to surface access_key_id
    'access_key_id = "AKIATEST"\n'
    'secret_access_key = "SECRETTEST"\n'
    "force_path_style = true\n"
    "verify_tls = false\n"
)


def _prep_config(tmp_path: Path, toml_text: str = "") -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


@pytest.mark.asyncio
async def test_add_flow_persists_to_toml(tmp_path: Path) -> None:
    """Empty config; open settings via comma; click +Add; fill the
    form; save; close; verify the TOML round-trip."""
    config_dir = _prep_config(tmp_path)  # empty
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            # Open settings.
            await pilot.press("comma")
            await pilot.pause()
            # Empty state → "#add-empty" button.
            await pilot.click("#add-empty")
            # The click triggers a @work worker that pushes S3CompatFormModal.
            # Wait for the modal screen to appear on the stack.
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()
            # Fill fields programmatically — more reliable than key simulation
            # inside run_test for multi-character text entry.
            # Query from pilot.app.screen (the current top-of-stack screen,
            # which is S3CompatFormModal) not from pilot.app which resolves
            # to the default/base screen.
            from textual.widgets import Input

            form_screen = pilot.app.screen
            name_input = form_screen.query_one("#form-name", Input)
            name_input.value = "minio-test"
            endpoint_input = form_screen.query_one("#form-endpoint_url", Input)
            endpoint_input.value = "http://localhost:9000"
            region_input = form_screen.query_one("#form-region", Input)
            region_input.value = "us-east-1"
            access_input = form_screen.query_one("#form-access_key_id", Input)
            access_input.value = "AKIATEST"
            secret_input = form_screen.query_one("#form-secret_access_key", Input)
            secret_input.value = "SECRETTEST"
            await pilot.pause()
            # Save — ModalButton uses a custom button_id attribute (not the
            # DOM id) so pilot.click("#form-save-btn") doesn't find it.
            # Call action_submit() directly on the current screen instead.
            from aws_tui.ui.widgets.first_run_modal import S3CompatFormModal

            assert isinstance(pilot.app.screen, S3CompatFormModal)
            pilot.app.screen.action_submit()
            await pilot.pause()
            # Dismiss settings.
            await pilot.press("escape")
            await pilot.pause()
    finally:
        # build_app_context returns disposable VMs — clean up explicitly
        # so the test isolation is total.
        ctx.settings_vm.dispose()
        ctx.s3_connections_vm.dispose()
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()

    # On-disk verification.
    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-test" in cfg.connections
    entry = cfg.connections["minio-test"]
    assert entry.endpoint_url == "http://localhost:9000"
    assert entry.access_key_id == "AKIATEST"
    assert entry.secret_access_key == "SECRETTEST"


@pytest.mark.asyncio
async def test_edit_with_locked_name_persists_endpoint_change(
    tmp_path: Path,
) -> None:
    """Seed minio-local; open settings; click edit; verify the name
    field is locked; change the endpoint; save; close; verify TOML."""
    config_dir = _prep_config(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.press("comma")
            await pilot.pause()
            await pilot.click("#edit-minio-local")
            # The click triggers a @work worker that pushes S3CompatFormModal.
            # Wait for the modal screen to appear on the stack.
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()
            # Name field must be locked in edit mode.
            # Query from pilot.app.screen (the current top-of-stack screen,
            # S3CompatFormModal) not from pilot.app which resolves to the
            # default/base screen.
            from textual.widgets import Input

            form_screen = pilot.app.screen
            name_input = form_screen.query_one("#form-name", Input)
            assert name_input.disabled is True
            # Change endpoint programmatically — set the value directly.
            # Mixing .value="" + pilot.press() is unreliable because
            # clearing does not refocus and keystrokes may land elsewhere.
            endpoint_input = form_screen.query_one("#form-endpoint_url", Input)
            endpoint_input.value = "http://127.0.0.1:2"
            await pilot.pause()
            # Save — ModalButton uses a custom button_id attribute (not the
            # DOM id) so pilot.click("#form-save-btn") doesn't find it.
            # Call action_submit() directly on the current screen instead.
            from aws_tui.ui.widgets.first_run_modal import S3CompatFormModal

            assert isinstance(pilot.app.screen, S3CompatFormModal)
            pilot.app.screen.action_submit()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
    finally:
        ctx.settings_vm.dispose()
        ctx.s3_connections_vm.dispose()
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert cfg.connections["minio-local"].endpoint_url == "http://127.0.0.1:2"


@pytest.mark.asyncio
async def test_delete_via_confirm_removes_from_toml(tmp_path: Path) -> None:
    """Seed minio-local; open settings; click delete; confirm; close;
    verify the entry is gone from TOML."""
    config_dir = _prep_config(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await pilot.press("comma")
            await pilot.pause()
            await pilot.click("#delete-minio-local")
            await pilot.pause()
            # ConfirmModal opens. For danger dialogs the default focus
            # is Cancel; press Right to move to Confirm, then Enter.
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
    finally:
        ctx.settings_vm.dispose()
        ctx.s3_connections_vm.dispose()
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-local" not in cfg.connections
