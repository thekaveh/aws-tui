"""Bounded, display-neutral structured previews with parent-owned source reads."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aws_tui.domain.filesystem import (
    BoundedPreviewProvider,
    FileSystemProvider,
    PathRef,
    PreviewReadSession,
)
from aws_tui.domain.preview_limits import (
    PREVIEW_MAX_CELL_CHARS,
    PREVIEW_MAX_COLUMNS,
    PREVIEW_MAX_NESTING,
    PREVIEW_MAX_NODES,
    PREVIEW_MAX_RENDER_CHARS,
    PREVIEW_MAX_ROWS,
    PREVIEW_SNIFF_BYTES,
    RAW_PREVIEW_BYTES,
    PreviewBudget,
    PreviewLimitExceeded,
)


class PreviewFormat(StrEnum):
    RAW = "raw"
    CSV = "csv"
    JSON = "json"
    JSONL = "jsonl"
    PARQUET = "parquet"


class PreviewCellKind(StrEnum):
    NULL = "null"
    EMPTY = "empty"
    SCALAR = "scalar"
    NESTED = "nested"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class PreviewCell:
    text: str
    kind: PreviewCellKind
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class PreviewColumn:
    name: str
    type_name: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewResult:
    format: PreviewFormat
    raw: bytes
    columns: tuple[PreviewColumn, ...]
    rows: tuple[tuple[PreviewCell, ...], ...]
    notes: tuple[str, ...] = ()


_MISSING = object()
_SUFFIX = "… [truncated]"
_BIDI = (
    frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A)) | {0x061C, 0x200E, 0x200F}
)


def safe_text(text: str, *, limit: int = PREVIEW_MAX_CELL_CHARS) -> tuple[str, bool]:
    """Escape disruptive controls and cap the escaped display, never interpret markup."""
    pieces: list[str] = []
    length = 0
    shortened = False
    for char in text:
        code = ord(char)
        if char in "\n\r\t":
            piece = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}[char]
        elif code < 32 or 0x7F <= code <= 0x9F:
            piece = f"\\x{code:02x}"
        elif code in _BIDI or 0xD800 <= code <= 0xDFFF:
            piece = f"\\u{code:04x}"
        else:
            piece = char
        if length + len(piece) > limit:
            shortened = True
            break
        pieces.append(piece)
        length += len(piece)
    result = "".join(pieces)
    if shortened:
        result = result[: limit - len(_SUFFIX)] + _SUFFIX
    return result, shortened


def normalize_cell(value: Any) -> PreviewCell:
    if value is _MISSING:
        return PreviewCell("— (missing)", PreviewCellKind.MISSING)
    if value is None:
        return PreviewCell("null", PreviewCellKind.NULL)
    if value == "":
        return PreviewCell('""', PreviewCellKind.EMPTY)
    kind = PreviewCellKind.SCALAR
    if isinstance(value, (list, dict)):
        kind = PreviewCellKind.NESTED
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    elif isinstance(value, str):
        if (
            value in {"null", '""', "— (missing)"}
            or value.startswith(("{", "[", '"'))
            or _SUFFIX in value
        ):
            value = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool):
        value = "true" if value else "false"
    else:
        value = str(value)
    text, truncated = safe_text(value)
    return PreviewCell(text, kind, truncated)


def _raw(raw: bytes, note: str | None = None) -> PreviewResult:
    return PreviewResult(PreviewFormat.RAW, raw, (), (), (note,) if note else ())


def _preflight_json(text: str, budget: PreviewBudget) -> None:
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if index % 1024 == 0:
            budget.check()
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > PREVIEW_MAX_NESTING:
                raise ValueError("JSON nesting exceeds preview budget")
        elif char in "]}":
            depth -= 1


def _reject_constant(value: str) -> Any:
    raise ValueError("Nonfinite JSON constant")


def _check_nodes(value: Any, budget: PreviewBudget) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > PREVIEW_MAX_NODES or depth > PREVIEW_MAX_NESTING:
            raise ValueError("JSON nodes exceed preview budget")
        budget.check()
        if isinstance(current, dict):
            nodes += len(current)  # Keys also consume the traversal budget.
            stack.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, list):
            stack.extend((v, depth + 1) for v in current)


def _json_table(value: Any, format_: PreviewFormat, raw: bytes) -> PreviewResult:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        keys: list[str] = []
        for row in value:
            for key in row:
                if key not in keys and len(keys) < PREVIEW_MAX_COLUMNS:
                    keys.append(key)
        columns = tuple(PreviewColumn(safe_text(key)[0]) for key in keys)
        rows = tuple(
            tuple(normalize_cell(row.get(key, _MISSING)) for key in keys)
            for row in value[:PREVIEW_MAX_ROWS]
        )
    else:
        columns = (PreviewColumn("path"), PreviewColumn("value"))
        entries = list(value.items()) if isinstance(value, dict) else [("$", value)]
        rows = tuple(
            (normalize_cell(key), normalize_cell(item)) for key, item in entries[:PREVIEW_MAX_ROWS]
        )
    return _bounded_result(format_, raw, columns, rows)


def _bounded_result(
    format_: PreviewFormat,
    raw: bytes,
    columns: tuple[PreviewColumn, ...],
    rows: tuple[tuple[PreviewCell, ...], ...],
    notes: tuple[str, ...] = (),
) -> PreviewResult:
    size = sum(len(c.name) + len(c.type_name or "") for c in columns)
    size += sum(len(cell.text) for row in rows for cell in row)
    if size > PREVIEW_MAX_RENDER_CHARS:
        raise ValueError("Structured output exceeds preview budget")
    return PreviewResult(format_, raw, columns, rows, notes)


def _csv_table(text: str, raw: bytes, truncated: bool, hinted: bool) -> PreviewResult | None:
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records: list[list[str]] = []
    lines = len(text.splitlines())
    try:
        for row in reader:
            # A prefix ending inside a physical line cannot prove its final record complete.
            if truncated and reader.line_num == lines and not text.endswith(("\n", "\r")):
                break
            records.append(row)
            if len(records) > PREVIEW_MAX_ROWS:
                break
    except csv.Error:
        if not truncated:
            return None
    if not records or len(records[0]) < 2:
        return None
    if not hinted and (len(records) < 2 or any(len(r) != len(records[0]) for r in records[1:])):
        return None
    columns = tuple(PreviewColumn(safe_text(c)[0]) for c in records[0][:PREVIEW_MAX_COLUMNS])
    rows = tuple(
        tuple(normalize_cell(row[i] if i < len(row) else _MISSING) for i in range(len(columns)))
        for row in records[1 : PREVIEW_MAX_ROWS + 1]
    )
    return _bounded_result(PreviewFormat.CSV, raw, columns, rows)


def parse_text(
    raw: bytes, *, name: str, mime: str, truncated: bool, budget: PreviewBudget
) -> PreviewResult:
    budget.check()
    if len(raw) > RAW_PREVIEW_BYTES:
        raise PreviewLimitExceeded("Preview range exceeds budget")
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return _raw(raw)
    sniff = raw[:PREVIEW_SNIFF_BYTES].decode("utf-8-sig", errors="ignore").lstrip()
    suffix = name.rsplit(".", 1)[-1].lower()
    jsonl_hint = suffix in {"jsonl", "ndjson"} or "ndjson" in mime or "jsonl" in mime
    json_hint = suffix == "json" or "json" in mime or sniff.startswith(("{", "["))
    if not truncated and (json_hint or jsonl_hint):
        try:
            _preflight_json(text, budget)
            if jsonl_hint:
                value = [
                    json.loads(line, parse_constant=_reject_constant)
                    for line in text.splitlines()
                    if line.strip()
                ]
                if not value or not all(isinstance(row, dict) for row in value):
                    raise ValueError("JSONL requires object records")
                format_ = PreviewFormat.JSONL
            else:
                value = json.loads(text, parse_constant=_reject_constant)
                format_ = PreviewFormat.JSON
            _check_nodes(value, budget)
            return _json_table(value, format_, raw)
        except (ValueError, RecursionError):
            # Never reinterpret malformed JSON-shaped documents as delimited records.
            if sniff.startswith(("{", "[")):
                return _raw(raw, "Malformed or truncated JSON preview")
    if truncated and (json_hint or jsonl_hint) and sniff.startswith(("{", "[")):
        return _raw(raw, "Malformed or truncated JSON preview")
    hinted = suffix == "csv" or "csv" in mime
    result = _csv_table(text, raw, truncated, hinted)
    return result if result is not None else _raw(raw)


async def load_legacy_preview(
    chunks: AsyncIterator[bytes], *, name: str, mime: str
) -> PreviewResult:
    return await _load_legacy_preview(chunks, name=name, mime=mime, budget=PreviewBudget.start())


async def _load_legacy_preview(
    chunks: AsyncIterator[bytes], *, name: str, mime: str, budget: PreviewBudget
) -> PreviewResult:
    raw = bytearray()
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            async for chunk in chunks:
                raw.extend(chunk[: RAW_PREVIEW_BYTES - len(raw)])
                budget.check()
                if len(raw) >= RAW_PREVIEW_BYTES:
                    break
            if bytes(raw[:4]) in (b"PAR1", b"PARE"):
                return _raw(bytes(raw), "Parquet preview requires bounded ranges")
            return parse_text(
                bytes(raw),
                name=name,
                mime=mime,
                truncated=len(raw) == RAW_PREVIEW_BYTES,
                budget=budget,
            )
    except TimeoutError as exc:
        raise PreviewLimitExceeded("Preview timed out") from exc
    finally:
        close = getattr(chunks, "aclose", None)
        if close is not None:
            await close()


async def load_preview(
    provider: FileSystemProvider, path: PathRef, *, name: str, mime: str
) -> PreviewResult:
    budget = PreviewBudget.start()
    session: PreviewReadSession | None = None
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            if not isinstance(provider, BoundedPreviewProvider):
                return await _load_legacy_preview(
                    await provider.read_stream(path, chunk_size=RAW_PREVIEW_BYTES),
                    name=name,
                    mime=mime,
                    budget=budget,
                )
            session = await provider.open_preview(path, budget=budget)
            raw = await session.read_range(0, min(session.snapshot.size, RAW_PREVIEW_BYTES))
            try:
                if raw[:4] in (b"PAR1", b"PARE"):
                    from aws_tui.domain._parquet_preview_worker import preview_parquet

                    result = await preview_parquet(session, raw, budget=budget)
                else:
                    result = parse_text(
                        raw,
                        name=name,
                        mime=mime,
                        truncated=session.snapshot.size > len(raw),
                        budget=budget,
                    )
            except PreviewLimitExceeded as exc:
                budget.check()  # An expired deadline may never publish even raw fallback.
                result = _raw(raw, str(exc))
            await session.validate()
            budget.check()
            return result
    except TimeoutError as exc:
        raise PreviewLimitExceeded("Preview timed out") from exc
    finally:
        if session is not None:
            await session.aclose()
