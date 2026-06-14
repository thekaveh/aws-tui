"""Session-scoped MinIO container fixture for the integration tier.

If Docker isn't available (no daemon socket, missing client, CI runner
without container support), every test that depends on the fixture is
skipped cleanly with ``pytest.skip`` rather than the suite failing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[tuple[str, str, str]]:
    """Spin up a real MinIO container.

    Returns ``(endpoint_url, access_key, secret_key)``. The container is
    reused for every test in the session.
    """
    try:
        from testcontainers.minio import MinioContainer  # lazy import
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"testcontainers MinIO unavailable: {exc}")

    try:
        container = MinioContainer()
        container.start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"could not start MinIO container (Docker missing?): {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        endpoint = f"http://{host}:{port}"
        access_key = container.access_key
        secret_key = container.secret_key
        yield (endpoint, access_key, secret_key)
    finally:
        with contextlib.suppress(Exception):  # pragma: no cover
            container.stop()
