"""HelpModal — read-only overlay listing keybindings, mouse, and docs.

Theme switching lives in its own keyboard-navigable
:class:`ThemePickerModal` (press ``t``) — keeps the help modal focused
on documentation and avoids cramming a stateful list inside a static
overlay.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import ClassVar
from unicodedata import category

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Static

from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.paths import cache_home
from aws_tui.infra.redaction import redact_text
from aws_tui.vm.chrome.action_catalog import (
    ActionPresentation,
    format_effective_keys,
    literal_display,
    scope_label,
)


class HelpActionRow(Static):
    """Literal action row shared with the palette's presentation."""

    action_id: str
    presentation: ActionPresentation

    def __init__(self, presentation: ActionPresentation) -> None:
        self.action_id = presentation.id
        self.presentation = presentation
        text = Text(format_effective_keys(presentation.effective_keys), style="bold")
        text.append("  ")
        text.append(literal_display(presentation.label))
        super().__init__(text, classes="help-row")


class HelpModal(ModalScreen[None]):
    """Help overlay listing keybindings, mouse, and docs."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > #help-frame {
        width: 78;
        max-height: 32;
        padding: 1 0;
    }
    HelpModal #help-title {
        text-style: bold;
        padding: 0 2 1 2;
        text-align: center;
        width: 100%;
    }
    HelpModal #help-subtitle {
        padding: 0 2 1 2;
        text-align: center;
        width: 100%;
    }
    HelpModal VerticalScroll {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    HelpModal #help-actions {
        height: auto;
    }
    HelpModal .help-section {
        text-style: bold;
        padding: 1 2 0 2;
    }
    HelpModal .help-row {
        padding: 0 2;
    }
    HelpModal .help-dim {
        padding: 0 2;
    }
    HelpModal #help-footer {
        padding: 1 2 0 2;
        text-align: center;
        width: 100%;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,question_mark,q,colon", "dismiss", "Close", show=True, priority=True),
    ]

    def __init__(
        self,
        *,
        actions: tuple[ActionPresentation, ...] = (),
        active_service_id: str | None = None,
        keymap: KeymapStore | None = None,
        log_path: Path | None = None,
        crash_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._actions = actions
        self._active_service_id = active_service_id
        self._keymap = keymap or KeymapStore()
        self._log_path = log_path if log_path is not None else cache_home() / "log" / "aws-tui.log"
        self._crash_path = crash_path if crash_path is not None else cache_home() / "crash"

    def compose(self) -> ComposeResult:
        with Vertical(id="help-frame"):
            yield Static("aws-tui — help", id="help-title")
            yield Static("keyboard · mouse · themes · docs", id="help-subtitle")
            with VerticalScroll():
                with Vertical(id="help-actions"):
                    yield from self._action_widgets()

                yield Static("Mouse / Trackpad", classes="help-section")
                yield self._key_row("Click pane", "switch focus to it")
                yield self._key_row("Click row", "move cursor")
                yield self._key_row("Click again", "descend / ascend on '..'")
                yield self._key_row("Scroll wheel", "scroll pane content")

                yield Static("Docs", classes="help-section")
                yield Static(
                    "  https://thekaveh.github.io/aws-tui/connections/\n"
                    "  https://thekaveh.github.io/aws-tui/theming/\n"
                    "  https://thekaveh.github.io/aws-tui/keybindings/\n"
                    "  https://thekaveh.github.io/aws-tui/cookbook/",
                    classes="help-dim",
                )
                yield Static("Diagnostics", classes="help-section")
                yield Static(
                    "  aws-tui doctor — offline setup checks\n"
                    "  aws-tui doctor --json\n"
                    "  aws-tui doctor --probe NAME — explicit read-only access check",
                    classes="help-dim",
                    markup=False,
                )
                yield Static(
                    f"  Log file: {self._display_path(self._log_path)}\n"
                    f"  Crash directory: {self._display_path(self._crash_path)}",
                    classes="help-dim",
                    markup=False,
                )
            yield Static(Text("press ? / Esc to close"), id="help-footer")

    def action_move_up(self) -> None:
        self._scroll_body(-1)

    @staticmethod
    def _display_path(path: Path) -> str:
        """Keep path punctuation literal and escape terminal controls."""
        return redact_text(
            "".join(
                ascii(char)[1:-1] if category(char).startswith("C") else char for char in str(path)
            )
        )

    def action_move_down(self) -> None:
        self._scroll_body(1)

    def _scroll_body(self, delta: int) -> None:
        """Scroll the help body by one line.

        The App binds ↑/↓ with ``priority=True``, so the keys never reach
        this screen's own scroll handling; the App forwards them here
        instead. Without these handlers the body could only be scrolled
        with PageDown/End or the mouse wheel, and everything past the
        fold — the whole App section and the docs links — was
        unreachable from the keyboard on any terminal short enough to
        clip it, which is every supported size.
        """
        with suppress(NoMatches):
            body = self.query_one(VerticalScroll)
            if delta < 0:
                body.scroll_up(animate=False)
            else:
                body.scroll_down(animate=False)

    def _action_widgets(self) -> ComposeResult:
        groups: dict[tuple[str, str], list[ActionPresentation]] = {}
        for action in self._actions:
            if action.available:
                group = (scope_label(action.service_ids, self._active_service_id), action.category)
                groups.setdefault(group, []).append(action)
        for (scope, action_category), actions in groups.items():
            yield Static(Text(f"{scope} — {action_category}"), classes="help-section")
            for action in actions:
                yield HelpActionRow(action)

    def update_actions(self, actions: tuple[ActionPresentation, ...]) -> None:
        if actions == self._actions:
            return
        self._actions = actions
        self.call_after_refresh(self._replace_action_widgets)

    async def _replace_action_widgets(self) -> None:
        if not self.is_mounted:
            return
        body = self.query_one(VerticalScroll)
        offset = body.scroll_offset
        container = self.query_one("#help-actions", Vertical)
        await container.remove_children()
        await container.mount(*self._action_widgets())
        body.scroll_to(offset.x, offset.y, animate=False)

    def _key_row(self, key: str, label: str) -> Static:
        text = Text(key, style="bold")
        text.append("  ")
        text.append(label, style="dim")
        return Static(text, classes="help-row")


__all__ = ["HelpActionRow", "HelpModal"]
