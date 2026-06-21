"""Test apps for NavMenu snapshots — expanded + collapsed."""

from __future__ import annotations

import tempfile
from pathlib import Path

from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


def _build_vm() -> NavMenuVM:
    hub: MessageHub[Message] = MessageHub()
    registry = ServiceRegistry()
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


class NavMenuExpandedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm = _build_vm()
        self._tmp = Path(tempfile.mkdtemp(prefix="navmenu-exp-"))

    def compose(self) -> ComposeResult:
        nav = NavMenu(vm=self._vm, hub=MessageHub())
        nav.add_class("-expanded")
        nav.toggle_collapsed()  # flip from default-collapsed to expanded (full labels)
        yield nav


class NavMenuCollapsedApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm = _build_vm()
        self._tmp = Path(tempfile.mkdtemp(prefix="navmenu-col-"))

    def compose(self) -> ComposeResult:
        nav = NavMenu(vm=self._vm, hub=MessageHub())
        nav.add_class("-expanded")  # visible but in icon-only (collapsed) mode
        yield nav


__all__ = ["NavMenuCollapsedApp", "NavMenuExpandedApp"]
