"""Test apps for NavMenu snapshots — expanded + collapsed.

These snapshots must visibly demonstrate the docking behavior the user
asked for: service items at the top, Settings pinned to the bottom.
To do that, the test apps register a fake ``S3`` service AND seed an
active connection so :class:`NavMenuVM` includes the service item in
``#menu-services`` alongside the always-present Settings entry in
``#menu-pinned``. Without the fake service the rail shows only
Settings and the dock-bottom layout is indistinguishable from a
single-item list.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from textual.app import App, ComposeResult
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.nav_menu import NavMenu
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


class _FakeS3Service:
    """Bare-minimum :class:`Service` impl for snapshot scaffolding.

    The snapshot only needs the descriptor for ``NavMenuVM`` to render
    the row; ``build_vm`` / ``supports`` are stubs.
    """

    descriptor = ServiceDescriptor(id="s3", label="S3", icon="🪣")

    def supports(self, _connection: Connection) -> bool:
        return True

    def build_vm(self, _connection: Connection) -> object:  # pragma: no cover
        raise NotImplementedError


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


def _build_vm() -> NavMenuVM:
    hub: MessageHub[Message] = MessageHub()
    registry = ServiceRegistry()
    registry.register(_FakeS3Service())  # type: ignore[arg-type]
    vm = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    # Seed an active connection so the S3 row passes the supports()
    # filter and lands in #menu-services. Without this the rail would
    # render only Settings (pinned) and the docking wouldn't be visible.
    vm.update_connection(
        Connection(
            name="snapshot",
            kind="s3-compatible",
            region="us-east-1",
            source="snapshot",
        )
    )
    # Drive ``selected_id`` to ``"s3"`` — matches what the real app's
    # ``on_mount`` does via ``RootVM.switch_service`` and what
    # ``_rebuild_options`` reads to decide which row gets the ``▌``
    # ribbon prefix. The fixture used to skip this step, so the
    # ribbon code path went untested and the snapshot rendered with
    # spaces in both rows instead of the active-service indicator.
    vm.switch_service_command.execute("s3")
    return vm


class _UnfocusedMixin:
    """When NavMenu is the only widget, Textual auto-focuses its first
    OptionList, which trips the ``:focus-within`` rule and paints the
    rail's accent border. These layout snapshots want the unfocused
    state; drop Textual focus in ``on_ready`` so the focus pass has
    already run before we clear it."""

    def on_ready(self) -> None:  # type: ignore[no-untyped-def]
        self.set_focus(None)  # type: ignore[attr-defined]


class NavMenuExpandedApp(_UnfocusedMixin, App[None]):
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


class NavMenuCollapsedApp(_UnfocusedMixin, App[None]):
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
