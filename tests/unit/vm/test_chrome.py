"""Tests for the ChromeVM."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.vm.chrome.chrome_vm import ChromeVM
from aws_tui.vm.messages import ConnectionChangedMessage


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _conn() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="kaveh-dev",
    )


def _build() -> tuple[ChromeVM, MessageHub[Message]]:
    hub = _hub()
    chrome = ChromeVM(hub=hub, dispatcher=NULL_DISPATCHER, keymap=KeymapStore())
    chrome.construct()
    return chrome, hub


def test_chrome_constructs_three_children() -> None:
    chrome, _hub = _build()
    assert chrome.hint_legend is not None
    assert chrome.status_bar is not None
    assert chrome.toast_stack is not None
    chrome.dispose()


def test_chrome_status_bar_reacts_to_connection_changed() -> None:
    chrome, hub = _build()
    hub.send(ConnectionChangedMessage(connection=_conn(), auth_state=TokenState.CONNECTED))
    assert chrome.status_bar.connection_label == "kaveh-dev (aws)"
    chrome.dispose()


def test_chrome_dispose_cascades() -> None:
    chrome, _hub = _build()
    chrome.dispose()
    assert chrome.status == ConstructionStatus.DISPOSED


def test_chrome_hint_legend_starts_with_global_command_palette() -> None:
    # Post-PR-81: app-level globals (command_palette, help, etc.)
    # moved from ``.actions`` (LEFT, service) to ``.global_actions``
    # (RIGHT, always-visible) so the bottom Commands pane can show
    # service-specific chips on the left and the global app-chrome
    # chips on the right.
    chrome, _hub = _build()
    global_ids = {a.action_id for a in chrome.hint_legend.global_actions}
    assert "app.command_palette" in global_ids
    chrome.dispose()
