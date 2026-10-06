"""Integration: Space opens the Quick Look preview modal for the cursor file."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.ui.widgets.pane import EntryRow
from aws_tui.ui.widgets.quick_look import QuickLook
from tests.helpers import wait_until
from tests.integration.conftest import AppContextBuilder


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _inject_connection(ctx: object) -> None:
    ctx.config_store.path.write_text(  # type: ignore[attr-defined]
        '[defaults]\nconnection = "test"\n\n'
        "[connections.test]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9000"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
        'region = "us-east-1"\n'
    )


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"hello quick look"))
    return fs


@pytest.mark.asyncio
async def test_space_opens_quick_look(app_context_factory: AppContextBuilder) -> None:
    ctx = app_context_factory(fs=await _seed())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
        async with asyncio.timeout(5.0):
            while not list(app.query(EntryRow)):
                await pilot.pause(0.01)

        await pilot.press("space")
        await wait_until(
            lambda: isinstance(app.screen, QuickLook),
            what="Quick Look modal to open",
        )

        assert isinstance(app.screen, QuickLook)
        content = app.screen.vm.content
        assert content is not None
        assert content.title == "alpha.txt"
        assert app._crash_report is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quick_look_noop_when_no_file(app_context_factory: AppContextBuilder) -> None:
    # Empty FS -> the pane has no cursor entry -> Space is a no-op (no modal,
    # no crash).
    ctx = app_context_factory(fs=InMemoryFS())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()

        app.action_quick_look()
        # Deliver action callbacks before checking that no modal or crash appeared.
        await pilot.pause()

        assert not isinstance(app.screen, QuickLook)
        assert app._crash_report is None  # type: ignore[attr-defined]


class RecordingFS(InMemoryFS):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.closed = asyncio.Event()
        self.reading = asyncio.Event()
        self.body_closed = asyncio.Event()
        self.block = False
        self.writes = 0

    async def write_stream(self, *args, **kwargs):
        self.writes += 1
        return await super().write_stream(*args, **kwargs)

    async def open_preview(self, path, *, budget):
        self.requests.append(("open", path))
        session = await super().open_preview(path, budget=budget)
        fs = self

        class Session:
            snapshot = session.snapshot

            async def read_range(self, offset, length):
                fs.requests.append(("range", offset, length))
                fs.reading.set()
                if fs.block:

                    async def body():
                        try:
                            await asyncio.Event().wait()
                            yield b"unreachable"
                        finally:
                            fs.body_closed.set()

                    source = body()
                    try:
                        return await anext(source)
                    finally:
                        await source.aclose()
                return await session.read_range(offset, length)

            async def validate(self):
                fs.requests.append(("validate",))
                await session.validate()

            async def aclose(self):
                await session.aclose()
                fs.closed.set()

        return Session()


async def _open_file(app, pilot):
    await wait_until(
        lambda: (
            app._dual_pane() is not None
            and app._dual_pane().focused_pane.selected_entry is not None
            and not app._dual_pane().focused_pane.selected_entry.is_parent_link
        ),
        what="file cursor ready",
    )
    app.action_quick_look()
    await wait_until(
        lambda: isinstance(app.screen, QuickLook) and app.screen.is_mounted, what="preview open"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("format_name", ["csv", "csv-bracket", "json", "parquet"])
async def test_actual_app_structured_toggle_cached_and_horizontal_scroll(
    app_context_factory, format_name
):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from rich.console import Console
    from textual.containers import ScrollableContainer
    from textual.widgets import Static

    # A 256-column literal cell is wide enough to put the final marker offscreen.
    wide = "W" * 230 + "offscreen-value"
    header = "[red]sample_id" if format_name == "csv-bracket" else "sample_id[red]"
    row = {header: "structured-preview-row", "wide": wide}
    if format_name.startswith("csv"):
        raw = (header + ",wide\nstructured-preview-row," + wide + "\n").encode()
    elif format_name == "json":
        row.update(empty="", null=None, nested={"x": 1}, escaped="\x1b[red]", cut="C" * 300)
        raw = json.dumps([row, {"wide": wide}]).encode()
    else:
        sink = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist([row]), sink)
        raw = sink.getvalue().to_pybytes()
    fs = RecordingFS()
    await fs.write_stream(PathRef((f"sample.{format_name.split('-')[0]}",)), _stream(raw))
    writes = fs.writes
    ctx = app_context_factory(fs=fs)
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        await wait_until(lambda: fs.closed.is_set(), what="preview session drained")
        await pilot.pause()
        screen = app.screen
        assert screen.vm.content.load_preview is not None
        mode = screen.query_one("#quicklook-mode", Static)
        assert "Structured" in str(mode.render())
        console = Console(width=1000, force_terminal=False)
        with console.capture() as capture:
            console.print(screen.query_one("#quicklook-body", Static).content)
        rendered = capture.get()
        assert header in rendered
        assert "structured-preview-row" in rendered
        assert "offscreen-value" in rendered
        if format_name == "json":
            for needle in ('""', "null", '{"x":1}', r"\x1b[red]", "… [truncated]", "— (missing)"):
                assert needle in rendered
            assert "\x1b" not in rendered
        before = tuple(fs.requests)
        await pilot.press("r")
        await pilot.pause()
        assert "Raw" in str(mode.render())
        await pilot.press("r")
        await pilot.pause()
        assert "Structured" in str(mode.render())
        assert tuple(fs.requests) == before
        scroll = screen.query_one("#quicklook-body-scroll", ScrollableContainer)
        assert scroll.max_scroll_x > 0
        await pilot.press("right")
        await pilot.pause()
        assert scroll.scroll_x > 0
        await pilot.press("left")
        await pilot.pause()
        assert scroll.scroll_x == 0
        assert fs.writes == writes
        assert app._crash_report is None


@pytest.mark.asyncio
async def test_actual_app_close_cancels_blocked_read_and_drains_session(app_context_factory):
    fs = RecordingFS()
    await fs.write_stream(PathRef(("sample.csv",)), _stream(b"a,b\n1,2\n"))
    writes = fs.writes
    fs.block = True
    ctx = app_context_factory(fs=fs)
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        screen = app.screen
        await wait_until(fs.reading.is_set, what="blocked preview read")
        await pilot.press("escape")
        await wait_until(fs.closed.is_set, what="closed preview session")
        await wait_until(lambda: not screen.is_attached, what="preview screen unmounted")
        assert not screen.is_attached
        assert not screen.vm.is_open
        assert screen._result is None
        assert screen._hub_subscription is None
        assert fs.requests[-1][0] == "range"
        assert fs.body_closed.is_set()
        assert fs.writes == writes
        assert app._crash_report is None


@pytest.mark.asyncio
async def test_actual_app_replacement_reaps_blocked_decoder(app_context_factory, monkeypatch):
    import sys

    from textual.widgets import Static

    from aws_tui.domain import _parquet_preview_worker as worker
    from aws_tui.vm.chrome.quick_look_vm import QuickLookContent

    children = []
    real_spawn = asyncio.create_subprocess_exec

    async def hanging_spawn(*args, **kwargs):
        process = await real_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", hanging_spawn)
    fs = RecordingFS()
    await fs.write_stream(PathRef(("sample.parquet",)), _stream(b"PAR1fake\x04\x00\x00\x00PAR1"))
    writes = fs.writes
    ctx = app_context_factory(fs=fs)
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await _open_file(app, pilot)
            screen = app.screen
            await wait_until(lambda: bool(children), what="decoder child running")
            replacement = QuickLookContent(
                "replacement.txt", "text/plain", _stream(b"replacement-body"), None
            )
            screen.vm.open_command.execute(replacement)
            await wait_until(lambda: children[0].returncode is not None, what="decoder reaped")
            await wait_until(fs.closed.is_set, what="decoder provider session closed")
            await wait_until(
                lambda: (
                    "replacement-body" in str(screen.query_one("#quicklook-body", Static).render())
                ),
                what="replacement body loaded",
            )
            assert screen.vm.content is replacement
            assert screen._result.raw == b"replacement-body"
            assert children[0].stdin.is_closing()
            assert children[0].stdout.at_eof()
            assert fs.writes == writes
            assert app._crash_report is None
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_error", [False, True])
async def test_actual_app_stale_loader_cannot_publish_after_replacement(
    app_context_factory, late_error
):
    from textual.widgets import Static

    from aws_tui.domain.preview import PreviewFormat, PreviewResult
    from aws_tui.vm.chrome.quick_look_vm import QuickLookContent

    ctx = app_context_factory(fs=await _seed())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    started, cancelled, release, drained = (asyncio.Event() for _ in range(4))

    async def stale_loader():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        finally:
            drained.set()
        if late_error:
            raise RuntimeError("stale-error")
        return PreviewResult(PreviewFormat.RAW, b"stale-success", (), ())

    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        screen = app.screen
        first = QuickLookContent("old", "text/plain", _stream(b"unused"), None, stale_loader)
        screen.vm.open_command.execute(first)
        try:
            await wait_until(started.is_set, what="old loader entered")
            second = QuickLookContent("new", "text/plain", _stream(b"fresh-body"), None)
            screen.vm.open_command.execute(second)
            await wait_until(cancelled.is_set, what="old loader cancelled")
        finally:
            release.set()
        await wait_until(drained.is_set, what="old loader drained")
        await wait_until(lambda: screen._result is not None, what="fresh result loaded")
        await pilot.pause()
        body = str(screen.query_one("#quicklook-body", Static).render())
        assert "fresh-body" in body
        assert "stale" not in body
        assert screen.vm.content is second
        assert screen._result.raw == b"fresh-body"


@pytest.mark.asyncio
async def test_actual_app_legacy_iterator_closes_on_replacement(app_context_factory):
    from aws_tui.vm.chrome.quick_look_vm import QuickLookContent

    entered, closed = asyncio.Event(), asyncio.Event()

    async def blocked_chunks():
        try:
            yield b"prefix"
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    ctx = app_context_factory(fs=await _seed())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        screen = app.screen
        screen.vm.open_command.execute(
            QuickLookContent("old", "text/plain", blocked_chunks(), None)
        )
        await wait_until(entered.is_set, what="legacy iterator blocked")
        screen.vm.open_command.execute(
            QuickLookContent("new", "text/plain", _stream(b"fresh"), None)
        )
        await wait_until(closed.is_set, what="legacy iterator closed")
        await wait_until(lambda: screen._result is not None, what="replacement loaded")
        assert screen._result.raw == b"fresh"
        assert app._crash_report is None


@pytest.mark.asyncio
@pytest.mark.parametrize("excessive", [False, True])
async def test_actual_app_direct_results_are_literal_and_render_bounded(
    app_context_factory, excessive
):
    from rich.console import Console
    from textual.widgets import Static

    from aws_tui.domain.preview import (
        PreviewCell,
        PreviewCellKind,
        PreviewColumn,
        PreviewFormat,
        PreviewResult,
    )
    from aws_tui.domain.preview_limits import PREVIEW_MAX_RENDER_CHARS
    from aws_tui.vm.chrome.quick_look_vm import QuickLookContent

    if excessive:
        result = PreviewResult(
            PreviewFormat.JSON,
            b"bounded-raw",
            tuple(PreviewColumn("c") for _ in range(24)),
            tuple(
                tuple(PreviewCell("x" * 256, PreviewCellKind.SCALAR) for _ in range(24))
                for _ in range(50)
            ),
        )
    else:
        result = PreviewResult(
            PreviewFormat.PARQUET,
            b"raw\x1b[red]\n\t[bold]",
            (PreviewColumn("[red]name", "[bold]type\x1b"),),
            ((PreviewCell("[red]literal\x1b", PreviewCellKind.SCALAR),),),
            ("[red]note\x1b",),
        )

    async def loader():
        return result

    ctx = app_context_factory(fs=await _seed())
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        screen = app.screen
        content = QuickLookContent(
            "[red]title\x1b", "application/octet-stream", _stream(b"unused"), None, loader
        )
        screen.vm.open_command.execute(content)
        await wait_until(lambda: screen._result is result, what="direct result rendered")
        await pilot.pause()
        console = Console(width=10000, force_terminal=False)
        with console.capture() as capture:
            console.print(screen.query_one("#quicklook-body", Static).content)
        text = capture.get()
        assert len(text) <= PREVIEW_MAX_RENDER_CHARS
        assert "\x1b" not in text
        if excessive:
            assert "bounded-raw" in text
            assert "Structured output exceeds preview budget" in text
        else:
            for needle in ("[red]name", "[bold]type", "[red]literal", "[red]note", r"\x1b"):
                assert needle in text
            title = screen.query_one("#quicklook-title", Static).content.plain
            assert title == r"[red]title\x1b"
            await pilot.press("r")
            await pilot.pause()
            raw = screen.query_one("#quicklook-body", Static).content.plain
            assert "\n\t[bold]" in raw
            assert r"raw\x1b[red]" in raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cell_text", "domain_admitted", "structured"),
    [
        pytest.param("a" + "\u0301" * 217, True, False, id="admitted-combining-over-cap"),
        pytest.param("a" + "\u0301" * 255, False, False, id="direct-combining-over-cap"),
        pytest.param("a" + "\u0301" * 199, True, True, id="admitted-combining-below-cap"),
        pytest.param("\u0301" * 218, True, False, id="admitted-zero-width-over-cap"),
        pytest.param("界" * 80, True, True, id="admitted-wide-below-cap"),
    ],
)
async def test_actual_app_unicode_render_budget_and_cached_toggle(
    app_context_factory, cell_text, domain_admitted, structured
):
    from rich.console import Console
    from textual.widgets import Static

    from aws_tui.domain.preview import (
        PreviewCell,
        PreviewCellKind,
        PreviewColumn,
        PreviewFormat,
        PreviewResult,
        _bounded_result,
    )
    from aws_tui.domain.preview_limits import PREVIEW_MAX_RENDER_CHARS
    from aws_tui.vm.chrome.quick_look_vm import QuickLookContent

    columns = tuple(PreviewColumn("c") for _ in range(24))
    rows = tuple(
        tuple(PreviewCell(cell_text, PreviewCellKind.SCALAR) for _ in columns) for _ in range(50)
    )
    raw = b"cached-unicode-raw"
    notes = ("combining-note\u0301",)
    if domain_admitted:
        result = _bounded_result(PreviewFormat.PARQUET, raw, columns, rows, notes)
        assert result.format is PreviewFormat.PARQUET
        assert result.columns == columns
        assert result.rows == rows
    else:
        result = PreviewResult(PreviewFormat.PARQUET, raw, columns, rows, notes)
    loader_calls = 0

    async def loader():
        nonlocal loader_calls
        loader_calls += 1
        return result

    fs = RecordingFS()
    await fs.write_stream(PathRef(("sample.csv",)), _stream(b"a,b\n1,2\n"))
    writes = fs.writes
    ctx = app_context_factory(fs=fs)
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        await wait_until(fs.closed.is_set, what="initial preview session drained")
        screen = app.screen
        screen.vm.open_command.execute(
            QuickLookContent(
                "unicode.parquet", "application/octet-stream", _stream(raw), None, loader
            )
        )
        await wait_until(lambda: screen._result is result, what="Unicode result loaded")
        await pilot.pause()
        before = tuple(fs.requests)
        console = Console(width=10000, force_terminal=False)
        mode = screen.query_one("#quicklook-mode", Static)
        for toggle in range(3):
            if toggle:
                await pilot.press("r")
                await pilot.pause()
            with console.capture() as capture:
                console.print(screen.query_one("#quicklook-body", Static).content)
            rendered = capture.get()
            assert len(rendered) <= PREVIEW_MAX_RENDER_CHARS
            if structured and toggle != 1:
                assert "Structured" in str(mode.render())
                assert cell_text in rendered
                assert "combining-note\u0301" in rendered
            else:
                assert "Raw" in str(mode.render())
                assert raw.decode() in rendered
                if not structured and toggle != 1:
                    assert "Structured output exceeds preview budget" in rendered
            assert loader_calls == 1
            assert tuple(fs.requests) == before
        assert fs.writes == writes
        assert app._crash_report is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cell_chars", "structured"),
    [
        pytest.param(204, False, id="near-cap-wrapped-notes"),
        pytest.param(190, True, id="below-cap-wrapped-notes"),
    ],
)
async def test_actual_app_wrapped_notes_render_budget_and_cached_toggle(
    app_context_factory, cell_chars, structured
):
    from rich.console import Console
    from textual.widgets import Static

    from aws_tui.domain.preview import (
        PreviewCell,
        PreviewCellKind,
        PreviewColumn,
        PreviewFormat,
        _bounded_result,
    )
    from aws_tui.domain.preview_limits import PREVIEW_MAX_RENDER_CHARS
    from aws_tui.vm.chrome.quick_look_vm import QuickLookContent

    columns = tuple(PreviewColumn("c") for _ in range(24))
    rows = tuple(
        tuple(
            PreviewCell(
                "a" + "\u0301" * (cell_chars - 1 + (23 if row < 16 and col == 0 else 0)),
                PreviewCellKind.SCALAR,
            )
            for col in range(24)
        )
        for row in range(50)
    )
    notes = tuple("n" * 256 for _ in range(50))
    raw = b"cached-wrapped-notes-raw"
    result = _bounded_result(PreviewFormat.PARQUET, raw, columns, rows, notes)
    assert result.format is PreviewFormat.PARQUET
    assert result.columns == columns
    assert result.rows == rows
    assert result.notes == notes
    loader_calls = 0

    async def loader():
        nonlocal loader_calls
        loader_calls += 1
        return result

    fs = RecordingFS()
    await fs.write_stream(PathRef(("sample.csv",)), _stream(b"a,b\n1,2\n"))
    writes = fs.writes
    ctx = app_context_factory(fs=fs)
    _inject_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_file(app, pilot)
        await wait_until(fs.closed.is_set, what="initial preview session drained")
        screen = app.screen
        screen.vm.open_command.execute(
            QuickLookContent(
                "notes.parquet", "application/octet-stream", _stream(raw), None, loader
            )
        )
        await wait_until(lambda: screen._result is result, what="wrapped-note result loaded")
        await pilot.pause()
        before = tuple(fs.requests)
        mode = screen.query_one("#quicklook-mode", Static)
        body = screen.query_one("#quicklook-body", Static)
        for toggle in range(3):
            if toggle:
                await pilot.press("r")
                await pilot.pause()
            assert body.size.width > 0
            console = Console(width=body.size.width, force_terminal=False)
            with console.capture() as capture:
                console.print(body.content)
            rendered = capture.get()
            assert len(rendered) <= PREVIEW_MAX_RENDER_CHARS
            if structured and toggle != 1:
                assert "Structured" in str(mode.render())
                assert console.width == 97
                assert rendered.count("n") == 50 * 256
                assert rendered.endswith("n" * 62 + "\n")
            else:
                assert "Raw" in str(mode.render())
                assert raw.decode() in rendered
                if not structured and toggle != 1:
                    assert "Structured output exceeds preview budget" in rendered
            assert loader_calls == 1
            assert tuple(fs.requests) == before
        assert fs.writes == writes
        assert app._crash_report is None
