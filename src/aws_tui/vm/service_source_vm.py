from __future__ import annotations

from dataclasses import dataclass

from aws_tui.domain.navigation_workspace import NavigationTarget, WorkspaceError
from aws_tui.infra.connection_resolver import Connection

_NAVIGATION_KEYS = {
    "athena": {"workgroup": "workgroup", "catalog": "catalog", "database": "database"},
    "glue": {"database": "database_name", "table": "table_name"},
    "emr-serverless": {"application": "application_id"},
}


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
        self._navigation_scopes: set[SelectionScope] = set()
        self._adopted: ServiceSelectionStore | None = None
        self._adopted_target: NavigationTarget | None = None
        self._adopted_recovery_scope: SelectionScope | None = None

    def _current(self) -> ServiceSelectionStore:
        store = self
        while store._adopted is not None:
            store = store._adopted
        return store

    def get(self, scope: SelectionScope, key: str) -> str | None:
        return self._current()._values.get((scope, key))

    def set(self, scope: SelectionScope, key: str, value: str) -> None:
        if self._adopted is not None:
            self._current().set(scope, key, value)
            return
        self._values[(scope, key)] = value
        if scope.service_id in _NAVIGATION_KEYS:
            self._navigation_scopes.add(scope)

    def discard(self, scope: SelectionScope, key: str) -> None:
        self._current()._values.pop((scope, key), None)

    def remember_navigation_scope(self, scope: SelectionScope) -> None:
        """Retain a visited source even when its resource selectors are empty."""
        NavigationTarget(scope.service_id, scope.connection_name, scope.region)
        self._current()._navigation_scopes.add(scope)

    def navigation_targets(self, service_id: str) -> tuple[NavigationTarget, ...]:
        """Export known scopes through a closed navigation-field allowlist."""
        if self._adopted is not None:
            return self._current().navigation_targets(service_id)
        keys = _NAVIGATION_KEYS.get(service_id)
        if keys is None:
            return ()
        scopes = sorted(
            {scope for scope, _ in self._values if scope.service_id == service_id}
            | {scope for scope in self._navigation_scopes if scope.service_id == service_id},
            key=lambda scope: (scope.connection_name, scope.region),
        )
        return tuple(
            NavigationTarget(
                service_id,
                scope.connection_name,
                scope.region,
                tuple(
                    (field, self._values[(scope, key)])
                    for field, key in keys.items()
                    if (scope, key) in self._values
                ),
            )
            for scope in scopes
        )

    def install_navigation_targets(
        self, targets: tuple[NavigationTarget, ...], *, service_id: str | None = None
    ) -> None:
        """Replace only named navigation fields after whole-batch validation."""
        if self._adopted is not None:
            self._current().install_navigation_targets(targets, service_id=service_id)
            return
        if type(targets) is not tuple or any(
            not isinstance(target, NavigationTarget)
            or target.service not in _NAVIGATION_KEYS
            or (service_id is not None and target.service != service_id)
            for target in targets
        ):
            raise WorkspaceError("invalid")
        scopes = tuple(
            SelectionScope(target.service, target.connection, target.region)
            for target in targets
            if target.connection is not None and target.region is not None
        )
        if len(scopes) != len(targets) or len(set(scopes)) != len(scopes):
            raise WorkspaceError("invalid")
        updated = self._values.copy()
        for scope, target in zip(scopes, targets, strict=True):
            keys = _NAVIGATION_KEYS[target.service]
            for key in keys.values():
                updated.pop((scope, key), None)
            updated.update(((scope, keys[field]), value) for field, value in target.selectors)
        self._values = updated
        self._navigation_scopes.update(scopes)

    def adopt_navigation_into(
        self, destination: ServiceSelectionStore, target: NavigationTarget
    ) -> None:
        """Publish only navigation, then bind ordinary future reads/writes.

        Retained collaborators (including Glue's metadata inspector) share this
        seed object. They must see the live store after adoption, while no live
        selection may be read or written during speculative preparation.
        """
        live = destination._current()
        if live is self:
            raise WorkspaceError("invalid")
        if self._adopted is not None:
            if self._current() is not live or self._adopted_target != target:
                raise WorkspaceError("invalid")
            return  # Repeated acknowledgement cannot reset newer selection.
        resolved = next(
            (
                item
                for item in self.navigation_targets(target.service)
                if (item.connection, item.region) == (target.connection, target.region)
            ),
            None,
        )
        if resolved is None or any(
            dict(resolved.selectors).get(key) != value for key, value in target.selectors
        ):
            raise WorkspaceError("invalid")
        live.install_navigation_targets((resolved,))
        self._adopted_target = target
        self._adopted = live
        self._values.clear()
        self._navigation_scopes.clear()

    def adopt_recovery_scope_into(
        self, destination: ServiceSelectionStore, scope: SelectionScope
    ) -> None:
        """Preserve ordinary recovery's full scope, then bind its future choices."""
        live = destination._current()
        if destination is self or live is self or scope.service_id not in _NAVIGATION_KEYS:
            raise WorkspaceError("invalid")
        if self._adopted is not None:
            if self._current() is not live or self._adopted_recovery_scope != scope:
                raise WorkspaceError("invalid")
            return
        live.replace_scope_from(self, scope)
        self._adopted_recovery_scope = scope
        self._adopted = live
        self._values.clear()
        self._navigation_scopes.clear()

    def clone(self) -> ServiceSelectionStore:
        """Return an isolated copy suitable for speculative service setup."""
        if self._adopted is not None:
            return self._current().clone()
        clone = ServiceSelectionStore()
        clone._values = dict(self._values)
        clone._navigation_scopes = self._navigation_scopes.copy()
        return clone

    def replace_scope_from(
        self,
        source: ServiceSelectionStore,
        scope: SelectionScope,
    ) -> None:
        """Commit one candidate scope without overwriting unrelated updates."""
        if self._adopted is not None:
            self._current().replace_scope_from(source, scope)
            return
        source = source._current()
        if source is self:
            return
        self._values = {key: value for key, value in self._values.items() if key[0] != scope}
        self._values.update(
            {key: value for key, value in source._values.items() if key[0] == scope}
        )
        self._navigation_scopes.discard(scope)
        if scope in source._navigation_scopes:
            self._navigation_scopes.add(scope)


def navigation_seed(
    service_id: str, connection: Connection, target: NavigationTarget
) -> ServiceSelectionStore:
    """Create a fresh AWS-service seed, after current source validation."""
    if (
        not isinstance(target, NavigationTarget)
        or service_id not in _NAVIGATION_KEYS
        or target.service != service_id
        or connection.kind != "aws"
        or (target.connection, target.region) != (connection.name, connection.region)
    ):
        raise WorkspaceError("invalid")
    seed = ServiceSelectionStore()
    seed.install_navigation_targets((target,), service_id=service_id)
    return seed


__all__ = ["SelectionScope", "ServiceSelectionStore", "ServiceSourceContext"]
