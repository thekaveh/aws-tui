"""``lifecycle_owners`` is the one list of VMs that the context disposes."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace

from aws_tui.composition import AppContext, lifecycle_owners

OWNER_NAMES = (
    "s3_connections_vm",
    "command_palette_vm",
    "quick_look_vm",
    "confirm_vm",
    "transfers_vm",
    "athena_drafts_vm",
    "transfer_history_vm",
    "table_clipboard_vm",
    "clipboard_vm",
    "root_vm",
    "focus_coordinator",
)


def _context(events: list[str]) -> SimpleNamespace:
    owners = {name: SimpleNamespace(dispose=partial(events.append, name)) for name in OWNER_NAMES}
    return SimpleNamespace(
        **owners,
        demo_emrs={},
        log_sink=SimpleNamespace(flush=lambda: None, close=lambda: None),
    )


def test_lifecycle_owners_lists_every_owner_once_with_root_last_but_one() -> None:
    names = tuple(name for name, _ in lifecycle_owners(_context([])))  # type: ignore[arg-type]
    assert names == OWNER_NAMES


def test_close_unstarted_disposes_the_owners_in_list_order() -> None:
    events: list[str] = []
    AppContext.close_unstarted(_context(events))  # type: ignore[arg-type]
    assert events == list(OWNER_NAMES)
