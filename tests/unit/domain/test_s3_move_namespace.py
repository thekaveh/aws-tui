"""Destructive move admission against real loopback S3 HTTP storage."""

from __future__ import annotations

import multiprocessing

import aioboto3
import pytest

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import ConflictError, NotFoundError, PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.s3_fs import S3FS
from tests.unit.domain.test_cross_fs import _put_file, _read_file


def _session():
    return aioboto3.Session(
        aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1"
    )


def _route(session, endpoint, kind, *, bucket="move-alias", **kwargs):
    fs = S3FS(
        session=session,
        bucket=None if kind == "bucketless" else bucket,
        prefix="root" if kind == "prefix" else "",
        endpoint_url=endpoint,
        force_path_style=True,
        **kwargs,
    )
    path = {
        "fixed": ("root", "key"),
        "bucketless": (bucket, "root", "key"),
        "prefix": ("key",),
    }[kind]
    return fs, PathRef(path)


@pytest.mark.parametrize("payload", [b"", b"one-part payload"], ids=["empty", "multipart"])
@pytest.mark.parametrize(
    ("source_route", "destination_route"),
    [("fixed", "fixed"), ("fixed", "bucketless"), ("prefix", "fixed"), ("bucketless", "prefix")],
)
async def test_alias_self_move_refuses_before_http_mutation(
    s3_endpoint, payload, source_route, destination_route
):
    session = _session()
    requests = []
    session.events.register(
        "before-send.s3", lambda request, **_kwargs: requests.append(request.method)
    )
    async with session.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="move-alias")
    source, src = _route(session, s3_endpoint, source_route)
    destination, dst = _route(
        session, s3_endpoint.replace("127.0.0.1", "localhost"), destination_route
    )
    await _put_file(source, src, payload)
    assert await _read_file(destination, dst) == payload
    requests.clear()

    with pytest.raises(ConflictError, match=r"same path|namespace"):
        await CrossFsMove(source=source, destination=destination).move(
            src, dst, on_conflict=ConflictResolution.OVERWRITE
        )

    assert set(requests) <= {"GET", "HEAD"}
    assert await _read_file(source, src) == payload
    assert await _read_file(destination, dst) == payload


@pytest.mark.parametrize(
    ("source_namespace", "destination_namespace"),
    [(None, None), ("shared", "shared"), (None, "known"), ("known", None)],
)
async def test_unknown_or_shared_namespaces_cannot_authorize_an_alias_self_move(
    s3_endpoint, source_namespace, destination_namespace
):
    session = _session()
    async with session.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="move-alias")
    source, src = _route(session, s3_endpoint, "fixed", move_namespace=source_namespace)
    destination, dst = _route(
        session,
        s3_endpoint.replace("127.0.0.1", "localhost"),
        "bucketless",
        move_namespace=destination_namespace,
    )
    await _put_file(source, src, b"keep")
    with pytest.raises(ConflictError, match="same path"):
        await CrossFsMove(source=source, destination=destination).move(
            src, dst, on_conflict=ConflictResolution.OVERWRITE
        )
    assert await _read_file(source, src) == b"keep"


@pytest.mark.parametrize("endpoint_form", ["identical", "slash", "authority"])
async def test_known_same_endpoint_refusal_survives_different_asserted_namespaces(
    s3_endpoint, endpoint_form
):
    endpoint = s3_endpoint.replace("127.0.0.1", "localhost")
    destination_endpoint = {
        "identical": endpoint,
        "slash": endpoint + "/",
        "authority": endpoint.replace("localhost", "LOCALHOST") + "/",
    }[endpoint_form]
    session = _session()
    async with session.client("s3", endpoint_url=endpoint) as client:
        await client.create_bucket(Bucket="move-alias")
    source, src = _route(session, endpoint, "prefix", move_namespace="asserted-left")
    destination, dst = _route(
        session, destination_endpoint, "bucketless", move_namespace="asserted-right"
    )
    await _put_file(source, src, b"keep")
    with pytest.raises(ConflictError, match="same path"):
        await CrossFsMove(source=source, destination=destination).move(
            src, dst, on_conflict=ConflictResolution.OVERWRITE
        )
    assert await _read_file(source, src) == b"keep"
    assert await _read_file(destination, dst) == b"keep"


@pytest.mark.parametrize("moving", [False, True], ids=["copy-same-key", "move-different-key"])
async def test_alias_guard_preserves_copy_and_different_key_moves(s3_endpoint, moving):
    session = _session()
    async with session.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="move-alias")
    source, src = _route(session, s3_endpoint, "fixed")
    destination, dst = _route(session, s3_endpoint.replace("127.0.0.1", "localhost"), "bucketless")
    await _put_file(source, src, b"payload")
    if moving:
        dst = dst.with_name("other-key")
        assert await CrossFsMove(source=source, destination=destination).move(
            src, dst, on_conflict=ConflictResolution.OVERWRITE
        )
        with pytest.raises(NotFoundError):
            await source.stat(src)
    else:
        assert await CrossFsCopy(source=source, destination=destination).copy(
            src, dst, on_conflict=ConflictResolution.OVERWRITE
        )
        assert await _read_file(source, src) == b"payload"
    assert await _read_file(destination, dst) == b"payload"


def _serve_isolated_moto(ready, stop):
    from moto.server import ThreadedMotoServer

    from tests.unit.domain.conftest import _start_moto_server

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    try:
        _start_moto_server(server)
        host, port = server.get_host_and_port()
        ready.send(f"http://{host}:{port}")
        stop.wait(timeout=60)
    finally:
        server.stop()
        ready.close()


@pytest.fixture
def isolated_s3_endpoint():
    # Threaded servers in one process share Moto's backend. A spawned process
    # establishes an actually independent store for the disjointness control.
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    stop = context.Event()
    process = context.Process(target=_serve_isolated_moto, args=(writer, stop), daemon=True)
    process.start()
    writer.close()
    try:
        if not reader.poll(15):
            raise RuntimeError("isolated Moto server did not become ready")
        yield reader.recv()
    finally:
        stop.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        reader.close()


@pytest.mark.parametrize("trusted", [False, True])
async def test_distinct_stores_with_same_bucket_and_key_require_trusted_namespaces(
    s3_endpoint, isolated_s3_endpoint, trusted
):
    session = _session()
    for endpoint in (s3_endpoint, isolated_s3_endpoint):
        async with session.client("s3", endpoint_url=endpoint) as client:
            await client.create_bucket(Bucket="move-alias")
    source, src = _route(
        session, s3_endpoint, "fixed", move_namespace="independent-left" if trusted else None
    )
    destination, dst = _route(
        session,
        isolated_s3_endpoint,
        "bucketless",
        move_namespace="independent-right" if trusted else None,
    )
    await _put_file(source, src, b"original source")
    await _put_file(destination, dst, b"independent destination")
    assert await _read_file(source, src) == b"original source"
    assert await _read_file(destination, dst) == b"independent destination"
    mover = CrossFsMove(source=source, destination=destination)
    if trusted:
        assert await mover.move(src, dst, on_conflict=ConflictResolution.OVERWRITE)
        with pytest.raises(NotFoundError):
            await source.stat(src)
        assert await _read_file(destination, dst) == b"original source"
    else:
        with pytest.raises(ConflictError, match="same path"):
            await mover.move(src, dst, on_conflict=ConflictResolution.OVERWRITE)
        assert await _read_file(source, src) == b"original source"
        assert await _read_file(destination, dst) == b"independent destination"


@pytest.mark.parametrize("s3_is_source", [False, True])
async def test_virtual_service_root_is_rejected_in_mixed_provider_moves(tmp_path, s3_is_source):
    s3 = S3FS(session=object(), bucket=None)
    local = LocalFS(root=tmp_path)
    source, destination = (s3, local) if s3_is_source else (local, s3)
    with pytest.raises(ConflictError, match="virtual root"):
        await CrossFsMove(source=source, destination=destination).move(PathRef(), PathRef())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("segments", [("", "a", "", ".", "..", "%2F", "é"), ("key", "")])
def test_move_location_keeps_literal_bucket_and_key_components(segments):
    source = S3FS(session=object(), bucket="bucket", prefix="root")
    destination = S3FS(session=object(), bucket=None)
    expected = PathRef(("bucket", "root", *segments))
    assert source.canonical_move_path(PathRef(segments)) == (None, expected)
    assert destination.canonical_move_path(expected) == (None, expected)
    assert S3FS(session=object(), bucket="bucket").canonical_move_path(PathRef()) == (
        None,
        PathRef(("bucket",)),
    )


@pytest.mark.parametrize("identifier", ["", "  ", 1, False])
def test_move_namespace_rejects_an_invalid_assertion(identifier):
    with pytest.raises(ValueError, match="nonempty trusted identifier"):
        S3FS(session=object(), bucket="bucket", move_namespace=identifier)


async def test_alias_directory_descendant_refuses_before_http_mutation(s3_endpoint):
    session = _session()
    requests = []
    session.events.register(
        "before-send.s3", lambda request, **_kwargs: requests.append(request.method)
    )
    async with session.client("s3", endpoint_url=s3_endpoint) as client:
        await client.create_bucket(Bucket="move-alias")
    source = S3FS(session=session, bucket="move-alias", endpoint_url=s3_endpoint)
    destination = S3FS(
        session=session,
        bucket=None,
        endpoint_url=s3_endpoint.replace("127.0.0.1", "localhost"),
        force_path_style=True,
    )
    await _put_file(source, PathRef(("tree", "keep")), b"keep")
    requests.clear()
    with pytest.raises(ConflictError, match="inside source directory"):
        await CrossFsMove(source=source, destination=destination).move(
            PathRef(("tree",)), PathRef(("move-alias", "tree", "nested"))
        )
    assert set(requests) <= {"GET", "HEAD"}
    assert await _read_file(source, PathRef(("tree", "keep"))) == b"keep"


@pytest.mark.parametrize(
    "explicit_endpoint",
    [
        "https://s3.us-east-1.amazonaws.com",
        "https://s3.dualstack.us-east-1.amazonaws.com",
    ],
)
async def test_default_and_explicit_aws_endpoints_do_not_prove_disjointness(explicit_endpoint):
    # Admission-only check: session=object prohibits any SDK or live AWS call.
    source = S3FS(session=object(), bucket="bucket")
    destination = S3FS(session=object(), bucket="bucket", endpoint_url=explicit_endpoint)
    with pytest.raises(ConflictError, match="same path"):
        await CrossFsMove(source=source, destination=destination).move(
            PathRef(("key",)), PathRef(("key",)), on_conflict=ConflictResolution.OVERWRITE
        )
