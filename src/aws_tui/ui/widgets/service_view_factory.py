"""Build the content widget for a registered service view."""

from __future__ import annotations

from typing import Any

from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM


def build_service_view(
    service_id: str,
    vm: Any,
    *,
    hub: MessageHub[Message],
    focus_coordinator: FocusCoordinatorVM,
    dual_pane_class: type[DualPane] = DualPane,
    emr_page_class: type[EmrServerlessPage] = EmrServerlessPage,
    glue_page_class: type[GluePage] = GluePage,
) -> Widget:
    if service_id == "s3":
        return dual_pane_class(
            vm,
            hub=hub,
            focus_coordinator=focus_coordinator,
            id="content-dual-pane",
        )
    if service_id == "emr-serverless":
        return emr_page_class(
            vm,
            hub=hub,
            focus_coordinator=focus_coordinator,
            id="content-emr-page",
        )
    if service_id == "glue":
        return glue_page_class(
            vm,
            hub=hub,
            focus_coordinator=focus_coordinator,
            id="content-glue-page",
        )
    raise ValueError(f"unknown service view: {service_id}")


__all__ = ["build_service_view"]
