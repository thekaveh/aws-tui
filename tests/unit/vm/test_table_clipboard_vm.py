from __future__ import annotations

from typing import Any, cast

import pytest
from vmx import NULL_DISPATCHER, ComponentVMOf, MessageHub, RelayCommandOf
from vmx.lifecycle.status import ConstructionStatus

from aws_tui.domain.data_catalog import TableRef
from aws_tui.vm.table_clipboard_vm import CopiedTableReference, TableClipboardVM


def _make_vm() -> TableClipboardVM:
    vm = TableClipboardVM(hub=MessageHub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


def _ref(
    *,
    table: str = "events",
    connection: str = "analytics",
    region: str = "us-east-1",
) -> TableRef:
    return TableRef("AwsDataCatalog", "warehouse", table, connection, region)


def test_copy_command_preserves_exact_table_ref_and_canonical_identifier() -> None:
    vm = _make_vm()
    ref = _ref(table='event"log')
    try:
        vm.copy_command.execute(ref)

        assert vm.copied_table == CopiedTableReference(
            table_ref=ref,
            sql_identifier='"AwsDataCatalog"."warehouse"."event""log"',
        )
        assert vm.copied_table.table_ref is ref
    finally:
        vm.dispose()


def test_equal_copy_is_a_notification_no_op() -> None:
    vm = _make_vm()
    ref = _ref()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(notifications.append)
    try:
        vm.copy_command.execute(ref)
        assert notifications == ["model"]

        notifications.clear()
        vm.copy_command.execute(ref)

        assert notifications == []
    finally:
        subscription.dispose()
        vm.dispose()


def test_equal_but_distinct_table_ref_copy_is_a_notification_no_op() -> None:
    vm = _make_vm()
    first = _ref()
    equal_copy = _ref()
    assert equal_copy == first
    assert equal_copy is not first
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(notifications.append)
    try:
        vm.copy_command.execute(first)
        notifications.clear()

        vm.copy_command.execute(equal_copy)

        assert notifications == []
        assert vm.copied_table is not None
        assert vm.copied_table.table_ref is first
    finally:
        subscription.dispose()
        vm.dispose()


def test_replacement_copy_emits_one_model_notification() -> None:
    vm = _make_vm()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(notifications.append)
    try:
        vm.copy_command.execute(_ref(table="events"))
        notifications.clear()

        replacement = _ref(table="sessions", connection="prod", region="us-west-2")
        vm.copy_command.execute(replacement)

        assert notifications == ["model"]
        assert vm.copied_table is not None
        assert vm.copied_table.table_ref is replacement
    finally:
        subscription.dispose()
        vm.dispose()


def test_public_shape_uses_vmx_modeled_component_and_typed_relay_command() -> None:
    vm = _make_vm()
    try:
        assert not hasattr(vm, "inner")
        inner = vars(vm)["_inner"]
        assert isinstance(inner, ComponentVMOf)
        assert isinstance(vm.copy_command, RelayCommandOf)
        assert inner.model is None
        assert vm.on_property_changed is not None
    finally:
        vm.dispose()


def test_dispose_is_idempotent_and_makes_command_inert() -> None:
    vm = _make_vm()
    command = vm.copy_command

    vm.dispose()
    vm.dispose()
    command.execute(_ref())

    assert vm.status is ConstructionStatus.DISPOSED
    assert command.can_execute(_ref()) is False
    assert vm.copied_table is None


def test_dispose_finishes_component_cleanup_when_command_observer_raises() -> None:
    vm = _make_vm()

    def fail(_value: None) -> None:
        raise RuntimeError("observer failed")

    vm.copy_command.can_execute_changed.subscribe(fail)

    with pytest.raises(RuntimeError, match="observer failed"):
        vm.dispose()

    assert vm.status is ConstructionStatus.DISPOSED


def test_copy_command_rejects_runtime_non_table_ref_without_mutation() -> None:
    vm = _make_vm()
    hostile = cast(Any, object())
    try:
        assert vm.copy_command.can_execute(hostile) is False

        vm.copy_command.execute(hostile)

        assert vm.copied_table is None
    finally:
        vm.dispose()
