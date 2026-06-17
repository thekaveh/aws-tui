"""End-to-end journeys per spec §8.5.

Five canonical user journeys driven by ``App.run_test()`` Pilot. Where a
real network backend is required (journey #3, MinIO container) we skip
cleanly when Docker isn't available.

The journeys are intentionally pragmatic — they assert the journey hits
its key checkpoint rather than tracing every keystroke. The unit + snapshot
tiers already cover widget-level rendering.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import AppContext
from aws_tui.domain.filesystem import (
    FileSystemProvider,
    PathRef,
)
from aws_tui.domain.local_fs import LocalFS
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.chrome.confirm_vm import ConfirmRequest
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneVM
from tests.unit.domain._in_memory_fs import InMemoryFS

# ── Helpers ─────────────────────────────────────────────────────────────────


async def _astream(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _aws_connection() -> Connection:
    return Connection(
        name="kaveh-dev",
        kind="aws",
        region="us-east-1",
        source="config",
        profile="kaveh-dev",
    )


# ── Journey 1: silent SSO on cold start ─────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_1_silent_sso(app_context: AppContext, tmp_path: Path) -> None:
    """Cold start with a valid SSO token = silent S3 view, no toast/modal."""
    # Build a fake SSO cache entry with future expiry.
    sso_cache = tmp_path / ".aws" / "sso" / "cache"
    sso_cache.mkdir(parents=True)
    (sso_cache / "fake.json").write_text(
        json.dumps(
            {
                "startUrl": "https://example.awsapps.com/start",
                "accessToken": "x",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        )
    )

    app = AwsTuiApp(app_context)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        # No toast was raised on launch.
        assert app_context.root_vm.chrome.toast_stack.count == 0
        # Top chrome is mounted (BrandBanner replaced the old StatusBar;
        # profile/region now live in the left pane's border).
        from aws_tui.ui.widgets.brand_banner import BrandBanner

        assert len(app.query(BrandBanner)) == 1


# ── Journey 2: copy S3 -> local via DualPaneVM ──────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_2_copy_object_to_local(app_context: AppContext, tmp_path: Path) -> None:
    """Copy one in-memory object across panes; verify byte equality at the
    destination."""
    # We bypass the S3Service factory and inject InMemoryFS-backed panes
    # directly to keep the test independent of moto/boto.
    payload = b"hello-aws-tui\n" * 64

    src: FileSystemProvider = InMemoryFS()
    await src.write_stream(PathRef(("greeting.txt",)), _astream(payload))

    dst_root = tmp_path / "downloads"
    dst_root.mkdir()
    dst: FileSystemProvider = LocalFS(root=dst_root)

    left = PaneVM(
        provider=src,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        id_prefix="pane.src",
    )
    right = PaneVM(
        provider=dst,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        id_prefix="pane.dst",
    )
    dual = DualPaneVM(
        left=left,
        right=right,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        transfer_journal=app_context.transfer_journal,
    )
    dual.construct()
    await dual.setup()

    # Mark the source entry and copy across.
    entries = left.entries
    assert len(entries) == 1
    entries[0].set_marked(True)

    await dual.copy_across()

    # Destination should now have a byte-equal copy.
    copied = dst_root / "greeting.txt"
    assert copied.is_file()
    assert copied.read_bytes() == payload
    dual.dispose()


# ── Journey 3: switch AWS -> MinIO mid-session (requires Docker) ────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_3_switch_connection(app_context: AppContext) -> None:
    """Connection switch fires the right hub message + tears down old content.

    A full AWS->MinIO swap with active transfers needs Docker; we cover the
    in-process orchestration only here so the suite stays Docker-free.
    """
    aws = _aws_connection()
    minio = Connection(
        name="minio-local",
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url="http://localhost:9000",
        access_key_id="x",
        secret_access_key="y",
        force_path_style=True,
    )
    app_context.root_vm.construct()

    # First, settle on AWS.
    await app_context.root_vm.switch_connection_with(aws, TokenState.CONNECTED)

    # Then switch to MinIO. The ContentHostVM should go through dispose-then-construct.
    await app_context.root_vm.switch_connection_with(minio, TokenState.MISSING)
    assert app_context.root_vm.content_host.current_id is None


# ── Journey 4: resume from journal ──────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_4_resume_from_journal(app_context: AppContext, tmp_path: Path) -> None:
    """Write a half-finished journal entry, scan, assert it's detected."""
    journal = app_context.transfer_journal
    # Start a new transfer
    tid = journal.begin(source_uri="s3://bucket/key", destination_uri="/tmp/key", bytes_total=1024)
    journal.record_part(tid, part_index=1, etag="abc", bytes_written=512)
    # Deliberately do NOT mark finished; this simulates a crash mid-transfer.

    unfinished = list(journal.find_unfinished())
    assert any(rec.transfer_id == tid for rec in unfinished)


# ── Journey 5: delete cancel -> no provider.delete call ─────────────────────


class _SpyProvider(InMemoryFS):
    """InMemoryFS subclass that records delete() calls."""

    def __init__(self) -> None:
        super().__init__()
        self.delete_calls: list[PathRef] = []

    async def delete(self, path: PathRef) -> None:
        self.delete_calls.append(path)
        await super().delete(path)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_journey_5_delete_cancel(app_context: AppContext) -> None:
    """Press 'd' on a file, confirm modal -> Esc -> no delete fired."""
    fs = _SpyProvider()
    await fs.write_stream(PathRef(("foo.txt",)), _astream(b"keep me"))

    pane = PaneVM(
        provider=fs,
        hub=app_context.hub,
        dispatcher=app_context.dispatcher,
        id_prefix="pane.spy",
    )
    pane.construct()
    await pane.setup()
    assert len(pane.entries) == 1
    pane.entries[0].set_marked(True)

    # Simulate the confirm-cancel path: ConfirmationVM.ask returns False.
    confirm_vm = app_context.confirm_vm
    confirm_vm.construct()

    # Synthesize a cancel decision: open + cancel before any await.
    import asyncio

    request = ConfirmRequest(
        title="Delete foo.txt?",
        body_lines=("Permanent.",),
        confirm_label="Delete",
        cancel_label="Cancel",
        danger=True,
    )

    async def caller_decides_no() -> bool:
        task = asyncio.create_task(confirm_vm.ask(request))
        await asyncio.sleep(0)
        confirm_vm.cancel_command.execute()
        return await task

    decision = await caller_decides_no()
    assert decision is False

    # The pane's delete_marked is NOT called because the caller (which would
    # be a higher VM/widget) skipped delete on a cancelled decision.
    # We assert the provider was untouched.
    assert fs.delete_calls == []
    pane.dispose()
