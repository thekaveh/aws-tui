"""TransfersOverlay snapshot harness."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM


def _model(
    *,
    id: str,
    bytes_done: int,
    bytes_total: int | None,
    state: TransferState,
    src: str,
    dst: str,
    direction: str = "upload",
) -> TransferModel:
    return TransferModel(
        id=id,
        direction=direction,
        source_label=src,
        destination_label=dst,
        bytes_done=bytes_done,
        bytes_total=bytes_total,
        state=state,
    )


class TransfersSnapshotApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._theme = theme
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._transfers = TransfersVM(hub=self._hub, dispatcher=self._dispatcher)
        # Inject a fake clock so speed/eta render deterministically in the
        # snapshot. Two samples 1s apart with 700_000 vs 1_240_000 bytes ->
        # speed = 540_000 B/s ≈ 527.3 KB/s; ETA = (2_000_000-1_240_000)/540_000
        # ≈ 1.41 s
        clock_ticks = iter([0.0, 1.0])

        def fake_clock() -> float:
            return next(clock_ticks)

        running = TransferVM(
            _model(
                id="r",
                bytes_done=700_000,
                bytes_total=2_000_000,
                state=TransferState.RUNNING,
                src="/Users/kaveh/repo.tar.gz",
                dst="s3://prod/repo.tar.gz",
            ),
            hub=self._hub,
            dispatcher=self._dispatcher,
            clock=fake_clock,
        )
        # First sample at t=0 with bytes_done=700_000; second sample at t=1.0
        # with bytes_done=1_240_000. The second apply_update IS the visible
        # state in the snapshot. Use register_vm (Task 6.5) so the fake-clock
        # samples survive — register(model) would discard this VM and build
        # its own with time.monotonic.
        running.construct()
        running.apply_update(bytes_done=700_000, bytes_total=2_000_000, state=TransferState.RUNNING)
        running.apply_update(
            bytes_done=1_240_000, bytes_total=2_000_000, state=TransferState.RUNNING
        )
        self._transfers.register_vm(running)

        done = TransferVM(
            _model(
                id="d",
                bytes_done=458_000,
                bytes_total=458_000,
                state=TransferState.COMPLETED,
                src="/Users/kaveh/backup.zip",
                dst="s3://archive/backup.zip",
            ),
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        done.construct()
        self._transfers.register_vm(done)

        failed = TransferVM(
            _model(
                id="f",
                bytes_done=120_000,
                bytes_total=4_200_000,
                state=TransferState.FAILED,
                src="/Users/kaveh/2026-Q2.csv",
                dst="s3://reports/2026-Q2.csv",
            ),
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        failed.construct()
        self._transfers.register_vm(failed)
        self._transfers.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui (behind transfers overlay)", id="placeholder")
        yield TransfersOverlay(self._transfers, hub=self._hub)


__all__ = ["TransfersSnapshotApp"]
