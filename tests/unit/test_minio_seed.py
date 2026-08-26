from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from botocore.exceptions import ClientError


def _load_seed_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/test-services/s3/seed.py"
    spec = importlib.util.spec_from_file_location("aws_tui_minio_seed", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _BucketClient:
    def __init__(self, code: str) -> None:
        self._code = code
        self.created: list[str] = []

    async def head_bucket(self, *, Bucket: str) -> None:
        raise ClientError(
            {"Error": {"Code": self._code, "Message": "head failed"}},
            "HeadBucket",
        )

    async def create_bucket(self, *, Bucket: str) -> None:
        self.created.append(Bucket)


@pytest.mark.asyncio
async def test_minio_seed_creates_only_for_a_missing_bucket() -> None:
    seed = _load_seed_module()
    client = _BucketClient("404")

    assert await seed._ensure_bucket(client, "missing") is True
    assert client.created == ["missing"]


@pytest.mark.asyncio
async def test_minio_seed_does_not_mask_head_bucket_authorization_failure() -> None:
    seed = _load_seed_module()
    client = _BucketClient("AccessDenied")

    with pytest.raises(ClientError) as exc_info:
        await seed._ensure_bucket(client, "private")

    assert exc_info.value.response["Error"]["Code"] == "AccessDenied"
    assert client.created == []
