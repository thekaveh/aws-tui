"""Exact directory identity across S3 operations, using only in-memory fake calls."""

from collections.abc import AsyncIterator

import pytest
from botocore.exceptions import ClientError

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProviderError,
)
from aws_tui.domain.s3_fs import S3FS

pytestmark = pytest.mark.unit


class DirectoryClient:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.calls = []
        self.mutations = []

    def client(self, service, **kwargs):
        assert service == "s3"
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        if kwargs["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ETag": self.objects[kwargs["Key"]], "ContentLength": 0}

    async def list_objects_v2(self, **kwargs):
        self.calls.append(("list", kwargs))
        objects = [
            {"Key": key, "ETag": etag}
            for key, etag in sorted(self.objects.items())
            if key.startswith(kwargs["Prefix"])
        ][: kwargs.get("MaxKeys")]
        return {"Contents": objects, "KeyCount": len(objects)}

    async def put_object(self, **kwargs):
        self.mutations.append(("put", kwargs))
        self.objects[kwargs["Key"]] = '"created"'
        return {}

    async def delete_object(self, **kwargs):
        self.mutations.append(("delete", kwargs))
        if "IfMatch" in kwargs and self.objects.get(kwargs["Key"]) != kwargs["IfMatch"]:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "DeleteObject")
        self.objects.pop(kwargs["Key"], None)
        return {}

    async def delete_objects(self, **kwargs):
        self.mutations.append(("batch", kwargs))
        for obj in kwargs["Delete"]["Objects"]:
            del self.objects[obj["Key"]]
        return {}

    def __getattr__(self, name):
        pytest.fail(f"unexpected client operation: {name}")


@pytest.fixture(params=[None, "reports-bucket"])
def bucket(request):
    return request.param


@pytest.fixture(params=["", "/base//root/"])
def configured_prefix(request):
    return request.param


@pytest.fixture(
    params=[
        (("daily",), "daily/"),
        (("daily", ""), "daily//"),
        (("daily", "", ""), "daily///"),
        (("",), "/"),
        (("", "daily", ""), "/daily//"),
        (("daily", "", "雪%2F?#"), "daily//雪%2F?#/"),
    ]
)
def directory(request, bucket, configured_prefix):
    segments, relative_marker = request.param
    base = configured_prefix.strip("/")
    marker = (base + "/" if base else "") + relative_marker
    key = marker[:-1]
    # Parent markers and sibling-prefix files must survive every operation.
    outside = {key + "unrelated.csv": '"sibling"'}
    if key.endswith("/"):
        outside[key] = '"parent-marker"'
    client = DirectoryClient(outside)
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    path = PathRef(segments if bucket else ("reports-bucket", *segments))
    return fs, client, path, marker, outside


@pytest.mark.parametrize("content", ["missing", "marker", "child"])
async def test_stat_uses_only_requested_directory(directory, content):
    fs, client, path, marker, outside = directory
    if content != "missing":
        client.objects[marker + ("child.csv" if content == "child" else "")] = '"target"'
        assert (await fs.stat(path)).kind is EntryKind.DIRECTORY
    else:
        with pytest.raises(NotFoundError):
            await fs.stat(path)
    assert [kwargs["Prefix"] for op, kwargs in client.calls if op == "list"] == [marker]
    if not marker[:-1] or marker[:-1].endswith("/"):
        assert not any(op == "head" for op, _ in client.calls)
    assert not client.mutations
    assert all(client.objects[key] == etag for key, etag in outside.items())


async def test_mkdir_writes_exact_marker(directory):
    fs, client, path, marker, outside = directory
    await fs.mkdir(path)
    assert client.mutations == [("put", {"Bucket": "reports-bucket", "Key": marker, "Body": b""})]
    assert all(client.objects[key] == etag for key, etag in outside.items())


@pytest.mark.parametrize("target_marker", [False, True])
async def test_recursive_delete_confines_all_keys_to_requested_directory(directory, target_marker):
    fs, client, path, marker, outside = directory
    targets = {marker + "child.csv": '"child"', marker + "nested/deep.csv": '"nested"'}
    if target_marker:
        targets[marker] = '"target-marker"'
    client.objects.update(targets)
    await fs.delete(path)
    assert client.objects == outside
    assert [kwargs["Prefix"] for op, kwargs in client.calls if op == "list"] == [marker]
    assert all(op == "batch" for op, _ in client.mutations)
    assert {
        obj["Key"] for _, kwargs in client.mutations for obj in kwargs["Delete"]["Objects"]
    } == set(targets)


async def test_missing_directory_delete_does_not_reach_parent(directory):
    fs, client, path, marker, outside = directory
    with pytest.raises(NotFoundError):
        await fs.delete(path)
    assert client.objects == outside
    assert not client.mutations
    assert [kwargs["Prefix"] for op, kwargs in client.calls if op == "list"] == [marker]


@pytest.mark.parametrize("content", ["missing", "marker", "child", "marker_and_child"])
async def test_empty_directory_removal_checks_exact_marker(directory, content):
    fs, client, path, marker, outside = directory
    if "marker" in content:
        client.objects[marker] = '"target-marker"'
    if "child" in content:
        client.objects[marker + "child.csv"] = '"child"'
        before = dict(client.objects)
        with pytest.raises(ConflictError, match="not empty"):
            await fs.delete_empty_directory(path)
        assert client.objects == before
        assert not client.mutations
    else:
        await fs.delete_empty_directory(path)
        assert client.objects == outside
        assert client.mutations == (
            [("delete", {"Bucket": "reports-bucket", "Key": marker, "IfMatch": '"target-marker"'})]
            if content == "marker"
            else []
        )
    assert client.calls == [("list", {"Bucket": "reports-bucket", "Prefix": marker, "MaxKeys": 2})]


async def test_directory_rename_remains_refused(directory):
    fs, client, path, marker, _ = directory
    client.objects[marker] = '"target-marker"'
    with pytest.raises(ProviderError, match="directory rename"):
        await fs.rename(path, path.parent().join("renamed"))
    assert not client.mutations
    assert [kwargs["Prefix"] for op, kwargs in client.calls if op == "list"] == [marker]


@pytest.mark.parametrize("method", ["delete", "delete_empty_directory", "mkdir", "stat"])
async def test_provider_and_bucket_root_guards(bucket, configured_prefix, method):
    client = DirectoryClient({"base//root/": '"keep"'})
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    roots = [PathRef()]
    if bucket is None:
        roots.append(PathRef(("reports-bucket",)))
    for path in roots:
        if method == "stat":
            assert (await fs.stat(path)).kind is EntryKind.DIRECTORY
        elif method == "mkdir" and path.is_root:
            await fs.mkdir(path)
        else:
            with pytest.raises(ProviderError, match=r"root|bucket"):
                await getattr(fs, method)(path)
    assert not client.calls
    assert not client.mutations


@pytest.mark.parametrize("segments", [("daily", ""), ("daily", "", ""), ("",)])
async def test_conditional_delete_refuses_directory_only_path(bucket, configured_prefix, segments):
    client = DirectoryClient({"daily/": '"parent-marker"', "daily//": '"target-marker"'})
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    path = PathRef(segments if bucket else ("reports-bucket", *segments))
    with pytest.raises(ProviderError, match="file path"):
        await fs.delete(path, expected_etag='"parent-marker"')
    assert not client.calls
    assert not client.mutations


@pytest.mark.parametrize("stale", [False, True])
async def test_conditional_file_delete_preserves_revision_and_prefix_boundary(
    bucket, configured_prefix, stale
):
    base = configured_prefix.strip("/")
    key = (base + "/" if base else "") + "daily//report.csv"
    client = DirectoryClient({key: '"current"', key + "/child": '"keep"'})
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    path = PathRef(
        ("daily", "", "report.csv") if bucket else ("reports-bucket", "daily", "", "report.csv")
    )
    if stale:
        with pytest.raises(ConflictError):
            await fs.delete(path, expected_etag='"stale"')
        assert client.objects[key] == '"current"'
    else:
        await fs.delete(path, expected_etag='"current"')
        assert key not in client.objects
    assert client.objects[key + "/child"] == '"keep"'
    assert client.mutations == [
        (
            "delete",
            {
                "Bucket": "reports-bucket",
                "Key": key,
                "IfMatch": '"stale"' if stale else '"current"',
            },
        )
    ]
    assert not client.calls


async def test_unconditional_file_prefix_ambiguity_still_refused(bucket, configured_prefix):
    base = configured_prefix.strip("/")
    key = (base + "/" if base else "") + "daily//report.csv"
    original = {key: '"file"', key + "/child": '"child"'}
    client = DirectoryClient(original)
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    path = PathRef(
        ("daily", "", "report.csv") if bucket else ("reports-bucket", "daily", "", "report.csv")
    )
    with pytest.raises(ProviderError, match="ambiguous S3 name"):
        await fs.delete(path)
    assert client.objects == original
    assert not client.mutations


@pytest.mark.parametrize("operation", ["read", "write", "details", "rename_destination"])
@pytest.mark.parametrize("segments", [("daily", ""), ("daily", "", ""), ("",), ()])
async def test_file_operations_refuse_directory_only_keys(
    bucket, configured_prefix, operation, segments
):
    client = DirectoryClient({"source.csv": '"source"', "daily/": '"parent-marker"'})
    fs = S3FS(session=client, bucket=bucket, prefix=configured_prefix)
    path = PathRef(segments if bucket else ("reports-bucket", *segments))

    async def content() -> AsyncIterator[bytes]:
        pytest.fail("directory rejection must precede consuming input")
        yield b"unreachable"

    async def invoke():
        if operation == "read":
            await fs.read_stream(path)
        elif operation == "write":
            await fs.write_stream(path, content())
        elif operation == "details":
            await fs.read_object_details(path)
        else:
            source = PathRef(("source.csv",) if bucket else ("reports-bucket", "source.csv"))
            await fs.rename(source, path)

    with pytest.raises(ProviderError, match=r"file path|root|object path"):
        await invoke()
    assert not client.calls
    assert not client.mutations


@pytest.mark.parametrize("operation", ["stat", "copy", "move", "rename"])
async def test_ambiguous_object_and_prefix_never_resolve_to_wrong_source(tmp_path, operation):
    from aws_tui.domain.cross_fs import CrossFsCopy, CrossFsMove
    from aws_tui.domain.local_fs import LocalFS

    original = {"k": '"file"', "k/child": '"child"'}
    client = DirectoryClient(original)
    fs = S3FS(session=client, bucket="b")
    path = PathRef(("k",))
    destination = LocalFS(root=tmp_path)

    async def invoke():
        if operation == "stat":
            await fs.stat(path)
        elif operation == "rename":
            await fs.rename(path, PathRef(("renamed",)))
        elif operation == "copy":
            await CrossFsCopy(source=fs, destination=destination).copy(path, path)
        else:
            await CrossFsMove(source=fs, destination=destination).move(path, path)

    with pytest.raises(ProviderError, match="ambiguous S3 name"):
        await invoke()
    assert client.objects == original
    assert not client.mutations
    assert not list(tmp_path.iterdir())


async def test_stat_denied_ambiguity_probe_stays_fail_closed():
    class RestrictedClient(DirectoryClient):
        async def list_objects_v2(self, **kwargs):
            self.calls.append(("list", kwargs))
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")

    client = RestrictedClient({"k": '"file"'})
    fs = S3FS(session=client, bucket="b")
    with pytest.raises(PermissionDeniedError):
        await fs.stat(PathRef(("k",)))
    assert client.calls == [
        ("head", {"Bucket": "b", "Key": "k"}),
        ("list", {"Bucket": "b", "Prefix": "k/", "MaxKeys": 1}),
    ]
    assert not client.mutations


@pytest.mark.parametrize("operation", ["list", "copy", "move"])
async def test_empty_common_prefix_component_cannot_publish_incomplete_transfer(
    tmp_path, operation
):
    from aws_tui.domain.cross_fs import CrossFsCopy, CrossFsMove
    from aws_tui.domain.local_fs import LocalFS

    class DelimitedClient(DirectoryClient):
        async def list_objects_v2(self, **kwargs):
            if "Delimiter" in kwargs:
                self.calls.append(("list", kwargs))
                # Real S3 collapses the empty child of daily/ to daily//.
                return {"CommonPrefixes": [{"Prefix": "daily//"}], "Contents": []}
            return await super().list_objects_v2(**kwargs)

    original = {"daily//child": '"child"'}
    client = DelimitedClient(original)
    fs = S3FS(session=client, bucket="b")
    path = PathRef(("daily",))
    destination = LocalFS(root=tmp_path)

    async def invoke():
        if operation == "list":
            await fs.list(path)
        elif operation == "copy":
            await CrossFsCopy(source=fs, destination=destination).copy(path, path)
        else:
            await CrossFsMove(source=fs, destination=destination).move(path, path)

    with pytest.raises(ProviderError, match=r"empty.*component"):
        await invoke()
    assert client.objects == original
    assert not client.mutations
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("operation", ["copy", "move"])
@pytest.mark.parametrize(
    "alias", ["bucketless", "configured-prefix", "endpoint-slash", "endpoint-authority"]
)
async def test_s3_path_aliases_are_refused_before_read_or_mutation(operation, alias):
    from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove

    client = DirectoryClient({"k": '"empty"', "base/k": '"empty"'})
    source = S3FS(session=client, bucket="b", endpoint_url="https://s3.example.test")
    source_path = PathRef(("k",))
    if alias == "bucketless":
        destination = S3FS(session=client, bucket=None, endpoint_url="https://s3.example.test")
        target = PathRef(("b", "k"))
    elif alias == "configured-prefix":
        source = S3FS(
            session=client, bucket="b", prefix="base", endpoint_url="https://s3.example.test"
        )
        destination = S3FS(session=client, bucket="b", endpoint_url="https://s3.example.test")
        target = PathRef(("base", "k"))
    elif alias == "endpoint-authority":
        destination = S3FS(session=client, bucket="b", endpoint_url="https://S3.EXAMPLE.TEST:443")
        target = source_path
    else:
        destination = S3FS(session=client, bucket="b", endpoint_url="https://s3.example.test/")
        target = source_path
    transfer = (CrossFsCopy if operation == "copy" else CrossFsMove)(
        source=source, destination=destination
    )

    async def invoke():
        if operation == "copy":
            await transfer.copy(source_path, target, on_conflict=ConflictResolution.OVERWRITE)
        else:
            await transfer.move(source_path, target, on_conflict=ConflictResolution.OVERWRITE)

    with pytest.raises(ConflictError, match="same path"):
        await invoke()
    assert not client.mutations
    assert not any(op == "get" for op, _ in client.calls)


async def test_s3_alias_descendant_directory_is_refused_before_staging():
    from aws_tui.domain.cross_fs import CrossFsCopy

    client = DirectoryClient({"root/a": '"a"'})
    source = S3FS(session=client, bucket="b")
    destination = S3FS(session=client, bucket=None)
    with pytest.raises(ConflictError, match="inside source"):
        await CrossFsCopy(source=source, destination=destination).copy(
            PathRef(("root",)), PathRef(("b", "root", "child"))
        )
    assert not client.mutations
