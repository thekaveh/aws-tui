"""Tests for the S3 service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import AuthRequiredError
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.s3 import S3Service
from aws_tui.services.s3 import service as s3_service_module
from aws_tui.services.s3.service import _aioboto3_session_for
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.services_protocol import Service


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
        session_token="tok",
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

    provider = svc.build_remote_provider(_minio_conn())

    assert isinstance(provider, RecordingS3FS)
    assert calls[0]["verify_tls"] is False


def test_s3_service_exposes_app_orchestration_dependencies(tmp_path: Path) -> None:
    journal = TransferJournal(base_dir=tmp_path / "journal")
    svc = S3Service(
        transfer_journal=journal,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        local_root=tmp_path / "local",
        s3_fs_factory=lambda _conn: InMemoryFS(),
    )

    assert svc.transfer_journal is journal
    assert isinstance(svc.build_remote_provider(_aws_conn()), InMemoryFS)
    local = svc.build_local_provider()
    assert isinstance(local, LocalFS)
    assert local._root == (tmp_path / "local").resolve()  # type: ignore[attr-defined]


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


def test_aioboto3_session_factory_minio(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.region_name = kwargs["region_name"]

    monkeypatch.setattr(s3_service_module.aioboto3, "Session", FakeSession)

    sess = _aioboto3_session_for(_minio_conn())
    assert sess.region_name == "us-east-1"
    assert captured["aws_access_key_id"] == "ak"
    assert captured["aws_secret_access_key"] == "sk"
    assert captured["aws_session_token"] == "tok"


def test_aioboto3_session_factory_unsupported() -> None:
    rogue = Connection(name="x", kind="azure-blob", region="us-east-1", source="explicit")
    with pytest.raises(ValueError, match="unsupported connection kind"):
        _aioboto3_session_for(rogue)


def test_aioboto3_session_factory_rejects_missing_compatible_credentials() -> None:
    connection = Connection(
        name="unsafe",
        kind="s3-compatible",
        region="us-east-1",
        source="explicit",
        endpoint_url="http://localhost:9000",
    )
    with pytest.raises(AuthRequiredError, match="requires an access key and secret key"):
        _aioboto3_session_for(connection)


def test_missing_aws_profile_raises_a_typed_error_not_a_raw_botocore_one() -> None:
    """`build_vm` must only ever raise domain errors.

    botocore raises `ProfileNotFound` at Session CONSTRUCTION when the named
    profile is missing — a renamed or deleted `~/.aws/config` entry. Letting it
    escape took it out through `build_vm` to the generic handler in `app.py`,
    which logs only `error_type` and drops the message, so the user got a blank
    mount with the profile name nowhere. `services/emr_serverless` already
    guarded the identical call; this is the S3 side catching up.
    """
    connection = Connection(
        name="broken",
        kind="aws",
        region="us-east-1",
        source="explicit",
        profile="definitely-not-a-real-profile-xyz",
    )

    with pytest.raises(AuthRequiredError) as excinfo:
        _aioboto3_session_for(connection)

    message = str(excinfo.value)
    assert "broken" in message
    assert "definitely-not-a-real-profile-xyz" in message
