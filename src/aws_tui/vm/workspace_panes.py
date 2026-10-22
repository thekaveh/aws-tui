"""File navigation capture and isolated preparation, with independent routing."""

import asyncio
from collections.abc import Awaitable, Callable

from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.navigation_workspace import FileLocation, NavigationTarget, WorkspaceError
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.domain.unavailable_navigation_fs import UnavailableNavigationFS
from aws_tui.vm.file_manager.dual_pane_vm import DualPaneVM
from aws_tui.vm.file_manager.pane_vm import PaneState, PaneVM
from aws_tui.vm.file_manager.transfer_runtime import TransferRuntime
from aws_tui.vm.workspace_navigation import WorkspaceNavigationError


def capture_pane_target(pane: PaneVM) -> NavigationTarget:
    if isinstance(pane.provider, UnavailableNavigationFS):
        return pane.provider.navigation_target
    key = pane.current_connection_key
    if key is None:
        if not isinstance(pane.provider, LocalFS):
            raise WorkspaceError("invalid")
        return NavigationTarget(
            "s3", location=FileLocation(local=pane.provider.navigation_path(pane.path))
        )
    if key[0] not in {"aws", "s3-compatible"} or pane.path_protocol != "s3:":
        raise WorkspaceError("invalid")
    return NavigationTarget(
        "s3", key[1], pane.current_connection_region, location=FileLocation(s3=pane.path.segments)
    )


def validate_pane_targets(targets: tuple[NavigationTarget, NavigationTarget]) -> None:
    """Validate the entire structural batch before installing any pane intent."""
    if (
        type(targets) is not tuple
        or len(targets) != 2
        or any(
            not isinstance(target, NavigationTarget) or target.service != "s3" for target in targets
        )
    ):
        raise WorkspaceError("invalid")


class WorkspacePaneVM(DualPaneVM):
    """A candidate owns its reads, and gains shared-transfer authority only on adoption."""

    def __init__(
        self,
        *,
        left: PaneVM,
        right: PaneVM,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        transfer_journal: TransferJournal,
        transfer_runtime: TransferRuntime,
    ) -> None:
        super().__init__(
            left=left,
            right=right,
            hub=hub,
            dispatcher=dispatcher,
            transfer_journal=transfer_journal,
            transfer_runtime=transfer_runtime,
        )
        self._expected_navigation = (capture_pane_target(left), capture_pane_target(right))
        self._navigation_prepared = False
        self._navigation_adopted = False

    def _verify_navigation(self) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if (
            self._disposed
            or self._shutdown_started
            or (capture_pane_target(self.left), capture_pane_target(self.right))
            != self._expected_navigation
            or any(
                pane.state not in {PaneState.IDLE, PaneState.EMPTY}
                for pane in self._validated_panes()
            )
        ):
            raise WorkspaceNavigationError("destination_unavailable")

    async def setup(self) -> None:
        self._navigation_prepared = False
        if self._disposed or self._shutdown_started:
            raise WorkspaceNavigationError("destination_unavailable")
        # Initial reads need the same owned, cancellation-resistant drain as
        # later refreshes. Shielding also prevents repeated parent cancellation
        # from interrupting a provider's in-progress cleanup.
        task = asyncio.create_task(self._prepare_panes())
        self._refresh_tasks.add(task)
        try:
            await asyncio.shield(task)
            self._verify_navigation()
            self._navigation_prepared = True
        except BaseException:
            await self._cancel_and_drain_refreshes((task,))
            raise
        finally:
            self._refresh_tasks.discard(task)

    async def _prepare_panes(self) -> None:
        # Strict missing handling is essential for a removed local root: the
        # normal browser intentionally treats a missing root as an empty view.
        async with asyncio.TaskGroup() as tasks:
            for pane in self._validated_panes():
                tasks.create_task(pane.setup(strict_missing=True))

    def _validated_panes(self) -> tuple[PaneVM, ...]:
        return self.left, self.right

    def activate_navigation(self) -> None:
        """Commit only a successfully prepared candidate; repeated acknowledgement is inert."""
        if self._navigation_adopted:
            return
        if not self._navigation_prepared:
            raise WorkspaceNavigationError("destination_unavailable")
        self._verify_navigation()
        self._navigation_adopted = True

    async def shutdown(self) -> None:
        if self._navigation_adopted:
            await super().shutdown()
        else:
            self._shutdown_started = True
            await self._cancel_and_drain_refreshes()


class BookmarkPaneVM(WorkspacePaneVM):
    """Validate only the destination; normal companion reads start after publication."""

    def __init__(
        self,
        *,
        left: PaneVM,
        right: PaneVM,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        transfer_journal: TransferJournal,
        transfer_runtime: TransferRuntime,
        companion_loader: Callable[[PaneVM], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(
            left=left,
            right=right,
            hub=hub,
            dispatcher=dispatcher,
            transfer_journal=transfer_journal,
            transfer_runtime=transfer_runtime,
        )
        self._companion_loader = companion_loader

    def _validated_panes(self) -> tuple[PaneVM, ...]:
        return (self.left,)

    def activate_navigation(self) -> None:
        already_adopted = self._navigation_adopted
        super().activate_navigation()
        if not already_adopted:
            if self._companion_loader is None:
                self._schedule_owned_refresh(self.right)
            else:
                self._schedule_owned_operation(self._load_companion)

    async def _load_companion(self) -> None:
        assert self._companion_loader is not None
        await self._companion_loader(self.right)
