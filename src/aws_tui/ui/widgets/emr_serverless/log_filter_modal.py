"""LogFilterModal — edit log filter patterns and settings.

A push-screen modal for editing the active LogFilter: a TextArea with
one regex pattern per line, a "Show all" switch (sets mode=PASSTHROUGH),
a "Match case" switch, and three buttons (Apply / Reset to defaults / Cancel).
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static, Switch, TextArea

from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, FilterMode, LogFilter
from aws_tui.ui.widgets.modal_button import ModalButton as _ModalButton


class LogFilterModal(ModalScreen[LogFilter | None]):
    """Modal form for editing a LogFilter.

    Dismiss values: a new LogFilter on Apply, or None on Cancel.
    Reset to defaults repopulates the form without dismissing.
    """

    DEFAULT_CSS: ClassVar[str] = """
    LogFilterModal > Container {
        width: 80;
        max-width: 90%;
        height: auto;
        padding: 1 2;
    }
    LogFilterModal TextArea {
        margin-bottom: 1;
        height: 7;
    }
    LogFilterModal .modal-field-label {
        margin-top: 1;
    }
    LogFilterModal .switch-row {
        margin-bottom: 1;
        height: auto;
    }
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, current: LogFilter) -> None:
        super().__init__()
        self._current: LogFilter = current

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Edit log filter", classes="modal-title")

            yield Static("Regex patterns (one per line)", classes="modal-field-label")
            yield TextArea(
                "\n".join(self._current.patterns),
                id="log-patterns",
            )
            # Inline regex-validation error slot; hidden until the
            # user presses Apply with a bad pattern. See
            # ``_show_validation_error``.
            err = Static("", id="log-patterns-error", classes="modal-field-error")
            err.styles.display = "none"
            yield err

            with Horizontal(classes="switch-row"):
                yield Static("Show all", classes="modal-field-label")
                yield Switch(
                    value=(self._current.mode == FilterMode.PASSTHROUGH),
                    id="show-all-switch",
                )

            with Horizontal(classes="switch-row"):
                yield Static("Match case", classes="modal-field-label")
                yield Switch(
                    value=(not self._current.case_insensitive),
                    id="match-case-switch",
                )

            with Horizontal(classes="modal-footer"):
                yield _ModalButton("Cancel", button_id="cancel")
                yield _ModalButton("Reset to defaults", button_id="reset")
                yield _ModalButton("Apply", button_id="apply", classes="-primary")

    # ── Form-state sync ────────────────────────────────────────────────────

    def _build_filter(self) -> LogFilter:
        """Build a LogFilter from the current form state.

        Split patterns on newlines, strip whitespace, drop empty lines.
        """
        textarea = self.query_one("#log-patterns", TextArea)
        patterns_text = textarea.text
        patterns = tuple(line.strip() for line in patterns_text.splitlines() if line.strip())

        show_all_switch = self.query_one("#show-all-switch", Switch)
        mode = FilterMode.PASSTHROUGH if show_all_switch.value else FilterMode.MATCH

        match_case_switch = self.query_one("#match-case-switch", Switch)
        case_insensitive = not match_case_switch.value

        return LogFilter(
            patterns=patterns,
            mode=mode,
            case_insensitive=case_insensitive,
        )

    # ── Actions ────────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        # ``LogFilter.__post_init__`` validates every regex eagerly.
        # If the user typed an invalid pattern (e.g. ``(``,
        # ``[abc``), surface the error inline and keep the modal
        # open so they can fix it — without this the modal would
        # close, the bad filter would reach the stream loop, and
        # the pane would land in opaque ``LogsState.ERROR``.
        try:
            new_filter = self._build_filter()
        except ValueError as exc:
            self._show_validation_error(str(exc))
            return
        self.dismiss(new_filter)

    def _show_validation_error(self, message: str) -> None:
        """Render the regex-validation message under the TextArea.

        Looks up the placeholder Static added in compose(); falls
        back to a no-op if not found (defensive — the modal should
        always have it but tests sometimes mount a stripped-down
        variant)."""
        try:
            err = self.query_one("#log-patterns-error", Static)
        except Exception:
            return
        err.update(f"⚠ {message}")
        err.styles.display = "block"

    def action_reset(self) -> None:
        """Reset form to DEFAULT_LOG_FILTER without dismissing."""
        self.query_one("#log-patterns", TextArea).text = "\n".join(DEFAULT_LOG_FILTER.patterns)
        self.query_one("#show-all-switch", Switch).value = (
            DEFAULT_LOG_FILTER.mode == FilterMode.PASSTHROUGH
        )
        self.query_one("#match-case-switch", Switch).value = not DEFAULT_LOG_FILTER.case_insensitive

    async def on_click(self, event: Click) -> None:
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, _ModalButton):
                if node.button_id == "apply":
                    self.action_apply()
                elif node.button_id == "reset":
                    self.action_reset()
                else:
                    self.action_cancel()
                return
            node = getattr(node, "parent", None)


__all__ = ["LogFilterModal"]
