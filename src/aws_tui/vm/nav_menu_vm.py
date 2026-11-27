"""NavMenuVM — left-rail nav picker.

The menu filters the global :class:`ServiceRegistry` by the active
:class:`Connection` (via :meth:`Service.supports`). A hard-coded Settings
entry is always appended last so it appears as a top-level nav peer to
service items regardless of the active connection.

Selecting a service fires the :attr:`switch_service_command`; the actual
swap of ``ContentHostVM`` happens in :class:`RootVM`.
"""

from __future__ import annotations

from typing import Final

from reactivex.abc import DisposableBase
from vmx import (
    ComponentVMOf,
    CompositeVM,
    ConstructionStatus,
    Dispatcher,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommandOf,
)

from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm._observable import send_value_free
from aws_tui.vm.messages import ConnectionChangedMessage, ConnectionListChangedMessage
from aws_tui.vm.selection_projection import selection_changed
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry

#: Canonical id for the synthetic Settings nav peer. Lives on the VM
#: layer so the View (``NavMenu``) and the App's
#: ``action_open_settings`` dispatch can share one source of truth
#: instead of each carrying the bare ``"settings"`` literal.
SETTINGS_NAV_ID: Final[str] = "settings"


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
        self._local_navigation = False
        # Selection lives in `self._inner.current` (the CompositeVM's
        # canonical slot). `selected_id` is a derived property — see the
        # round-3 "compose, don't reject" directive (spec §9.bis.11) and
        # the §4.2.0 finish-the-incomplete-adoption brief.

        # CompositeVM tracks the inner VMx instances of each item so the view
        # can observe collection mutations via ``on_collection_changed``.
        self._inner: CompositeVM[ComponentVMOf[ServiceDescriptor]] = (
            CompositeVM[ComponentVMOf[ServiceDescriptor]]
            .builder()
            .name("nav_menu")
            .services(hub, dispatcher)
            .children(lambda: ())
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
    def items(self) -> tuple[ComponentVMOf[ServiceDescriptor], ...]:
        return self._inner.snapshot()

    @property
    def selected_id(self) -> str | None:
        current = self._inner.current
        if current is None:
            return None
        return current.model.id

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
        # Populate the items list (including the hard-coded Settings entry)
        # immediately so it is never empty after construct(), even when no
        # connection has been set yet.  _desired_service_ids() already guards
        # against a None connection, so this is safe to call unconditionally.
        # Pass notify=False: the View hasn't mounted yet, so there are no
        # subscribers that need the PropertyChangedMessage; suppressing it
        # avoids a spurious "items" notification that would race with the
        # "items" notification sent once the real connection is resolved.
        self._rebuild_items(notify=False)

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
        self._inner.dispose()

    # ── Public API ─────────────────────────────────────────────────────────

    def update_connection(self, connection: Connection | None) -> None:
        """Filter supported services, or explicitly expose local file navigation."""
        self._connection = connection
        self._local_navigation = connection is None
        self._rebuild_items()

    def clear_selection(self) -> None:
        """Clear the tentative service selection and notify observers."""
        if self._inner.current is None:
            return
        self._inner.current = None
        send_value_free(self._hub, selection_changed(self, self.name, self.selected_id))

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_message(self, msg: object) -> None:
        if isinstance(msg, ConnectionChangedMessage):
            self.update_connection(msg.connection)
        elif isinstance(msg, ConnectionListChangedMessage):
            self._rebuild_items()

    def _can_switch(self, service_id: str | None) -> bool:
        if service_id is None:
            return False
        return any(item.model.id == service_id for item in self._inner.snapshot())

    def _switch_service(self, service_id: str | None) -> None:
        if service_id is None:
            return
        # Idempotent: re-selecting the active service is a no-op.
        if self.selected_id == service_id:
            return
        # Find the matching row. Unknown ids are a silent no-op
        # (preserves the pre-Phase-1 behaviour exercised by
        # tests/unit/vm/test_nav_menu_vm.py::test_menu_switch_service_unknown_id_noop).
        match = next((item for item in self._inner.snapshot() if item.model.id == service_id), None)
        if match is None:
            return
        # Commit the composite's current child before publishing selected_id
        # so the view reads the new selection when it handles the message.
        self._inner.current = match
        send_value_free(self._hub, selection_changed(self, self.name, self.selected_id))

    def _rebuild_items(self, *, notify: bool = True) -> None:
        prior_selected_id = self.selected_id
        desired_ids = self._desired_service_ids()
        current_ids = [item.model.id for item in self._inner.snapshot()]
        # The Settings item is always last; compare only service-derived items.
        current_service_ids = [id_ for id_ in current_ids if id_ != SETTINGS_NAV_ID]
        if desired_ids == current_service_ids and any(
            item.model.id == SETTINGS_NAV_ID for item in self._inner.snapshot()
        ):
            return
        self._clear_items()
        self._repopulate_items(desired_ids)

        # Hard-coded Settings nav peer — always present, always last.
        # Built from a synthetic ``ServiceDescriptor`` so the item shares the
        # render/select machinery with service items but doesn't require the
        # ServiceRegistry to know about it.
        settings_descriptor = ServiceDescriptor(
            id=SETTINGS_NAV_ID,
            label="Settings",
            # Gear emoji (U+2699 GEAR + U+FE0F VARIATION SELECTOR-16).
            # The trailing VS-16 is what flips terminal rendering from
            # the text-style outline (often a tiny grey line-art glyph)
            # to the colored emoji presentation — without it, ``⚙``
            # alone is treated as a text symbol per Unicode TR51.
            icon="⚙️",
        )
        self._add_item(settings_descriptor)
        # The rows were rebuilt; a service that is still offered becomes current again,
        # so the rebuild does not change the selection.
        if prior_selected_id is not None:
            restored = next(
                (item for item in self._inner.snapshot() if item.model.id == prior_selected_id),
                None,
            )
            if restored is not None:
                self._inner.current = restored

        if notify:
            # Notify subscribers that the items collection changed so the View
            # layer can re-mount the rows. Without this, the NavMenu widget
            # binds to the initial (empty) item set at mount and never re-renders
            # when the connection resolution adds entries.
            send_value_free(self._hub, PropertyChangedMessage.create(self, self.name, "items"))

        # The CompositeVM enforces "current must be in children" — its
        # `_remove_at` (composite_vm.py:264-272) drops `_current` to None
        # whenever the current item is removed. `_clear_items` calls
        # `self._inner.remove(...)` per item above, so by the time we get
        # here the composite's `current` reflects the post-rebuild reality.
        # Only emit the user-visible `selected_id` notification if the
        # rebuild actually changed which service is current.
        if notify and self._inner.current is None and prior_selected_id is not None:
            send_value_free(self._hub, selection_changed(self, self.name, self.selected_id))

    def _desired_service_ids(self) -> list[str]:
        """Service ids the current connection supports, in registry order."""
        conn = self._connection
        if conn is None:
            return ["s3"] if self._local_navigation and "s3" in self._registry else []
        return [s.descriptor.id for s in self._registry.all() if s.supports(conn)]

    def _add_item(self, descriptor: ServiceDescriptor) -> None:
        # auto_construct_on_add(True) constructs the row when the menu is constructed.
        self._inner.append(
            ComponentVMOf[ServiceDescriptor]
            .builder()
            .name(f"nav_item.{descriptor.id}")
            .model(descriptor)
            .services(self._hub, self._dispatcher)
            .build()
        )

    def _clear_items(self) -> None:
        """Detach and dispose every current row."""
        for item in self._inner.snapshot():
            self._inner.remove(item)
            item.dispose()

    def _repopulate_items(self, desired_ids: list[str]) -> None:
        """Build new rows for ``desired_ids`` in registry order."""
        desired = set(desired_ids)
        for service in self._registry.all():
            if service.descriptor.id not in desired:
                continue
            self._add_item(service.descriptor)


__all__ = ["SETTINGS_NAV_ID", "NavMenuVM"]
