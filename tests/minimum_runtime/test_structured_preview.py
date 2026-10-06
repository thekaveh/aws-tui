"""Required native decoder smoke proof on the supported minimum Python runtime."""

import io
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.preview import PreviewFormat, load_preview


@pytest.mark.asyncio
async def test_native_decoder_minimum_runtime():
    sink = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "name": ["first", "second"]}), sink, row_group_size=1)
    data = sink.getvalue()
    assert pq.ParquetFile(io.BytesIO(data)).num_row_groups == 2
    fs = InMemoryFS()
    path = PathRef(("minimum.parquet",))

    async def chunks():
        yield data

    await fs.write_stream(path, chunks())
    result = await load_preview(fs, path, name="minimum.parquet", mime="application/octet-stream")
    assert result.format is PreviewFormat.PARQUET
    assert [column.name for column in result.columns] == ["id", "name"]
    assert [row[0].text for row in result.rows] == ["1", "2"]
    print(
        f"Native runtime {sys.version.split()[0]}, PyArrow {pa.__version__}: schema + 2 rows / 2 groups"
    )
