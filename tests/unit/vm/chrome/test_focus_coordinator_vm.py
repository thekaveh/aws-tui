"""Tests for FocusCoordinatorVM (Phase 7, §4.3)."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, DiscriminatorVM, MessageHub
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


def test_focus_coordinator_uses_vmx_discriminator() -> None:
    vm = _make()
    try:
        assert isinstance(vm._focus_discriminator, DiscriminatorVM)
    finally:
        vm.dispose()


def test_service_ring_uses_vmx_discriminator() -> None:
    vm = _make()
    try:
        assert isinstance(vm._focus_discriminator, DiscriminatorVM)
        selected = vm.cycle_focus_ring((FocusSlot.GLUE_SOURCE, FocusSlot.GLUE_TABS))
        assert selected is FocusSlot.GLUE_SOURCE
        assert vm.focused_slot is FocusSlot.GLUE_SOURCE
    finally:
        vm.dispose()


def test_service_ring_wraps_and_reverse_is_the_exact_inverse() -> None:
    ring = (
        FocusSlot.ATHENA_SOURCE,
        FocusSlot.ATHENA_WORKGROUP,
        FocusSlot.ATHENA_TABS,
    )
    vm = _make(initial=FocusSlot.ATHENA_SOURCE)
    try:
        assert vm.cycle_focus_ring(ring) is FocusSlot.ATHENA_WORKGROUP
        assert vm.cycle_focus_ring(ring) is FocusSlot.ATHENA_TABS
        assert vm.cycle_focus_ring(ring) is FocusSlot.ATHENA_SOURCE
        assert vm.cycle_focus_ring(ring, reverse=True) is FocusSlot.ATHENA_TABS
        assert vm.cycle_focus_ring(ring, reverse=True) is FocusSlot.ATHENA_WORKGROUP
        assert vm.cycle_focus_ring(ring, reverse=True) is FocusSlot.ATHENA_SOURCE
    finally:
        vm.dispose()


def test_service_ring_uses_first_caller_supplied_slot_when_current_is_absent() -> None:
    vm = _make(initial=FocusSlot.GLUE_FILTER)
    try:
        selected = vm.cycle_focus_ring(
            (FocusSlot.GLUE_SOURCE, FocusSlot.GLUE_PRIMARY),
            reverse=True,
        )
        assert selected is FocusSlot.GLUE_SOURCE
    finally:
        vm.dispose()


def test_service_ring_respects_slots_omitted_by_the_caller() -> None:
    vm = _make(initial=FocusSlot.GLUE_SOURCE)
    try:
        selected = vm.cycle_focus_ring(
            (FocusSlot.GLUE_SOURCE, FocusSlot.GLUE_PRIMARY, FocusSlot.GLUE_DETAIL)
        )
        assert selected is FocusSlot.GLUE_PRIMARY
        assert (
            vm.cycle_focus_ring((FocusSlot.GLUE_SOURCE, FocusSlot.GLUE_DETAIL))
            is FocusSlot.GLUE_SOURCE
        )
    finally:
        vm.dispose()


def test_service_ring_rejects_an_empty_ring() -> None:
    vm = _make()
    try:
        with pytest.raises(ValueError, match="at least one"):
            vm.cycle_focus_ring(())
    finally:
        vm.dispose()


def test_nearest_focus_slot_prefers_the_forward_neighbor_on_a_tie() -> None:
    vm = _make(initial=FocusSlot.GLUE_SECONDARY)
    try:
        selected = vm.select_nearest_focus_slot(
            (FocusSlot.GLUE_PRIMARY, FocusSlot.GLUE_DETAIL),
            order=(
                FocusSlot.GLUE_PRIMARY,
                FocusSlot.GLUE_SECONDARY,
                FocusSlot.GLUE_DETAIL,
            ),
        )
        assert selected is FocusSlot.GLUE_DETAIL
        assert vm.focused_slot is FocusSlot.GLUE_DETAIL
    finally:
        vm.dispose()


def test_nearest_focus_slot_uses_the_only_available_neighbor() -> None:
    vm = _make(initial=FocusSlot.ATHENA_CANCEL)
    try:
        selected = vm.select_nearest_focus_slot(
            (FocusSlot.ATHENA_PRIMARY,),
            order=(
                FocusSlot.ATHENA_PRIMARY,
                FocusSlot.ATHENA_SECONDARY,
                FocusSlot.ATHENA_CANCEL,
                FocusSlot.ATHENA_DETAIL,
                FocusSlot.ATHENA_HISTORY_MORE,
                FocusSlot.NAV_MENU,
            ),
        )
        assert selected is FocusSlot.ATHENA_PRIMARY
    finally:
        vm.dispose()


def test_nearest_focus_slot_ignores_inactive_slots_and_uses_forward_tie() -> None:
    vm = _make(initial=FocusSlot.ATHENA_SAVED_NAMED_MORE)
    try:
        selected = vm.select_nearest_focus_slot(
            (
                FocusSlot.ATHENA_PRIMARY,
                FocusSlot.ATHENA_SECONDARY,
                FocusSlot.ATHENA_DETAIL,
            ),
            order=(
                FocusSlot.ATHENA_PRIMARY,
                FocusSlot.ATHENA_SAVED_NAMED_MORE,
                FocusSlot.ATHENA_HISTORY_MORE,
                FocusSlot.ATHENA_SECONDARY,
                FocusSlot.ATHENA_CANCEL,
                FocusSlot.ATHENA_DETAIL,
            ),
        )
        assert selected is FocusSlot.ATHENA_SECONDARY
        assert vm.focused_slot is FocusSlot.ATHENA_SECONDARY
    finally:
        vm.dispose()


def test_nearest_focus_slot_rejects_an_empty_available_set() -> None:
    vm = _make()
    try:
        with pytest.raises(ValueError, match="at least one"):
            vm.select_nearest_focus_slot((), order=(FocusSlot.NAV_MENU,))
    finally:
        vm.dispose()


def test_service_ring_modal_close_restores_the_service_slot() -> None:
    vm = _make(initial=FocusSlot.ATHENA_DATABASE)
    try:
        vm.modal_open()
        vm.modal_close()
        assert vm.focused_slot is FocusSlot.ATHENA_DATABASE
    finally:
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


def test_set_focused_slot_modal_then_close_restores_prior_slot() -> None:
    vm = _make()
    vm.set_focused_slot(FocusSlot.MODAL)
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
    vm.modal_close()
    assert vm.focused_slot is FocusSlot.EMR_RUNS
    vm.dispose()


def test_set_non_modal_while_modal_uses_public_discriminator_api() -> None:
    """The facade owns restore semantics; it must not mutate VMx internals."""

    class _PrivateModalStackSentinel:
        def clear(self) -> None:
            raise AssertionError("FocusCoordinatorVM must not clear VMx private modal stack")

    vm = _make()
    vm.set_focused_slot(FocusSlot.S3_LEFT)
    vm.modal_open()
    vm._focus_discriminator._modal_stack = _PrivateModalStackSentinel()
    try:
        vm.set_focused_slot(FocusSlot.EMR_RUNS)
        assert vm.focused_slot is FocusSlot.EMR_RUNS
        vm.modal_close()
        assert vm.focused_slot is FocusSlot.EMR_RUNS
    finally:
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
        "EMR_SOURCE",
        "EMR_APPLICATION",
        "EMR_RUNS",
        "EMR_DETAIL",
        "EMR_LOGS",
        "GLUE_SOURCE",
        "GLUE_FILTER",
        "GLUE_TABS",
        "GLUE_PRIMARY",
        "GLUE_SECONDARY",
        "GLUE_DETAIL",
        "GLUE_ICEBERG_SNAPSHOTS",
        "GLUE_ICEBERG_HISTORY",
        "GLUE_ICEBERG_MANIFESTS",
        "GLUE_ICEBERG_FILES",
        "GLUE_ICEBERG_PARTITIONS",
        "GLUE_ICEBERG_REFS",
        "GLUE_ICEBERG_TABLE",
        "GLUE_ICEBERG_MORE",
        "GLUE_ICEBERG_RETRY",
        "GLUE_ICEBERG_TIME_TRAVEL",
        "ATHENA_SOURCE",
        "ATHENA_WORKGROUP",
        "ATHENA_WORKGROUP_MORE",
        "ATHENA_CATALOG",
        "ATHENA_CATALOG_MORE",
        "ATHENA_DATABASE",
        "ATHENA_DATABASE_MORE",
        "ATHENA_TABS",
        "ATHENA_PRIMARY",
        "ATHENA_SECONDARY",
        "ATHENA_CANCEL",
        "ATHENA_DETAIL",
        "ATHENA_HISTORY_MORE",
        "ATHENA_SAVED_NAMED_MORE",
        "ATHENA_SAVED_PREPARED_MORE",
        "ATHENA_SAVED_OPEN_EDITOR",
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
    assert FocusSlot.EMR_SOURCE.value == "emr.source"
    assert FocusSlot.EMR_APPLICATION.value == "emr.application"
    assert FocusSlot.EMR_RUNS.value == "emr.runs"
    assert FocusSlot.EMR_DETAIL.value == "emr.detail"
    assert FocusSlot.EMR_LOGS.value == "emr.logs"
    assert FocusSlot.GLUE_SOURCE.value == "glue.source"
    assert FocusSlot.GLUE_FILTER.value == "glue.filter"
    assert FocusSlot.GLUE_TABS.value == "glue.tabs"
    assert FocusSlot.GLUE_PRIMARY.value == "glue.primary"
    assert FocusSlot.GLUE_SECONDARY.value == "glue.secondary"
    assert FocusSlot.GLUE_DETAIL.value == "glue.detail"
    assert FocusSlot.GLUE_ICEBERG_SNAPSHOTS.value == "glue.iceberg.snapshots"
    assert FocusSlot.GLUE_ICEBERG_HISTORY.value == "glue.iceberg.history"
    assert FocusSlot.GLUE_ICEBERG_MANIFESTS.value == "glue.iceberg.manifests"
    assert FocusSlot.GLUE_ICEBERG_FILES.value == "glue.iceberg.files"
    assert FocusSlot.GLUE_ICEBERG_PARTITIONS.value == "glue.iceberg.partitions"
    assert FocusSlot.GLUE_ICEBERG_REFS.value == "glue.iceberg.refs"
    assert FocusSlot.GLUE_ICEBERG_TABLE.value == "glue.iceberg.table"
    assert FocusSlot.GLUE_ICEBERG_MORE.value == "glue.iceberg.more"
    assert FocusSlot.GLUE_ICEBERG_RETRY.value == "glue.iceberg.retry"
    assert FocusSlot.GLUE_ICEBERG_TIME_TRAVEL.value == "glue.iceberg.time_travel"
    assert FocusSlot.ATHENA_SOURCE.value == "athena.source"
    assert FocusSlot.ATHENA_WORKGROUP.value == "athena.workgroup"
    assert FocusSlot.ATHENA_WORKGROUP_MORE.value == "athena.workgroup.more"
    assert FocusSlot.ATHENA_CATALOG.value == "athena.catalog"
    assert FocusSlot.ATHENA_CATALOG_MORE.value == "athena.catalog.more"
    assert FocusSlot.ATHENA_DATABASE.value == "athena.database"
    assert FocusSlot.ATHENA_DATABASE_MORE.value == "athena.database.more"
    assert FocusSlot.ATHENA_TABS.value == "athena.tabs"
    assert FocusSlot.ATHENA_PRIMARY.value == "athena.primary"
    assert FocusSlot.ATHENA_SECONDARY.value == "athena.secondary"
    assert FocusSlot.ATHENA_CANCEL.value == "athena.cancel"
    assert FocusSlot.ATHENA_DETAIL.value == "athena.detail"
    assert FocusSlot.ATHENA_HISTORY_MORE.value == "athena.history.more"
    assert FocusSlot.ATHENA_SAVED_NAMED_MORE.value == "athena.saved.named.more"
    assert FocusSlot.ATHENA_SAVED_PREPARED_MORE.value == "athena.saved.prepared.more"
    assert FocusSlot.ATHENA_SAVED_OPEN_EDITOR.value == "athena.saved.open_editor"
    assert FocusSlot.SETTINGS.value == "settings"
    assert FocusSlot.MODAL.value == "modal"
