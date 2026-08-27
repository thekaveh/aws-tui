"""Public service protocol facade for built-in and future plugins.

In v1 the registry is in-tree; v1.1 promotes to entry-point discovery
(see docs/adding-a-service.md).

Concrete services register under stable ids in the composition root. The
``Service`` protocol and ``ServiceRegistry`` themselves live in
:mod:`aws_tui.vm.services_protocol` so the VM layer can consume them
without violating the layer-rule check (vm/ cannot import
aws_tui.services.*). This module re-exports those symbols so callers in
the ``services/`` subtree can write ``from aws_tui.services import
Service, ServiceRegistry`` for ergonomics.
"""

from aws_tui.vm.services_protocol import (
    Service,
    ServiceDescriptor,
    ServiceNotFound,
    ServiceRegistry,
)

__all__ = [
    "Service",
    "ServiceDescriptor",
    "ServiceNotFound",
    "ServiceRegistry",
]
