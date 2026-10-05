"""Isolated composed app with real local endpoints for history pilots."""

from pathlib import Path

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.transfer_history import TransferHistoryDescriptor
from aws_tui.infra.clipboard import InMemoryClipboard
from aws_tui.vm.credential_recovery import connection_history_identity
from aws_tui.vm.file_manager.transfer_history_vm import ResolvedTransferEndpoint, TransferHistoryVM

NAME = "report [final] with spaces.csv"


def history_app(
    tmp_path: Path, *, seeded: bool = True, operation: str = "copy", attempted: bool = False
):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / NAME).write_bytes(b"payload")
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache", demo=True)
    ctx.registry.get("s3")._local_root = source
    ctx.clipboard_vm._clipboard = InMemoryClipboard()
    providers = [LocalFS(root=source), LocalFS(root=destination)]
    identities = [connection_history_identity(None, provider) for provider in providers]
    resolutions = []

    async def resolve(identity):
        resolutions.append(identity)
        index = identities.index(identity)
        return ResolvedTransferEndpoint(providers[index], identities[index])

    ctx.transfer_history_vm.dispose()
    ctx.transfer_history_vm = TransferHistoryVM(
        ctx.transfer_journal,
        resolve,
        ctx.hub,
        ctx.dispatcher,
        runtime=ctx.registry.get("s3").transfer_runtime,
    )
    tid = None
    if seeded:
        descriptor = TransferHistoryDescriptor(
            operation,
            identities[0],
            identities[1] if operation != "delete" else None,
            f"/{NAME}",
            f"/{NAME}" if operation != "delete" else None,
            7,
        )
        tid = ctx.transfer_journal.begin(
            source_uri=descriptor.source_uri,
            destination_uri=descriptor.destination_uri or "",
            bytes_total=7,
            descriptor=descriptor,
        )
        if attempted:
            ctx.transfer_journal.mark_attempted(tid)
    return AwsTuiApp(ctx), tid, source, destination, resolutions
