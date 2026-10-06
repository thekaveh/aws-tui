"""Unit tests for the Quick Look content builder helpers in ``aws_tui.app``."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from aws_tui.app import _build_quick_look_content, _first_bytes
from aws_tui.domain.filesystem import EntryKind, FileEntry


async def _gen(*chunks: bytes) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


class _FakeProvider:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def read_stream(self, path: object, *, chunk_size: int) -> AsyncIterator[bytes]:
        # Matches the real provider idiom: ``async def`` returning an iterator
        # (callers ``await`` it, then ``async for`` over the result).
        return _gen(*self._chunks)


def _file(name: str) -> FileEntry:
    return FileEntry(name=name, kind=EntryKind.FILE, size=10, modified=None)


@pytest.mark.asyncio
async def test_first_bytes_caps_at_limit() -> None:
    out = b"".join([c async for c in _first_bytes(_gen(b"a" * 40000, b"b" * 40000), 64 * 1024)])
    assert len(out) == 64 * 1024


@pytest.mark.asyncio
async def test_first_bytes_passes_through_when_under_limit() -> None:
    out = b"".join([c async for c in _first_bytes(_gen(b"hello"), 64 * 1024)])
    assert out == b"hello"


@pytest.mark.asyncio
async def test_first_bytes_closes_source_when_consumer_stops_early() -> None:
    closed = False

    async def source() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"first"
            yield b"second"
        finally:
            closed = True

    bounded = _first_bytes(source(), 64 * 1024)
    assert await anext(bounded) == b"first"
    await bounded.aclose()

    assert closed


def test_build_content_sets_title_and_mime() -> None:
    content = _build_quick_look_content(_file("notes.txt"), _FakeProvider(b"x"), path="notes.txt")
    assert content.title == "notes.txt"
    assert content.mime == "text/plain"
    assert content.chunks is not None


def test_build_content_unknown_mime_defaults_octet_stream() -> None:
    content = _build_quick_look_content(_file("blob.zzz"), _FakeProvider(b"x"), path="blob.zzz")
    assert content.mime == "application/octet-stream"


@pytest.mark.asyncio
async def test_build_content_stream_is_capped() -> None:
    provider = _FakeProvider(b"a" * 40000, b"b" * 40000)
    content = _build_quick_look_content(_file("big.bin"), provider, path="big.bin")
    assert content.chunks is not None
    total = b"".join([c async for c in content.chunks])
    assert len(total) == 64 * 1024


# Structured engine contracts; the historical helper assertions above stay intact.
async def _structured(raw: bytes, name: str, mime: str = ""):
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.domain.filesystem import PathRef
    from aws_tui.domain.preview import load_preview

    fs = InMemoryFS()
    path = PathRef((name,))
    await fs.write_stream(path, _gen(raw))
    return await load_preview(fs, path, name=name, mime=mime)


@pytest.mark.asyncio
async def test_csv_complete_multiline_and_empty_cells():
    from aws_tui.domain.preview import PreviewCellKind, PreviewFormat

    raw = b'name,note\r\nAda,"line one\nline two"\r\nBob,""\r\n'
    result = await _structured(raw, "rows.csv", "text/csv")
    assert result.format is PreviewFormat.CSV
    assert tuple(c.name for c in result.columns) == ("name", "note")
    assert result.rows[0][1].text == "line one\\nline two"
    assert result.rows[1][1].kind is PreviewCellKind.EMPTY
    assert result.raw == raw


@pytest.mark.asyncio
async def test_structured_row_column_and_cell_caps():
    from aws_tui.domain.preview import PreviewFormat

    raw = (
        ",".join(f"c{i}" for i in range(25))
        + "\n"
        + (",".join(["x" * 300] + ["a"] * 24) + "\n") * 51
    ).encode()
    result = await _structured(raw, "rows.csv")
    assert result.format is PreviewFormat.CSV
    assert len(result.columns) == 24
    assert len(result.rows) == 50
    assert len(result.rows[0][0].text) <= 256
    assert result.rows[0][0].truncated
    assert result.rows[0][0].text.endswith("… [truncated]")


@pytest.mark.asyncio
async def test_json_distinctions_and_visible_control_escape():
    from aws_tui.domain.preview import PreviewCellKind, PreviewFormat

    result = await _structured(
        b'[{"a":null,"b":"","c":{"x":1},"d":"null","e":"\\u001b[red]"},{"b":2}]',
        "data.json",
    )
    assert result.format is PreviewFormat.JSON
    assert [c.kind for c in result.rows[0]] == [
        PreviewCellKind.NULL,
        PreviewCellKind.EMPTY,
        PreviewCellKind.NESTED,
        PreviewCellKind.SCALAR,
        PreviewCellKind.SCALAR,
    ]
    assert result.rows[0][3].text == '"null"'
    assert result.rows[0][4].text == r"\x1b[red]"
    assert result.rows[1][0].kind is PreviewCellKind.MISSING
    assert result.rows[1][0].text == "— (missing)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "name"),
    [
        (b'{"a":', "a.json"),
        (b'{"a":1}\nnope', "a.jsonl"),
        (b"[" + b"[" * 17 + b"0" + b"]" * 18, "a.json"),
        (b"[" + b"0," * 4096 + b"0]", "a.json"),
        (b"hello world", "a.json"),
        (b"hello world", "a.csv"),
    ],
)
async def test_invalid_or_excessive_text_is_raw(raw, name):
    from aws_tui.domain.preview import PreviewFormat

    result = await _structured(raw, name)
    assert result.format is PreviewFormat.RAW
    assert result.raw == raw


@pytest.mark.asyncio
async def test_jsonl_requires_all_captured_records_and_untruncated_source():
    from aws_tui.domain.preview import PreviewFormat

    result = await _structured(b'{"a":1}\n{"b":2}\n', "a.jsonl")
    assert result.format is PreviewFormat.JSONL
    assert tuple(c.name for c in result.columns) == ("a", "b")
    prefix = b'{"a":1}' + b" " * (65536 - 7)
    result = await _structured(prefix + b" ", "a.json")
    assert result.format is PreviewFormat.RAW
    assert len(result.raw) == 65536


@pytest.mark.asyncio
async def test_hint_conflicts_use_valid_bounded_sniff():
    from aws_tui.domain.preview import PreviewFormat

    result = await _structured(b'[{"x":1}]', "wrong.csv", "text/csv")
    assert result.format is PreviewFormat.JSON
    result = await _structured(b"a,b\n1,2\n", "wrong.json", "application/json")
    assert result.format is PreviewFormat.CSV


@pytest.mark.asyncio
async def test_legacy_exact_cap_closes_without_extra_pull():
    from aws_tui.domain.preview import PreviewFormat, load_legacy_preview

    pulled = 0
    closed = False

    async def source():
        nonlocal pulled, closed
        try:
            pulled += 1
            yield b'{"a":1}' + b" " * (65536 - 7)
            pulled += 1
            raise AssertionError("must not pull after cap")
        finally:
            closed = True

    result = await load_legacy_preview(source(), name="a.json", mime="application/json")
    assert result.format is PreviewFormat.RAW
    assert len(result.raw) == 65536
    assert pulled == 1
    assert closed


@pytest.mark.asyncio
async def test_csv_cut_quoted_record_is_not_a_row():
    from aws_tui.domain.preview import PreviewFormat

    raw = b'a,b\n1,2\n3,"' + b"x" * 65536
    result = await _structured(raw, "a.csv")
    assert result.format is PreviewFormat.CSV
    assert len(result.rows) == 1
    assert result.rows[0][0].text == "1"


@pytest.mark.asyncio
async def test_jsonl_nonfinite_constants_are_malformed():
    from aws_tui.domain.preview import PreviewFormat

    result = await _structured(b'{"a":NaN}\n', "a.jsonl")
    assert result.format is PreviewFormat.RAW


@pytest.mark.asyncio
async def test_invalid_jsonl_record_after_sample_cap_still_falls_back():
    from aws_tui.domain.preview import PreviewFormat

    result = await _structured(b'{"a":1}\n' * 51 + b'{"a":\n', "a.jsonl")
    assert result.format is PreviewFormat.RAW


@pytest.mark.asyncio
async def test_compatibility_provider_open_consumes_shared_absolute_deadline(monkeypatch):
    import asyncio
    from time import monotonic

    from aws_tui.domain import preview
    from aws_tui.domain.filesystem import PathRef
    from aws_tui.domain.preview_limits import PreviewBudget, PreviewLimitExceeded

    cancelled = False

    class LegacyProvider:
        async def read_stream(self, path, *, chunk_size):
            nonlocal cancelled
            try:
                await asyncio.sleep(600)
            finally:
                cancelled = True

    monkeypatch.setattr(
        preview.PreviewBudget, "start", lambda: PreviewBudget(monotonic() + 0.05, monotonic)
    )
    # The outer test guard makes a broken absolute-open timeout fail quickly.
    with pytest.raises(PreviewLimitExceeded, match="Preview timed out"):
        async with asyncio.timeout(0.5):
            await preview.load_preview(LegacyProvider(), PathRef(("a",)), name="a", mime="")
    assert cancelled


@pytest.mark.parametrize("control", [b"\x0b", b"\x0c", b"\x1c", b"\x85"])
@pytest.mark.parametrize("newline", [b"\n", b"\r", b"\r\n"])
@pytest.mark.parametrize("multiline", [False, True])
def test_csv_controls_do_not_make_cut_final_record_complete(control, newline, multiline):
    from aws_tui.domain.preview import PreviewFormat, parse_text
    from aws_tui.domain.preview_limits import PreviewBudget

    # C1 must be UTF-8, while the other controls are single-byte UTF-8.
    control = control.decode("latin1").encode()
    complete = b'1,"line' + control + (newline + b'two"' if multiline else b'"')
    raw = b"a,b" + newline + complete + newline + b'2,"cut' + newline + b'partial"'
    result = parse_text(
        raw, name="a.csv", mime="text/csv", truncated=True, budget=PreviewBudget.start()
    )
    assert result.format is PreviewFormat.CSV
    assert len(result.rows) == 1
    assert result.rows[0][0].text == "1"
    assert result.raw == raw


def test_csv_control_does_not_publish_unquoted_partial_field():
    from aws_tui.domain.preview import PreviewFormat, parse_text
    from aws_tui.domain.preview_limits import PreviewBudget

    raw = b"a,b\n1,\x0b\n2,partial"
    result = parse_text(
        raw, name="a.csv", mime="text/csv", truncated=True, budget=PreviewBudget.start()
    )
    assert result.format is PreviewFormat.CSV
    assert len(result.rows) == 1
    assert result.rows[0][1].text == r"\x0b"
    assert result.raw == raw


@pytest.mark.asyncio
async def test_csv_render_budget_fallback_retains_raw_validates_and_closes():
    from aws_tui.demo.in_memory_fs import InMemoryFS
    from aws_tui.domain.filesystem import PathRef
    from aws_tui.domain.preview import PreviewFormat, load_preview

    raw = b",".join([b"c"] * 24) + b"\n" + (b",".join([b"[" + b"\x1b" * 43] * 24) + b"\n") * 50
    assert len(raw) == 54048
    fs = InMemoryFS()
    path = PathRef(("a.csv",))
    await fs.write_stream(path, _gen(raw))
    real_open = fs.open_preview
    events = []

    class TrackingSession:
        def __init__(self, session):
            self.session = session
            self.snapshot = session.snapshot

        async def read_range(self, offset, length):
            events.append("read")
            return await self.session.read_range(offset, length)

        async def validate(self):
            await self.session.validate()
            events.append("validated")

        async def aclose(self):
            await self.session.aclose()
            events.append("closed")

    async def open_preview(path, *, budget):
        return TrackingSession(await real_open(path, budget=budget))

    fs.open_preview = open_preview
    result = await load_preview(fs, path, name="a.csv", mime="text/csv")
    assert result.format is PreviewFormat.RAW
    assert result.raw == raw
    assert result.notes == ("Structured output exceeds preview budget",)
    assert events == ["read", "validated", "closed"]


@pytest.mark.asyncio
async def test_first_bytes_exact_cap_does_not_pull_next_item() -> None:
    closed = False

    async def source():
        nonlocal closed
        try:
            yield b"x" * 65536
            raise AssertionError("read after exactly-full prefix")
        finally:
            closed = True

    assert b"".join([c async for c in _first_bytes(source(), 65536)]) == b"x" * 65536
    assert closed


@pytest.mark.asyncio
async def test_content_loader_is_lazy_and_closing_nested_prefix_closes_source() -> None:
    opened = 0
    closed = False

    class Provider(_FakeProvider):
        async def read_stream(self, path, *, chunk_size):
            nonlocal opened
            opened += 1

            async def source():
                nonlocal closed
                try:
                    yield b"first"
                    yield b"second"
                finally:
                    closed = True

            return source()

    provider = Provider()
    content = _build_quick_look_content(_file("a.txt"), provider, path="a.txt")
    assert content.load_preview is not None
    assert content.load_preview.func.__name__ == "load_preview"
    assert content.load_preview.args == (provider, "a.txt")
    assert opened == 0
    assert await anext(content.chunks) == b"first"
    await content.chunks.aclose()
    assert opened == 1
    assert closed


@pytest.mark.asyncio
async def test_bracket_leading_csv_header_is_literal_and_not_json() -> None:
    from aws_tui.domain.preview import PreviewFormat

    result = await _structured(
        b"[red]sample_id,wide\nstructured-preview-row,ordinary\n", "sample.csv", "text/csv"
    )
    assert result.format is PreviewFormat.CSV
    assert result.columns[0].name == "[red]sample_id"
    assert result.rows[0][0].text == "structured-preview-row"


@pytest.mark.parametrize("raw", [b"[1,2", b'{"a":1,', b'[{"a":1},\n{"b":2'])
def test_malformed_json_with_csv_hint_is_still_raw(raw) -> None:
    from aws_tui.domain.preview import PreviewFormat, parse_text
    from aws_tui.domain.preview_limits import PreviewBudget

    for truncated in (False, True):
        result = parse_text(
            raw,
            name="wrong.csv",
            mime="text/csv",
            truncated=truncated,
            budget=PreviewBudget.start(),
        )
        assert result.format is PreviewFormat.RAW
        assert result.raw == raw


@pytest.mark.asyncio
async def test_bracket_leading_hinted_csv_retains_missing_cells() -> None:
    from aws_tui.domain.preview import PreviewCellKind, PreviewFormat

    result = await _structured(
        b"[red]sample_id,wide\nstructured-preview-row\n", "sample.csv", "text/csv"
    )
    assert result.format is PreviewFormat.CSV
    assert result.rows[0][1].kind is PreviewCellKind.MISSING
    assert result.rows[0][1].text == "— (missing)"
