"""Public-API checks for the disposal of VMx token pagers.

VMx 3.23 has no public ``is_disposed`` on a pager. ``dispose()`` disposes both
commands, and the refresh command is executable on an idle live pager, so
``refresh_command.can_execute()`` is the public proxy for disposal.
"""

from __future__ import annotations

from typing import Protocol

from vmx import AsyncRelayCommand


class _HasRefreshCommand(Protocol):
    @property
    def refresh_command(self) -> AsyncRelayCommand: ...


def assert_pager_live(pager: _HasRefreshCommand) -> None:
    """Prove that the pager is live and idle, so a later disposed check is not vacuous."""
    assert pager.refresh_command.can_execute()


def assert_pager_disposed(pager: _HasRefreshCommand) -> None:
    """Assert that the pager is disposed. The pager must be idle: no refresh in flight."""
    assert not pager.refresh_command.can_execute()
