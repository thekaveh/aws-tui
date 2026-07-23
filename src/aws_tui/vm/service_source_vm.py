from __future__ import annotations

from dataclasses import dataclass

from aws_tui.infra.connection_resolver import Connection


@dataclass(frozen=True, slots=True)
class ServiceSourceContext:
    connection_name: str
    profile: str | None
    region: str

    @classmethod
    def from_connection(cls, connection: Connection) -> ServiceSourceContext:
        return cls(
            connection_name=connection.name,
            profile=connection.profile,
            region=connection.region,
        )

    @property
    def connection_key(self) -> tuple[str, str]:
        return self.connection_name, self.region

    @property
    def label(self) -> str:
        parts = [self.connection_name]
        if self.profile and self.profile != self.connection_name:
            parts.append(self.profile)
        parts.append(self.region)
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class SelectionScope:
    service_id: str
    connection_name: str
    region: str


class ServiceSelectionStore:
    def __init__(self) -> None:
        self._values: dict[tuple[SelectionScope, str], str] = {}

    def get(self, scope: SelectionScope, key: str) -> str | None:
        return self._values.get((scope, key))

    def set(self, scope: SelectionScope, key: str, value: str) -> None:
        self._values[(scope, key)] = value

    def discard(self, scope: SelectionScope, key: str) -> None:
        self._values.pop((scope, key), None)


__all__ = ["SelectionScope", "ServiceSelectionStore", "ServiceSourceContext"]
