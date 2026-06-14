"""Modal-snapshot harness apps.

Each app composes one overlay (CommandPalette, ConfirmModal, QuickLook,
TransfersTray, CrashModal, ResumeModal, FirstRunModal) on top of a
near-empty base so the snapshot focuses on the overlay's rendering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.domain.transfer_journal import TransferJournalEntry
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.crash_modal import CrashModal
from aws_tui.ui.widgets.first_run_modal import FirstRunModal
from aws_tui.ui.widgets.quick_look import QuickLook
from aws_tui.ui.widgets.resume_modal import ResumeModal
from aws_tui.ui.widgets.transfers_tray import TransfersTray
from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM, PaletteEntry
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest
from aws_tui.vm.chrome.crash_vm import CrashReport, CrashVM
from aws_tui.vm.chrome.first_run_vm import FirstRunVM
from aws_tui.vm.chrome.quick_look_vm import QuickLookContent, QuickLookVM
from aws_tui.vm.chrome.resume_vm import ResumeVM
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


# ── Crash modal ────────────────────────────────────────────────────────────


class CrashModalApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        report = CrashReport(
            timestamp=datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC),
            exception_type="TypeError",
            exception_message="unsupported operand type(s) for +: 'int' and 'str'",
            traceback_short=(
                "Traceback (most recent call last):\n"
                '  File "/aws-tui/vm/pane.py", line 142, in _on_navigate\n'
                "    cursor = current + label\n"
                "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
            ),
            dump_path=Path("/Users/kaveh/.cache/aws-tui/crash/2026-06-14T12-00-00.txt"),
            can_continue=False,
            last_action_id="pane.delete_marked",
        )
        self._vm = CrashVM(report, hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind crash modal)", id="placeholder")

    async def on_mount(self) -> None:
        self._vm.construct()
        await self.push_screen(CrashModal(self._vm, hub=self._hub))


# ── Resume modal ───────────────────────────────────────────────────────────


def _resume_entries() -> list[TransferJournalEntry]:
    return [
        TransferJournalEntry(
            transfer_id="abc123",
            source_uri="local:///Users/kaveh/data/api-2026-06-13.json",
            destination_uri="s3://kaveh-dev/uploads/api-2026-06-13.json",
            upload_id="mpu-aaa-111",
            bytes_total=4 * 1024 * 1024 + 200_000,
            started_at=datetime(2026, 6, 13, 23, 30, 0, tzinfo=UTC),
            last_progress=datetime(2026, 6, 13, 23, 45, 0, tzinfo=UTC),
            completed_parts=(1, 2, 3),
            completed_etags=("e1", "e2", "e3"),
        ),
        TransferJournalEntry(
            transfer_id="def456",
            source_uri="local:///Users/kaveh/data/db-slowq-06-13.csv",
            destination_uri="s3://kaveh-dev/uploads/db-slowq-06-13.csv",
            upload_id="mpu-bbb-222",
            bytes_total=892_000,
            started_at=datetime(2026, 6, 13, 23, 50, 0, tzinfo=UTC),
            last_progress=datetime(2026, 6, 13, 23, 55, 0, tzinfo=UTC),
            completed_parts=(1,),
            completed_etags=("e1",),
        ),
    ]


class ResumeModalApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._vm = ResumeVM(_resume_entries(), hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind resume modal)", id="placeholder")

    async def on_mount(self) -> None:
        self._vm.construct()
        await self.push_screen(ResumeModal(self._vm, hub=self._hub))


# ── First-run modal ────────────────────────────────────────────────────────


class FirstRunModalApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._vm = FirstRunVM(hub=self._hub, dispatcher=self._dispatcher)

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind first-run modal)", id="placeholder")

    async def on_mount(self) -> None:
        self._vm.construct()
        await self.push_screen(FirstRunModal(self._vm, hub=self._hub))


__all__ = [
    "CommandPaletteApp",
    "ConfirmModalApp",
    "CrashModalApp",
    "FirstRunModalApp",
    "QuickLookApp",
    "ResumeModalApp",
    "TransfersTrayApp",
]
