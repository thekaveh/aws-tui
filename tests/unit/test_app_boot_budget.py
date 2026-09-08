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


@pytest.mark.asyncio
async def test_boot_chain_stops_when_the_time_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the budget break itself, not the timeout break below it.

    The test above returns ``"timeout"``, which hits
    ``if outcome == "timeout": break`` at the END of the loop body — so the
    chain stops for a reason that has nothing to do with the budget, and the
    ``remaining <= 0`` break at the TOP was never executed by any test. It could
    be deleted outright with the suite green. Here each attempt reports a
    non-terminal outcome and consumes the whole budget, so the SECOND iteration
    must be refused by the budget itself.
    """
    initial = _connection("initial")
    others = [_connection("second"), _connection("third")]
    attempts: list[str] = []
    mounted: list[str] = []
    logged: list[tuple[str, dict[str, object]]] = []

    class _Log:
        def info(self, event: str, **fields: object) -> None:
            logged.append((event, fields))

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
        del self, timeout
        attempts.append(connection.name)
        # Burn the whole budget, then report a NON-timeout failure so the
        # loop is allowed to continue to the next candidate.
        await asyncio.sleep(0.05)
        return "unreachable"

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

    # One attempt ran; the budget refused the rest even though the outcome
    # was not "timeout".
    assert attempts == ["initial"]
    exhausted = [fields for event, fields in logged if event == "app.boot_chain.budget_exhausted"]
    assert exhausted == [{"attempted": 1, "total": 3}]
    assert mounted == ["chain-exhausted"]
    assert app._boot_in_flight is False
