"""Transient, explicitly applied per-query result reuse controls."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.dom import DOMNode
from textual.events import Click
from textual.widgets import Input, Static, Switch

from aws_tui.domain.filesystem import ValidationError
from aws_tui.ui.widgets.athena.authoring_dialogs import _SqlPrompt
from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.athena.query_vm import AthenaQueryVM


class QueryOptionsModal(_SqlPrompt):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS: ClassVar[str] = """
    QueryOptionsModal { align: center middle; background: $background 60%; }
    QueryOptionsModal > Vertical { width: 68; max-width: 96%; height: auto; max-height: 90%; padding: 1 2; }
    QueryOptionsModal VerticalScroll { height: auto; max-height: 13; }
    QueryOptionsModal Static { height: auto; }
    QueryOptionsModal Horizontal { height: 3; align-horizontal: right; }
    QueryOptionsModal ModalButton { min-width: 10; margin-left: 1; }
    QueryOptionsModal #athena-reuse-error { color: $error; }
    """

    def __init__(self, vm: AthenaQueryVM, *, current: Callable[[], bool]) -> None:
        super().__init__()
        self._vm = vm
        self._current = current

    def compose(self) -> ComposeResult:
        reason = self._vm.reuse_unavailable_reason
        with Vertical(classes="modal-frame"):
            yield Static("Query result reuse", classes="modal-title")
            with VerticalScroll():
                yield Static("Reuse eligible previous results", markup=False)
                yield Switch(
                    self._vm.reuse_enabled, id="athena-reuse-enabled", disabled=reason is not None
                )
                yield Static("Maximum age in minutes (0 through 10080)", markup=False)
                yield Input(str(self._vm.reuse_max_age_minutes), id="athena-reuse-age")
                yield Static(
                    reason
                    or "AWS decides eligibility; results may be stale. LIMIT without ORDER BY is not cached.",
                    id="athena-reuse-reason",
                    markup=False,
                )
                yield Static("", id="athena-reuse-error", markup=False)
            with Horizontal(classes="modal-footer"):
                for label, key in (("Cancel", "cancel"), ("Apply", "apply")):
                    button = ModalButton(
                        label, button_id=key, classes="-primary" if key == "apply" else ""
                    )
                    button.id = f"athena-reuse-{key}"
                    yield button

    def on_click(self, event: Click) -> None:
        node: DOMNode | None = event.widget
        while node is not None:
            if isinstance(node, ModalButton):
                event.stop()
                if node.button_id == "cancel":
                    self.action_cancel()
                elif node.button_id == "apply":
                    self._apply()
                return
            node = node.parent

    def _apply(self) -> None:
        error = self.query_one("#athena-reuse-error", Static)
        if not self._current() or self._invalidated:
            error.update("The query context changed. Reopen options for the selected workgroup.")
            return
        try:
            age = int(self.query_one("#athena-reuse-age", Input).value)
            self._vm.set_reuse_options(
                enabled=self.query_one("#athena-reuse-enabled", Switch).value,
                max_age_minutes=age,
            )
        except ValueError:
            error.update("Reuse age must be an integer from 0 through 10080 minutes")
            return
        except ValidationError as exc:
            error.update(str(exc))
            return
        self.dismiss("applied")

    def action_cancel(self) -> None:
        self.dismiss(None)
