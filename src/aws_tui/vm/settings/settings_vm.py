"""SettingsVM — parent VM for the settings shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase


class SettingsVM:
    """Parent VM for the settings shell.

    Owns the active section (one of :attr:`SECTIONS`) and a dirty-set
    of connection names that changed during the modal's lifetime.
    ``AwsTuiApp`` reads :attr:`dirty_connection_names` when the
    SettingsModal dismisses and reloads any pane bound to a dirty
    connection (see the reload-on-close logic in `app.py`).
    """

    SECTIONS: Final[tuple[str, ...]] = ("connections", "themes", "keymap")
    ENABLED: Final[frozenset[str]] = frozenset({"connections"})

    def __init__(
        self,
        *,
        s3: S3ConnectionsVM,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._s3: S3ConnectionsVM = s3
        self._active_section: str = "connections"
        self._dirty_connection_names: set[str] = set()
        self._sub: DisposableBase | None = None
        self._inner: ComponentVM = (
            ComponentVM.builder().name("settings").services(hub, dispatcher).build()
        )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def s3(self) -> S3ConnectionsVM:
        return self._s3

    @property
    def active_section(self) -> str:
        return self._active_section

    @property
    def dirty_connection_names(self) -> frozenset[str]:
        return frozenset(self._dirty_connection_names)

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        # Subscribe AFTER inner construct so any message that arrives
        # mid-construction doesn't fire on a half-built VM.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.dispose()

    # ── Actions ────────────────────────────────────────────────────────────

    def change_section(self, section_id: str) -> None:
        """Switch active section; no-op if the section is disabled."""
        if section_id not in self.ENABLED:
            return
        if section_id == self._active_section:
            return
        self._active_section = section_id
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "active_section"))

    def clear_dirty(self) -> None:
        """Reset the dirty-set. Called by ``AwsTuiApp`` after the
        post-close pane reload has finished."""
        self._dirty_connection_names.clear()

    # ── Hub subscriber ─────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        """Accumulate names from 'updated' and 'deleted' events only.

        'added' is excluded because a brand-new connection can't be
        bound to any pane yet — there's nothing to reload.
        """
        if not isinstance(msg, ConnectionListChangedMessage):
            return
        if msg.change == "added":
            return
        self._dirty_connection_names.update(msg.names)


__all__ = ["SettingsVM"]
