"""Tests for the S3 service."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.s3 import S3Service
from aws_tui.services.s3 import service as s3_service_module
from aws_tui.services.s3.service import _aioboto3_session_for
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.services_protocol import Service
from tests.unit.domain._in_memory_fs import InMemoryFS


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _aws_conn() -> Connection:
    return Connection(
        name="aws-prod",
        kind="aws",
        region="us-east-1",
        source="explicit",
        profile="prod",
    )


def _minio_conn() -> Connection:
    return Connection(
        name="minio-local",
        kind="s3-compatible",
        region="us-east-1",
        source="explicit",
        endpoint_url="http://localhost:9000",
        access_key_id="ak",
        secret_access_key="sk",
        force_path_style=True,
        verify_tls=False,
    )


def _service(tmp_path: Path) -> S3Service:
    return S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
        s3_fs_factory=lambda _conn: InMemoryFS(),
    )


def test_s3_service_satisfies_protocol(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    assert isinstance(svc, Service)
    assert svc.descriptor.id == "s3"


def test_s3_service_supports_aws_and_minio(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    assert svc.supports(_aws_conn())
    assert svc.supports(_minio_conn())


def test_s3_service_does_not_support_other_kinds(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    rogue = Connection(name="x", kind="azure-blob", region="us-east-1", source="explicit")
    assert not svc.supports(rogue)


def test_s3_service_build_vm_returns_dualpane(tmp_path: Path) -> None:
    (tmp_path / "local").mkdir()
    svc = _service(tmp_path)
    dual = svc.build_vm(_aws_conn())
    assert isinstance(dual, DualPaneVM)
    # Left pane wraps the (in-memory) S3 provider; right is LocalFS.
    assert isinstance(dual.left.provider, InMemoryFS)
    assert isinstance(dual.right.provider, LocalFS)
    dual.construct()
    # Setup should be deferred until awaited — verify left has no entries yet.
    assert dual.left.entries == ()
    dual.dispose()


def test_s3_service_provider_threads_verify_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    class RecordingS3FS:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(s3_service_module, "_aioboto3_session_for", lambda _conn: object())
    monkeypatch.setattr(s3_service_module, "S3FS", RecordingS3FS)
    svc = S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
    )

    provider = svc._make_s3_provider(_minio_conn())

    assert isinstance(provider, RecordingS3FS)
    assert calls[0]["verify_tls"] is False


@pytest.mark.asyncio
async def test_s3_service_build_vm_setup_populates(tmp_path: Path) -> None:
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "readme.txt").write_text("hi")
    svc = _service(tmp_path)
    dual = svc.build_vm(_aws_conn())
    dual.construct()
    await dual.setup()
    assert any(e.entry.name == "readme.txt" for e in dual.right.entries)
    dual.dispose()


def test_aioboto3_session_factory_aws_profileless() -> None:
    # Using ``profile=None`` avoids hitting botocore's profile validation,
    # which fires lazily on first attribute lookup and rightfully rejects
    # unknown profile names. Either branch of ``_aioboto3_session_for``
    # produces a valid Session — the AWS branch only differs by profile.
    import aioboto3

    conn = Connection(
        name="aws-default", kind="aws", region="us-east-1", source="explicit", profile=None
    )
    sess = _aioboto3_session_for(conn)
    assert isinstance(sess, aioboto3.Session)


def test_aioboto3_session_factory_minio() -> None:
    sess = _aioboto3_session_for(_minio_conn())
    assert sess.region_name == "us-east-1"


def test_aioboto3_session_factory_unsupported() -> None:
    rogue = Connection(name="x", kind="azure-blob", region="us-east-1", source="explicit")
    with pytest.raises(ValueError, match="unsupported connection kind"):
        _aioboto3_session_for(rogue)
