"""Tests for FocusCoordinatorVM (Phase 7, §4.3)."""

from __future__ import annotations

from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM, FocusSlot


def _hub() -> MessageHub[Message]:
    return MessageHub()


def _make(initial: FocusSlot = FocusSlot.NAV_MENU) -> FocusCoordinatorVM:
    vm = FocusCoordinatorVM(hub=_hub(), dispatcher=NULL_DISPATCHER, initial=initial)
    vm.construct()
    return vm


# -------------------- initial state --------------------


def test_default_initial_slot_is_nav_menu() -> None:
    vm = _make()
    assert vm.focused_slot is FocusSlot.NAV_MENU
    assert vm.is_modal is False
    vm.dispose()


def test_initial_can_be_overridden() -> None:
    vm = _make(initial=FocusSlot.S3_LEFT)
    assert vm.focused_slot is FocusSlot.S3_LEFT
    vm.dispose()


# -------------------- set_focused_slot --------------------


def test_set_focused_slot_emits_on_change() -> None:
    vm = _make()
    events: list[FocusSlot] = []
    sub = vm.on_focused_slot_changed.subscribe(on_next=events.append)
    try:
        vm.set_focused_slot(FocusSlot.EMR_RUNS)
        assert vm.focused_slot is FocusSlot.EMR_RUNS
        assert events == [FocusSlot.EMR_RUNS]
    finally:
        sub.dispose()
        vm.dispose()


def test_set_focused_slot_to_same_is_noop() -> None:
    vm = _make()
    vm.set_focused_slot(FocusSlot.S3_LEFT)
    events: list[FocusSlot] = []
    sub = vm.on_focused_slot_changed.subscribe(on_next=events.append)
    try:
        vm.set_focused_slot(FocusSlot.S3_LEFT)  # same
        assert events == []
    finally:
        sub.dispose()
        vm.dispose()


def test_cycle_s3_focus_forward_rotates_left_right_nav() -> None:
    vm = _make(initial=FocusSlot.S3_LEFT)
    try:
        vm.cycle_s3_focus()
        assert vm.focused_slot is FocusSlot.S3_RIGHT
        vm.cycle_s3_focus()
        assert vm.focused_slot is FocusSlot.NAV_MENU
        vm.cycle_s3_focus()
        assert vm.focused_slot is FocusSlot.S3_LEFT
    finally:
        vm.dispose()


def test_cycle_s3_focus_reverse_rotates_left_nav_right() -> None:
    vm = _make(initial=FocusSlot.S3_LEFT)
    try:
        vm.cycle_s3_focus(reverse=True)
        assert vm.focused_slot is FocusSlot.NAV_MENU
        vm.cycle_s3_focus(reverse=True)
        assert vm.focused_slot is FocusSlot.S3_RIGHT
        vm.cycle_s3_focus(reverse=True)
        assert vm.focused_slot is FocusSlot.S3_LEFT
    finally:
        vm.dispose()


def test_cycle_settings_focus_toggles_settings_and_nav() -> None:
    vm = _make(initial=FocusSlot.SETTINGS)
    try:
        vm.cycle_settings_focus()
        assert vm.focused_slot is FocusSlot.NAV_MENU
        vm.cycle_settings_focus(reverse=True)
        assert vm.focused_slot is FocusSlot.SETTINGS
    finally:
        vm.dispose()


# -------------------- modal precedence --------------------


def test_modal_open_saves_and_promotes() -> None:
    vm = _make()
    vm.set_focused_slot(FocusSlot.EMR_RUNS)
    vm.modal_open()
    assert vm.focused_slot is FocusSlot.MODAL
    assert vm.is_modal is True
    vm.dispose()


def test_modal_close_restores_saved_slot() -> None:
    vm = _make()
    vm.set_focused_slot(FocusSlot.SETTINGS)
    vm.modal_open()
    vm.modal_close()
    assert vm.focused_slot is FocusSlot.SETTINGS
    assert vm.is_modal is False
    vm.dispose()


def test_modal_open_when_already_modal_is_noop() -> None:
    vm = _make()
    vm.set_focused_slot(FocusSlot.S3_RIGHT)
    vm.modal_open()
    events: list[FocusSlot] = []
    sub = vm.on_focused_slot_changed.subscribe(on_next=events.append)
    try:
        vm.modal_open()  # already modal
        assert events == []
    finally:
        sub.dispose()
        vm.dispose()


def test_modal_close_when_not_modal_is_noop() -> None:
    vm = _make()
    events: list[FocusSlot] = []
    sub = vm.on_focused_slot_changed.subscribe(on_next=events.append)
    try:
        vm.modal_close()
        assert events == []
    finally:
        sub.dispose()
        vm.dispose()


def test_modal_close_defaults_to_nav_menu_when_no_saved_slot() -> None:
    """Defensive — should never happen via normal API, but guards
    against modal_close being called without a matching open."""
    vm = _make()
    # Force MODAL slot via set_focused_slot (which routes through
    # modal_open and SAVES nav_menu).
    vm.set_focused_slot(FocusSlot.MODAL)
    # Now manually clear saved_slot to exercise the defensive branch.
    vm._saved_slot = None  # type: ignore[attr-defined]
    vm.modal_close()
    assert vm.focused_slot is FocusSlot.NAV_MENU
    vm.dispose()


# -------------------- set_focused_slot interactions with modal --------------------


def test_set_focused_slot_modal_routes_to_modal_open() -> None:
    vm = _make()
    vm.set_focused_slot(FocusSlot.S3_LEFT)
    vm.set_focused_slot(FocusSlot.MODAL)
    assert vm.focused_slot is FocusSlot.MODAL
    vm.modal_close()
    # Restoration from MODAL → S3_LEFT preserved.
    assert vm.focused_slot is FocusSlot.S3_LEFT
    vm.dispose()


def test_set_non_modal_while_modal_clears_saved_slot() -> None:
    """Implicit modal close via explicit non-MODAL slot."""
    vm = _make()
    vm.set_focused_slot(FocusSlot.S3_LEFT)
    vm.modal_open()
    vm.set_focused_slot(FocusSlot.EMR_RUNS)
    assert vm.focused_slot is FocusSlot.EMR_RUNS
    assert vm._saved_slot is None  # type: ignore[attr-defined]
    vm.dispose()


# -------------------- hub propagation --------------------


def test_set_focused_slot_emits_property_changed_on_hub() -> None:
    hub = _hub()
    vm = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    notified: list[str] = []
    sub = hub.messages.subscribe(on_next=lambda m: notified.append(getattr(m, "property_name", "")))
    try:
        vm.set_focused_slot(FocusSlot.S3_LEFT)
        assert "focused_slot" in notified
    finally:
        sub.dispose()
        vm.dispose()


# -------------------- lifecycle / dispose --------------------


def test_dispose_is_idempotent() -> None:
    vm = _make()
    vm.dispose()
    vm.dispose()


def test_construct_status_reflects_inner() -> None:
    """``status`` proxies the composed inner ComponentVM, so after
    ``_make()`` (which calls ``construct()``) the wrapper reads as
    CONSTRUCTED. Vacuous ``hasattr`` form previously collapsed to
    ``assert True`` because ``is_constructed`` is not on the public
    surface."""
    vm = _make()
    assert vm.status is ConstructionStatus.CONSTRUCTED
    vm.dispose()


# -------------------- FocusSlot enum surface --------------------


def test_focus_slot_enum_has_all_required_members() -> None:
    """Pin the spec §4.3 + round-3 spec slot set so deletions show
    up as test failures rather than silent regressions."""
    expected = {
        "NAV_MENU",
        "S3_LEFT",
        "S3_RIGHT",
        "EMR_RUNS",
        "EMR_DETAIL",
        "EMR_LOGS",
        "SETTINGS",
        "MODAL",
    }
    actual = {member.name for member in FocusSlot}
    assert actual == expected, f"FocusSlot set drift: {actual ^ expected}"


def test_focus_slot_values_are_canonical_strings() -> None:
    """The slot values are used as Textual / hub keys; pinning them
    prevents accidental renaming."""
    assert FocusSlot.NAV_MENU.value == "nav_menu"
    assert FocusSlot.S3_LEFT.value == "s3.left"
    assert FocusSlot.S3_RIGHT.value == "s3.right"
    assert FocusSlot.EMR_RUNS.value == "emr.runs"
    assert FocusSlot.EMR_DETAIL.value == "emr.detail"
    assert FocusSlot.EMR_LOGS.value == "emr.logs"
    assert FocusSlot.SETTINGS.value == "settings"
    assert FocusSlot.MODAL.value == "modal"
