"""S3ConnectionsVM — CRUD over kind='s3-compatible' connections."""

from __future__ import annotations

from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.vm.messages import ConnectionListChangedMessage


class S3ConnectionsVM:
    """List + CRUD over the s3-compatible subset of TOML connections.

    The CRUD verbs (``add`` / ``update`` / ``remove``) validate, persist
    via :class:`ConfigStore`, then publish a
    :class:`ConnectionListChangedMessage` on the hub. Subscribers
    (``SettingsVM``, ``ServicesMenuVM``, ``AwsTuiApp``) react to the
    message; this VM never tells them directly.
    """

    def __init__(
        self,
        *,
        resolver: ConnectionResolver,
        config_store: ConfigStore,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._resolver: ConnectionResolver = resolver
        self._config_store: ConfigStore = config_store
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._inner: ComponentVM = (
            ComponentVM.builder().name("s3_connections").services(hub, dispatcher).build()
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Read ───────────────────────────────────────────────────────────────

    @property
    def connections(self) -> tuple[Connection, ...]:
        """All s3-compatible connections, in resolver order.

        Re-derived from the resolver each call — the resolver has no
        cache, so a recent CRUD is reflected immediately.
        """
        return tuple(c for c in self._resolver.list() if c.kind == "s3-compatible")

    @property
    def names(self) -> frozenset[str]:
        return frozenset(c.name for c in self.connections)

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, entry: ConnectionEntry) -> None:
        """Validate uniqueness, persist via ConfigStore, publish 'added'."""
        if entry.name in self.names:
            raise ValueError(f"connection {entry.name!r} already exists")
        self._config_store.add_connection(entry)
        self._hub.send(ConnectionListChangedMessage(names=(entry.name,), change="added"))

    def update(self, name: str, entry: ConnectionEntry) -> None:
        """Validate rename-disallowed, persist, publish 'updated'."""
        if entry.name != name:
            raise ValueError(
                f"connection cannot be renamed in place: old={name!r}, new={entry.name!r}"
            )
        self._config_store.update_connection(name, entry)
        self._hub.send(ConnectionListChangedMessage(names=(name,), change="updated"))

    def remove(self, name: str) -> None:
        """Persist removal, publish 'deleted'."""
        self._config_store.remove_connection(name)
        self._hub.send(ConnectionListChangedMessage(names=(name,), change="deleted"))


__all__ = ["S3ConnectionsVM"]
