"""Exact-key history is separate from current-object and mutation ownership."""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from aiobotocore.response import StreamingBody
from botocore.exceptions import ClientError, EndpointConnectionError

from aws_tui.domain.filesystem import (
    ConflictError,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProviderError,
)
from aws_tui.domain.s3_fs import S3FS
from aws_tui.domain.s3_versions import S3ObjectVersion

pytestmark = pytest.mark.unit
MODIFIED = datetime(2026, 10, 21, 12, tzinfo=UTC)
PATH = PathRef(("bucket", "prefix", "object"))


class VersionSession:
    """Only queued operations can run; no SDK or ambient credentials are used."""

    def __init__(self, *pages: dict[str, Any] | BaseException, state: Any = None) -> None:
        self.pages = deque(pages)
        self.state = {"Status": "Enabled"} if state is None else state
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = 0

    @asynccontextmanager
    async def client(self, service: str, **kwargs: Any):
        assert service == "s3"
        try:
            yield self
        finally:
            self.closed += 1

    async def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_object_versions", kwargs))
        assert self.pages, "unexpected extra listing request"
        result = self.pages.popleft()
        if isinstance(result, BaseException):
            raise result
        return result

    async def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_bucket_versioning", kwargs))
        if isinstance(self.state, BaseException):
            raise self.state
        return self.state


def row(version: str = "v1", *, key: str = "prefix/object", **changes: Any) -> dict[str, Any]:
    return {
        "Key": key,
        "VersionId": version,
        "LastModified": MODIFIED,
        "Size": 7,
        "IsLatest": False,
        "ETag": '"etag"',
        **changes,
    }


def error(code: str, operation: str = "ListObjectVersions") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "fixture refusal"}}, operation)


def fs(session: VersionSession, **kwargs: Any) -> S3FS:
    return S3FS(session=session, bucket=kwargs.pop("bucket", None), **kwargs)


async def test_exact_key_filters_neighbours_and_preserves_both_entry_kinds() -> None:
    marker = row("deleted", IsLatest=True)
    marker.pop("Size")
    session = VersionSession(
        {
            "Versions": [row("null"), row("neighbour", key="prefix/object-extra")],
            "DeleteMarkers": [marker, row("other-marker", key="prefix/object/child")],
            "IsTruncated": False,
        }
    )
    listing = await fs(session).list_versions(PATH)
    assert (listing.bucket, listing.key, listing.versioning) == (
        "bucket",
        "prefix/object",
        "enabled",
    )
    assert {v.version_id for v in listing.versions} == {"null", "deleted"}
    content = next(v for v in listing.versions if not v.is_delete_marker)
    assert (content.path, content.bucket, content.key) == (PATH, "bucket", "prefix/object")
    assert (content.version_id, content.modified, content.size, content.is_latest) == (
        "null",
        MODIFIED,
        7,
        False,
    )
    deleted = next(v for v in listing.versions if v.is_delete_marker)
    assert deleted.size is None
    assert deleted.is_latest
    assert deleted.modified == MODIFIED
    assert session.calls[0] == (
        "list_object_versions",
        {"Bucket": "bucket", "Prefix": "prefix/object", "MaxKeys": 1000},
    )
    assert session.closed == 1


async def test_interleaved_pages_use_both_markers_without_losing_entries() -> None:
    session = VersionSession(
        {
            "Versions": [row("v3")],
            "DeleteMarkers": [row("d4", IsLatest=True)],
            "IsTruncated": True,
            "NextKeyMarker": "prefix/object",
            "NextVersionIdMarker": "v3",
        },
        {"Versions": [row("v1")], "DeleteMarkers": [row("d2")], "IsTruncated": False},
    )
    listing = await fs(session).list_versions(PATH)
    assert {v.version_id for v in listing.versions} == {"v1", "d2", "v3", "d4"}
    reads = [kwargs for op, kwargs in session.calls if op == "list_object_versions"]
    assert len(reads) == 2
    assert reads[1] == {**reads[0], "KeyMarker": "prefix/object", "VersionIdMarker": "v3"}


@pytest.mark.parametrize(
    "markers",
    [
        {},
        {"NextKeyMarker": "prefix/object"},
        {"NextVersionIdMarker": "v1"},
        {"NextKeyMarker": "", "NextVersionIdMarker": "v1"},
    ],
)
async def test_truncated_page_requires_complete_marker_pair(markers: dict[str, Any]) -> None:
    session = VersionSession({"Versions": [row()], "IsTruncated": True, **markers})
    with pytest.raises(ProviderError, match=r"marker"):
        await fs(session).list_versions(PATH)
    assert sum(op == "list_object_versions" for op, _ in session.calls) == 1


@pytest.mark.parametrize("cycle", [False, True])
async def test_repeated_or_cyclic_marker_pair_fails_without_looping(cycle: bool) -> None:
    def page(marker: str) -> dict[str, Any]:
        return {
            "IsTruncated": True,
            "NextKeyMarker": "prefix/object",
            "NextVersionIdMarker": marker,
        }

    pages = [page("a"), page("b"), page("a")] if cycle else [page("a"), page("a")]
    session = VersionSession(*pages)
    with pytest.raises(ProviderError, match=r"marker"):
        await fs(session).list_versions(PATH)
    assert sum(op == "list_object_versions" for op, _ in session.calls) == len(pages)


@pytest.mark.parametrize(
    "change",
    [
        {"VersionId": ""},
        {"VersionId": None},
        {"Size": -1},
        {"Size": True},
        {"LastModified": None},
        {"IsLatest": "false"},
    ],
)
async def test_malformed_target_row_is_not_silently_omitted(change: dict[str, Any]) -> None:
    session = VersionSession({"Versions": [row(**change)], "IsTruncated": False})
    with pytest.raises(ProviderError, match=r"version.*response|invalid.*version"):
        await fs(session).list_versions(PATH)


@pytest.mark.parametrize(
    ("state", "expected", "text"),
    [
        ({}, "unversioned", "never enabled"),
        ({"Status": "Suspended"}, "suspended", "suspended"),
        ({"Status": "Enabled"}, "enabled", "enabled"),
    ],
)
async def test_bucket_states_are_distinct_and_do_not_hide_retained_null_versions(
    state: dict[str, str], expected: str, text: str
) -> None:
    session = VersionSession({"Versions": [row("null")]}, state=state)
    listing = await fs(session).list_versions(PATH)
    assert listing.versioning == expected
    assert text in listing.notice.lower()
    assert listing.versions[0].version_id == "null"


async def test_denied_bucket_state_does_not_veto_allowed_version_listing() -> None:
    session = VersionSession(
        {"Versions": [row()]}, state=error("AccessDenied", "GetBucketVersioning")
    )
    listing = await fs(session).list_versions(PATH)
    assert listing.versioning == "unavailable"
    assert "versioning state" in listing.notice.lower()
    assert "denied" in listing.notice.lower()
    assert listing.versions[0].version_id == "v1"


async def test_denied_list_permission_is_distinct_from_empty_history() -> None:
    session = VersionSession(error("AccessDenied"))
    with pytest.raises(PermissionDeniedError, match=r"ListBucketVersions"):
        await fs(session).list_versions(PATH)


async def test_directory_bucket_is_refused_without_a_history_request() -> None:
    session = VersionSession()
    with pytest.raises(ProviderError, match=r"directory bucket"):
        await fs(session, bucket="demo--usw2-az1--x-s3").list_versions(PathRef(("object",)))
    assert not session.calls


async def test_literal_key_components_and_fixed_provider_prefix_are_retained() -> None:
    key = "fixed/a//../object"
    session = VersionSession({"Versions": [row(key=key)]})
    path = PathRef(("a", "", "..", "object"))
    listing = await fs(session, bucket="bucket", prefix="fixed").list_versions(path)
    assert listing.key == key
    assert listing.versions[0].path == path
    assert session.calls[0][1]["Prefix"] == key


@pytest.mark.parametrize("bound", ["pages", "entries"])
async def test_history_traversal_is_bounded_without_returning_partial_results(
    monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    import aws_tui.domain.s3_fs as module

    monkeypatch.setattr(module, "_MAX_LISTING_PAGES", 1)
    monkeypatch.setattr(module, "_MAX_LISTING_ENTRIES", 1)
    page = (
        {"Versions": [row(), row("v2")]}
        if bound == "entries"
        else {
            "Versions": [row()],
            "IsTruncated": True,
            "NextKeyMarker": "prefix/object",
            "NextVersionIdMarker": "v1",
        }
    )
    session = VersionSession(page)
    with pytest.raises(ProviderError, match=r"bounded.*limit"):
        await fs(session).list_versions(PATH)
    assert sum(op == "list_object_versions" for op, _ in session.calls) == 1


async def test_same_id_cannot_be_both_content_and_delete_marker() -> None:
    session = VersionSession({"Versions": [row()], "DeleteMarkers": [row()]})
    with pytest.raises(ProviderError, match=r"conflicting identity"):
        await fs(session).list_versions(PATH)


class BytesBody:
    def __init__(self, content: bytes = b"payload") -> None:
        self.content = content
        self.closed = False

    async def read(self, size: int) -> bytes:
        result, self.content = self.content[:size], self.content[size:]
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True


class ReadVersionSession(VersionSession):
    def __init__(self, *reads: tuple[str, dict[str, Any] | BaseException]) -> None:
        super().__init__()
        self.reads = deque(reads)

    async def _read(self, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, kwargs))
        assert self.reads, f"unexpected read: {operation}"
        expected, result = self.reads.popleft()
        assert operation == expected
        if isinstance(result, BaseException):
            raise result
        return result

    async def head_object(self, **kwargs: Any):
        return await self._read("head_object", kwargs)

    async def get_object_tagging(self, **kwargs: Any):
        return await self._read("get_object_tagging", kwargs)

    async def get_object(self, **kwargs: Any):
        return await self._read("get_object", kwargs)


def selected(version_id: str = "v1", **changes: Any) -> S3ObjectVersion:
    return S3ObjectVersion(
        **{
            "path": PATH,
            "bucket": "bucket",
            "key": "prefix/object",
            "version_id": version_id,
            "modified": MODIFIED,
            "size": 7,
            "is_latest": False,
            "is_delete_marker": False,
            "etag": '"etag"',
            **changes,
        }
    )


def head(version_id: str = "v1", **changes: Any) -> dict[str, Any]:
    return {
        "VersionId": version_id,
        "LastModified": MODIFIED,
        "ContentLength": 7,
        "ETag": '"etag"',
        "Metadata": {"owner": "test"},
        **changes,
    }


@pytest.mark.parametrize("version_id", ["v1", "null", "id +/="])
async def test_version_source_pins_every_head_and_get_to_the_selected_id(version_id: str) -> None:
    body = BytesBody()
    session = ReadVersionSession(
        ("head_object", head(version_id)),
        ("head_object", head(version_id)),
        ("get_object", head(version_id, Body=body)),
    )
    source = fs(session).version_source(selected(version_id))
    entry = await source.stat(PATH)
    assert entry.size == 7
    assert entry.modified == MODIFIED
    stream = await source.read_stream(PATH, chunk_size=3)
    assert b"".join([chunk async for chunk in stream]) == b"payload"
    assert body.closed
    assert [op for op, _ in session.calls] == ["head_object", "head_object", "get_object"]
    assert all(kwargs["VersionId"] == version_id for _, kwargs in session.calls)
    assert all(kwargs["IfMatch"] == '"etag"' for _, kwargs in session.calls)


@pytest.mark.parametrize("version_id", ["v1", "null"])
async def test_details_checksum_fallback_and_tags_keep_selected_version(version_id: str) -> None:
    session = ReadVersionSession(
        ("head_object", error("AccessDenied", "HeadObject")),
        ("head_object", head(version_id)),
        ("get_object_tagging", {"TagSet": [{"Key": "tag", "Value": "value"}]}),
    )
    result = await fs(session).read_version_details(selected(version_id))
    assert result.version_id == version_id
    assert result.tags == (("tag", "value"),)
    assert result.checksums_error is not None
    assert all(kwargs["VersionId"] == version_id for _, kwargs in session.calls)
    assert session.calls[-1] == (
        "get_object_tagging",
        {"Bucket": "bucket", "Key": "prefix/object", "VersionId": version_id},
    )


@pytest.mark.parametrize("omit_id", [True, False])
async def test_null_response_may_omit_id_but_present_mismatch_is_rejected(omit_id: bool) -> None:
    response = head("another")
    if omit_id:
        response.pop("VersionId")
    session = ReadVersionSession(("head_object", response))
    source = fs(session).version_source(selected("null"))
    if omit_id:
        assert (await source.stat(PATH)).size == 7
    else:
        with pytest.raises(ConflictError, match=r"version.*changed|version.*mismatch"):
            await source.stat(PATH)
    assert session.calls[0][1]["VersionId"] == "null"


@pytest.mark.parametrize(
    "change",
    [{"ETag": '"new"'}, {"ContentLength": 8}, {"LastModified": datetime(2026, 10, 22, tzinfo=UTC)}],
)
async def test_changed_null_observation_cannot_be_retrieved_as_the_chosen_version(
    change: dict[str, Any],
) -> None:
    session = ReadVersionSession(("head_object", head("null", **change)))
    with pytest.raises(ConflictError, match=r"changed"):
        await fs(session).version_source(selected("null")).read_stream(PATH)
    assert [op for op, _ in session.calls] == ["head_object"]


async def test_get_response_identity_is_checked_and_body_closed_on_rejection() -> None:
    body = BytesBody()
    session = ReadVersionSession(
        ("head_object", head()), ("get_object", head("unexpected", Body=body))
    )
    stream = await fs(session).version_source(selected()).read_stream(PATH)
    with pytest.raises(ConflictError, match=r"version.*changed|version.*mismatch"):
        _ = [chunk async for chunk in stream]
    assert body.closed


async def test_delete_marker_has_no_downloadable_content_or_mutation_route() -> None:
    session = ReadVersionSession()
    with pytest.raises(ConflictError, match=r"delete marker"):
        await (
            fs(session).version_source(selected(is_delete_marker=True, size=None)).read_stream(PATH)
        )
    assert not session.calls


@pytest.mark.parametrize(
    ("code", "kind", "message"),
    [
        ("NoSuchVersion", NotFoundError, "missing or expired"),
        ("404", NotFoundError, "missing or expired"),
        ("AccessDenied", PermissionDeniedError, "GetObjectVersion"),
        ("InvalidObjectState", ProviderError, "restore"),
        ("MethodNotAllowed", ConflictError, "delete marker"),
    ],
)
async def test_exact_version_errors_have_distinct_actionable_messages(
    code: str, kind: type[Exception], message: str
) -> None:
    session = ReadVersionSession(("head_object", error(code, "HeadObject")))
    with pytest.raises(kind, match=message):
        await fs(session).version_source(selected()).read_stream(PATH)
    assert session.calls[0][1]["VersionId"] == "v1"


async def test_historical_reader_cannot_redirect_to_another_path_or_be_moved() -> None:
    session = ReadVersionSession()
    source = fs(session).version_source(selected())
    with pytest.raises(ConflictError, match=r"bound|exact"):
        await source.stat(PathRef(("bucket", "another")))
    with pytest.raises(ConflictError, match=r"read.only|historical"):
        await source.preflight_move_revision(PATH, '"etag"')
    assert not session.calls


async def test_exact_historical_object_ending_in_slash_avoids_directory_fallback() -> None:
    path = PathRef(("bucket", "prefix", "marker", ""))
    session = ReadVersionSession(("head_object", head()))
    version = selected(path=path, key="prefix/marker/")
    assert (await fs(session).version_source(version).stat(path)).size == 7
    assert session.calls[0][1]["Key"] == "prefix/marker/"


async def test_sdk_stream_wrapper_must_remain_the_reader_after_context_entry() -> None:
    class RawResponse:
        def __init__(self) -> None:
            self.content = BytesBody()
            self.closed = False

        async def read(self) -> bytes:
            raise AssertionError("read the SDK StreamingBody, not the raw response")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any) -> None:
            self.closed = True

    raw = RawResponse()
    wrapped = StreamingBody(raw, 7)
    session = ReadVersionSession(("head_object", head()), ("get_object", head(Body=wrapped)))
    stream = await fs(session).version_source(selected()).read_stream(PATH, chunk_size=3)
    assert b"".join([chunk async for chunk in stream]) == b"payload"
    assert raw.closed
    assert wrapped.tell() == 7


@pytest.mark.parametrize("version_id", ["v1", "null"])
async def test_mismatching_tag_response_is_unavailable_not_mislabeled(version_id: str) -> None:
    session = ReadVersionSession(
        ("head_object", head(version_id)),
        (
            "get_object_tagging",
            {"VersionId": "another", "TagSet": [{"Key": "wrong", "Value": "row"}]},
        ),
    )
    details = await fs(session).read_version_details(selected(version_id))
    assert details.version_id == version_id
    assert details.tags is None
    assert details.tags_error is not None
    assert "version" in details.tags_error.lower()
    assert session.calls[-1][1]["VersionId"] == version_id


async def test_ancillary_version_state_transport_failure_keeps_completed_history() -> None:
    session = VersionSession(
        {"Versions": [row()]}, state=EndpointConnectionError(endpoint_url="http://fixture.invalid")
    )
    listing = await fs(session).list_versions(PATH)
    assert listing.versioning == "unavailable"
    assert "unreachable" in listing.notice.lower()
    assert listing.versions[0].version_id == "v1"
