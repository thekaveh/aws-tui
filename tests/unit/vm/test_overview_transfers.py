from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.scoped_overview import WatchScope
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from aws_tui.vm.file_manager.transfers_vm import TransfersVM
from aws_tui.vm.overview_transfers import project_session_transfers


def test_overview_projects_only_session_transfers_with_existing_finished_cap() -> None:
    vm = TransfersVM(hub=MessageHub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        for index in range(110):
            vm.register(
                TransferModel(
                    str(index),
                    "upload",
                    "local",
                    "s3://fixture",
                    1,
                    1,
                    TransferState.FAILED,
                    "secret_access_key=synthetic",
                )
            )
        vm.register(
            TransferModel(
                "active", "download", "s3://fixture", "local", 0, 10, TransferState.RUNNING
            )
        )
        scope = WatchScope("transfers", "session", "local", "")
        page = project_session_transfers(scope, vm)
        assert len(vm.finished) == 100
        assert len(page.entries) == 101
        assert page.entries[0].identifier == "active"
        assert all(entry.times == () for entry in page.entries)
        assert all("synthetic" not in (entry.error_summary or "") for entry in page.entries)
        assert page.cap is not None
        assert "Session-local" in page.cap
        assert "100 finished" in page.cap
        assert "durable" in page.cap
    finally:
        vm.dispose()


def test_active_projection_cap_does_not_change_retained_transfers() -> None:
    vm = TransfersVM(hub=MessageHub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        for index in range(105):
            vm.register(
                TransferModel(
                    str(index), "upload", "local", "s3://fixture", 0, 10, TransferState.RUNNING
                )
            )
        page = project_session_transfers(WatchScope("transfers", "session", "local", ""), vm)
        assert len(vm.active) == 105
        assert len(page.entries) == 100
        assert "100 active" in page.cap
    finally:
        vm.dispose()
