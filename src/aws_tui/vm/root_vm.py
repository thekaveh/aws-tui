"""RootVM — top of the aws-tui VM tree.

The root owns:

- the :class:`MessageHub` for the whole VM tree;
- the three direct children (:class:`NavMenuVM`, :class:`ContentHostVM`,
  :class:`ChromeVM`);
- the orchestration of connection / service / theme switches.

It also owns the infra refs (:class:`KeymapStore`, :class:`ThemeStore`,
:class:`LogSink`) because they are the surfaces the orchestrator commands
talk to. Concrete :class:`AwsSession` is intentionally *not* held here —
the connection switch path receives ``(Connection, TokenState)`` from the
caller so we can keep the test surface free of boto3.
"""

from __future__ import annotations

from vmx import ComponentVM, Message, MessageHub, RxDispatcher
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.vm.chrome.chrome_vm import ChromeVM
from aws_tui.vm.content_host_vm import ContentHostVM
from aws_tui.vm.messages import (
    ConnectionChangedMessage,
    FocusChangedMessage,
    ThemeChangedMessage,
)
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceRegistry


class RootVM:
    """Top of the VM tree."""

    def __init__(
        self,
        *,
        registry: ServiceRegistry,
        keymap: KeymapStore,
        theme: ThemeStore,
        log: LogSink,
        dispatcher: Dispatcher | None = None,
        hub: MessageHub[Message] | None = None,
    ) -> None:
        self._registry: ServiceRegistry = registry
        self._keymap: KeymapStore = keymap
        self._theme: ThemeStore = theme
        self._log: LogSink = log

        self._hub: MessageHub[Message] = hub if hub is not None else MessageHub()
        self._dispatcher: Dispatcher = (
            dispatcher if dispatcher is not None else RxDispatcher.immediate()
        )

        self._connection: Connection | None = None
        self._auth_state: TokenState | None = None
        self._focused_vm_id: str | None = None
        self._theme_name: str = "carbon"

        self._services_menu: NavMenuVM = NavMenuVM(
            registry=registry, hub=self._hub, dispatcher=self._dispatcher
        )
        self._content_host: ContentHostVM = ContentHostVM(
            hub=self._hub, dispatcher=self._dispatcher
        )
        self._chrome: ChromeVM = ChromeVM(hub=self._hub, dispatcher=self._dispatcher, keymap=keymap)

        self._inner: ComponentVM = (
            ComponentVM.builder().name("root").services(self._hub, self._dispatcher).build()
        )

    # ── Children accessors ──────────────────────────────────────────────────

    @property
    def services_menu(self) -> NavMenuVM:
        # Legacy property name preserved deliberately. The underlying VM
        # was renamed ServicesMenuVM → NavMenuVM when Settings became a
        # peer of S3 in the rail; the property accessor is left as
        # ``services_menu`` to avoid touching every call site
        # (composition.py, app.py, integration tests). Rename queued for
        # a future minor-version-bump cleanup.
        return self._services_menu

    @property
    def content_host(self) -> ContentHostVM:
        return self._content_host

    @property
    def chrome(self) -> ChromeVM:
        return self._chrome

    @property
    def message_hub(self) -> MessageHub[Message]:
        return self._hub

    @property
    def focused_vm_id(self) -> str | None:
        return self._focused_vm_id

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
        # Depth-first child construction so the chrome can listen to messages
        # the menu / content publish during their own construct.
        self._chrome.construct()
        self._services_menu.construct()
        self._content_host.construct()

    def destruct(self) -> None:
        self._content_host.destruct()
        self._services_menu.destruct()
        self._chrome.destruct()
        self._inner.destruct()

    def dispose(self) -> None:
        self._content_host.dispose()
        self._services_menu.dispose()
        self._chrome.dispose()
        self._inner.dispose()
        self._hub.dispose()

    # ── Orchestration commands ─────────────────────────────────────────────

    async def switch_connection_with(self, connection: Connection, auth_state: TokenState) -> None:
        """Update the active connection + auth.

        Disposes the currently hosted service content (caller re-issues a
        ``switch_service`` afterwards if desired) and republishes the
        :class:`ConnectionChangedMessage` so descendants react.
        """
        # Dispose any active service content for the previous connection.
        await self._content_host.set_content(None, service_id=None)
        self._connection = connection
        self._auth_state = auth_state
        # Send the message; NavMenuVM and StatusBarVM are subscribed.
        self._hub.send(ConnectionChangedMessage(connection=connection, auth_state=auth_state))

    async def switch_service(self, service_id: str) -> None:
        """Build the named service's VM tree and host it.

        Idempotent if ``service_id`` matches the currently hosted id.
        Raises :class:`ServiceNotFound` if the registry is unaware.

        The menu's ``selected_id`` is updated BEFORE ``set_content``
        returns so the nav-rail ribbon reflects user intent instantly,
        even when the hosted VM's ``setup()`` is slow (e.g. S3 listing
        on an unreachable endpoint). ``ContentHostVM.set_content`` now
        dispatches ``setup`` as a background task; the await here
        returns as soon as adoption + the ``"current"`` message fire,
        not when the slow setup completes.
        """
        if self._content_host.current_id == service_id:
            return
        if self._connection is None:
            raise RuntimeError("switch_service called before switch_connection_with")
        service = self._registry.get(service_id)
        if not service.supports(self._connection):
            raise RuntimeError(
                f"service {service_id!r} does not support connection {self._connection.name!r}"
            )
        # ``build_vm`` is service-implementation territory — it returns any
        # facade (or VMx VM) the service decides to host. We just need a
        # construct/destruct/dispose surface.
        vm = service.build_vm(self._connection)
        # Reflect the selection in the menu BEFORE adoption — the user
        # clicked S3, the ribbon should jump to S3 the next render
        # tick, not after a 60-second botocore retry budget. The
        # previously-final position (after the await) was the root
        # cause of the "ribbon never appears for the active service"
        # user report.
        #
        # Capture the prior selection FIRST so we can revert if the
        # host fails to adopt the new VM — without this revert the
        # ribbon would lie ("S3 selected" but content still on the
        # previous service after a ``set_content`` exception).
        prior_selection = self._services_menu.selected_id
        self._services_menu.switch_service_command.execute(service_id)
        try:
            await self._content_host.set_content(vm, service_id=service_id)
        except Exception:
            # Revert — host failed to adopt, ribbon must not advance.
            if prior_selection is not None:
                self._services_menu.switch_service_command.execute(prior_selection)
            raise

    async def switch_theme(self, name: str) -> None:
        """Publish a theme-changed message; the view layer reloads ``.tcss``."""
        if not self._theme.exists(name):
            # Quietly ignore unknown themes; UI layer offers a list to the user.
            self._log.warning("theme.switch.unknown", name=name)
            return
        if self._theme_name == name:
            return
        self._theme_name = name
        self._hub.send(ThemeChangedMessage(name=name))

    def focus(self, vm_id: str) -> None:
        """Notify subscribers (HintLegendVM, services menu) of focus changes."""
        if self._focused_vm_id == vm_id:
            return
        self._focused_vm_id = vm_id
        self._hub.send(FocusChangedMessage(focused_vm_id=vm_id))

    async def shutdown(self) -> None:
        """Graceful shutdown: dispose the entire tree.

        The async-drain step (cancelling in-flight transfers, closing aioboto3
        clients) belongs to the infra/app layer per spec §5.4. RootVM merely
        owns the synchronous depth-first dispose cascade afterwards.
        """
        self.dispose()


__all__ = ["RootVM"]
