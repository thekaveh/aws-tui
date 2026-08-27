from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static

from aws_tui.ui.widgets.athena.load_more_button import AthenaLoadMoreButton
from aws_tui.ui.widgets.glue.detail_rows import ResourceListPane
from aws_tui.vm.file_manager.pane_vm import PaneState


class _ListApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ResourceListPane("items", id="items", empty_text="No items")


async def test_resource_list_footer_surfaces_safety_limit() -> None:
    async with _ListApp().run_test() as pilot:
        pane = pilot.app.query_one(ResourceListPane)
        pane.replace(
            (("one", "one"),),
            selected_id="one",
            state=PaneState.IDLE,
            error_text=None,
            has_more=False,
            limit_reached=True,
        )

        footer = pane.query_one(".glue-list-footer", Static)
        assert "1 item · safety limit" in str(footer.render())


def test_athena_load_more_button_surfaces_safety_limit() -> None:
    button = AthenaLoadMoreButton(id="more", tooltip="Load more")

    button.sync(
        has_more=False,
        busy=False,
        state=PaneState.IDLE,
        error_text=None,
        limit_reached=True,
    )

    assert button.display
    assert button.disabled
    assert str(button.label) == "!"
    assert "safety limit" in str(button.tooltip)
