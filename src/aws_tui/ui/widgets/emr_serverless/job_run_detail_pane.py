# src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py
"""JobRunDetailPane — RIGHT pane of the EMR page.

PR-A renders the static detail (state, timings, IAM, entry point,
args, Spark params). PR-B adds the log surface below the KV table
as a child widget; PR-A leaves the bottom empty so PR-B's layout
slot is reserved."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_serverless import JobRunDetail
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_TERMINAL_GLYPH: dict[str, str] = {
    "SUCCESS": "✓",
    "FAILED": "✗",
    "CANCELLED": "⊘",
    "CANCELLING": "⊘",
    "RUNNING": "●",
    "PENDING": "⏸",
}


class JobRunDetailPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = """
    JobRunDetailPane {
        height: 1fr;
        layout: vertical;
    }
    JobRunDetailPane > VerticalScroll {
        height: 1fr;
    }
    JobRunDetailPane .detail-row {
        height: auto;
        padding: 0 1;
    }
    JobRunDetailPane .detail-key {
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "request_refresh", "Refresh"),
    ]

    class RefreshRequested(TextualMessage):
        pass

    def __init__(
        self,
        vm: JobRunDetailVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunDetailVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="detail-body")

    def on_mount(self) -> None:
        self.border_title = "detail"
        self._refresh()
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_request_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name in {"detail", "state"}:
            self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            body = self.query_one("#detail-body", VerticalScroll)
        except Exception:
            return
        body.remove_children()
        state = self._vm.state
        d = self._vm.detail
        if state is PaneState.LOADING:
            body.mount(Static("loading…", classes="detail-placeholder"))
            return
        if state is PaneState.UNREACHABLE:
            body.mount(
                Static(
                    self._vm.error_text or "endpoint unreachable — press r to retry",
                    classes="detail-placeholder",
                )
            )
            return
        if state is PaneState.AUTH_REQUIRED:
            body.mount(
                Static(
                    "authentication required — aws sso login --profile <X>",
                    classes="detail-placeholder",
                )
            )
            return
        if d is None:
            body.mount(Static("(no run selected)", classes="detail-placeholder"))
            return
        body.mount(Static(_format_kv("State", _state_label(d)), classes="detail-row"))
        body.mount(
            Static(
                _format_kv("Started", d.created_at.strftime("%Y-%m-%d %H:%M:%S")),
                classes="detail-row",
            )
        )
        body.mount(
            Static(
                _format_kv(
                    "Duration",
                    f"{d.duration_ms // 1000} s" if d.duration_ms is not None else "—",
                ),
                classes="detail-row",
            )
        )
        body.mount(Static(_format_kv("IAM", d.execution_role_arn or "—"), classes="detail-row"))
        body.mount(Static(_format_kv("Entry point", d.entry_point or "—"), classes="detail-row"))
        body.mount(
            Static(
                _format_kv(
                    "Args", " ".join(d.entry_point_arguments) if d.entry_point_arguments else "—"
                ),
                classes="detail-row",
            )
        )
        body.mount(
            Static(
                _format_kv("Spark", d.spark_submit_parameters or "—"),
                classes="detail-row",
            )
        )


def _state_label(d: JobRunDetail) -> str:
    glyph = _TERMINAL_GLYPH.get(d.state.value, "?")
    return f"{glyph} {d.state.value}"


def _format_kv(key: str, value: str) -> str:
    return f"{key:<12}  {value}"


__all__ = ["JobRunDetailPane"]
