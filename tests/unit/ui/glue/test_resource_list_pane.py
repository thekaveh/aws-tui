from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import OptionList

from aws_tui.ui.widgets.glue.detail_rows import ResourceListPane
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.helpers import focus_and_settle, wait_until


class _ListApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.highlights: list[str | None] = []

    def compose(self) -> ComposeResult:
        yield ResourceListPane("tables", id="tables", empty_text="no tables")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.highlights.append(event.option.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_id", ["first", "second"])
async def test_projection_does_not_emit_selection_intent(selected_id: str) -> None:
    app = _ListApp()
    async with app.run_test() as pilot:
        pane = app.query_one(ResourceListPane)
        # Rendering must not enqueue a selection that can arrive after a newer
        # VM selection and replace it. Exercise initial and repeated projection.
        for state in (PaneState.IDLE, PaneState.LOADING, PaneState.IDLE):
            pane.replace(
                (("first", "First"), ("second", "Second")),
                selected_id=selected_id,
                state=state,
                error_text=None,
                has_more=False,
            )
            # Deliver all generated messages before asserting absence of intent.
            await pilot.pause()
            assert app.highlights == []
            if state is PaneState.IDLE:
                options = pane.option_list
                assert options.highlighted is not None
                assert options.get_option_at_index(options.highlighted).id == selected_id


@pytest.mark.asyncio
async def test_keyboard_highlight_still_emits_selection_intent() -> None:
    app = _ListApp()
    async with app.run_test() as pilot:
        pane = app.query_one(ResourceListPane)
        pane.replace(
            (("first", "First"), ("second", "Second")),
            selected_id="first",
            state=PaneState.IDLE,
            error_text=None,
            has_more=False,
        )
        # Isolate user input from projection messages in the unfixed baseline.
        await pilot.pause()
        app.highlights.clear()
        await focus_and_settle(pane.option_list)
        await pilot.press("down")
        await wait_until(
            lambda: app.highlights == ["second"],
            what="keyboard navigation to dispatch the second table selection",
        )
        # Deliver any extra messages before checking the exact dispatch count.
        await pilot.pause()
        assert app.highlights == ["second"]
