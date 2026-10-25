"""Metadata-only settings for per-connection mutation protection."""

from dataclasses import dataclass, field, replace
from functools import partial

import anyio
from vmx import Message, MessageHub

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.vm.messages import ConnectionProtectionChangedMessage


@dataclass(frozen=True, slots=True)
class ConnectionProtectionRow:
    name: str
    kind: str
    region: str
    protected: bool
    _captured: Connection = field(repr=False)

    @classmethod
    def from_connection(cls, connection: Connection) -> "ConnectionProtectionRow":
        return cls(
            connection.name, connection.kind, connection.region, connection.protected, connection
        )


class ConnectionProtectionVM:
    def __init__(
        self, *, config: ConfigStore, resolver: ConnectionResolver, hub: MessageHub[Message]
    ) -> None:
        self._config = config
        self._resolver = resolver
        self._hub = hub

    @property
    def read_only(self) -> bool:
        return self._config.read_only

    async def rows(self) -> tuple[ConnectionProtectionRow, ...]:
        connections = await anyio.to_thread.run_sync(self._resolver.list_metadata)
        return tuple(ConnectionProtectionRow.from_connection(c) for c in connections)

    async def set_protected(self, captured: ConnectionProtectionRow, protected: bool) -> None:
        current = await anyio.to_thread.run_sync(partial(self._save, captured._captured, protected))
        self._hub.send(ConnectionProtectionChangedMessage(connection=current))

    def _save(self, captured: Connection, protected: bool) -> Connection:
        if self.read_only:
            raise ValueError("Connection protection is unavailable in demo mode")
        with self._config.transaction():
            current = next(
                (c for c in self._resolver.list_metadata() if c.name == captured.name), None
            )
            if current != captured:
                raise ValueError("Connection changed; refresh Settings and try again")
            entry = self._config.load().connections.get(captured.name)
            if entry is None:
                if current.kind != "aws":
                    raise ValueError("Connection changed; refresh Settings and try again")
                entry = ConnectionEntry(
                    name=current.name,
                    kind=current.kind,
                    profile=current.profile,
                )
                self._config.add_connection(replace(entry, protected=protected))
            else:
                self._config.update_connection(entry.name, replace(entry, protected=protected))
            return replace(current, protected=protected, source="config")
