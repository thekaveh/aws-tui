"""Shared moto fixtures for the domain test tier.

The same ``ThreadedMotoServer`` + per-test reset pattern was duplicated
across ``test_s3_fs_with_moto.py``, ``test_s3_fs_bucketless_ops.py``,
and ``test_cross_fs.py``. Consolidated here so the three suites share
one moto process per pytest run.

Why ``ThreadedMotoServer`` (rather than ``mock_aws``): moto's in-process
monkey-patching doesn't compose with aiobotocore's awaited response
body. The threaded server speaks HTTP, so aiobotocore drives it as if
it were real S3 — at no network cost.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Iterator

import pytest
from moto.server import ThreadedMotoServer


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    """Spin up a single shared moto HTTP server for the module's tests."""
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def s3_endpoint(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Wipe S3 state between tests so each starts with a clean slate.

    Returns the moto-server base URL (a plain ``str``, not a generator).
    The fixture body has no teardown so ``return`` is correct here —
    the previously declared ``-> Iterator[str]`` annotation was wrong;
    pytest gates this distinction on whether the body actually
    ``yield``s.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    urllib.request.urlopen(
        urllib.request.Request(f"{moto_server}/moto-api/reset", method="POST")
    ).read()
    return moto_server
