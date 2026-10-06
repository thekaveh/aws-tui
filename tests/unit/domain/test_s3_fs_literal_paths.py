"""Directory delimiters are implicit; empty key components remain meaningful."""

import pytest

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.s3_fs import S3FS
from tests.s3_readonly import LiteralS3Client

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("bucket", [None, "reports-bucket"])
@pytest.mark.parametrize("configured_prefix", ["", "base//root"])
@pytest.mark.parametrize(
    ("segments", "relative_prefix"),
    [
        ((), ""),
        (("daily",), "daily/"),
        (("daily", ""), "daily//"),
        (("daily", "", ""), "daily///"),
        (("",), "/"),
        (("", "daily"), "/daily/"),
        (("daily", "", "雪%2F?#"), "daily//雪%2F?#/"),
    ],
)
async def test_literal_directory_prefix_and_returned_keys(
    bucket, configured_prefix, segments, relative_prefix
):
    prefix = (configured_prefix + "/" if configured_prefix else "") + relative_prefix
    client = LiteralS3Client(prefix)
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    path = PathRef(segments if bucket else ("reports-bucket", *segments))
    entries = await fs.list(path)
    assert client.calls == [
        ("list", {"Bucket": "reports-bucket", "Prefix": prefix, "Delimiter": "/"})
    ]
    assert [entry.name for entry in entries] == ["child", "report.csv"]
    file_path = path.join(entries[1].name)
    assert fs._resolve(file_path) == ("reports-bucket", prefix + "report.csv")
    assert b"".join([chunk async for chunk in await fs.read_stream(file_path)]) == b"listed report"
    child = path.join(entries[0].name)
    assert fs._resolve(child) == ("reports-bucket", prefix + "child")
    assert [entry.name for entry in await fs.list(child)] == ["nested.csv"]
    assert client.calls[-1][1]["Prefix"] == prefix + "child/"
    assert child.parent() == path
    await fs.list(child.parent())
    assert client.calls[-1][1]["Prefix"] == prefix
