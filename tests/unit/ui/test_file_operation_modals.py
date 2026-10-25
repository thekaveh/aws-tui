"""File-operation prompts return intent without performing filesystem work."""

from typing import ClassVar

import pytest
from textual.app import App
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input, Static


class Host(App):
    result = "pending"

    def done(self, result):
        self.result = result


class ForwardingHost(Host):
    """Exercise the same priority forwarding entry points as AwsTuiApp."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "modal_commit", priority=True),
        Binding("tab", "modal_next", priority=True),
        Binding("shift+tab", "modal_prev", priority=True),
        Binding("escape", "modal_cancel", priority=True),
    ]

    def action_modal_commit(self):
        self.screen.action_commit_focused()

    def action_modal_next(self):
        self.screen.action_focus_next()

    def action_modal_prev(self):
        self.screen.action_focus_prev()

    def action_modal_cancel(self):
        self.screen.action_cancel()


@pytest.mark.asyncio
async def test_name_prompt_rejects_paths_then_returns_literal_basename():
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    app = Host()
    async with app.run_test() as pilot:
        modal = FileNameModal(title="New folder", location="local · /synthetic", initial="")
        await app.push_screen(modal, app.done)
        field = modal.query_one(Input)
        field.value = "../escape"
        await pilot.press("enter")
        assert app.screen is modal
        assert app.result == "pending"
        assert "single path segment" in str(modal.query_one("#file-name-error", Static).render())
        field.value = " 日本語 folder "
        await pilot.press("enter")
        assert app.result == " 日本語 folder "


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["move", "rename"])
async def test_move_review_returns_only_the_explicit_choice(operation):
    from aws_tui.ui.widgets.file_operation_modals import MoveReviewModal

    app = Host()
    async with app.run_test(size=(80, 24)) as pilot:
        modal = MoveReviewModal(
            source_identity="AWS · east",
            destination_identity="Local",
            source_paths=("/bucket/[bold]literal",),
            destination_path="/synthetic/destination",
        )
        await app.push_screen(modal, app.done)
        text = str(modal.query_one("#file-move-review", Static).render())
        assert "[bold]literal" in text
        assert "1 item" in text
        assert "ERROR" in text
        assert "AWS · east" in text
        assert "Local" in text
        assert await pilot.click(f"#file-operation-{operation}")
        assert app.result == operation


@pytest.mark.asyncio
async def test_batch_review_cannot_rename_and_cancel_returns_no_intent():
    from aws_tui.ui.widgets.file_operation_modals import MoveReviewModal
    from aws_tui.ui.widgets.modal_button import ModalButton

    app = Host()
    async with app.run_test() as pilot:
        modal = MoveReviewModal(
            source_identity="source",
            destination_identity="destination",
            source_paths=("/a", "/b"),
            destination_path="/target",
        )
        await app.push_screen(modal, app.done)
        assert modal.query_one("#file-operation-rename", ModalButton).disabled
        await pilot.press("escape")
        assert app.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\\b", "a\0b", "a\nb", "a\x7fb"])
async def test_name_prompt_shows_inline_error_without_closing(name):
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal

    app = ForwardingHost()
    async with app.run_test() as pilot:
        modal = FileNameModal(title="Rename [literal]", location="local · /[literal]", initial="")
        await app.push_screen(modal, app.done)
        modal.query_one(Input).value = name
        await pilot.press("enter")
        assert app.screen is modal
        assert app.result == "pending"
        assert str(modal.query_one("#file-name-error", Static).render())


@pytest.mark.asyncio
async def test_name_prompt_forwarded_tab_can_cancel_and_accept_literal_value():
    from aws_tui.ui.widgets.file_operation_modals import FileNameModal
    from aws_tui.ui.widgets.modal_button import ModalButton

    app = ForwardingHost()
    async with app.run_test() as pilot:
        modal = FileNameModal(title="New folder", location="Local", initial=" [literal] ")
        await app.push_screen(modal, app.done)
        assert isinstance(app.focused, Input)
        await pilot.press("tab")
        assert isinstance(app.focused, ModalButton)
        assert app.focused.button_id == "review"
        await pilot.press("tab")
        assert app.focused.button_id == "cancel"
        await pilot.press("shift+tab", "enter")
        assert app.result == " [literal] "

        modal = FileNameModal(title="New folder", location="Local", initial="unused")
        await app.push_screen(modal, app.done)
        await pilot.press("shift+tab", "shift+tab", "enter")
        assert app.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_move_review_forwarded_keyboard_skips_disabled_rename(count):
    from aws_tui.ui.widgets.file_operation_modals import MoveReviewModal

    app = ForwardingHost()
    async with app.run_test() as pilot:
        modal = MoveReviewModal(
            source_identity="source",
            destination_identity="target",
            source_paths=tuple(f"/item-{i}" for i in range(count)),
            destination_path="/target",
        )
        await app.push_screen(modal, app.done)
        assert app.focused.button_id == "move"
        await pilot.press("tab")
        assert app.focused.button_id == ("rename" if count == 1 else "cancel")
        await pilot.press("enter")
        assert app.result == ("rename" if count == 1 else None)


@pytest.mark.asyncio
async def test_move_review_long_paths_scroll_with_visible_footer_at_80_by_24():
    from aws_tui.ui.widgets.file_operation_modals import MoveReviewModal
    from aws_tui.ui.widgets.modal_button import ModalButton

    paths = tuple(f"/source/{'long-directory/' * 12}entry-{i}[literal]" for i in range(20))
    destination = f"/destination/{'long-directory/' * 12}final[literal]"
    app = ForwardingHost()
    async with app.run_test(size=(80, 24)) as pilot:
        modal = MoveReviewModal(
            source_identity="source [literal]",
            destination_identity="target [literal]",
            source_paths=paths,
            destination_path=destination,
        )
        await app.push_screen(modal, app.done)
        await pilot.pause()
        review = modal.query_one("#file-move-review", Static)
        rendered = str(review.render())
        assert all(path in rendered for path in paths)
        assert destination in rendered
        assert "20 items" in rendered
        scroll = modal.query_one(VerticalScroll)
        assert scroll.max_scroll_y > 0
        await pilot.press("shift+tab")
        assert app.focused is scroll
        await pilot.press("end")
        await pilot.pause()
        assert scroll.scroll_y == scroll.max_scroll_y
        for button in modal.query(ModalButton):
            assert 0 <= button.region.y < button.region.bottom <= 24
            assert 0 <= button.region.x < button.region.right <= 80
        assert await pilot.click("#file-operation-cancel")
        assert app.result is None
