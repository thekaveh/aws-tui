"""Every editor has an ownership revision, including with drafts disabled."""

from __future__ import annotations

from tests.unit.vm.athena.test_query_vm import InMemoryAthena, make_query_vm


async def test_editor_revision_changes_on_real_edits_including_clear_without_drafts():
    vm = make_query_vm(InMemoryAthena())
    try:
        assert hasattr(vm, "editor_revision"), "Authoring needs an unconditional editor revision"
        revision = vm.editor_revision
        vm.set_sql("SELECT 1")
        assert vm.editor_revision > revision
        revision = vm.editor_revision
        vm.set_sql("SELECT 1")
        assert vm.editor_revision == revision
        vm.set_sql("")
        assert vm.editor_revision > revision
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_editor_snapshot_install_invalidates_earlier_authoring_ownership():
    vm = make_query_vm(InMemoryAthena())
    try:
        assert hasattr(vm, "editor_revision"), "Authoring needs an unconditional editor revision"
        vm.set_sql("SELECT 1")
        snapshot = vm.export_editor_snapshot()
        revision = vm.editor_revision
        vm.install_editor_snapshot(snapshot)
        assert vm.sql == "SELECT 1"
        assert vm.editor_revision > revision
        full = vm.export_snapshot()
        revision = vm.editor_revision
        await vm.restore_snapshot(full)
        assert vm.editor_revision > revision
    finally:
        await vm.shutdown()
        vm.dispose()


async def test_closed_editor_rejects_late_input_without_changing_revision():
    vm = make_query_vm(InMemoryAthena())
    try:
        assert hasattr(vm, "editor_revision"), "Authoring needs an unconditional editor revision"
        vm.set_sql("SELECT 1")
        revision = vm.editor_revision
        vm.close_input_admission()
        vm.set_sql("SELECT 2")
        assert vm.sql == "SELECT 1"
        assert vm.editor_revision == revision
    finally:
        await vm.shutdown()
        vm.dispose()
