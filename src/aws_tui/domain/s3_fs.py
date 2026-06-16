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
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
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

    @staticmethod
    def _strip(key: str, prefix: str) -> str:
        return key[len(prefix) :] if prefix and key.startswith(prefix) else key

    # ------------------------------------------------------------------
    # list / stat
    # ------------------------------------------------------------------

    async def list(self, path: PathRef) -> list[FileEntry]:
        if self._bucket is None and path.is_root:
            return await self._list_buckets()
        if self._bucket is None:
            raise ProviderError("S3FS without bucket can only list at root")
        prefix = self._key_for(path)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        return await self._list_objects(prefix)

    async def _list_buckets(self) -> _List[FileEntry]:
        try:
            async with self._client() as s3:
                resp = await s3.list_buckets()
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, "<service>") from exc
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

    async def _list_objects(self, prefix: str) -> _List[FileEntry]:
        assert self._bucket is not None
        entries: list[FileEntry] = []
        try:
            async with self._client() as s3:
                token: str | None = None
                while True:
                    kwargs: dict[str, Any] = {
                        "Bucket": self._bucket,
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
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, prefix) from exc
        entries.sort(key=lambda e: (e.kind != EntryKind.DIRECTORY, e.name))
        return entries

    async def stat(self, path: PathRef) -> FileEntry:
        if self._bucket is None:
            if path.is_root:
                return FileEntry(name="", kind=EntryKind.DIRECTORY, size=None, modified=None)
            raise NotFoundError(path.as_posix())
        if path.is_root:
            return FileEntry(name="", kind=EntryKind.DIRECTORY, size=None, modified=None)

        key = self._key_for(path)
        try:
            async with self._client() as s3:
                try:
                    resp = await s3.head_object(Bucket=self._bucket, Key=key)
                except ClientError as exc:
                    if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                        # Maybe it's a directory; probe via list with that
                        # prefix.
                        marker = f"{key}/" if not key.endswith("/") else key
                        resp_list = await s3.list_objects_v2(
                            Bucket=self._bucket, Prefix=marker, MaxKeys=1
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
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
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
        if self._bucket is None:
            raise ProviderError("cannot mkdir at S3 service root")
        if path.is_root:
            return
        key = self._key_for(path)
        if not key.endswith("/"):
            key = f"{key}/"
        try:
            async with self._client() as s3:
                await s3.put_object(Bucket=self._bucket, Key=key, Body=b"")
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
            raise ProviderUnreachableError(str(exc)) from exc
        except ClientError as exc:
            raise _map_client_error(exc, key) from exc

    async def delete(self, path: PathRef) -> None:
        if self._bucket is None:
            raise ProviderError("cannot delete at S3 service root")
        key = self._key_for(path)
        try:
            async with self._client() as s3:
                # Try object delete first; if that "succeeds" but no
                # such object existed, fall through to prefix-delete.
                try:
                    await s3.head_object(Bucket=self._bucket, Key=key)
                    file_exists = True
                except ClientError as exc:
                    if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                        file_exists = False
                    else:
                        raise _map_client_error(exc, key) from exc

                if file_exists:
                    await s3.delete_object(Bucket=self._bucket, Key=key)
                    return

                # Directory delete: enumerate + batch-delete.
                prefix = f"{key}/" if not key.endswith("/") else key
                deleted_any = False
                token: str | None = None
                while True:
                    list_kwargs: dict[str, Any] = {
                        "Bucket": self._bucket,
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
                                Bucket=self._bucket,
                                Delete={
                                    "Objects": [{"Key": o["Key"]} for o in batch],
                                    "Quiet": True,
                                },
                            )
                    if not resp.get("IsTruncated"):
                        break
                    token = resp.get("NextContinuationToken")
                if not deleted_any:
                    raise NotFoundError(path.as_posix())
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
            raise ProviderUnreachableError(str(exc)) from exc

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        if self._bucket is None:
            raise ProviderError("cannot rename at S3 service root")
        src_key = self._key_for(src)
        dst_key = self._key_for(dst)
        try:
            async with self._client() as s3:
                # ConflictError if dst exists.
                try:
                    await s3.head_object(Bucket=self._bucket, Key=dst_key)
                    raise ConflictError(dst.as_posix())
                except ClientError as exc:
                    if _error_code(exc) not in {"404", "NoSuchKey", "NotFound"}:
                        raise _map_client_error(exc, dst_key) from exc
                try:
                    await s3.copy_object(
                        Bucket=self._bucket,
                        Key=dst_key,
                        CopySource={"Bucket": self._bucket, "Key": src_key},
                    )
                except ClientError as exc:
                    raise _map_client_error(exc, src_key) from exc
                await s3.delete_object(Bucket=self._bucket, Key=src_key)
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
            raise ProviderUnreachableError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Streaming I/O
    # ------------------------------------------------------------------

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = _DEFAULT_CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        if self._bucket is None:
            raise ProviderError("cannot read at S3 service root")
        key = self._key_for(path)
        return self._read_chunks(key, chunk_size, path.as_posix())

    async def _read_chunks(
        self, key: str, chunk_size: int, display_path: str
    ) -> AsyncIterator[bytes]:
        assert self._bucket is not None
        try:
            async with self._client() as s3:
                try:
                    resp = await s3.get_object(Bucket=self._bucket, Key=key)
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
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
            raise ProviderUnreachableError(str(exc)) from exc

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        if self._bucket is None:
            raise ProviderError("cannot write at S3 service root")
        if path.is_root:
            raise ConflictError("cannot write to root")
        key = self._key_for(path)

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
                        self._bucket,
                        key,
                        Callback=_on_progress,
                    )
                except ClientError as exc:
                    raise _map_client_error(exc, key) from exc
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound) as exc:
            raise PermissionDeniedError(
                f"AWS auth: {exc}.\n"
                "If `aws s3 ls` works on the CLI but this fails, your profile likely\n"
                "uses an auth path aioboto3 can't read directly. Try:\n"
                "  - `aws sso login --profile <name>` to refresh SSO\n"
                "  - check ~/.aws/config for `credential_process` / `source_profile`\n"
                "  - export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY explicitly"
            ) from exc
        except EndpointConnectionError as exc:
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


def _map_client_error(exc: ClientError, target: str) -> ProviderError:
    code = _error_code(exc)
    if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
        return NotFoundError(target)
    if code in {"AccessDenied", "403", "Forbidden", "SignatureDoesNotMatch"}:
        return PermissionDeniedError(target)
    return ProviderError(f"{code}: {target}")


def _clean_etag(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip('"')


def _to_aware(dt: datetime | None) -> datetime | None:
    return dt


__all__ = ["S3FS"]
