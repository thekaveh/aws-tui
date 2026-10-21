"""JSONL record boundaries do not consume valid Unicode string content."""

from __future__ import annotations

import json

import pytest

from aws_tui.domain import preview
from aws_tui.domain.preview_limits import PreviewBudget


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize("escaped", [False, True], ids=["literal", "escaped"])
@pytest.mark.parametrize(
    ("name", "mime"),
    [("data.jsonl", ""), ("data.ndjson", ""), ("data.txt", "application/x-ndjson")],
)
def test_production_jsonl_preserves_decoded_values(
    separator, newline, escaped, name, mime, monkeypatch
):
    values = [f"left{separator}[red]literal", "second"]
    text = newline.join(json.dumps({"value": value}, ensure_ascii=escaped) for value in values)
    raw = (text + newline).encode()
    decoded = []
    normalize = preview.normalize_cell

    def record_value(value):
        decoded.append(value)
        return normalize(value)

    monkeypatch.setattr(preview, "normalize_cell", record_value)
    result = preview.parse_text(
        raw, name=name, mime=mime, truncated=False, budget=PreviewBudget.start()
    )
    assert result.format is preview.PreviewFormat.JSONL
    assert decoded == values
    assert result.raw == raw
    assert result.notes == ()
    assert len(result.rows) == 2
    displayed = values[0].replace("\u0085", r"\x85")
    assert [row[0].text for row in result.rows] == [displayed, "second"]


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_plain_records_keep_existing_blank_line_and_final_newline_behavior(newline):
    raw = (newline + '{"value":"first"}' + newline * 2 + '{"value":"second"}').encode()
    result = preview.parse_text(
        raw, name="data.jsonl", mime="", truncated=False, budget=PreviewBudget.start()
    )
    assert result.format is preview.PreviewFormat.JSONL
    assert [row[0].text for row in result.rows] == ["first", "second"]
    assert result.raw == raw


@pytest.mark.parametrize("separator", ["\r", "\u0085", "\u2028", "\u2029"])
def test_non_lf_separators_cannot_delimit_json_records(separator):
    raw = ('{"value":1}' + separator + '{"value":2}').encode()
    result = preview.parse_text(
        raw, name="data.jsonl", mime="", truncated=False, budget=PreviewBudget.start()
    )
    assert result.format is preview.PreviewFormat.RAW
    assert result.notes == ("Malformed or truncated JSON preview",)
    assert result.raw == raw
