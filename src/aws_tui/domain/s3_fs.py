"""S3FS — :class:`~.filesystem.FileSystemProvider` over an S3 bucket.

Wraps ``aioboto3`` to expose object storage as a filesystem:

- ``bucket=None``: the FS is rooted at the *service* root. ``list(root)``
  returns one DIRECTORY entry per accessible bucket.
- ``bucket=set``: standard object listing under ``prefix`` using
  ``Delimiter="/"``. Common prefixes surface as DIRECTORY entries.
- ``mkdir``: writes an empty marker object whose key ends with ``/`` so
  the "directory" shows up in subsequent listings.
- ``delete``: object delete for a file; for a "directory" key, enumerates
  every key under the prefix and batch-deletes 1000 at a time.
- ``rename``: server-side ``CopyObject`` + ``DeleteObject``.
- ``read_stream``: streams ``GetObject``'s body in fixed-size chunks.
- ``write_stream``: performs explicit sequential multipart upload, aborting
  the upload on failure or cancellation; empty streams use ``PutObject``.

Botocore ``ClientError`` codes are mapped to the ProviderError taxonomy
(NoSuchKey/NoSuchBucket → NotFound, AccessDenied → PermissionDenied,
EndpointConnection → Unreachable).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    ClientError,
    CredentialRetrievalError,
)

from aws_tui.domain.aws_auth import AWS_AUTH_ERROR_CODES, AWS_CREDENTIAL_EXCEPTIONS
from aws_tui.domain.aws_transport import AWS_TRANSPORT_EXCEPTIONS
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    ConflictError,
    EntryKind,
    FileEntry,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProgressCallback,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    TransferProgress,
)

# Family of transport-layer failures that the user should see as
# "endpoint unreachable" rather than a generic provider error. We
# build a single tuple so every site that translates a connection
# failure to ``ProviderUnreachableError`` catches the same shapes.
# - ``EndpointConnectionError`` — DNS / TCP-connect / TLS-handshake
#   failure (most common cause of "S3 unreachable" today).
# - ``ConnectTimeoutError`` / ``ReadTimeoutError`` — the connect/read
#   timeouts configured on the botocore client (10s / 60s) firing.
#   Subclasses of ``HTTPClientError``, NOT subclasses of
#   ``EndpointConnectionError`` — the original ``except
#   EndpointConnectionError`` chain missed them.
# - ``BotoConnectionError`` — the base ``ConnectionError`` for any
#   other transport failure shape botocore introduces in the future.
_TRANSPORT_FAILURE_EXCEPTIONS = AWS_TRANSPORT_EXCEPTIONS
_AUTH_FAILURE_EXCEPTIONS = AWS_CREDENTIAL_EXCEPTIONS

if TYPE_CHECKING:
    from collections.abc import Iterator

# Default streaming chunk size.
_DEFAULT_CHUNK_SIZE: int = 8 * 1024 * 1024
# Multipart bounds from the public S3 API.
_MAX_MULTIPART_PARTS: int = 10_000
_MAX_MULTIPART_PART_SIZE: int = 5 * 1024 * 1024 * 1024
# S3's documented per-object ceiling, NOT the multipart arithmetic maximum.
# `_MAX_MULTIPART_PARTS * _MAX_MULTIPART_PART_SIZE` is 48.8 TiB — what the part
# limits happen to allow — so the guard admitted objects roughly 10x larger than
# S3 accepts. An oversized upload then streamed every byte and failed at
# CompleteMultipartUpload with EntityTooLarge. AWS states the cap as "5 TB";
# 5 TiB is the conventional reading and is the more permissive of the two, so it
# cannot reject an upload S3 would have taken.
_MAX_OBJECT_SIZE: int = 5 * 1024**4
_MAX_COPY_OBJECT_SIZE: int = 5 * 1024 * 1024 * 1024
# S3 batch-delete limit.
_DELETE_BATCH_SIZE: int = 1000
_S3_REVISION_PREFIX = "s3:v1:"
# The file-manager protocol returns a complete directory as one value. Keep
# remote traversal bounded until that protocol grows an explicit continuation
# contract; fail instead of presenting a partial listing as complete.
_MAX_LISTING_ENTRIES: int = 10_000
_MAX_LISTING_PAGES: int = 100

# Alias for the builtin ``list`` so internal method annotations don't
# accidentally resolve to ``S3FS.list`` (which the class defines).
_List = list


class S3FS:
    """A FileSystemProvider over an S3 bucket (or service root).

    Parameters
    ----------
    session:
        An ``aioboto3.Session`` configured with credentials. M1's
        :class:`aws_tui.infra.aws_session.AwsSession` builds these.
    bucket:
        If ``None``, ``list(root)`` returns buckets. Otherwise, all
        operations are scoped to the given bucket.
    prefix:
        Optional key prefix prepended to every PathRef-derived key.
    endpoint_url:
        Optional custom S3 endpoint (e.g., MinIO). Pairs with
        ``force_path_style=True``.
    force_path_style:
        When True, S3 addressing uses path-style URLs (required by
        MinIO and many S3-compatible servers).
    verify_tls:
        Whether botocore should verify TLS certificates for HTTPS
        endpoints. Set False for explicitly configured self-signed
        development endpoints.
    """

    def __init__(
        self,
        *,
        session: aioboto3.Session,
        bucket: str | None,
        prefix: str = "",
        endpoint_url: str | None = None,
        force_path_style: bool = False,
        verify_tls: bool = True,
    ) -> None:
        self._session = session
        self._bucket: str | None = bucket
        self._prefix: str = prefix.strip("/")
        self._endpoint_url: str | None = endpoint_url
        self._verify_tls: bool = verify_tls
        # Apply the same retry / timeout policy spec §6.3 + §7.3 mandates for
        # every AWS client. infra/AwsSession.client() does the equivalent for
        # service callers; S3FS is constructed directly with an aioboto3
        # Session by S3Service, so the budget has to live here too.
        self._config = BotoConfig(
            s3={"addressing_style": "path" if force_path_style else "auto"},
            signature_version="s3v4",
            retries={"total_max_attempts": 6, "mode": "adaptive"},
            connect_timeout=10,
            read_timeout=60,
        )

    @property
    def storage_identity(self) -> tuple[str, str | None, str | None, str]:
        return ("s3", self._endpoint_url, self._bucket, self._prefix)

    atomic_write_replaces = True
    atomic_directory_replace = False

    # ------------------------------------------------------------------
    # Client helper
    # ------------------------------------------------------------------

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {"config": self._config}
        if self._endpoint_url is not None:
            kwargs["endpoint_url"] = self._endpoint_url
        kwargs["verify"] = self._verify_tls
        return self._session.client("s3", **kwargs)

    def _key_for(self, path: PathRef) -> str:
        """Convert a PathRef to an absolute S3 object key (within bucket)."""
        joined = "/".join(path.segments)
        if self._prefix:
            return f"{self._prefix}/{joined}" if joined else self._prefix
        return joined

    def _resolve(self, path: PathRef) -> tuple[str, str]:
        """Return ``(bucket, key)`` for ``path``.

        - When this S3FS has a fixed ``bucket``, the bucket comes from
          ``self._bucket`` and the key from the full path.
        - When this S3FS is bucketless (``bucket=None``, the service-root
          flavor used by ``S3Service``), the first path segment is the
          bucket and the rest becomes the key. This is what makes a
          single ``S3FS(bucket=None)`` instance drive both bucket-listing
          at the root *and* object operations inside any bucket — which
          is in turn what the dual-pane copy/delete/stat flows depend on.

        Raises :class:`ProviderError` if a non-root operation is requested
        on a bucketless FS with no bucket segment to peel off.
        """
        if self._bucket is not None:
            return self._bucket, self._key_for(path)
        if path.is_root or not path.segments:
            raise ProviderError("S3 path needs a bucket as its first segment")
        bucket = path.segments[0]
        sub = PathRef(path.segments[1:])
        return bucket, self._key_for(sub)

    # ------------------------------------------------------------------
    # list / stat
    # ------------------------------------------------------------------

    async def list(self, path: PathRef) -> list[FileEntry]:
        # bucket-less (service-root) S3FS: at root we list buckets; any
        # deeper path is interpreted with the first segment as the bucket
        # so the same provider can drive a single-pane "buckets → objects"
        # navigation (PaneVM.navigate_to appends one segment at a time).
        if self._bucket is None:
            if path.is_root:
                return await self._list_buckets()
            bucket = path.segments[0]
            sub = PathRef(path.segments[1:])
            prefix = self._key_for(sub)
            if prefix and not prefix.endswith("/"):
                prefix = f"{prefix}/"
            return await self._list_objects(prefix, bucket=bucket)
        prefix = self._key_for(path)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        return await self._list_objects(prefix)

    async def _list_buckets(self) -> _List[FileEntry]:
        entries: list[FileEntry] = []
        try:
            async with self._client() as s3:
                token: str | None = None
                seen_tokens: set[str] = set()
                page_count = 0
                while True:
                    if page_count >= _MAX_LISTING_PAGES:
                        raise ProviderError(
                            "S3 bucket listing exceeded the pagination safety limit"
                        )
                    page_count += 1
                    kwargs: dict[str, Any] = {"MaxBuckets": 1000}
                    if token is not None:
                        kwargs["ContinuationToken"] = token
                    resp = await s3.list_buckets(**kwargs)
                    for bucket in resp.get("Buckets", []):
                        entries.append(
                            FileEntry(
                                name=bucket["Name"],
                                kind=EntryKind.DIRECTORY,
                                size=None,
                                modified=_to_aware(bucket.get("CreationDate")),
                            )
                        )
                    if len(entries) > _MAX_LISTING_ENTRIES:
                        raise ProviderError("S3 bucket listing exceeded the listing safety limit")
                    next_token = resp.get("ContinuationToken")
                    if not next_token:
                        break
                    if next_token in seen_tokens:
                        raise ProviderError(
                            "S3 returned a repeated bucket-listing continuation token"
                        )
                    seen_tokens.add(next_token)
                    token = next_token
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, "buckets") from exc
        entries.sort(key=lambda e: e.name)
        return entries

    async def _list_objects(self, prefix: str, *, bucket: str | None = None) -> _List[FileEntry]:
        # When the caller passes ``bucket`` explicitly (virtual-root navigation
        # via the bucketless service FS), use that instead of ``self._bucket``.
        target_bucket = bucket if bucket is not None else self._bucket
        if target_bucket is None:
            # ``assert`` is removable under ``python -O``; raise so the
            # invariant survives optimized builds. The caller must
            # either be bucket-rooted (``self._bucket``) or pass an
            # explicit bucket via virtual-root navigation.
            raise ProviderError(
                "S3FS._list_objects requires a bucket — instance is "
                "bucketless and no explicit bucket= was passed."
            )
        entries: list[FileEntry] = []
        try:
            async with self._client() as s3:
                token: str | None = None
                seen_tokens: set[str] = set()
                page_count = 0
                while True:
                    if page_count >= _MAX_LISTING_PAGES:
                        raise ProviderError(
                            "S3 object listing exceeded the pagination safety limit"
                        )
                    page_count += 1
                    kwargs: dict[str, Any] = {
                        "Bucket": target_bucket,
                        "Prefix": prefix,
                        "Delimiter": "/",
                    }
                    if token is not None:
                        kwargs["ContinuationToken"] = token
                    resp = await s3.list_objects_v2(**kwargs)
                    for cp in resp.get("CommonPrefixes", []) or []:
                        key = cp["Prefix"]
                        name = key[len(prefix) :].rstrip("/")
                        if not name:
                            continue
                        entries.append(
                            FileEntry(
                                name=name,
                                kind=EntryKind.DIRECTORY,
                                size=None,
                                modified=None,
                            )
                        )
                    for obj in resp.get("Contents", []) or []:
                        key = obj["Key"]
                        name = key[len(prefix) :]
                        if not name or name.endswith("/"):
                            # Skip the directory marker for the current prefix.
                            continue
                        entries.append(
                            FileEntry(
                                name=name,
                                kind=EntryKind.FILE,
                                size=int(obj.get("Size", 0)),
                                modified=_to_aware(obj.get("LastModified")),
                                etag=_s3_revision_token(obj),
                            )
                        )
                    if len(entries) > _MAX_LISTING_ENTRIES:
                        raise ProviderError("S3 object listing exceeded the listing safety limit")
                    if not resp.get("IsTruncated"):
                        break
                    token = resp.get("NextContinuationToken")
                    if not token or token in seen_tokens:
                        raise ProviderError(
                            "S3 returned a truncated listing without a new continuation token"
                        )
                    seen_tokens.add(token)
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, prefix) from exc
        entries.sort(key=lambda e: (e.kind != EntryKind.DIRECTORY, e.name))
        return entries

    async def stat(self, path: PathRef) -> FileEntry:
        if path.is_root:
            return FileEntry(name="", kind=EntryKind.DIRECTORY, size=None, modified=None)
        if self._bucket is None and len(path.segments) == 1:
            # Bucketless FS, single-segment path → that segment IS a
            # bucket name. Report it as a directory (matches what list()
            # would do at this level).
            return FileEntry(
                name=path.segments[0], kind=EntryKind.DIRECTORY, size=None, modified=None
            )
        bucket, key = self._resolve(path)
        try:
            async with self._client() as s3:
                try:
                    resp = await s3.head_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                        # Maybe it's a directory; probe via list with that
                        # prefix.
                        marker = f"{key}/" if not key.endswith("/") else key
                        resp_list = await s3.list_objects_v2(
                            Bucket=bucket, Prefix=marker, MaxKeys=1
                        )
                        if resp_list.get("KeyCount", 0) > 0:
                            return FileEntry(
                                name=path.name,
                                kind=EntryKind.DIRECTORY,
                                size=None,
                                modified=None,
                            )
                        raise NotFoundError(path.as_posix()) from exc
                    raise _map_client_error(exc, key) from exc
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, key) from exc
        return FileEntry(
            name=path.name,
            kind=EntryKind.FILE,
            size=int(resp.get("ContentLength", 0)),
            modified=_to_aware(resp.get("LastModified")),
            etag=_s3_revision_token(resp),
        )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    async def mkdir(self, path: PathRef) -> None:
        if path.is_root:
            return
        bucket, key = self._resolve(path)
        if not key:
            # Single-segment path on a bucketless FS == bucket itself.
            # Creating buckets is out of scope for this layer.
            raise ProviderError("cannot mkdir a bucket via S3FS — use the AWS console / CLI")
        if not key.endswith("/"):
            key = f"{key}/"
        try:
            async with self._client() as s3:
                await s3.put_object(Bucket=bucket, Key=key, Body=b"")
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, key) from exc

    async def preflight_move_revision(self, path: PathRef, revision: str | None) -> None:
        if revision is None:
            raise ProviderError(f"S3 move source has no revision: {path.as_posix()}")
        parsed = _parse_s3_revision(revision)
        if parsed.version_id is not None:
            raise ProviderError(
                "cannot move a versioned S3 object: "
                "DeleteObject cannot condition the current key on VersionId"
            )
        if parsed.etag is None:
            raise ProviderError(f"S3 move source has no ETag: {path.as_posix()}")

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        if path.is_root:
            raise ProviderError("cannot delete the S3 filesystem provider root")
        if self._bucket is None and len(path.segments) == 1:
            # On a bucketless provider the first path segment is a bucket,
            # even when a configured prefix makes ``_resolve`` return a
            # non-empty key. Deleting it must never become a recursive
            # deletion of that configured prefix.
            raise ProviderError("cannot delete a bucket via S3FS — use the AWS console / CLI")
        bucket, key = self._resolve(path)
        if not key:
            raise ProviderError("cannot delete a bucket via S3FS — use the AWS console / CLI")
        try:
            async with self._client() as s3:
                if expected_etag is not None:
                    await s3.delete_object(**_conditional_delete_args(bucket, key, expected_etag))
                    return
                # Try object delete first; if that "succeeds" but no
                # such object existed, fall through to prefix-delete.
                try:
                    await s3.head_object(Bucket=bucket, Key=key)
                    file_exists = True
                except ClientError as exc:
                    if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                        file_exists = False
                    else:
                        raise _map_client_error(exc, key) from exc

                if file_exists:
                    await s3.delete_object(Bucket=bucket, Key=key)
                    return

                # Directory delete: enumerate + batch-delete.
                prefix = f"{key}/" if not key.endswith("/") else key
                deleted_any = False
                token: str | None = None
                seen_tokens: set[str] = set()
                page_count = 0
                deleted_count = 0
                while True:
                    if page_count >= _MAX_LISTING_PAGES:
                        raise ProviderError(
                            "S3 delete pagination safety limit exceeded after deleting "
                            f"{deleted_count} object(s)"
                        )
                    page_count += 1
                    list_kwargs: dict[str, Any] = {
                        "Bucket": bucket,
                        "Prefix": prefix,
                    }
                    if token is not None:
                        list_kwargs["ContinuationToken"] = token
                    resp = await s3.list_objects_v2(**list_kwargs)
                    next_token = resp.get("NextContinuationToken")
                    if resp.get("IsTruncated") and (not next_token or next_token in seen_tokens):
                        raise ProviderError(
                            "S3 returned a truncated delete listing without a new continuation token"
                        )
                    objects = resp.get("Contents") or []
                    if objects:
                        if deleted_count + len(objects) > _MAX_LISTING_ENTRIES:
                            raise ProviderError(
                                "S3 delete collection safety limit exceeded after deleting "
                                f"{deleted_count} object(s)"
                            )
                        deleted_any = True
                        for batch in _chunks(objects, _DELETE_BATCH_SIZE):
                            delete_response = await s3.delete_objects(
                                Bucket=bucket,
                                Delete={
                                    "Objects": [{"Key": o["Key"]} for o in batch],
                                    "Quiet": True,
                                },
                            )
                            errors = delete_response.get("Errors") or []
                            if errors:
                                codes = sorted(
                                    {str(error.get("Code") or "Unknown") for error in errors}
                                )
                                raise ProviderError(
                                    f"failed to delete {len(errors)} object(s) "
                                    f"from S3 (codes: {', '.join(codes)})"
                                )
                            deleted_count += len(batch)
                    if not resp.get("IsTruncated"):
                        break
                    token = next_token
                    assert token is not None
                    seen_tokens.add(token)
                if not deleted_any:
                    raise NotFoundError(path.as_posix())
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            # Outer catch for ClientErrors raised by delete_object,
            # list_objects_v2, or delete_objects after the initial
            # head_object probe — e.g. a bucket policy that grants
            # s3:GetObject but denies s3:DeleteObject. Without this
            # the raw botocore exception would bypass the
            # ProviderError taxonomy DualPaneVM expects.
            raise _map_client_error(exc, key) from exc

    async def delete_empty_directory(self, path: PathRef) -> None:
        if path.is_root:
            raise ProviderError("cannot delete the S3 filesystem provider root")
        if self._bucket is None and len(path.segments) == 1:
            raise ProviderError("cannot delete a bucket via S3FS — use the AWS console / CLI")
        bucket, key = self._resolve(path)
        if not key:
            raise ProviderError("cannot delete a bucket via S3FS — use the AWS console / CLI")
        marker = f"{key.rstrip('/')}/"
        try:
            async with self._client() as s3:
                response = await s3.list_objects_v2(Bucket=bucket, Prefix=marker, MaxKeys=2)
                objects = response.get("Contents") or []
                if any(obj.get("Key") != marker for obj in objects):
                    raise ConflictError(f"directory is not empty: {path.as_posix()}")
                marker_object = next((obj for obj in objects if obj.get("Key") == marker), None)
                if marker_object is None:
                    return
                kwargs: dict[str, Any] = {"Bucket": bucket, "Key": marker}
                marker_etag = marker_object.get("ETag")
                if marker_etag:
                    kwargs["IfMatch"] = _quoted_etag(str(marker_etag))
                await s3.delete_object(**kwargs)
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, marker) from exc

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        source_entry = await self.stat(src)
        if source_entry.kind == EntryKind.DIRECTORY:
            raise ProviderError("S3 directory rename is unsupported; move the directory instead")
        try:
            await self.stat(dst)
        except NotFoundError:
            pass
        else:
            raise ConflictError(dst.as_posix())
        src_bucket, src_key = self._resolve(src)
        dst_bucket, dst_key = self._resolve(dst)
        if not src_key or not dst_key:
            raise ProviderError("cannot rename buckets via S3FS — use the AWS console / CLI")
        if src_bucket != dst_bucket:
            raise ProviderError("cross-bucket rename is not supported by this provider")
        bucket = src_bucket
        try:
            async with self._client() as s3:
                try:
                    if source_entry.size is None:
                        raise ProviderError(f"S3 returned no size for {src.as_posix()}")
                    if source_entry.etag is None:
                        raise ProviderError(f"S3 returned no revision for {src.as_posix()}")
                    source_revision = _parse_s3_revision(source_entry.etag)
                    if source_revision.etag is None:
                        raise ProviderError(f"S3 returned no ETag for {src.as_posix()}")
                    if source_revision.version_id is not None:
                        raise ProviderError(
                            "cannot atomically rename a versioned S3 object: "
                            "DeleteObject cannot condition the current key on VersionId"
                        )
                    source_etag = _quoted_etag(source_revision.etag)
                    if source_entry.size <= _MAX_COPY_OBJECT_SIZE:
                        copy_task = asyncio.create_task(
                            s3.copy_object(
                                Bucket=bucket,
                                Key=dst_key,
                                CopySource={"Bucket": bucket, "Key": src_key},
                                CopySourceIfMatch=source_etag,
                                IfNoneMatch="*",
                            )
                        )
                        copy_response, copy_cancelled = await _drain_task(copy_task)
                    else:
                        copy_response, copy_cancelled = await self._multipart_copy(
                            s3,
                            bucket=bucket,
                            src_key=src_key,
                            dst_key=dst_key,
                            total_size=source_entry.size,
                            source_etag=source_etag,
                        )
                except ClientError as exc:
                    raise _map_client_error(exc, src_key) from exc
                response_revision = _copy_response_revision(copy_response, size=source_entry.size)
                if response_revision is None:
                    message = _manual_cleanup_message(
                        dst_key,
                        "the copy response ETag ownership token was missing",
                    )
                    if copy_cancelled:
                        cancellation = asyncio.CancelledError()
                        cancellation.add_note(message)
                        raise cancellation
                    raise ProviderError(message)
                if copy_cancelled:
                    failure = asyncio.CancelledError()
                    await _cleanup_failed_rename_copy(
                        s3,
                        bucket=bucket,
                        key=dst_key,
                        ownership_revision=response_revision,
                        failure=failure,
                    )
                    raise failure
                try:
                    delete_task = asyncio.create_task(
                        s3.delete_object(
                            **_conditional_delete_args(bucket, src_key, source_entry.etag)
                        )
                    )
                    _, cancelled = await _drain_task(delete_task)
                except BaseException as exc:
                    if not isinstance(exc, asyncio.CancelledError) and _cancellation_pending():
                        cancellation = asyncio.CancelledError()
                        cancellation.add_note(
                            f"S3 source delete failed after caller cancellation: {exc}"
                        )
                        await _cleanup_failed_rename_copy(
                            s3,
                            bucket=bucket,
                            key=dst_key,
                            ownership_revision=response_revision,
                            failure=cancellation,
                        )
                        raise cancellation from exc
                    await _cleanup_failed_rename_copy(
                        s3,
                        bucket=bucket,
                        key=dst_key,
                        ownership_revision=response_revision,
                        failure=exc,
                    )
                    if isinstance(exc, ClientError):
                        raise _map_client_error(exc, src_key) from exc
                    raise
                if cancelled:
                    raise asyncio.CancelledError
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            # Outer catch for the post-copy `delete_object(src_key)`
            # on line 441 — a partial rename (copy succeeded, source
            # delete denied) otherwise propagates raw botocore
            # ClientError instead of going through ProviderError.
            raise _map_client_error(exc, src_key) from exc

    async def _multipart_copy(
        self,
        s3: Any,
        *,
        bucket: str,
        src_key: str,
        dst_key: str,
        total_size: int,
        source_etag: str,
    ) -> tuple[dict[str, Any], bool]:
        """Copy a large object with the public multipart-copy API."""
        copy_source = {"Bucket": bucket, "Key": src_key}
        source_args: dict[str, Any] = {"Bucket": bucket, "Key": src_key}
        head = await s3.head_object(**source_args, IfMatch=source_etag)
        create_args = _multipart_copy_metadata(head)
        tag_response = await s3.get_object_tagging(**source_args)
        tags = tag_response.get("TagSet") or []
        if tags:
            create_args["Tagging"] = urlencode(
                [(str(tag["Key"]), str(tag["Value"])) for tag in tags]
            )

        upload_id: str | None = None
        try:
            create_task = asyncio.create_task(
                s3.create_multipart_upload(Bucket=bucket, Key=dst_key, **create_args)
            )
            created, cancelled = await _drain_task(create_task)
            upload_id = str(created["UploadId"])
            if cancelled:
                raise asyncio.CancelledError

            part_size = _multipart_part_size(total_size)
            parts: list[dict[str, Any]] = []
            for part_number, start in enumerate(range(0, total_size, part_size), start=1):
                end = min(start + part_size, total_size) - 1
                response = await s3.upload_part_copy(
                    Bucket=bucket,
                    Key=dst_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    CopySource=copy_source,
                    CopySourceRange=f"bytes={start}-{end}",
                    CopySourceIfMatch=source_etag,
                )
                etag = (response.get("CopyPartResult") or {}).get("ETag")
                if not etag:
                    raise ProviderError(
                        f"S3 multipart copy returned no ETag for part {part_number}"
                    )
                parts.append({"ETag": etag, "PartNumber": part_number})

            complete_task = asyncio.create_task(
                s3.complete_multipart_upload(
                    Bucket=bucket,
                    Key=dst_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                    IfNoneMatch="*",
                )
            )
            completed, cancelled = await _drain_task(complete_task)
            upload_id = None
            return completed, cancelled
        except BaseException as exc:
            if upload_id is not None:
                cleanup_cancelled, cleanup_error = await _abort_multipart_upload(
                    s3,
                    bucket=bucket,
                    key=dst_key,
                    upload_id=upload_id,
                )
                if cleanup_error is not None:
                    message = (
                        "S3 multipart copy failed and cleanup also failed; "
                        f"upload ID {upload_id!r} may require manual abort: {cleanup_error}"
                    )
                    if isinstance(exc, asyncio.CancelledError):
                        exc.add_note(message)
                        raise
                    raise ProviderError(message) from cleanup_error
                if cleanup_cancelled and not isinstance(exc, asyncio.CancelledError):
                    raise asyncio.CancelledError from None
            raise

    # ------------------------------------------------------------------
    # Streaming I/O
    # ------------------------------------------------------------------

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = _DEFAULT_CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        """Open a key for streaming.

        Eagerly probes ``head_object`` BEFORE returning the iterator
        so a missing source raises ``NotFoundError`` here — not later,
        from the first ``async for``. Without the eager probe,
        ``cross_fs.copy`` would open / partially write the destination
        before discovering the source doesn't exist, leaving an
        orphan mid-upload (S3) or a truncated file (local). The
        ``head_object`` is roughly free compared to a `get_object`
        round-trip and keeps the failure surface at the call site.
        """
        bucket, key = self._resolve(path)
        if not key:
            raise ProviderError("cannot read a bucket — pass a key path")
        try:
            async with self._client() as s3:
                try:
                    await s3.head_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if _error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
                        raise NotFoundError(path.as_posix()) from exc
                    raise _map_client_error(exc, key) from exc
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        return self._read_chunks(bucket, key, chunk_size, path.as_posix())

    async def _read_chunks(
        self, bucket: str, key: str, chunk_size: int, display_path: str
    ) -> AsyncIterator[bytes]:
        try:
            async with self._client() as s3:
                try:
                    resp = await s3.get_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if _error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
                        raise NotFoundError(display_path) from exc
                    raise _map_client_error(exc, key) from exc
                body = resp["Body"]
                while True:
                    chunk = await body.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = True,
    ) -> None:
        if path.is_root:
            raise ConflictError("cannot write to root")
        bucket, key = self._resolve(path)
        if not key:
            raise ProviderError("cannot write to a bucket itself — pass a key path")

        reader = _AsyncStreamReader(source)
        part_size = _multipart_part_size(total_size)
        bytes_written = 0

        def _on_progress(delta: int) -> None:
            nonlocal bytes_written
            bytes_written += delta
            if progress is not None:
                progress(TransferProgress(bytes_transferred=bytes_written, bytes_total=total_size))

        try:
            async with self._client() as s3:
                upload_id: str | None = None
                try:
                    chunk = await reader.read(part_size)
                    if not chunk:
                        put_args: dict[str, Any] = {"Bucket": bucket, "Key": key, "Body": b""}
                        if not overwrite:
                            put_args["IfNoneMatch"] = "*"
                        put_task = asyncio.create_task(s3.put_object(**put_args))
                        put_response, put_cancelled = await _drain_publication_task(put_task)
                        if put_cancelled:
                            cancellation = asyncio.CancelledError()
                            await _rollback_cancelled_upload(
                                s3,
                                bucket=bucket,
                                key=key,
                                response=put_response,
                                cancellation=cancellation,
                            )
                            raise cancellation
                        return
                    create_task = asyncio.create_task(
                        s3.create_multipart_upload(Bucket=bucket, Key=key)
                    )
                    try:
                        created = await asyncio.shield(create_task)
                    except asyncio.CancelledError:
                        while not create_task.done():
                            try:
                                await asyncio.shield(create_task)
                            except asyncio.CancelledError:
                                continue
                        if not create_task.cancelled():
                            created = create_task.result()
                            upload_id = str(created["UploadId"])
                        raise
                    upload_id = str(created["UploadId"])
                    parts: list[dict[str, Any]] = []
                    part_number = 1
                    while chunk:
                        if part_number > _MAX_MULTIPART_PARTS:
                            raise ProviderError(
                                "S3 multipart upload exceeded 10,000 parts; provide total_size "
                                "so aws-tui can choose a valid part size"
                            )
                        response = await s3.upload_part(
                            Bucket=bucket,
                            Key=key,
                            UploadId=upload_id,
                            PartNumber=part_number,
                            Body=chunk,
                        )
                        etag = response.get("ETag")
                        if not etag:
                            raise ProviderError(
                                f"S3 multipart upload returned no ETag for part {part_number}"
                            )
                        parts.append({"ETag": etag, "PartNumber": part_number})
                        _on_progress(len(chunk))
                        part_number += 1
                        chunk = await reader.read(part_size)
                    complete_args: dict[str, Any] = {
                        "Bucket": bucket,
                        "Key": key,
                        "UploadId": upload_id,
                        "MultipartUpload": {"Parts": parts},
                    }
                    if not overwrite:
                        complete_args["IfNoneMatch"] = "*"
                    complete_task = asyncio.create_task(
                        s3.complete_multipart_upload(**complete_args)
                    )
                    complete_response, complete_cancelled = await _drain_publication_task(
                        complete_task
                    )
                    upload_id = None
                    if complete_cancelled:
                        cancellation = asyncio.CancelledError()
                        await _rollback_cancelled_upload(
                            s3,
                            bucket=bucket,
                            key=key,
                            response=complete_response,
                            cancellation=cancellation,
                        )
                        raise cancellation
                except BaseException as exc:
                    abort_error: Exception | None = None
                    cancelled_during_abort = False
                    if upload_id is not None:
                        current = asyncio.current_task()
                        cancellation_count = current.cancelling() if current is not None else 0
                        abort_task = asyncio.create_task(
                            s3.abort_multipart_upload(
                                Bucket=bucket,
                                Key=key,
                                UploadId=upload_id,
                            )
                        )
                        try:
                            while not abort_task.done():
                                try:
                                    await asyncio.shield(abort_task)
                                except asyncio.CancelledError:
                                    # Cleanup owns the multipart upload ID.
                                    # Repeated cancellation must not orphan it.
                                    current_count = (
                                        current.cancelling() if current is not None else 0
                                    )
                                    if current_count > cancellation_count:
                                        cancelled_during_abort = True
                                        cancellation_count = current_count
                                    continue
                            if not abort_task.cancelled():
                                abort_task.result()
                        except Exception as cleanup_exc:
                            abort_error = cleanup_exc
                    if abort_error is not None:
                        message = (
                            "S3 multipart upload failed and cleanup also failed; "
                            f"upload ID {upload_id!r} may require manual abort: {abort_error}"
                        )
                        if isinstance(exc, asyncio.CancelledError):
                            exc.add_note(message)
                            raise
                        if cancelled_during_abort:
                            cancelled = asyncio.CancelledError()
                            cancelled.add_note(message)
                            raise cancelled from None
                        raise ProviderError(message) from abort_error
                    if cancelled_during_abort and not isinstance(exc, asyncio.CancelledError):
                        raise asyncio.CancelledError from None
                    if isinstance(exc, ClientError):
                        raise _map_client_error(exc, key) from exc
                    raise
        except _AUTH_FAILURE_EXCEPTIONS as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _S3Revision:
    etag: str | None
    version_id: str | None = None
    modified: datetime | None = None
    size: int | None = None


def _s3_revision_token(response: dict[str, Any]) -> str | None:
    revision = _S3Revision(
        etag=_clean_etag(response.get("ETag")),
        version_id=_owned_version_id(response.get("VersionId")),
        modified=_to_aware(response.get("LastModified")),
        size=(int(response["ContentLength"]) if "ContentLength" in response else None),
    )
    if revision.size is None and "Size" in response:
        revision = _S3Revision(
            etag=revision.etag,
            version_id=revision.version_id,
            modified=revision.modified,
            size=int(response["Size"]),
        )
    if revision.etag is None and revision.version_id is None:
        return None
    payload = {
        "etag": revision.etag,
        "version_id": revision.version_id,
        "modified": revision.modified.isoformat() if revision.modified is not None else None,
        "size": revision.size,
    }
    return _S3_REVISION_PREFIX + json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _copy_response_revision(response: dict[str, Any], *, size: int) -> str | None:
    result = response.get("CopyObjectResult") or response
    if _clean_etag(result.get("ETag")) is None:
        return None
    return _s3_revision_token(
        {
            "ETag": result.get("ETag"),
            "VersionId": response.get("VersionId"),
            "ContentLength": size,
        }
    )


def _parse_s3_revision(token: str) -> _S3Revision:
    if not token.startswith(_S3_REVISION_PREFIX):
        return _S3Revision(etag=_clean_etag(token))
    try:
        payload = json.loads(token.removeprefix(_S3_REVISION_PREFIX))
        modified_raw = payload.get("modified")
        return _S3Revision(
            etag=_clean_etag(payload.get("etag")),
            version_id=_owned_version_id(payload.get("version_id")),
            modified=datetime.fromisoformat(modified_raw) if modified_raw else None,
            size=int(payload["size"]) if payload.get("size") is not None else None,
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError("invalid S3 revision token") from exc


def _owned_version_id(value: Any) -> str | None:
    if value is None:
        return None
    version_id = str(value)
    normalized = version_id.strip()
    if not normalized or normalized.casefold() == "null":
        return None
    return version_id


def _conditional_delete_args(bucket: str, key: str, token: str) -> dict[str, Any]:
    revision = _parse_s3_revision(token)
    kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if revision.version_id is not None:
        raise ProviderError(
            "cannot atomically delete a versioned S3 object: "
            "DeleteObject cannot condition the current key on VersionId"
        )
    if revision.etag is None:
        raise ProviderError("S3 conditional deletion requires an object revision")
    kwargs["IfMatch"] = _quoted_etag(revision.etag)
    # S3 only supports the additional atomic size/time predicates for
    # directory buckets. General-purpose buckets expose ETag as their
    # documented unversioned conditional-delete token.
    if bucket.endswith("--x-s3"):
        if revision.modified is not None:
            kwargs["IfMatchLastModifiedTime"] = revision.modified
        if revision.size is not None:
            kwargs["IfMatchSize"] = revision.size
    return kwargs


def _cleanup_revision_args(bucket: str, key: str, token: str) -> dict[str, Any]:
    """Delete only the destination revision created by this operation."""
    revision = _parse_s3_revision(token)
    if revision.version_id is None:
        raise ProviderError("S3 automatic cleanup requires an operation-owned VersionId")
    return {"Bucket": bucket, "Key": key, "VersionId": revision.version_id}


def _manual_cleanup_message(key: str, detail: str) -> str:
    return f"S3 rename could not safely remove {key}; manual cleanup required: {detail}"


async def _rollback_cancelled_upload(
    s3: Any,
    *,
    bucket: str,
    key: str,
    response: dict[str, Any],
    cancellation: asyncio.CancelledError,
) -> None:
    """Remove only the exact object version published by a cancelled write."""
    version_id = _owned_version_id(response.get("VersionId"))
    if version_id is None:
        cancellation.add_note(
            f"S3 upload may have published {key}; manual cleanup required because "
            "the response contained no operation-owned VersionId"
        )
        return
    cleanup_task = asyncio.create_task(
        s3.delete_object(Bucket=bucket, Key=key, VersionId=version_id)
    )
    try:
        await _drain_task(cleanup_task)
    except BaseException as cleanup_exc:
        cancellation.add_note(
            f"S3 upload published version {version_id!r} for {key}; manual cleanup "
            f"required because exact-version rollback failed: {cleanup_exc}"
        )


async def _cleanup_failed_rename_copy(
    s3: Any,
    *,
    bucket: str,
    key: str,
    ownership_revision: str,
    failure: BaseException,
) -> None:
    revision = _parse_s3_revision(ownership_revision)
    if revision.version_id is None:
        message = _manual_cleanup_message(
            key,
            "the copy response contained no VersionId, so automatic rollback is unsafe",
        )
        if isinstance(failure, asyncio.CancelledError):
            failure.add_note(message)
            return
        if _cancellation_pending():
            cancellation = asyncio.CancelledError()
            cancellation.add_note(message)
            raise cancellation from None
        raise ProviderError(message) from failure
    cleanup_task = asyncio.create_task(
        s3.delete_object(**_cleanup_revision_args(bucket, key, ownership_revision))
    )
    try:
        _, cleanup_cancelled = await _drain_task(cleanup_task)
    except BaseException as cleanup_exc:
        message = _manual_cleanup_message(key, f"conditional rollback failed: {cleanup_exc}")
        if isinstance(failure, asyncio.CancelledError):
            failure.add_note(message)
            return
        if _cancellation_pending():
            cancellation = asyncio.CancelledError()
            cancellation.add_note(message)
            raise cancellation from None
        raise ProviderError(message) from failure
    if cleanup_cancelled and not isinstance(failure, asyncio.CancelledError):
        raise asyncio.CancelledError from None


def _multipart_part_size(total_size: int | None) -> int:
    """Choose a bounded part size that cannot exceed S3's part-count limit."""
    if total_size is None:
        return _DEFAULT_CHUNK_SIZE
    if total_size < 0:
        raise ProviderError("S3 upload total_size cannot be negative")
    if total_size > _MAX_OBJECT_SIZE:
        raise ProviderError("S3 objects cannot exceed 5 TiB")
    required = (total_size + _MAX_MULTIPART_PARTS - 1) // _MAX_MULTIPART_PARTS
    mebibyte = 1024 * 1024
    rounded_required = ((required + mebibyte - 1) // mebibyte) * mebibyte
    part_size = max(_DEFAULT_CHUNK_SIZE, rounded_required)
    if part_size > _MAX_MULTIPART_PART_SIZE:
        raise ProviderError("S3 multipart part size cannot exceed 5 GiB")
    return part_size


def _multipart_copy_metadata(head: dict[str, Any]) -> dict[str, Any]:
    """Translate source HEAD fields accepted by CreateMultipartUpload."""
    fields = (
        "CacheControl",
        "ContentDisposition",
        "ContentEncoding",
        "ContentLanguage",
        "ContentType",
        "Expires",
        "Metadata",
        "WebsiteRedirectLocation",
        "StorageClass",
        "ServerSideEncryption",
        "SSEKMSKeyId",
        "BucketKeyEnabled",
        "ObjectLockMode",
        "ObjectLockRetainUntilDate",
        "ObjectLockLegalHoldStatus",
    )
    return {field: head[field] for field in fields if field in head}


async def _drain_task(task: asyncio.Task[Any]) -> tuple[Any, bool]:
    """Drain a remote mutation despite cancellation and return its result."""
    current = asyncio.current_task()
    cancellation_count = current.cancelling() if current is not None else 0
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current_count = current.cancelling() if current is not None else 0
            if current_count > cancellation_count:
                cancelled = True
                cancellation_count = current_count
    return task.result(), cancelled


async def _drain_publication_task(task: asyncio.Task[Any]) -> tuple[Any, bool]:
    """Drain a final publication while preserving cancellation as primary."""
    try:
        return await _drain_task(task)
    except BaseException as exc:
        if not isinstance(exc, asyncio.CancelledError) and _cancellation_pending():
            cancellation = asyncio.CancelledError()
            cancellation.add_note(
                f"S3 publication also failed while cancellation was pending: "
                f"{type(exc).__name__}: {exc}"
            )
            raise cancellation from exc
        raise


def _cancellation_pending() -> bool:
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


async def _abort_multipart_upload(
    s3: Any, *, bucket: str, key: str, upload_id: str
) -> tuple[bool, Exception | None]:
    task = asyncio.create_task(
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    )
    try:
        _, cancelled = await _drain_task(task)
    except Exception as exc:
        return False, exc
    return cancelled, None


class _AsyncStreamReader:
    """Async file-like adapter exposing an ``async read(n)`` interface.

    Multipart parts have a fixed target size, so this adapter buffers chunks
    from the source iterator until enough bytes are available (or EOF).
    """

    def __init__(self, source: AsyncIterator[bytes]) -> None:
        self._source = source
        self._buffer = bytearray()
        self._eof = False

    async def read(self, num_bytes: int = -1) -> bytes:
        if num_bytes is None or num_bytes < 0:
            # Read everything that's left.
            while not self._eof:
                await self._pull_one()
            out = bytes(self._buffer)
            self._buffer.clear()
            return out
        while len(self._buffer) < num_bytes and not self._eof:
            await self._pull_one()
        n = min(num_bytes, len(self._buffer))
        out = bytes(self._buffer[:n])
        del self._buffer[:n]
        return out

    async def _pull_one(self) -> None:
        try:
            chunk = await self._source.__anext__()
        except StopAsyncIteration:
            self._eof = True
            return
        self._buffer.extend(chunk)


def _chunks(items: list[Any], n: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _error_code(exc: ClientError) -> str:
    code = exc.response.get("Error", {}).get("Code", "")
    return str(code)


# Auth-error message used across every `S3FS` operation that touches
# AWS. The hint covers the most common "boto can read it but aioboto3
# can't" causes (SSO refresh, ``credential_process`` chains, missing
# env vars). Centralised so the 8 call sites stay in sync.
_AUTH_HINT: str = (
    "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
    "uses an auth path aioboto3 can't read directly. Try:\n"
    "  - `aws sso login --profile <name>` to refresh SSO\n"
    "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
    "  - export AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and, for temporary "
    "credentials, AWS_SESSION_TOKEN explicitly"
)


def _auth_error(exc: BaseException) -> AuthRequiredError:
    """Wrap a boto auth/credentials exception in a domain error with
    the project's canonical recovery hint. Used by every auth catch
    site in :class:`S3FS` so the hint stays in one place."""
    if isinstance(exc, CredentialRetrievalError):
        return AuthRequiredError(f"AWS auth: credential process failed.\n{_AUTH_HINT}")
    return AuthRequiredError(f"AWS auth: {exc}.\n{_AUTH_HINT}")


def _map_client_error(exc: ClientError, target: str) -> ProviderError:
    code = _error_code(exc)
    if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
        return NotFoundError(target)
    if code in {"PreconditionFailed", "ConditionalRequestConflict", "409", "412"}:
        return ConflictError(target)
    if code in AWS_AUTH_ERROR_CODES:
        return _auth_error(exc)
    if code in {"AccessDenied", "403", "Forbidden"}:
        return PermissionDeniedError(target)
    if code in {"SlowDown", "RequestLimitExceeded"}:
        return ThrottledError(f"{code}: {target}")
    # Service-side availability failures map to ``ProviderUnreachableError``
    # so the UI surfaces them with the endpoint placeholder instead of the
    # generic error one. Rate-limit responses are handled above as throttling;
    # they must not poison connection-health fallback decisions.
    if code in {
        "ServiceUnavailable",
        "RequestTimeout",
        "RequestTimeoutException",
        "InternalError",
        "503",
        "504",
    }:
        return ProviderUnreachableError(f"{code}: {target}")
    return ProviderError(f"{code}: {target}")


def _clean_etag(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip('"')


def _quoted_etag(etag: str) -> str:
    """Return an ETag in the quoted form expected by S3 conditions."""
    return f'"{etag.strip(chr(34))}"'


def _to_aware(dt: datetime | None) -> datetime | None:
    """Coerce a naïve datetime to UTC-aware so callers can compare safely.

    boto3 normally returns tz-aware datetimes (UTC), but some S3-compatible
    providers (notably older MinIO releases) historically returned naïve
    timestamps. Treat those as UTC explicitly so downstream sort/format
    code never has to mix aware and naïve values.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


__all__ = ["S3FS"]
