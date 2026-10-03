"""Read-only connection metadata consumed by discovery views.

Resolver snapshots satisfy these contracts structurally; presentation never
needs credentials, parsing, or a resolver dependency.
"""

from typing import Protocol


class ConnectionDisplay(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def source(self) -> str: ...


class ConnectionDiscoveryDisplay(Protocol):
    @property
    def connections(self) -> tuple[ConnectionDisplay, ...]: ...

    @property
    def invalid_sources(self) -> tuple[str, ...]: ...
