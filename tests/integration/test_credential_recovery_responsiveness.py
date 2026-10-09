"""Keyboard credential retry must remain cancellable through actual quit."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from tests.integration.conftest import AppContextBuilder
from tests.integration.test_credential_recovery import _await_local_fallback
from tests.integration.test_s3_action_responsiveness import (
    _GatedListingFS,
    _isolate_runtime_paths,
    _QuitObservedApp,
)


async def test_keyboard_credential_retry_does_not_block_quit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_context_factory: AppContextBuilder,
) -> None:
    _isolate_runtime_paths(tmp_path, monkeypatch)
    provider = _GatedListingFS()
    ctx = app_context_factory(fs=provider)
    token_state = TokenState.MISSING
    monkeypatch.setattr(
        ctx.aws_session, "probe_token", lambda _: TokenProbeResult(state=token_state)
    )
    app = _QuitObservedApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _await_local_fallback(app)
        token_state = TokenState.CONNECTED
        provider.block_listing = True
        retry_press = asyncio.create_task(pilot.press("a"))
        quit_press: asyncio.Task[None] | None = None
        handled_before_release = False
        try:
            await asyncio.wait_for(provider.list_started.wait(), timeout=2.0)
            quit_press = asyncio.create_task(pilot.press("q"))
            try:
                await asyncio.wait_for(app.quit_requested.wait(), timeout=2.0)
                handled_before_release = True
                await asyncio.wait_for(provider.list_cancelled.wait(), timeout=2.0)
            except TimeoutError:
                pass
        finally:
            provider.release_listing.set()
            presses = [retry_press, *([quit_press] if quit_press is not None else [])]
            await asyncio.wait_for(asyncio.gather(*presses, return_exceptions=True), timeout=10.0)
        assert handled_before_release, "credential retry occupied the App input pump"
        assert provider.list_cancelled.is_set()
        assert app._auth_recovery_task is None
