"""SettingsVM — top-level VM for the Settings nav destination.

A peer to the service VMs hosted by :class:`ContentHostVM`. Owns the
S3 connections sub-VM. Construction and disposal follow the standard
VMx ``ComponentVM`` pattern; ``setup()`` is a no-op today (kept so the
ContentHost lifecycle calls it without an attribute error).
"""

from __future__ import annotations

from vmx import ComponentVM, Dispatcher, Message, MessageHub

from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.settings.connection_protection_vm import ConnectionProtectionVM
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


class SettingsVM(ComponentVM):
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
        athena_drafts: AthenaDraftsVM | None = None,
        protection: ConnectionProtectionVM | None = None,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        super().__init__(name="settings", hint="", hub=hub, dispatcher=dispatcher)
        self.protection = protection
        self._s3: S3ConnectionsVM = s3
        self._athena_drafts = athena_drafts

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def s3(self) -> S3ConnectionsVM:
        return self._s3

    @property
    def athena_drafts(self) -> AthenaDraftsVM | None:
        return self._athena_drafts

    async def setup(self) -> None:
        """No-op placeholder so :class:`ContentHostVM.set_content` can
        call it uniformly across all hosted VMs."""
        return None


__all__ = ["SettingsVM"]
