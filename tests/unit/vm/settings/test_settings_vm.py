"""Tests for SettingsVM (simplified, post-modal)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.messages.protocols import Message

from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM
from aws_tui.vm.settings.settings_vm import SettingsVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _make_vm(tmp_path: Path) -> tuple[SettingsVM, S3ConnectionsVM]:
    hub = _hub()
    store = ConfigStore(path=tmp_path / "config.toml")
    resolver = ConnectionResolver(config_store=store)
    s3 = S3ConnectionsVM(resolver=resolver, config_store=store, hub=hub, dispatcher=NULL_DISPATCHER)
    s3.construct()
    vm = SettingsVM(s3=s3, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, s3


def test_settings_vm_lifecycle(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    assert vm.status == ConstructionStatus.CONSTRUCTED
    assert vm.s3 is s3
    vm.dispose()
    assert vm.status == ConstructionStatus.DISPOSED
    s3.dispose()


@pytest.mark.asyncio
async def test_settings_vm_setup_is_noop(tmp_path: Path) -> None:
    vm, s3 = _make_vm(tmp_path)
    try:
        result = await vm.setup()
        assert result is None
    finally:
        vm.dispose()
        s3.dispose()


def test_settings_vm_no_longer_has_dirty_set_or_sections(tmp_path: Path) -> None:
    """Regression: PR #52's dirty-set + section-list surface was deleted.

    These attributes existed only because the modal had a lifetime to
    track. Now Settings is a nav-routed page; sections are a static
    View concern; reload-on-Save is immediate. If any of these
    attributes come back, something has regressed toward the old
    pattern.
    """
    vm, s3 = _make_vm(tmp_path)
    try:
        assert not hasattr(vm, "dirty_connection_names")
        assert not hasattr(vm, "clear_dirty")
        assert not hasattr(vm, "change_section")
        assert not hasattr(vm, "active_section")
        assert not hasattr(vm, "SECTIONS")
        assert not hasattr(vm, "ENABLED")
    finally:
        vm.dispose()
        s3.dispose()
