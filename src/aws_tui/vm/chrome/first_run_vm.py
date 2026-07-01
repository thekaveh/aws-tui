"""FirstRunVM — facade for the empty-config first-run modal.

Triggered by :mod:`aws_tui.composition` only when both ``ConfigStore``
returns no ``[connections.*]`` entries **and** ``~/.aws/{config,credentials}``
auto-discovery is also empty. Per spec §6.4 Flow 5 the modal offers:

- ``add aws`` — shell-out to ``aws configure sso`` (synchronous; freezes
  the TUI for the duration of the wizard, which is acceptable);
- ``add s3-compatible`` — in-TUI form prompting for the standard fields;
- ``skip`` — proceeds to the main screen with no connection selected.

The VM is a thin coordination shim. It does *not* run the subprocess or
write to the config store itself — those concerns live in the composition
root so the layer rules stay clean. The VM resolves an
:class:`FirstRunAction` which the composition root then applies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from vmx import ComponentVM, Message, MessageHub, PropertyChangedMessage, RelayCommand
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.redaction import safe_endpoint_display


class FirstRunAction(StrEnum):
    """User decision on the first-run modal."""

    ADD_AWS = "add_aws"
    ADD_S3_COMPAT = "add_s3_compat"
    SKIP = "skip"


@dataclass(frozen=True, slots=True, repr=False)
class S3CompatForm:
    """Inputs collected by the in-TUI s3-compatible form.

    ``name`` is the connection's display name and config-section key.
    """

    name: str
    endpoint_url: str
    region: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    force_path_style: bool = True
    verify_tls: bool = True

    def __repr__(self) -> str:
        masked_id = "***" if self.access_key_id else None
        masked_secret = "***" if self.secret_access_key else None
        masked_token = "***" if self.session_token else None
        return (
            f"S3CompatForm(name={self.name!r}, "
            f"endpoint_url={safe_endpoint_display(self.endpoint_url)!r}, "
            f"region={self.region!r}, access_key_id={masked_id!r}, "
            f"secret_access_key={masked_secret!r}, session_token={masked_token!r}, "
            f"force_path_style={self.force_path_style!r}, verify_tls={self.verify_tls!r})"
        )

    def is_valid(self) -> bool:
        return all(
            [
                self.name.strip(),
                self.endpoint_url.strip(),
                self.region.strip(),
                self.access_key_id.strip(),
                self.secret_access_key.strip(),
            ]
        )


class FirstRunVM:
    """Async ``ask`` facade returning a :class:`FirstRunAction`."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub

        self._is_open: bool = False
        self._future: asyncio.Future[FirstRunAction] | None = None
        self._disposed: bool = False

        self._inner: ComponentVM = (
            ComponentVM.builder().name("first_run").services(hub, dispatcher).build()
        )

        self._add_aws_command: RelayCommand = self._make_decision_command(FirstRunAction.ADD_AWS)
        self._add_s3_compat_command: RelayCommand = self._make_decision_command(
            FirstRunAction.ADD_S3_COMPAT
        )
        self._skip_command: RelayCommand = self._make_decision_command(FirstRunAction.SKIP)

    def _make_decision_command(self, action: FirstRunAction) -> RelayCommand:
        def _task() -> None:
            self._resolve(action)

        return RelayCommand.builder().predicate(lambda: self._is_open).task(_task).build()

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def add_aws_command(self) -> RelayCommand:
        return self._add_aws_command

    @property
    def add_s3_compat_command(self) -> RelayCommand:
        return self._add_s3_compat_command

    @property
    def skip_command(self) -> RelayCommand:
        return self._skip_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._future is not None and not self._future.done():
            self._future.set_result(FirstRunAction.SKIP)
        self._add_aws_command.dispose()
        self._add_s3_compat_command.dispose()
        self._skip_command.dispose()
        self._inner.dispose()

    # ── Async API ──────────────────────────────────────────────────────────

    async def ask(self) -> FirstRunAction:
        """Open the modal and return the user's choice."""
        if self._is_open or self._future is not None:
            raise RuntimeError("first-run modal is already open")
        if self._disposed:
            raise RuntimeError("first-run modal has been disposed")
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self._set_open(True)
        try:
            return await self._future
        finally:
            self._future = None
            self._set_open(False)

    # ── Internal ────────────────────────────────────────────────────────────

    def _resolve(self, action: FirstRunAction) -> None:
        if self._future is None or self._future.done():
            return
        self._future.set_result(action)

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))


__all__ = ["FirstRunAction", "FirstRunVM", "S3CompatForm"]
