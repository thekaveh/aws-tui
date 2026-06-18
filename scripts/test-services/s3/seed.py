"""Seed the local MinIO instance with a realistic mix of buckets / folders /
files so aws-tui has interesting content to navigate during development.

Idempotent: existing buckets are left in place; existing objects are
overwritten (so re-running the seed is harmless).

Run:
    uv run python scripts/test-services/s3/seed.py

Configuration:
    AWS_TUI_DEV_S3_ENDPOINT  default http://localhost:9000
    AWS_TUI_DEV_S3_KEY       default minioadmin
    AWS_TUI_DEV_S3_SECRET    default minioadmin

Buckets created:
    aws-tui-dev-photos    — nested year/month/day folders, ~30 small files
    aws-tui-dev-logs      — flat list of rotated log files, ~50 entries
    aws-tui-dev-archive   — deep nested + a 1MB and an 8MB file (multipart)
    aws-tui-dev-empty     — empty bucket (exercises the empty pane state)
    aws-tui-dev-unicode   — files with unicode + long names + spaces
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import aioboto3
from botocore.config import Config

ENDPOINT = os.environ.get("AWS_TUI_DEV_S3_ENDPOINT", "http://localhost:9000")
ACCESS_KEY = os.environ.get("AWS_TUI_DEV_S3_KEY", "minioadmin")
SECRET_KEY = os.environ.get("AWS_TUI_DEV_S3_SECRET", "minioadmin")
REGION = "us-east-1"


@dataclass(frozen=True, slots=True)
class _SeedObject:
    key: str
    size_bytes: int
    body: bytes


def _body(size_bytes: int, seed: int = 0) -> bytes:
    """Deterministic body of ``size_bytes`` — same seed → same content."""
    # Cheap deterministic pattern, NOT random — we don't need randomness for
    # dev seeding and using random would break idempotency.
    chunk = bytes((i + seed) % 256 for i in range(min(size_bytes, 4096)))
    if size_bytes <= 4096:
        return chunk[:size_bytes]
    # Repeat the chunk to reach size_bytes.
    n, rem = divmod(size_bytes, 4096)
    return chunk * n + chunk[:rem]


def _photos_seed() -> list[_SeedObject]:
    """Year / month / day folder tree with small image-like blobs."""
    base = date(2025, 1, 1)
    out: list[_SeedObject] = []
    # 3 months x 2 days x 5 photos = 30 objects.
    for month_off in range(3):
        for day_off in range(2):
            d = base + timedelta(days=month_off * 30 + day_off)
            prefix = f"{d.year}/{d.month:02d}/{d.day:02d}"
            for i in range(5):
                size = 8 * 1024 + i * 1024  # 8KB-12KB each
                out.append(
                    _SeedObject(
                        key=f"{prefix}/img-{i:03d}.jpg",
                        size_bytes=size,
                        body=_body(size, seed=i),
                    )
                )
    return out


def _logs_seed() -> list[_SeedObject]:
    """Flat list of rotated log files. Exercises a single-level pane with
    many entries (~50)."""
    out: list[_SeedObject] = []
    base = date(2025, 6, 1)
    for i in range(50):
        d = base + timedelta(days=i)
        size = 4 * 1024 + (i % 8) * 1024  # 4KB-12KB each
        out.append(
            _SeedObject(
                key=f"app-{d.isoformat()}.log",
                size_bytes=size,
                body=_body(size, seed=i + 100),
            )
        )
    return out


def _archive_seed() -> list[_SeedObject]:
    """Deeply nested + two large files to exercise multipart upload paths
    in the transfers overlay."""
    out: list[_SeedObject] = [
        _SeedObject(
            key="2024/q4/backup-snapshot.tar",
            size_bytes=1 * 1024 * 1024,
            body=_body(1 * 1024 * 1024, seed=42),
        ),
        _SeedObject(
            key="2025/q1/full-backup.tar.gz",
            size_bytes=8 * 1024 * 1024 + 7,
            body=_body(8 * 1024 * 1024 + 7, seed=43),
        ),
        # A few smaller siblings so the dir isn't single-file lonely.
        _SeedObject(
            key="2024/q4/checksums.txt",
            size_bytes=512,
            body=_body(512, seed=44),
        ),
        _SeedObject(
            key="2024/q4/manifest.json",
            size_bytes=1024,
            body=_body(1024, seed=45),
        ),
        _SeedObject(
            key="2025/q1/checksums.txt",
            size_bytes=512,
            body=_body(512, seed=46),
        ),
    ]
    return out


def _unicode_seed() -> list[_SeedObject]:
    """Filenames with unicode / spaces / long names / special chars."""
    return [
        _SeedObject(key="résumé.pdf", size_bytes=2048, body=_body(2048, seed=200)),
        _SeedObject(key="日本語ファイル.txt", size_bytes=1024, body=_body(1024, seed=201)),
        _SeedObject(key="Über die Wolken.mp3", size_bytes=4096, body=_body(4096, seed=202)),
        _SeedObject(key="emoji-🎉-test.png", size_bytes=3072, body=_body(3072, seed=203)),
        _SeedObject(
            key="a-very-very-very-long-filename-that-stresses-the-pane-column-width.dat",
            size_bytes=512,
            body=_body(512, seed=204),
        ),
        _SeedObject(
            key="folder with spaces/file with spaces.txt",
            size_bytes=512,
            body=_body(512, seed=205),
        ),
        _SeedObject(
            key="special-chars/file@2024-12-31_v1.2.3.txt",
            size_bytes=1024,
            body=_body(1024, seed=206),
        ),
    ]


# Bucket → seed function. Add new buckets here to extend the dataset.
_BUCKETS: dict[str, list[_SeedObject]] = {
    "aws-tui-dev-photos": _photos_seed(),
    "aws-tui-dev-logs": _logs_seed(),
    "aws-tui-dev-archive": _archive_seed(),
    "aws-tui-dev-empty": [],
    "aws-tui-dev-unicode": _unicode_seed(),
}


async def _wait_for_minio(client: object, *, max_attempts: int = 30) -> None:
    """Poll MinIO's readiness via a cheap list-buckets call. Useful when the
    container has just started and isn't ready to accept S3 traffic yet."""
    last_exc: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            await client.list_buckets()  # type: ignore[attr-defined]
            return
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(0.5)
    raise RuntimeError(f"MinIO not ready after {max_attempts} attempts; last error: {last_exc!r}")


async def _ensure_bucket(client: object, name: str) -> bool:
    """Create ``name`` if it doesn't exist. Returns True if created, False if
    it was already there."""
    try:
        await client.head_bucket(Bucket=name)  # type: ignore[attr-defined]
        return False
    except Exception:
        await client.create_bucket(Bucket=name)  # type: ignore[attr-defined]
        return True


async def _put_object(client: object, bucket: str, obj: _SeedObject) -> None:
    await client.put_object(  # type: ignore[attr-defined]
        Bucket=bucket,
        Key=obj.key,
        Body=obj.body,
    )


async def _seed_bucket(client: object, bucket: str, objects: list[_SeedObject]) -> None:
    created = await _ensure_bucket(client, bucket)
    print(f"  {'created' if created else 'reusing'} bucket: {bucket}")
    if not objects:
        return
    # Serialize the puts — MinIO can handle parallel but this keeps output
    # deterministic and the throughput is plenty for the dataset size.
    for obj in objects:
        await _put_object(client, bucket, obj)
    print(f"    seeded {len(objects)} objects ({_size_summary(objects)})")


def _size_summary(objects: list[_SeedObject]) -> str:
    total = sum(o.size_bytes for o in objects)
    if total < 1024:
        return f"{total} B"
    if total < 1024 * 1024:
        return f"{total / 1024:.1f} KB"
    if total < 1024 * 1024 * 1024:
        return f"{total / (1024 * 1024):.1f} MB"
    return f"{total / (1024 * 1024 * 1024):.1f} GB"


async def main() -> int:
    started = datetime.now(UTC)
    print(f"==> seeding MinIO at {ENDPOINT}")
    session = aioboto3.Session()
    config = Config(retries={"max_attempts": 6, "mode": "adaptive"})
    async with session.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name=REGION,
        config=config,
    ) as client:
        try:
            await _wait_for_minio(client)
        except RuntimeError as exc:
            print(f"==> {exc}", file=sys.stderr)
            return 1
        for bucket, objects in _BUCKETS.items():
            await _seed_bucket(client, bucket, objects)
    elapsed = (datetime.now(UTC) - started).total_seconds()
    total_objs = sum(len(v) for v in _BUCKETS.values())
    print(f"==> done. {len(_BUCKETS)} buckets, {total_objs} objects, {elapsed:.1f}s")
    print()
    print("Point aws-tui at this MinIO by adding to ~/.config/aws-tui/config.toml:")
    print("  (copy from scripts/test-services/s3/config-snippet.toml)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


__all__: tuple[str, ...] = ()
