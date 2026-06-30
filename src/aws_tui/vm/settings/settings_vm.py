"""SettingsVM — top-level VM for the Settings nav destination.

A peer to the service VMs hosted by :class:`ContentHostVM`. Owns the
S3 connections sub-VM. Construction and disposal follow the standard
VMx facade pattern; ``setup()`` is a no-op today (kept so the
ContentHost lifecycle calls it without an attribute error).
"""

from __future__ import annotations

from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


class SettingsVM:
    """Top-level VM hosted by ``ContentHostVM`` when the Settings nav
    item is selected.

    The PR #52 dirty-set + active-section machinery has been removed —
    Settings is no longer a modal so there is no "lifetime" to track,
    and the page lays out its sections statically via the View layer
    (a ``VerticalScroll`` of ``Collapsible``).
    """

    def __init__(
        self,
        *,
        s3: S3ConnectionsVM,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._s3: S3ConnectionsVM = s3
        self._inner: ComponentVM = (
            ComponentVM.builder().name("settings").services(hub, dispatcher).build()
        )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def s3(self) -> S3ConnectionsVM:
        return self._s3

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    async def setup(self) -> None:
        """No-op placeholder so :class:`ContentHostVM.set_content` can
        call it uniformly across all hosted VMs."""
        return None


__all__ = ["SettingsVM"]
