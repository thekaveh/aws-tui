"""Modal-snapshot harness apps.

Each app composes one overlay (CommandPalette, ConfirmModal, QuickLook,
TransfersTray) on top of a near-empty base so the snapshot focuses on the
overlay's rendering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.quick_look import QuickLook
from aws_tui.ui.widgets.transfers_tray import TransfersTray
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM, PaletteEntry
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent, QuickLookVM
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState
from aws_tui.vm.file_manager.transfers_vm import TransfersVM


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


# ── Command palette ─────────────────────────────────────────────────────────


class CommandPaletteApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._vm = CommandPaletteVM(hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind palette)", id="placeholder")

    async def on_mount(self) -> None:
        self._vm.construct()
        for spec in [
            ("conn.aws-dev", "connection switch: kaveh-dev", "connection"),
            ("conn.aws-prod", "connection switch: kaveh-prod", "connection"),
            ("conn.minio", "connection switch: minio-local", "connection"),
            ("theme.carbon", "theme switch: carbon", "theme"),
            ("theme.voidline", "theme switch: voidline", "theme"),
            ("log.show", "log show", "logs"),
        ]:
            eid, label, category = spec
            self._vm.register_entry(
                PaletteEntry(id=eid, label=label, category=category),
                lambda _eid=eid: None,
            )
        self._vm.open_command.execute()
        await self.push_screen(CommandPalette(self._vm, hub=self._hub))


# ── Confirm modal ──────────────────────────────────────────────────────────


class ConfirmModalApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._vm = ConfirmationVM(hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)", id="placeholder")

    async def on_mount(self) -> None:
        self._vm.construct()
        request = ConfirmRequest(
            title="Delete 3 objects?",
            body_lines=(
                "This will permanently delete:",
                "  data/alpha.txt",
                "  data/beta.json",
                "  data/gamma.csv",
                "",
                "This cannot be undone.",
            ),
            confirm_label="Delete",
            cancel_label="Cancel",
            danger=True,
        )
        await self.push_screen(ConfirmModal(self._vm, request, hub=self._hub))


# ── Quick Look ─────────────────────────────────────────────────────────────


async def _bytes_iter(data: bytes) -> AsyncIterator[bytes]:
    yield data


class QuickLookApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._vm = QuickLookVM(hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind quick look)", id="placeholder")

    async def on_mount(self) -> None:
        self._vm.construct()
        content = QuickLookContent(
            title="config.json (4.2 KB)",
            mime="application/json",
            chunks=_bytes_iter(
                b'{\n  "name": "aws-tui",\n  "version": "0.6.0",\n  "themes": [\n'
                b'    "carbon",\n    "voidline",\n    "lattice",\n    "amber"\n  ]\n}\n'
            ),
            line_count_estimate=9,
        )
        self._vm.open_command.execute(content)
        await self.push_screen(QuickLook(self._vm, hub=self._hub))


# ── Transfers tray ─────────────────────────────────────────────────────────


class TransfersTrayApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._vm = TransfersVM(hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view", id="placeholder")
        yield TransfersTray(self._vm, hub=self._hub, id="transfers-tray")

    async def on_mount(self) -> None:
        self._vm.construct()
        for i, (state, frac) in enumerate(
            [
                (TransferState.RUNNING, 0.4),
                (TransferState.RUNNING, 0.75),
                (TransferState.COMPLETED, 1.0),
            ]
        ):
            self._vm.register(
                TransferModel(
                    id=f"t-{i}",
                    direction="upload",
                    source_label=f"data/file-{i}.dat",
                    destination_label=f"s3://bucket/file-{i}.dat",
                    bytes_done=int(2048 * frac),
                    bytes_total=2048,
                    state=state,
                )
            )


__all__ = ["CommandPaletteApp", "ConfirmModalApp", "QuickLookApp", "TransfersTrayApp"]
