from __future__ import annotations

import threading

import pytest

from tests.integration.conftest import _minio_unavailable, _start_minio_or_unavailable
from tests.unit.domain.conftest import _start_moto_server


def test_minio_unavailability_fails_the_ci_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")

    with pytest.raises(pytest.fail.Exception, match="MinIO unavailable"):
        _minio_unavailable("MinIO unavailable")


def test_minio_unavailability_skips_an_optional_local_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(pytest.skip.Exception, match="MinIO unavailable"):
        _minio_unavailable("MinIO unavailable")


@pytest.mark.parametrize("ci", [False, True])
def test_failed_minio_startup_stops_partial_container(
    monkeypatch: pytest.MonkeyPatch, ci: bool
) -> None:
    class _PartialContainer:
        def __init__(self) -> None:
            self.stopped = False

        def start(self) -> None:
            raise RuntimeError("readiness failed")

        def stop(self) -> None:
            self.stopped = True

    if ci:
        monkeypatch.setenv("CI", "true")
        expected = pytest.fail.Exception
    else:
        monkeypatch.delenv("CI", raising=False)
        expected = pytest.skip.Exception
    container = _PartialContainer()

    with pytest.raises(expected, match="readiness failed"):
        _start_minio_or_unavailable(container)

    assert container.stopped is True


def test_moto_startup_has_a_hard_timeout() -> None:
    release = threading.Event()

    class _BlockedServer:
        def start(self) -> None:
            release.wait()

    try:
        with pytest.raises(RuntimeError, match="did not become ready"):
            _start_moto_server(_BlockedServer(), timeout=0.01)  # type: ignore[arg-type]
    finally:
        release.set()


def test_moto_startup_propagates_worker_failure() -> None:
    class _FailedServer:
        def start(self) -> None:
            raise OSError("bind failed")

    with pytest.raises(RuntimeError, match="bind failed"):
        _start_moto_server(_FailedServer(), timeout=0.1)  # type: ignore[arg-type]
