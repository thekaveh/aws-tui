"""One guarded provider call for the Glue VMs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from vmx import Message, MessageHub

from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue._errors import map_provider_error, map_unexpected_error
from aws_tui.vm.glue._lifecycle import GlueOperationSuperseded
from aws_tui.vm.service_diagnostics import report_unexpected_service_error

T = TypeVar("T")


async def guarded(
    call: Callable[[], Awaitable[T]],
    *,
    hub: MessageHub[Message],
    operation: str,
    is_current: Callable[[], bool],
    on_failure: Callable[[PaneState, str | None], None],
) -> tuple[bool, T | None]:
    """Run call. Return (True, value) only when it finished and is still current."""
    try:
        value = await call()
    except GlueOperationSuperseded:
        return False, None
    except ProviderError as exc:
        if is_current():
            on_failure(*map_provider_error(exc))
        return False, None
    except Exception as exc:
        if is_current():
            state, text = map_unexpected_error(exc)
            report_unexpected_service_error(hub, service="glue", operation=operation, error=exc)
            on_failure(state, text)
        return False, None
    return is_current(), value
