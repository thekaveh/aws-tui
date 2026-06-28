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
- ``write_stream``: uses ``upload_fileobj``'s multipart machinery via a
  blocking adapter that reads from the async source iterator.

Botocore ``ClientError`` codes are mapped to the ProviderError taxonomy
(NoSuchKey/NoSuchBucket → NotFound, AccessDenied → PermissionDenied,
EndpointConnection → Unreachable).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
    ReadTimeoutError,
)
from botocore.exceptions import (
    ConnectionError as BotoConnectionError,
)

from aws_tui.domain.filesystem import (
    ConflictError,
    EntryKind,
    FileEntry,
    NotFoundError,
    PathRef,
    PermissionDeniedError,
    ProgressCallback,
    ProviderError,
    ProviderUnreachableError,
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
_TRANSPORT_FAILURE_EXCEPTIONS = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    BotoConnectionError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# Default streaming chunk size.
_DEFAULT_CHUNK_SIZE: int = 8 * 1024 * 1024
# S3 batch-delete limit.
_DELETE_BATCH_SIZE: int = 1000

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
    """

    def __init__(
        self,
        *,
        session: aioboto3.Session,
        bucket: str | None,
        prefix: str = "",
        endpoint_url: str | None = None,
        force_path_style: bool = False,
    ) -> None:
        self._session = session
        self._bucket: str | None = bucket
        self._prefix: str = prefix.strip("/")
        self._endpoint_url: str | None = endpoint_url
        # Apply the same retry / timeout policy spec §6.3 + §7.3 mandates for
        # every AWS client. infra/AwsSession.client() does the equivalent for
        # service callers; S3FS is constructed directly with an aioboto3
        # Session by S3Service, so the budget has to live here too.
        self._config = BotoConfig(
            s3={"addressing_style": "path" if force_path_style else "auto"},
            signature_version="s3v4",
            retries={"max_attempts": 6, "mode": "adaptive"},
            connect_timeout=10,
            read_timeout=60,
        )

    # ------------------------------------------------------------------
    # Client helper
    # ------------------------------------------------------------------

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {"config": self._config}
        if self._endpoint_url is not None:
            kwargs["endpoint_url"] = self._endpoint_url
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

    @staticmethod
    def _strip(key: str, prefix: str) -> str:
        return key[len(prefix) :] if prefix and key.startswith(prefix) else key

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
        try:
            async with self._client() as s3:
                resp = await s3.list_buckets()
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, "buckets") from exc
        entries: list[FileEntry] = []
        for b in resp.get("Buckets", []):
            entries.append(
                FileEntry(
                    name=b["Name"],
                    kind=EntryKind.DIRECTORY,
                    size=None,
                    modified=_to_aware(b.get("CreationDate")),
                )
            )
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
                while True:
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
                                etag=_clean_etag(obj.get("ETag")),
                            )
                        )
                    if not resp.get("IsTruncated"):
                        break
                    token = resp.get("NextContinuationToken")
                    if not token:
                        # Defensive: a well-behaved S3 returns
                        # ``NextContinuationToken`` whenever ``IsTruncated``
                        # is true, but S3-compatible providers (MinIO,
                        # Cloudflare R2, Backblaze B2, …) have shipped
                        # responses with ``IsTruncated=True`` and no
                        # token. Without this break the loop re-sends
                        # the SAME request (token is None → the
                        # ``if token is not None`` guard above keeps
                        # ``ContinuationToken`` off the kwargs) and the
                        # pane hangs. Matches the pattern in
                        # ``emr_logs.py::list_log_files``.
                        break
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
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
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        return FileEntry(
            name=path.name,
            kind=EntryKind.FILE,
            size=int(resp.get("ContentLength", 0)),
            modified=_to_aware(resp.get("LastModified")),
            etag=_clean_etag(resp.get("ETag")),
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
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, key) from exc

    async def delete(self, path: PathRef) -> None:
        bucket, key = self._resolve(path)
        if not key:
            raise ProviderError("cannot delete a bucket via S3FS — use the AWS console / CLI")
        try:
            async with self._client() as s3:
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
                while True:
                    list_kwargs: dict[str, Any] = {
                        "Bucket": bucket,
                        "Prefix": prefix,
                    }
                    if token is not None:
                        list_kwargs["ContinuationToken"] = token
                    resp = await s3.list_objects_v2(**list_kwargs)
                    objects = resp.get("Contents") or []
                    if objects:
                        deleted_any = True
                        for batch in _chunks(objects, _DELETE_BATCH_SIZE):
                            await s3.delete_objects(
                                Bucket=bucket,
                                Delete={
                                    "Objects": [{"Key": o["Key"]} for o in batch],
                                    "Quiet": True,
                                },
                            )
                    if not resp.get("IsTruncated"):
                        break
                    token = resp.get("NextContinuationToken")
                    if not token:
                        # Same defensive break as the list() path
                        # above — protects against S3-compatible
                        # providers that return ``IsTruncated=True``
                        # with no continuation token, which would
                        # otherwise re-issue the same request forever
                        # and either hang the delete or delete the
                        # same batch repeatedly.
                        break
                if not deleted_any:
                    raise NotFoundError(path.as_posix())
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
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

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        src_bucket, src_key = self._resolve(src)
        dst_bucket, dst_key = self._resolve(dst)
        if not src_key or not dst_key:
            raise ProviderError("cannot rename buckets via S3FS — use the AWS console / CLI")
        if src_bucket != dst_bucket:
            raise ProviderError("cross-bucket rename is not supported by this provider")
        bucket = src_bucket
        try:
            async with self._client() as s3:
                # ConflictError if dst exists.
                try:
                    await s3.head_object(Bucket=bucket, Key=dst_key)
                    raise ConflictError(dst.as_posix())
                except ClientError as exc:
                    if _error_code(exc) not in {"404", "NoSuchKey", "NotFound"}:
                        raise _map_client_error(exc, dst_key) from exc
                try:
                    await s3.copy_object(
                        Bucket=bucket,
                        Key=dst_key,
                        CopySource={"Bucket": bucket, "Key": src_key},
                    )
                except ClientError as exc:
                    raise _map_client_error(exc, src_key) from exc
                await s3.delete_object(Bucket=bucket, Key=src_key)
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            # Outer catch for the post-copy `delete_object(src_key)`
            # on line 441 — a partial rename (copy succeeded, source
            # delete denied) otherwise propagates raw botocore
            # ClientError instead of going through ProviderError.
            raise _map_client_error(exc, src_key) from exc

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
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
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
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
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
    ) -> None:
        if path.is_root:
            raise ConflictError("cannot write to root")
        bucket, key = self._resolve(path)
        if not key:
            raise ProviderError("cannot write to a bucket itself — pass a key path")

        reader = _AsyncStreamReader(source)
        bytes_written = 0

        def _on_progress(delta: int) -> None:
            nonlocal bytes_written
            bytes_written += delta
            if progress is not None:
                progress(TransferProgress(bytes_transferred=bytes_written, bytes_total=total_size))

        try:
            async with self._client() as s3:
                try:
                    await s3.upload_fileobj(
                        reader,
                        bucket,
                        key,
                        Callback=_on_progress,
                    )
                except ClientError as exc:
                    raise _map_client_error(exc, key) from exc
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise _auth_error(exc) from exc
        except _TRANSPORT_FAILURE_EXCEPTIONS as exc:
            raise ProviderUnreachableError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _AsyncStreamReader:
    """Async file-like adapter exposing an ``async read(n)`` interface.

    ``aioboto3.s3.upload_fileobj`` awaits ``Fileobj.read(...)`` if it
    returns a coroutine. We satisfy that contract by buffering chunks
    pulled from the underlying async iterator until enough bytes are
    available (or EOF).
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
    "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
)


def _auth_error(exc: BaseException) -> PermissionDeniedError:
    """Wrap a boto auth/credentials exception in a domain error with
    the project's canonical recovery hint. Used by every auth catch
    site in :class:`S3FS` so the hint stays in one place."""
    return PermissionDeniedError(f"AWS auth: {exc}.\n{_AUTH_HINT}")


def _map_client_error(exc: ClientError, target: str) -> ProviderError:
    code = _error_code(exc)
    if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
        return NotFoundError(target)
    if code in {"AccessDenied", "403", "Forbidden", "SignatureDoesNotMatch"}:
        return PermissionDeniedError(target)
    # S3 service-side transient failures map to ``ProviderUnreachableError``
    # so the UI surfaces them with the "endpoint unreachable" placeholder
    # rather than the generic error one. Botocore's adaptive retry policy
    # (max_attempts=6) usually absorbs these, but a sustained
    # ``ServiceUnavailable`` / ``SlowDown`` storm can still exhaust the
    # retry budget. From the user's perspective the bucket is unreachable
    # — same recovery action as a DNS / timeout failure (press ``r`` to
    # retry, or wait + try again).
    if code in {
        "ServiceUnavailable",
        "RequestTimeout",
        "RequestTimeoutException",
        "SlowDown",
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
