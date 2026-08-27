from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from aws_tui import app as app_module
from aws_tui.app import AwsTuiApp
from aws_tui.infra.connection_resolver import Connection


def _connection(name: str) -> Connection:
    return Connection(
        name=name,
        kind="aws",
        region="us-east-1",
        source="config",
        profile=name,
    )


@pytest.mark.asyncio
async def test_initial_mount_bounds_the_entire_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _connection("initial")
    others = [_connection("second"), _connection("third")]
    attempts: list[tuple[str, float]] = []
    mounted: list[str] = []

    class _Log:
        def info(self, event: str, **fields: object) -> None:
            del event, fields

        def error(self, event: str, **fields: object) -> None:
            del event, fields

    app = object.__new__(AwsTuiApp)
    object.__setattr__(
        app,
        "_app_ctx",
        SimpleNamespace(
            connection_resolver=SimpleNamespace(list=lambda: others),
            log_sink=_Log(),
        ),
    )
    object.__setattr__(app, "_boot_in_flight", True)

    async def try_connection(
        self: AwsTuiApp,
        connection: Connection,
        *,
        timeout: float,
    ) -> str:
        del self
        attempts.append((connection.name, timeout))
        await asyncio.sleep(timeout)
        return "timeout"

    async def no_toast(
        self: AwsTuiApp,
        connection: Connection,
        *,
        index: int,
        total: int,
    ) -> bool:
        del self, connection, index, total
        return False

    async def mount_local(
        self: AwsTuiApp,
        *,
        initial_conn: Connection,
        reason: str,
    ) -> bool:
        del self, initial_conn
        mounted.append(reason)
        return True

    monkeypatch.setattr(app_module, "_BOOT_CHAIN_BUDGET_SECONDS", 0.01)
    monkeypatch.setattr(AwsTuiApp, "_try_connection", try_connection)
    monkeypatch.setattr(AwsTuiApp, "_raise_attempt_toast_after_grace", no_toast)
    monkeypatch.setattr(AwsTuiApp, "_mount_local_only_dual_pane", mount_local)
    monkeypatch.setattr(AwsTuiApp, "_mark_connection_unreachable", lambda *args: None)
    monkeypatch.setattr(AwsTuiApp, "_raise_failure_toast", lambda *args: None)
    monkeypatch.setattr(AwsTuiApp, "_raise_local_fallback_toast", lambda *args: None)

    await app._initial_mount_worker(initial_conn=initial)

    assert [name for name, _ in attempts] == ["initial"]
    assert attempts[0][1] <= 0.01
    assert mounted == ["chain-exhausted"]
    assert app._boot_in_flight is False
