"""Accepted historical transfer ownership, independent of browser lifetime."""

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy
from aws_tui.domain.s3_versions import S3VersionSource
from aws_tui.vm.file_manager.s3_versions_vm import VersionTransferRequest
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.messages import TransferState


async def execute_version_transfer(
    request: VersionTransferRequest, runtime: TransferRuntime
) -> bool:
    """Own begin/run/terminal/release in the accepting App worker's task.

    Legacy diagnostic labels retain the exact version. A generic recovery
    descriptor would lose VersionId and could retry against current content.
    The caller reads request.warning after actual drainage, including when
    runtime cancellation flattens the provider's CancelledError to False.
    """
    version = request.version
    tid = await runtime.begin(
        source_uri=request.source_uri,
        destination_uri=request.destination_label,
        bytes_total=version.size,
        descriptor=None,
    )
    consumed: set[str] = set()
    try:
        runtime.progress(
            tid,
            TransferState.PENDING,
            0,
            version.size,
            request.source_uri,
            request.destination_label,
        )
        before_publication, progress = runtime.copy_hooks(tid, version.path, directory=False)
        copier = CrossFsCopy(
            source=S3VersionSource(request.source, version),
            destination=request.destination,
            before_publication=before_publication,
            file_progress_transform=progress,
        )
        consumed.add(tid)
        completed = await runtime.run_one(
            operation=copier.copy,
            src_path=version.path,
            dst_path=request.destination_path,
            on_conflict=ConflictResolution.ERROR,
            transfer_id=tid,
            bytes_total=version.size,
        )
        if completed:
            await runtime.complete(tid, version.size or 0, version.size)
        return completed
    finally:
        await runtime.settle_batch([(tid, version.size)], consumed)
