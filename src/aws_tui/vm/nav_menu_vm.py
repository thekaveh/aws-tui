"""NavMenuVM — left-rail nav picker.

The menu filters the global :class:`ServiceRegistry` by the active
:class:`Connection` (via :meth:`Service.supports`). A hard-coded Settings
entry is always appended last so it appears as a top-level nav peer to
service items regardless of the active connection.

Selecting a service fires the :attr:`switch_service_command`; the actual
swap of ``ContentHostVM`` happens in :class:`RootVM`.
"""

from __future__ import annotations

from reactivex.abc import DisposableBase
from vmx import (
    ComponentVMOf,
    CompositeVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommandOf,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.messages import ConnectionChangedMessage, ConnectionListChangedMessage
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


class NavItemVM:
    """A single row in the nav menu."""

    def __init__(
        self,
        *,
        descriptor: ServiceDescriptor,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._descriptor: ServiceDescriptor = descriptor
        self._hub: MessageHub[Message] = hub
        self._is_focused: bool = False
        self._is_selected: bool = False

        self._inner: ComponentVMOf[ServiceDescriptor] = (
            ComponentVMOf[ServiceDescriptor]
            .builder()
            .name(f"nav_item.{descriptor.id}")
            .model(descriptor)
            .services(hub, dispatcher)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def descriptor(self) -> ServiceDescriptor:
        return self._descriptor

    @property
    def is_focused(self) -> bool:
        return self._is_focused

    @property
    def is_selected(self) -> bool:
        return self._is_selected

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> ComponentVMOf[ServiceDescriptor]:
        """Underlying VMx component. ``NavMenuVM`` composes a
        parent ``CompositeVM`` over the live items via this accessor,
        which matches the public ``inner`` pattern on the other VM
        facades (``EntryVM`` / ``TransferVM`` / ``PaneVM`` / ``ToastVM``).
        """
        return self._inner

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── Mutation ────────────────────────────────────────────────────────────

    def set_focused(self, value: bool) -> None:
        if self._is_focused == value:
            return
        self._is_focused = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_focused"))

    def set_selected(self, value: bool) -> None:
        if self._is_selected == value:
            return
        self._is_selected = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_selected"))


class NavMenuVM:
    """Left-rail nav-picker viewmodel."""

    def __init__(
        self,
        *,
        registry: ServiceRegistry,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._registry: ServiceRegistry = registry
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher

        self._connection: Connection | None = None
        self._items: list[NavItemVM] = []
        self._selected_id: str | None = None

        # CompositeVM tracks the inner VMx instances of each item so the view
        # can observe collection mutations via ``on_collection_changed``.
        self._inner: CompositeVM[ComponentVMOf[ServiceDescriptor]] = (
            CompositeVM[ComponentVMOf[ServiceDescriptor]]
            .builder()
            .name("nav_menu")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

        self._switch_command: RelayCommandOf[str] = (
            RelayCommandOf[str]
            .builder()
            .predicate(self._can_switch)
            .task(self._switch_service)
            .build()
        )
        self._sub: DisposableBase | None = None

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def items(self) -> tuple[NavItemVM, ...]:
        return tuple(self._items)

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def switch_service_command(self) -> RelayCommandOf[str]:
        return self._switch_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        if self._sub is None:
            self._sub = self._hub.messages.subscribe(on_next=self._on_message)

    def destruct(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._inner.destruct()

    def dispose(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self._switch_command.dispose()
        for item in self._items:
            item.dispose()
        self._inner.dispose()

    # ── Public API ─────────────────────────────────────────────────────────

    def update_connection(self, connection: Connection) -> None:
        """Re-filter the menu against ``connection.supports`` for each service."""
        self._connection = connection
        self._rebuild_items()

    # ── Internal ────────────────────────────────────────────────────────────

    def _initial_children(self) -> tuple[ComponentVMOf[ServiceDescriptor], ...]:
        # The initial pass uses whatever update_connection(...) has staged.
        return tuple(item.inner for item in self._items)

    def _on_message(self, msg: object) -> None:
        if isinstance(msg, ConnectionChangedMessage):
            self.update_connection(msg.connection)
        elif isinstance(msg, ConnectionListChangedMessage):
            self._rebuild_items()

    def _can_switch(self, service_id: str | None) -> bool:
        if service_id is None:
            return False
        return any(item.descriptor.id == service_id for item in self._items)

    def _switch_service(self, service_id: str | None) -> None:
        if service_id is None:
            return
        # Idempotent: re-selecting the active service is a no-op.
        if self._selected_id == service_id:
            return
        for item in self._items:
            is_match = item.descriptor.id == service_id
            item.set_selected(is_match)
        self._selected_id = service_id
        self._hub.send(PropertyChangedMessage.create(self, self.name, "selected_id"))

    def _rebuild_items(self) -> None:
        desired_ids = self._desired_service_ids()
        current_ids = [item.descriptor.id for item in self._items]
        # The Settings item is always last; compare only service-derived items.
        current_service_ids = [id_ for id_ in current_ids if id_ != "settings"]
        if desired_ids == current_service_ids and any(
            item.descriptor.id == "settings" for item in self._items
        ):
            return
        self._clear_items()
        self._repopulate_items(desired_ids)

        # Hard-coded Settings nav peer — always present, always last.
        # Built from a synthetic ``ServiceDescriptor`` so the item shares the
        # render/select machinery with service items but doesn't require the
        # ServiceRegistry to know about it.
        settings_descriptor = ServiceDescriptor(
            id="settings",
            label="Settings",
            icon="⚙",
        )
        settings_item = NavItemVM(
            descriptor=settings_descriptor,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        self._items.append(settings_item)
        if self._inner.is_constructed:
            settings_item.construct()
        self._inner.append(settings_item.inner)

        # Notify subscribers that the items collection changed so the View
        # layer can re-mount the rows. Without this, the NavMenu widget
        # binds to the initial (empty) item set at mount and never re-renders
        # when the connection resolution adds entries.
        self._hub.send(PropertyChangedMessage.create(self, self.name, "items"))

        # Clear stale selection if the active id is no longer in the menu.
        if self._selected_id is not None and not any(
            item.descriptor.id == self._selected_id for item in self._items
        ):
            self._selected_id = None
            self._hub.send(PropertyChangedMessage.create(self, self.name, "selected_id"))

    def _desired_service_ids(self) -> list[str]:
        """Service ids the current connection supports, in registry order."""
        conn = self._connection
        if conn is None:
            return []
        return [s.descriptor.id for s in self._registry.all() if s.supports(conn)]

    def _clear_items(self) -> None:
        """Detach + dispose every current ``NavItemVM``."""
        for item in list(self._items):
            if item.inner in self._inner:
                self._inner.remove(item.inner)
            item.dispose()
        self._items.clear()

    def _repopulate_items(self, desired_ids: list[str]) -> None:
        """Build new ``NavItemVM`` rows for ``desired_ids`` in registry order."""
        desired = set(desired_ids)
        for service in self._registry.all():
            if service.descriptor.id not in desired:
                continue
            item = NavItemVM(
                descriptor=service.descriptor,
                hub=self._hub,
                dispatcher=self._dispatcher,
            )
            self._items.append(item)
            if self._inner.is_constructed:
                item.construct()
            self._inner.append(item.inner)


__all__ = ["NavItemVM", "NavMenuVM"]
