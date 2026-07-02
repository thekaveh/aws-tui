"""Round-3 compliance pins: each migrated/verified VM composes
a VMx primitive (ComponentVM, CompositeVM, FormVM) internally
without exposing it in its public surface.

This file is intentionally cross-cutting: it walks every VM the
round-3 directive (spec §9.bis.11) covers and asserts the
composition pattern is in place. Per-VM behavior tests live in
their own files; this is the shape contract.
"""

from __future__ import annotations

from datetime import UTC

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message


def _hub() -> MessageHub[Message]:
    return MessageHub()


# -------------------- Chrome VMs --------------------


def test_confirmation_vm_composes_componentvm_internally() -> None:
    from aws_tui.vm.chrome.confirm_vm import ConfirmationVM

    vm = ConfirmationVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    # _inner is the composed VMx primitive (ComponentVM).
    assert hasattr(vm, "_inner")
    # Public surface exposes a status/name proxy, NOT the inner VM
    # itself.
    assert hasattr(vm, "status")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


def test_crash_vm_composes_componentvm_internally() -> None:
    from datetime import datetime
    from pathlib import Path

    from aws_tui.vm.chrome.crash_vm import CrashReport, CrashVM

    report = CrashReport(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        exception_type="RuntimeError",
        exception_message="boom",
        traceback_short="",
        dump_path=Path("/tmp/x"),
        can_continue=False,
    )
    vm = CrashVM(report=report, hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_inner")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


def test_resume_vm_composes_componentvm_internally() -> None:
    from aws_tui.vm.chrome.resume_vm import ResumeVM

    vm = ResumeVM([], hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_inner")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


def test_first_run_vm_composes_componentvm_internally() -> None:
    from aws_tui.vm.chrome.first_run_vm import FirstRunVM

    vm = FirstRunVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_inner")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


def test_toast_stack_vm_composes_compositevm_internally() -> None:
    from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM

    vm = ToastStackVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_inner")
    # The inner is a CompositeVM — not exposed publicly.
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


def test_focus_coordinator_vm_composes_componentvm_internally() -> None:
    from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM

    vm = FocusCoordinatorVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    vm.construct()
    assert hasattr(vm, "_inner")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


def test_command_palette_vm_composes_scored_filter_internally() -> None:
    from vmx import ScoredFilteredCompositeVM

    from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM

    vm = CommandPaletteVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_scored_filter")
    assert isinstance(vm._scored_filter, ScoredFilteredCompositeVM)
    assert not any("scored_filter" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


# -------------------- Settings VMs --------------------


def test_s3_connection_form_vm_composes_vmx_form_vm_internally() -> None:
    from vmx import FormVM

    from aws_tui.vm.chrome.first_run_vm import S3CompatForm
    from aws_tui.vm.settings.s3_connection_form_vm import S3ConnectionFormVM

    async def _persist(_m: S3CompatForm) -> None:
        pass

    blank = S3CompatForm(
        name="",
        endpoint_url="",
        region="",
        access_key_id="",
        secret_access_key="",
    )
    vm = S3ConnectionFormVM(initial=blank, persister=_persist)
    assert hasattr(vm, "_inner")
    assert isinstance(vm._inner, FormVM)
    # The VMx FormVM is composed internally and not exposed publicly.
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


# -------------------- File manager VMs --------------------


def test_transfers_vm_composes_compositevm_internally() -> None:
    from aws_tui.vm.file_manager.transfers_vm import TransfersVM

    vm = TransfersVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_inner")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()


# -------------------- Theme picker (composed but via facade VMs) --------------------


def test_theme_picker_vm_composes_compositevm_internally() -> None:
    from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM

    vm = ThemePickerVM(
        themes=("amber", "nord"),
        active_theme="amber",
        on_pick=lambda _: None,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    assert hasattr(vm, "_inner")
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()
