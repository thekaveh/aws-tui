"""Connection-specific policy controls; persistence runs away from the UI loop."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Button, Static

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.vm.settings.connection_protection_vm import (
    ConnectionProtectionRow,
    ConnectionProtectionVM,
)


class ConnectionProtectionPanel(DeferredWorkerMixin, Widget):
    DEFAULT_CSS = """
    ConnectionProtectionPanel { height: auto; }
    ConnectionProtectionPanel Vertical { height: auto; }
    ConnectionProtectionPanel Static { height: auto; }
    ConnectionProtectionPanel Button { width: auto; margin: 0 0 1 0; }
    """

    def __init__(self, *, vm: ConnectionProtectionVM) -> None:
        super().__init__()
        self._vm = vm
        self._rows: tuple[ConnectionProtectionRow, ...] = ()
        self._pending = False

    def compose(self) -> ComposeResult:
        yield Static(
            "Protection blocks new remote writes and executions. Reads and cancellation remain available.",
            markup=False,
        )
        yield Static("", id="connection-protection-status", markup=False)
        yield Vertical(id="connection-protection-rows")

    def on_mount(self) -> None:
        self._run_lifecycle_worker(self._load, group="connection-protection-load")

    async def _load(self) -> None:
        try:
            rows = await self._vm.rows()
        except Exception:
            self.query_one("#connection-protection-status", Static).update(
                "Cannot read connection policies. Reopen Settings to retry."
            )
            return
        if not self.is_mounted:
            return
        same_rows = bool(rows) and tuple(row.name for row in rows) == tuple(
            row.name for row in self._rows
        )
        self._rows = rows
        container = self.query_one("#connection-protection-rows", Vertical)
        if not same_rows:
            await container.remove_children()
        for index, row in enumerate(rows):
            label = f"{row.name} · {row.kind} · {row.region} · {'Protected' if row.protected else 'Unprotected'}"
            button_label = "Disable protection" if row.protected else "Enable protection"
            if same_rows:
                container.query_one(f"#connection-protection-label-{index}", Static).update(label)
                container.query_one(f"#connection-protection-{index}", Button).label = button_label
            else:
                await container.mount(
                    Static(label, id=f"connection-protection-label-{index}", markup=False),
                    Button(
                        button_label,
                        id=f"connection-protection-{index}",
                        disabled=self._vm.read_only,
                    ),
                )
        if not rows:
            await container.mount(Static("No connections are available.", markup=False))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id is None or not event.button.id.startswith("connection-protection-"):
            return
        event.stop()
        if (
            self._pending
            or not event.button.is_attached
            or not event.button.is_mounted
            or self.query_one(f"#{event.button.id}", Button) is not event.button
        ):
            return
        index = int(event.button.id.removeprefix("connection-protection-"))
        if index >= len(self._rows):
            return
        self._pending = True
        self.disabled = True
        self._run_lifecycle_worker(lambda: self._save(index), group="connection-protection-save")

    async def _save(self, index: int) -> None:
        row = self._rows[index]
        try:
            await self._vm.set_protected(row, not row.protected)
            if self.is_mounted:
                self.query_one("#connection-protection-status", Static).update(
                    "Policy saved. Blocked operations are not replayed."
                )
                await self._load()
        except Exception:
            if self.is_mounted:
                self.query_one("#connection-protection-status", Static).update(
                    "Could not save protection. Reopen Settings to refresh the connection."
                )
        finally:
            self._pending = False
            self.disabled = False
