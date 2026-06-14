"""Service protocol + ServiceRegistry (lives in vm/, not services/).

The protocol and registry must be reachable from the VM layer without
``vm/`` importing ``aws_tui.services.*`` (the layer-rule check forbids it).
We keep both here so the dependency direction is clean: concrete services
under ``aws_tui.services.<name>`` import this module to declare themselves;
the VM layer treats them through the structural :class:`Service` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aws_tui.infra.connection_resolver import Connection


class ServiceNotFound(Exception):
    """Raised by :meth:`ServiceRegistry.get` when the id is unknown."""


@dataclass(frozen=True, slots=True)
class ServiceDescriptor:
    """Immutable identifying metadata for a service shown in the left menu."""

    id: str
    label: str
    icon: str


@runtime_checkable
class Service(Protocol):
    """Structural protocol every service plugin implements.

    ``build_view`` is intentionally absent — that lives in M5 (the UI layer)
    so the VM layer never needs to know about Textual.
    """

    descriptor: ServiceDescriptor

    def supports(self, connection: Connection) -> bool: ...

    def build_vm(self, connection: Connection) -> Any: ...


class ServiceRegistry:
    """Ordered registry of :class:`Service` plugins."""

    def __init__(self) -> None:
        # Insertion order matters — the services menu surfaces entries in the
        # order they were registered (deterministic across launches).
        self._services: dict[str, Service] = {}

    def register(self, service: Service) -> None:
        """Register ``service`` under its descriptor id (replaces on collision)."""
        self._services[service.descriptor.id] = service

    def all(self) -> tuple[Service, ...]:
        return tuple(self._services.values())

    def get(self, service_id: str) -> Service:
        try:
            return self._services[service_id]
        except KeyError as exc:
            raise ServiceNotFound(service_id) from exc

    def __contains__(self, service_id: object) -> bool:
        return isinstance(service_id, str) and service_id in self._services


__all__ = [
    "Service",
    "ServiceDescriptor",
    "ServiceNotFound",
    "ServiceRegistry",
]
