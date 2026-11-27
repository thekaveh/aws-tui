from __future__ import annotations

from types import SimpleNamespace

import pytest

from aws_tui import app as app_module
from aws_tui.infra.aws_session import TokenState


@pytest.mark.asyncio
async def test_a_retry_with_no_bound_pane_says_so_instead_of_staying_silent(monkeypatch) -> None:
    """The retry returned None when no pane took it, and the handler returned with no message."""
    connection = SimpleNamespace(name="dev", kind="sso", region="eu-west-1")
    toasts: list[str] = []

    async def no_pane(**_kwargs):
        return None

    monkeypatch.setattr(app_module, "connection_binding_identity", lambda _c: "same")
    monkeypatch.setattr(
        app_module.notifications, "advise", lambda _stack, **kw: toasts.append(kw["message"])
    )

    app = object.__new__(app_module.AwsTuiApp)
    app._source_revision = 0  # type: ignore[attr-defined]
    app._refresh_source_for_credential_recovery = no_pane  # type: ignore[attr-defined]
    app._app_ctx = SimpleNamespace(  # type: ignore[attr-defined]
        root_vm=SimpleNamespace(
            active_connection=connection,
            content_host=SimpleNamespace(current_id="athena", current=object()),
            chrome=SimpleNamespace(toast_stack=object()),
        ),
        connection_resolver=SimpleNamespace(resolve=lambda _name: connection),
        aws_session=SimpleNamespace(
            probe_token=lambda _c: SimpleNamespace(state=TokenState.CONNECTED)
        ),
        log_sink=SimpleNamespace(warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
    )
    app.service_navigation = SimpleNamespace(owner=None)  # type: ignore[attr-defined]

    await app._recover_active_source()

    assert toasts == ["No open pane uses this source; reopen it to retry."]
