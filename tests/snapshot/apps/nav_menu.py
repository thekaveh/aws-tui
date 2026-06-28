"""Test app for the NavMenu snapshot — single fixed-width mode.

Post-PR-#94 the rail has ONE mode (no collapse / expand), no
hamburger, and shows TEXT labels ("S3", "Settings"). Snapshot is
exercised against all 10 themes; content-presence guards in
``tests/snapshot/test_nav_menu.py`` lock the labels + ribbon
indicator in.
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
    The snapshot only needs the descriptor for ``NavMenuVM`` to
    render the row; ``build_vm`` / ``supports`` are stubs.
    Note: ``icon`` is intentionally empty post-PR-#94 — the rail
    renders text labels only.
    """

    descriptor = ServiceDescriptor(id="s3", label="S3", icon="")

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
    # Seed an active connection so the S3 row passes ``supports()``
    # and lands in ``#menu-services``. Without this the rail would
    # render only Settings and the docking layout wouldn't be
    # visible.
    vm.update_connection(
        Connection(
            name="snapshot",
            kind="s3-compatible",
            region="us-east-1",
            source="snapshot",
        )
    )
    # Drive ``selected_id`` to ``"s3"`` so ``_rebuild_options``
    # paints the ``▌`` ribbon on that row — pins the indicator code
    # path.
    vm.switch_service_command.execute("s3")
    return vm


class _UnfocusedMixin:
    """When NavMenu is the only widget, Textual auto-focuses its
    first OptionList, which trips the ``:focus-within`` rule and
    paints the rail's accent border. These layout snapshots want
    the unfocused state; drop Textual focus in ``on_ready`` so the
    focus pass has already run before we clear it."""

    def on_ready(self) -> None:  # type: ignore[no-untyped-def]
        self.set_focus(None)  # type: ignore[attr-defined]


class NavMenuApp(_UnfocusedMixin, App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._vm = _build_vm()
        self._tmp = Path(tempfile.mkdtemp(prefix="navmenu-"))

    def compose(self) -> ComposeResult:
        yield NavMenu(vm=self._vm, hub=MessageHub())


__all__ = ["NavMenuApp"]
