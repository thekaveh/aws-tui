"""A framed, killable native decoder; only the parent can read the provider."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import struct
import sys
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, BinaryIO

from aws_tui.domain.filesystem import PreviewReadSession
from aws_tui.domain.preview import (
    PreviewCell,
    PreviewCellKind,
    PreviewColumn,
    PreviewFormat,
    PreviewResult,
    normalize_cell,
    safe_text,
)
from aws_tui.domain.preview_limits import (
    PARQUET_MAX_FOOTER_BYTES,
    PARQUET_MAX_UNCOMPRESSED_BYTES,
    PREVIEW_CLEANUP_SECONDS,
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_CELL_CHARS,
    PREVIEW_MAX_COLUMNS,
    PREVIEW_MAX_NESTING,
    PREVIEW_MAX_NODES,
    PREVIEW_MAX_RANGE_BYTES,
    PREVIEW_MAX_RENDER_CHARS,
    PREVIEW_MAX_REQUESTS,
    PREVIEW_MAX_ROWS,
    PreviewBudget,
    PreviewLimitExceeded,
)

_MAX_FRAME_BYTES = 2 * 1024 * 1024
_BUDGET_MESSAGE = "Parquet sample exceeds preview budget"
_FOOTER_MESSAGE = "Malformed Parquet footer"
_UNAVAILABLE_MESSAGE = "Parquet preview unavailable"
_ENCRYPTED_MESSAGE = "Encrypted Parquet preview is not supported"


class _ParquetFailure(Exception):
    pass


def _frame(message: Mapping[str, Any]) -> bytes:
    payload = json.dumps(
        message, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(payload) > _MAX_FRAME_BYTES:
        raise ValueError("Parquet frame exceeds budget")
    return struct.pack(">I", len(payload)) + payload


def _unframe(payload: bytes) -> dict[str, Any]:
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("Invalid Parquet frame")
    return result


async def _receive_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    length = struct.unpack(">I", await reader.readexactly(4))[0]
    if length > _MAX_FRAME_BYTES:
        raise ValueError("Parquet frame exceeds budget")
    return _unframe(await reader.readexactly(length))


def _read_frame(reader: BinaryIO) -> dict[str, Any]:
    header = reader.read(4)
    if len(header) != 4:
        raise ValueError("Incomplete Parquet frame")
    length = struct.unpack(">I", header)[0]
    if length > _MAX_FRAME_BYTES:
        raise ValueError("Parquet frame exceeds budget")
    payload = reader.read(length)
    if len(payload) != length:
        raise ValueError("Incomplete Parquet frame")
    return _unframe(payload)


def _worker_environment() -> dict[str, str]:
    # An allowlist transports only OS/runtime essentials, never AWS/auth/config state.
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _int(value: Any, *, minimum: int = 0, maximum: int = sys.maxsize) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _ParquetFailure(_BUDGET_MESSAGE)
    return value


def _segment(payload: Any, maximum: int) -> bytes:
    if not isinstance(payload, str) or len(payload) > ((maximum + 2) // 3) * 4:
        raise _ParquetFailure(_BUDGET_MESSAGE)
    result = base64.b64decode(payload, validate=True)
    if len(result) > maximum:
        raise _ParquetFailure(_BUDGET_MESSAGE)
    return result


class _SparseFile(io.RawIOBase):
    """Expose only admitted original offsets; absent bytes are an error, never zeros."""

    def __init__(self, size: int, segments: list[tuple[int, bytes]]) -> None:
        super().__init__()
        self._size = size
        self._segments = sorted(segments)
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        position = (
            offset
            if whence == io.SEEK_SET
            else self._position + offset
            if whence == io.SEEK_CUR
            else self._size + offset
        )
        if whence not in {io.SEEK_SET, io.SEEK_CUR, io.SEEK_END} or not 0 <= position <= self._size:
            raise OSError("Invalid Parquet sparse offset")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > PREVIEW_MAX_BYTES:
            raise OSError("Parquet sparse read exceeds budget")
        end = min(self._size, self._position + size)
        chunks = []
        position = self._position
        while position < end:
            for offset, data in self._segments:
                if offset <= position < offset + len(data):
                    count = min(end - position, offset + len(data) - position)
                    chunks.append(data[position - offset : position - offset + count])
                    position += count
                    break
            else:
                raise OSError("Missing admitted Parquet segment")
        self._position = end
        return b"".join(chunks)

    def readinto(self, target: Any) -> int:
        data = self.read(len(target))
        target[: len(data)] = data
        return len(data)


def _reader(source: Any, *, metadata: Any = None) -> Any:
    import pyarrow.parquet as pq

    return pq.ParquetFile(
        source,
        metadata=metadata,
        pre_buffer=False,
        buffer_size=0,
        thrift_string_size_limit=PARQUET_MAX_FOOTER_BYTES,
        thrift_container_size_limit=PREVIEW_MAX_NODES,
    )


def _leaves(type_: Any, depth: int = 0) -> int:
    import pyarrow as pa

    if depth > PREVIEW_MAX_NESTING:
        raise _ParquetFailure(_BUDGET_MESSAGE)
    if pa.types.is_struct(type_):
        return sum(_leaves(field.type, depth + 1) for field in type_)
    if (
        pa.types.is_list(type_)
        or pa.types.is_large_list(type_)
        or pa.types.is_fixed_size_list(type_)
    ):
        return _leaves(type_.value_type, depth + 1)
    if pa.types.is_map(type_):
        return _leaves(type_.key_type, depth + 1) + _leaves(type_.item_type, depth + 1)
    return 1


def _plan(metadata: Any, schema: Any, size: int, footer_offset: int) -> dict[str, Any]:
    import pyarrow as pa

    count = metadata.num_columns * metadata.num_row_groups
    if (
        count > PREVIEW_MAX_NODES
        or metadata.num_columns > PREVIEW_MAX_NODES
        or len(schema) > PREVIEW_MAX_NODES
    ):
        raise _ParquetFailure(_BUDGET_MESSAGE)
    # External references anywhere in the file are unsupported, even outside the sample.
    for group in range(metadata.num_row_groups):
        for index in range(metadata.num_columns):
            if metadata.row_group(group).column(index).file_path:
                raise _ParquetFailure(_FOOTER_MESSAGE)
    names = []
    columns = []
    indices: list[int] = []
    physical = 0
    admitted = 0
    for field in schema:
        leaves = _leaves(field.type)
        if admitted + leaves <= PREVIEW_MAX_COLUMNS:
            names.append(field.name)
            columns.append([safe_text(field.name)[0], safe_text(str(field.type))[0]])
            indices.extend(range(physical, physical + leaves))
            admitted += leaves
        physical += leaves
    if physical != metadata.num_columns:
        raise _ParquetFailure(_FOOTER_MESSAGE)
    groups = []
    chunks = []
    rows = 0
    uncompressed = 0
    for group in range(metadata.num_row_groups):
        if rows >= PREVIEW_MAX_ROWS:
            break
        row_group = metadata.row_group(group)
        _int(row_group.num_rows)
        if row_group.num_rows == 0:
            continue
        groups.append(group)
        rows += row_group.num_rows
        for index in indices:
            column = row_group.column(index)
            codec = column.compression.lower()
            try:
                available = codec == "uncompressed" or pa.Codec.is_available(codec)
            except ValueError:
                available = False
            if not available:
                raise _ParquetFailure("Unsupported Parquet codec: " + safe_text(codec, limit=40)[0])
            try:
                data_offset = _int(column.data_page_offset, minimum=4, maximum=footer_offset - 1)
                dictionary = column.dictionary_page_offset
                if dictionary is not None:
                    dictionary = _int(dictionary, minimum=4, maximum=data_offset)
            except _ParquetFailure as exc:
                raise _ParquetFailure(_FOOTER_MESSAGE) from exc
            start = dictionary if dictionary is not None else data_offset
            length = _int(column.total_compressed_size, minimum=1, maximum=PREVIEW_MAX_BYTES)
            if start + length > footer_offset:
                raise _ParquetFailure(_FOOTER_MESSAGE)
            if column.file_offset and not 4 <= column.file_offset < footer_offset:
                raise _ParquetFailure(_FOOTER_MESSAGE)
            uncompressed += _int(column.total_uncompressed_size)
            chunks.append([start, length])
    if uncompressed > PARQUET_MAX_UNCOMPRESSED_BYTES:
        return {
            "columns": columns,
            "names": names,
            "physical": indices,
            "groups": [],
            "chunks": [],
            "uncompressed": 0,
            "notes": [_BUDGET_MESSAGE],
        }
    return {
        "columns": columns,
        "names": names,
        "physical": indices,
        "groups": groups,
        "chunks": chunks,
        "uncompressed": uncompressed,
        "notes": [],
    }


def _arrow_value(scalar: Any, *, depth: int, state: list[int]) -> tuple[Any, bool]:
    """Convert bounded scalar pieces, never a table/column or unbounded nested as_py."""
    import pyarrow as pa

    state[0] += 1
    if state[0] > PREVIEW_MAX_NODES or depth > PREVIEW_MAX_NESTING or state[1] <= 0:
        return "… [truncated]", True
    if not scalar.is_valid:
        return None, False
    type_ = scalar.type
    if pa.types.is_struct(type_):
        value = {}
        truncated = False
        for field in type_:
            if state[1] <= 0 or state[0] >= PREVIEW_MAX_NODES:
                truncated = True
                break
            state[1] -= min(len(field.name), PREVIEW_MAX_CELL_CHARS)
            item, cut = _arrow_value(scalar[field.name], depth=depth + 1, state=state)
            value[safe_text(field.name)[0]] = item
            truncated |= cut
        return value, truncated
    if (
        pa.types.is_list(type_)
        or pa.types.is_large_list(type_)
        or pa.types.is_fixed_size_list(type_)
        or pa.types.is_map(type_)
    ):
        value_list = []
        truncated = False
        for item in scalar.values:
            if state[1] <= 0 or state[0] >= PREVIEW_MAX_NODES:
                truncated = True
                break
            value, cut = _arrow_value(item, depth=depth + 1, state=state)
            value_list.append(value)
            truncated |= cut
        return value_list, truncated
    if (
        pa.types.is_string(type_)
        or pa.types.is_large_string(type_)
        or pa.types.is_binary(type_)
        or pa.types.is_large_binary(type_)
    ):
        buffer = scalar.as_buffer()
        count = min(buffer.size, max(0, state[1]) * 4)
        captured = buffer.slice(0, count).to_pybytes()
        value = (
            captured.decode("utf-8", errors="replace")
            if pa.types.is_string(type_) or pa.types.is_large_string(type_)
            else captured[:PREVIEW_MAX_CELL_CHARS].hex()
        )
        state[1] -= len(value)
        return value[:PREVIEW_MAX_CELL_CHARS], buffer.size > count or len(
            value
        ) > PREVIEW_MAX_CELL_CHARS
    value = scalar.as_py()
    state[1] -= len(str(value))
    return value, False


def append_bounded_cells(batch: Any, remaining: int) -> list[list[dict[str, Any]]]:
    rows = []
    nodes = 0
    characters = 0
    for row_index in range(min(batch.num_rows, remaining)):
        row = []
        for column in batch.columns[:PREVIEW_MAX_COLUMNS]:
            state = [nodes, PREVIEW_MAX_CELL_CHARS]
            value, cut = _arrow_value(column[row_index], depth=0, state=state)
            nodes = state[0]
            cell = normalize_cell(value)
            if cut and not cell.truncated:
                text = cell.text[: PREVIEW_MAX_CELL_CHARS - len("… [truncated]")] + "… [truncated]"
                cell = PreviewCell(text, cell.kind, True)
            characters += len(cell.text)
            if characters > PREVIEW_MAX_RENDER_CHARS:
                raise _ParquetFailure(_BUDGET_MESSAGE)
            row.append({"text": cell.text, "kind": cell.kind.value, "truncated": cell.truncated})
        rows.append(row)
    return rows


def _decode(metadata: Any, plan: dict[str, Any], source: _SparseFile) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    parquet = None
    try:
        if not plan["names"]:
            return rows
        parquet = _reader(source, metadata=metadata)
        # PyArrow resolves dotted names as prefixes. Check its actual physical
        # selection before native iteration so ambiguous names cannot widen it.
        if (
            parquet._get_column_indices(plan["names"], use_pandas_metadata=False)
            != plan["physical"]
        ):
            raise _ParquetFailure(_UNAVAILABLE_MESSAGE)
        remaining = PREVIEW_MAX_ROWS
        characters = sum(len(name) + len(type_) for name, type_ in plan["columns"])
        characters += sum(len(note) for note in plan["notes"])
        for group in plan["groups"]:
            batches = parquet.iter_batches(
                batch_size=remaining,
                row_groups=[group],
                columns=plan["names"],
                use_threads=False,
                use_pandas_metadata=False,
            )
            for batch in batches:
                sample = append_bounded_cells(batch, remaining)
                characters += sum(len(cell["text"]) for row in sample for cell in row)
                if characters > PREVIEW_MAX_RENDER_CHARS:
                    raise _ParquetFailure(_BUDGET_MESSAGE)
                rows.extend(sample)
                remaining -= batch.num_rows
                if remaining == 0:
                    break
            if remaining == 0:
                break
        return rows
    finally:
        if parquet is not None:
            parquet.close()
        source.close()


def _child() -> None:
    initial = _read_frame(sys.stdin.buffer)
    size = _int(initial["size"], minimum=12)
    footer = _segment(initial["footer"], PARQUET_MAX_FOOTER_BYTES + 8)
    footer_offset = _int(initial["footer_offset"], minimum=4, maximum=size - 8)
    if footer_offset + len(footer) != size or footer[-4:] != b"PAR1":
        raise _ParquetFailure(_FOOTER_MESSAGE)
    compact = io.BytesIO(b"PAR1" + footer)
    try:
        parquet = _reader(compact)
        try:
            metadata = parquet.metadata
            schema = parquet.schema_arrow
            plan = _plan(metadata, schema, size, footer_offset)
        finally:
            parquet.close()
    except _ParquetFailure:
        raise
    except Exception as exc:
        # Classify locally; never transport native exception strings or tracebacks.
        footer_error = _ENCRYPTED_MESSAGE if "encrypt" in str(exc).lower() else _FOOTER_MESSAGE
        raise _ParquetFailure(footer_error) from exc
    finally:
        compact.close()
    sys.stdout.buffer.write(_frame({"phase": "plan", **plan}))
    sys.stdout.buffer.flush()
    segments = [(0, b"PAR1"), (footer_offset, footer)]
    total = 0
    expected: list[tuple[int, int]] = []
    for offset, length in plan["chunks"]:
        while length:
            part = min(length, PREVIEW_MAX_RANGE_BYTES)
            expected.append((offset, part))
            offset += part
            length -= part
    for offset, length in expected:
        message = _read_frame(sys.stdin.buffer)
        if message.get("phase") != "segment" or message.get("offset") != offset:
            raise _ParquetFailure(_BUDGET_MESSAGE)
        data = _segment(message.get("data"), PREVIEW_MAX_RANGE_BYTES)
        if len(data) != length:
            raise _ParquetFailure(_BUDGET_MESSAGE)
        total += length
        if total > PREVIEW_MAX_BYTES:
            raise _ParquetFailure(_BUDGET_MESSAGE)
        segments.append((offset, data))
    if _read_frame(sys.stdin.buffer).get("phase") != "decode":
        raise _ParquetFailure(_UNAVAILABLE_MESSAGE)
    rows = _decode(metadata, plan, _SparseFile(size, segments))
    sys.stdout.buffer.write(_frame({"phase": "result", "rows": rows}))
    sys.stdout.buffer.flush()


def main() -> None:
    try:
        _child()
    except Exception as exc:
        message = str(exc) if isinstance(exc, _ParquetFailure) else _UNAVAILABLE_MESSAGE
        with suppress(BrokenPipeError):
            sys.stdout.buffer.write(_frame({"phase": "error", "message": message}))
            sys.stdout.buffer.flush()


def _response_error(message: dict[str, Any]) -> None:
    if message.get("phase") != "error":
        return
    note = message.get("message")
    allowed = {_FOOTER_MESSAGE, _BUDGET_MESSAGE, _UNAVAILABLE_MESSAGE, _ENCRYPTED_MESSAGE}
    if isinstance(note, str) and (
        note in allowed or note.startswith("Unsupported Parquet codec: ")
    ):
        raise _ParquetFailure(safe_text(note)[0])
    raise _ParquetFailure(_UNAVAILABLE_MESSAGE)


def _admit_plan(
    plan: dict[str, Any], *, size: int, footer_offset: int, budget: PreviewBudget
) -> tuple[tuple[PreviewColumn, ...], list[tuple[int, int]], tuple[str, ...]]:
    if plan.get("phase") != "plan":
        raise _ParquetFailure(_UNAVAILABLE_MESSAGE)
    columns = plan.get("columns")
    names = plan.get("names")
    groups = plan.get("groups")
    chunks = plan.get("chunks")
    physical = plan.get("physical")
    if (
        not isinstance(columns, list)
        or not isinstance(names, list)
        or not isinstance(groups, list)
        or not isinstance(chunks, list)
        or not isinstance(physical, list)
    ):
        raise _ParquetFailure(_BUDGET_MESSAGE)
    if (
        len(columns) > PREVIEW_MAX_COLUMNS
        or len(names) != len(columns)
        or len(groups) > PREVIEW_MAX_ROWS
        or len(chunks) > PREVIEW_MAX_ROWS * PREVIEW_MAX_COLUMNS
        or len(physical) > PREVIEW_MAX_COLUMNS
        or len(chunks) != len(physical) * len(groups)
    ):
        raise _ParquetFailure(_BUDGET_MESSAGE)
    for name in names:
        if not isinstance(name, str) or len(name) > PARQUET_MAX_FOOTER_BYTES:
            raise _ParquetFailure(_BUDGET_MESSAGE)
    for group in groups:
        _int(group, maximum=PREVIEW_MAX_NODES)
    for index in physical:
        _int(index, maximum=PREVIEW_MAX_NODES)
    if physical != sorted(set(physical)):
        raise _ParquetFailure(_BUDGET_MESSAGE)
    if groups != sorted(set(groups)):
        raise _ParquetFailure(_BUDGET_MESSAGE)
    _int(plan.get("uncompressed"), maximum=PARQUET_MAX_UNCOMPRESSED_BYTES)
    result_columns = []
    for column in columns:
        if (
            not isinstance(column, list)
            or len(column) != 2
            or not all(isinstance(c, str) and len(c) <= PREVIEW_MAX_CELL_CHARS for c in column)
        ):
            raise _ParquetFailure(_BUDGET_MESSAGE)
        result_columns.append(PreviewColumn(*column))
    ranges = []
    byte_count = 0
    for chunk in chunks:
        if not isinstance(chunk, list) or len(chunk) != 2:
            raise _ParquetFailure(_BUDGET_MESSAGE)
        offset = _int(chunk[0], minimum=4, maximum=size)
        length = _int(chunk[1], minimum=1, maximum=PREVIEW_MAX_BYTES)
        if offset + length > footer_offset:
            raise _ParquetFailure(_FOOTER_MESSAGE)
        byte_count += length
        while length:
            part = min(length, PREVIEW_MAX_RANGE_BYTES)
            ranges.append((offset, part))
            offset += part
            length -= part
    if (
        budget.bytes_requested + byte_count > PREVIEW_MAX_BYTES
        or budget.requests + len(ranges) + 1 > PREVIEW_MAX_REQUESTS
    ):
        raise _ParquetFailure(_BUDGET_MESSAGE)
    notes = plan.get("notes", [])
    if notes not in ([], [_BUDGET_MESSAGE]):
        raise _ParquetFailure(_UNAVAILABLE_MESSAGE)
    return tuple(result_columns), ranges, tuple(notes)


def _result_rows(
    message: dict[str, Any], columns: tuple[PreviewColumn, ...], *, notes: tuple[str, ...] = ()
) -> tuple[tuple[PreviewCell, ...], ...]:
    if message.get("phase") != "result":
        raise _ParquetFailure(_UNAVAILABLE_MESSAGE)
    rows = message.get("rows")
    if not isinstance(rows, list) or len(rows) > PREVIEW_MAX_ROWS:
        raise _ParquetFailure(_BUDGET_MESSAGE)
    result = []
    characters = sum(len(column.name) + len(column.type_name or "") for column in columns)
    characters += sum(len(note) for note in notes)
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise _ParquetFailure(_BUDGET_MESSAGE)
        cells = []
        for cell in row:
            if (
                not isinstance(cell, dict)
                or not isinstance(cell.get("text"), str)
                or len(cell["text"]) > PREVIEW_MAX_CELL_CHARS
                or type(cell.get("truncated")) is not bool
            ):
                raise _ParquetFailure(_BUDGET_MESSAGE)
            characters += len(cell["text"])
            cells.append(
                PreviewCell(cell["text"], PreviewCellKind(cell["kind"]), cell["truncated"])
            )
        result.append(tuple(cells))
    if characters > PREVIEW_MAX_RENDER_CHARS:
        raise _ParquetFailure(_BUDGET_MESSAGE)
    return tuple(result)


async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), PREVIEW_CLEANUP_SECONDS / 2)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
    else:
        await process.wait()
    if process.stdin is not None:
        with suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()
    if process.stdout is not None:
        await process.stdout.read()


async def preview_parquet(
    session: PreviewReadSession, raw: bytes, *, budget: PreviewBudget
) -> PreviewResult:
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            if raw[:4] == b"PARE":
                raise _ParquetFailure(_ENCRYPTED_MESSAGE)
            size = session.snapshot.size
            if size < 12:
                raise _ParquetFailure(_FOOTER_MESSAGE)
            tail = await session.read_range(size - 8, 8)
            if tail[-4:] == b"PARE":
                raise _ParquetFailure(_ENCRYPTED_MESSAGE)
            if len(tail) != 8 or tail[-4:] != b"PAR1":
                raise _ParquetFailure(_FOOTER_MESSAGE)
            length = int.from_bytes(tail[:4], "little")
            if not 0 < length <= PARQUET_MAX_FOOTER_BYTES or length + 12 > size:
                raise _ParquetFailure(_FOOTER_MESSAGE)
            footer_offset = size - 8 - length
            footer = await session.read_range(footer_offset, length)
            if len(footer) != length:
                raise _ParquetFailure(_FOOTER_MESSAGE)
            startup = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "aws_tui.domain._parquet_preview_worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=_worker_environment(),
                    limit=_MAX_FRAME_BYTES + 4,
                )
            )
            try:
                process = await asyncio.shield(startup)
            except asyncio.CancelledError:
                # Cancellation cannot lose a child between OS spawn and handle publication.
                process = await asyncio.shield(startup)
                raise
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(
                _frame(
                    {
                        "size": size,
                        "footer_offset": footer_offset,
                        "footer": base64.b64encode(footer + tail).decode("ascii"),
                    }
                )
            )
            await process.stdin.drain()
            plan = await _receive_frame(process.stdout)
            _response_error(plan)
            columns, ranges, notes = _admit_plan(
                plan, size=size, footer_offset=footer_offset, budget=budget
            )
            for offset, length in ranges:
                data = await session.read_range(offset, length)
                if len(data) != length:
                    raise _ParquetFailure(_UNAVAILABLE_MESSAGE)
                process.stdin.write(
                    _frame(
                        {
                            "phase": "segment",
                            "offset": offset,
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    )
                )
                await process.stdin.drain()
            process.stdin.write(_frame({"phase": "decode"}))
            await process.stdin.drain()
            message = await _receive_frame(process.stdout)
            _response_error(message)
            rows = _result_rows(message, columns, notes=notes)
            budget.check()
            return PreviewResult(PreviewFormat.PARQUET, raw, columns, rows, notes)
    except TimeoutError as exc:
        raise PreviewLimitExceeded("Preview timed out") from exc
    except _ParquetFailure as exc:
        return PreviewResult(PreviewFormat.RAW, raw, (), (), (str(exc),))
    except (ValueError, OSError, asyncio.IncompleteReadError):
        return PreviewResult(PreviewFormat.RAW, raw, (), (), (_UNAVAILABLE_MESSAGE,))
    finally:
        if process is not None:
            cleanup = asyncio.create_task(_reap(process))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise


if __name__ == "__main__":
    main()
