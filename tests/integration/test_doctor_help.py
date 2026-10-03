"""Diagnostic help is reachable in the running app and uses its active paths."""

from __future__ import annotations

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static

from aws_tui.app import AwsTuiApp
from aws_tui.infra.log_sink import LogSink
from aws_tui.ui.widgets.help_modal import HelpModal
from tests.helpers import wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize("open_with_key", [False, True])
@pytest.mark.parametrize("unusual_paths", [False, True])
async def test_running_help_uses_literal_runtime_paths_and_keyboard_scroll(
    app_context_factory, tmp_path, open_with_key, unusual_paths
):
    ctx = app_context_factory()
    if unusual_paths:
        ctx.log_sink.close()
        ctx.log_sink = LogSink(
            base_dir=tmp_path / "[bold]cache[reset]\t\x07" / "[bold]log[reset]\n\x1b[31m"
        )
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(100, 24)) as pilot:
        if open_with_key:
            await pilot.press("question_mark")
        else:
            await app.action_help()
        await wait_until(lambda: isinstance(app.screen, HelpModal), what="mounted diagnostic help")
        await pilot.pause()
        rows = list(app.screen.query(Static))
        text = "\n".join(str(row.render()) for row in rows)
        assert "aws-tui doctor" in text
        for path in (ctx.log_sink.path, ctx.log_sink.path.parent.parent / "crash"):
            expected = str(path)
            if unusual_paths:
                expected = (
                    expected.replace("\n", "\\n")
                    .replace("\t", "\\t")
                    .replace("\x1b", "\\x1b")
                    .replace("\x07", "\\x07")
                )
            assert expected in text
        assert "\x1b" not in text
        assert "\x07" not in text
        body = app.screen.query_one(VerticalScroll)
        assert body.max_scroll_y > 0
        await pilot.press(*(["down"] * 70))
        await wait_until(
            lambda: body.scroll_y == body.max_scroll_y, what="diagnostic help at bottom"
        )
        diagnostic = next(row for row in rows if "aws-tui doctor" in str(row.render()))
        assert diagnostic.region.overlaps(body.region)
        await pilot.press("escape")
        await wait_until(lambda: not isinstance(app.screen, HelpModal), what="help dismissal")
