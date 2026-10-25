"""Admission for new remote changes; accepted provider calls retain their cleanup."""

from collections.abc import Awaitable, Callable
from typing import Any

from aws_tui.domain.filesystem import PermissionDeniedError
from aws_tui.domain.s3_versions import S3RecoveryDestination

MutationCheck = Callable[[], Awaitable[None]]
PROTECTED_MESSAGE = (
    "Connection is protected; remote changes are blocked. Change protection in Settings."
)


class ConnectionProtectedError(PermissionDeniedError):
    """App-owned refusal; callers must present the fixed safe explanation."""


async def ensure_mutation_allowed(provider: object) -> None:
    """Check an optional bound endpoint policy before starting a transfer."""
    check = getattr(provider, "check_mutation_allowed", None)
    if callable(check):
        await check()


def fixed_mutation_check(protected: bool) -> MutationCheck:
    """Admission for independently constructed services without a config store."""

    async def check() -> None:
        if protected:
            raise ConnectionProtectedError(PROTECTED_MESSAGE)

    return check


class GuardedRemote:
    """Delegate reads and guard initiation on one already-bound provider/client.

    Copy only existing capability attributes onto the instance: Python 3.12's
    runtime protocols use static lookup and do not consult ``__getattr__``.
    Never advertise unsupported optional filesystem capabilities.
    """

    _MUTATIONS = frozenset(
        {
            "mkdir",
            "delete",
            "delete_empty_directory",
            "rename",
            "write_stream",
            "write_upload_stream",
            "write_recovery_stream",
            "claim_directory",
            "atomic_publish_no_replace",
            "atomic_publish_directory_no_replace",
            "start_query",
            "start_job_run",
        }
    )
    _CAPABILITIES = (
        "list",
        "stat",
        "read_stream",
        "verify_directory",
        "storage_identity",
        "canonical_storage_path",
        "canonical_move_path",
        "canonical_path",
        "supports_atomic_publish",
        "capture_stage_revision",
        "capture_directory_revision",
        "preflight_move_revision",
        "open_preview",
        "atomic_write_replaces",
        "upload_location",
        "inspect_upload_destination",
        "read_sync_checksum",
        "read_object_details",
        "version_location",
        "list_versions",
        "stat_version",
        "read_version_details",
        "read_version_stream",
        "version_source",
    )

    def __init__(self, target: Any, check: MutationCheck) -> None:
        self._target = target
        self._check = check
        for name in self._CAPABILITIES:
            try:
                value = getattr(target, name)
            except AttributeError:
                continue
            setattr(self, name, value)
        for name in self._MUTATIONS:
            method = getattr(target, name, None)
            if callable(method):
                setattr(self, name, self._guard(method))
        recovery = getattr(target, "recovery_destination", None)
        if callable(recovery):

            async def guarded_recovery(*args: Any, **kwargs: Any) -> Any:
                destination = await recovery(*args, **kwargs)
                if isinstance(destination, S3RecoveryDestination):
                    return S3RecoveryDestination(
                        GuardedRemote(destination.provider, check), destination.context
                    )
                return GuardedRemote(destination, check)

            self.recovery_destination = guarded_recovery

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def _guard(self, method: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            await self._check()
            return await method(*args, **kwargs)

        return guarded

    async def check_mutation_allowed(self) -> None:
        await self._check()
