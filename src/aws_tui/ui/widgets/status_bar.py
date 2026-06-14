"""StatusBar widget — top-row status strip bound to :class:`StatusBarVM`.

Renders four sections separated by a dot:

    aws.tui · conn <name> · region <r> · sso ok · transfers idle

The widget subscribes to ``PropertyChangedMessage`` events whose sender is
the bound VM and refreshes its rendered text accordingly. No reactive
attributes are declared on the widget itself — Textual's diff-and-redraw
on ``refresh()`` is enough at this fidelity.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.status_bar_vm import StatusBarVM


class StatusBar(HubSubscriberMixin, Widget):
    """Top-row status strip widget."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        layout: horizontal;
    }
    """

    PROP_NAMES: ClassVar[frozenset[str]] = frozenset(
        {"connection_label", "region", "auth_indicator", "transfers_summary"}
    )

    def __init__(
        self,
        vm: StatusBarVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: StatusBarVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> StatusBarVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("aws.tui", classes="status-name")
        yield Static(self._connection_text(), classes="status-conn")
        yield Static(self._region_text(), classes="status-region")
        yield Static(self._vm.auth_indicator, classes=self._auth_classes())
        yield Static(self._vm.transfers_summary, classes="status-transfers")

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name in self.PROP_NAMES:
            self._refresh_section(property_name)

    def _refresh_section(self, property_name: str) -> None:
        if property_name == "connection_label":
            self._set_text("status-conn", self._connection_text())
        elif property_name == "region":
            self._set_text("status-region", self._region_text())
        elif property_name == "auth_indicator":
            target = self.query_one(".status-auth-ok, .status-auth-warn, .status-auth-err", Static)
            target.update(self._vm.auth_indicator)
            target.set_classes(self._auth_classes())
        elif property_name == "transfers_summary":
            self._set_text("status-transfers", self._vm.transfers_summary)

    def _set_text(self, css_class: str, value: str) -> None:
        try:
            target = self.query_one(f".{css_class}", Static)
        except Exception:
            return
        target.update(value)

    def _connection_text(self) -> str:
        return f"· {self._vm.connection_label} "

    def _region_text(self) -> str:
        region = self._vm.region
        return f"· {region} " if region else ""

    def _auth_classes(self) -> str:
        auth = self._vm.auth_indicator
        if "ok" in auth:
            return "status-auth-ok"
        if "needed" in auth or "expired" in auth:
            return "status-auth-warn"
        if "no session" in auth:
            return "status-auth-err"
        return "status-auth-ok"


__all__ = ["StatusBar"]
