"""Service view factory tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.ui.widgets.service_view_factory import build_service_view
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.service_source_vm import ServiceSourceContext
from tests.unit.domain._in_memory_fs import InMemoryFS
from tests.unit.ui.athena.test_page import _build_vm as _build_athena_vm
from tests.unit.ui.emr_serverless.test_emr_page_pollers import _build_page
from tests.unit.ui.glue.test_page import _build_vm as _build_glue_vm

_SOURCE_CANDIDATES = (
    ServiceSourceContext("analytics-dev", "dev", "us-east-1"),
    ServiceSourceContext("analytics-prod", "prod", "us-west-2"),
)


def _build_dual_pane_vm(tmp_path: Path, hub: MessageHub[Message]) -> DualPaneVM:
    left = PaneVM(
        provider=InMemoryFS(),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        id_prefix="pane.left",
    )
    right = PaneVM(
        provider=InMemoryFS(),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        id_prefix="pane.right",
    )
    vm = DualPaneVM(
        left=left,
        right=right,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
    )
    vm.construct()
    return vm


def test_factory_builds_dual_pane_for_s3(tmp_path: Path) -> None:
    hub: MessageHub[Message] = MessageHub()
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)
    dual_pane_vm = _build_dual_pane_vm(tmp_path, hub)

    view = build_service_view(
        "s3",
        dual_pane_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
    )

    assert isinstance(view, DualPane)
    assert view.id == "content-dual-pane"


def test_factory_builds_emr_page() -> None:
    emr_page, emr_page_vm, _fake = _build_page()
    hub = emr_page._hub  # type: ignore[attr-defined]
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)

    view = build_service_view(
        "emr-serverless",
        emr_page_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
    )

    assert isinstance(view, EmrServerlessPage)
    assert view.id == "content-emr-page"


def test_factory_builds_glue_page() -> None:
    glue_page_vm, _fake = _build_glue_vm()
    hub = glue_page_vm.hub
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)

    view = build_service_view(
        "glue",
        glue_page_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
        source_candidates=_SOURCE_CANDIDATES,
    )

    assert isinstance(view, GluePage)
    assert view.id == "content-glue-page"
    assert view._source_candidates == _SOURCE_CANDIDATES  # type: ignore[attr-defined]


def test_factory_threads_keymap_to_glue_page() -> None:
    glue_page_vm, _fake = _build_glue_vm()
    hub = glue_page_vm.hub
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)
    keymap = KeymapStore(overlay={"glue.jobs": "8"})

    view = build_service_view(
        "glue",
        glue_page_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
        keymap=keymap,
    )

    assert isinstance(view, GluePage)
    assert view._keymap is keymap  # type: ignore[attr-defined]


def test_factory_builds_athena_page() -> None:
    athena_page_vm, _fake = _build_athena_vm()
    hub = athena_page_vm._hub  # type: ignore[attr-defined]
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)

    view = build_service_view(
        "athena",
        athena_page_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
        source_candidates=_SOURCE_CANDIDATES,
    )

    assert isinstance(view, AthenaPage)
    assert view.id == "content-athena-page"
    assert view._source_candidates == _SOURCE_CANDIDATES  # type: ignore[attr-defined]


def test_factory_rejects_unknown_service() -> None:
    hub: MessageHub[Message] = MessageHub()
    focus_coordinator = FocusCoordinatorVM(hub=hub, dispatcher=NULL_DISPATCHER)

    with pytest.raises(ValueError, match="unknown service view"):
        build_service_view("unknown", object(), hub=hub, focus_coordinator=focus_coordinator)
