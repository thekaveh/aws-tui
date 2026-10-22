"""Read-only projection of the existing session transfer collection."""

from aws_tui.domain.scoped_overview import OverviewEntry, OverviewPage, WatchScope
from aws_tui.vm.file_manager.transfers_vm import TransfersVM

MAX_VISIBLE_ACTIVE_TRANSFERS = 100


def project_session_transfers(scope: WatchScope, transfers: TransfersVM) -> OverviewPage:
    if scope.kind != "transfers":
        raise ValueError("Session transfer projection requires a transfers scope")
    active = transfers.active
    selected = (*active[:MAX_VISIBLE_ACTIVE_TRANSFERS], *transfers.finished)
    label = (
        f"Session-local: at most {transfers.finished_limit} finished transfers retained "
        "across all connections; not durable history"
    )
    if len(active) > MAX_VISIBLE_ACTIVE_TRANSFERS:
        label += f"; showing first {MAX_VISIBLE_ACTIVE_TRANSFERS} active transfers"
    return OverviewPage(
        tuple(
            OverviewEntry(
                scope, transfer.id, transfer.state.value, error_summary=transfer.model.error
            )
            for transfer in selected
        ),
        label,
    )
