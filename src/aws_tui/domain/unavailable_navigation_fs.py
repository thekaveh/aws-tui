"""An unresolved companion pane retains intent and cannot perform filesystem work."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import NoReturn

from aws_tui.domain.filesystem import FileEntry, PathRef, ProgressCallback, ProviderUnreachableError
from aws_tui.domain.navigation_workspace import NavigationTarget, WorkspaceError


@dataclass(frozen=True, slots=True)
class UnavailableNavigationFS:
    navigation_target: NavigationTarget

    def __post_init__(self) -> None:
        if (
            not isinstance(self.navigation_target, NavigationTarget)
            or self.navigation_target.service != "s3"
        ):
            raise WorkspaceError("invalid")

    @staticmethod
    def _unavailable() -> NoReturn:
        raise ProviderUnreachableError(
            "The previous pane's source is unavailable; choose another source"
        )

    async def list(self, path: PathRef) -> list[FileEntry]:
        self._unavailable()

    async def stat(self, path: PathRef) -> FileEntry:
        self._unavailable()

    async def mkdir(self, path: PathRef) -> None:
        self._unavailable()

    async def delete(self, path: PathRef, *, expected_etag: str | None = None) -> None:
        self._unavailable()

    async def delete_empty_directory(
        self, path: PathRef, *, expected_etag: str | None = None
    ) -> None:
        self._unavailable()

    async def rename(self, src: PathRef, dst: PathRef) -> None:
        self._unavailable()

    async def read_stream(
        self, path: PathRef, *, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        self._unavailable()

    async def write_stream(
        self,
        path: PathRef,
        source: AsyncIterator[bytes],
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
        overwrite: bool = True,
        stage_claim: Callable[[str], None] | None = None,
    ) -> None:
        self._unavailable()
