"""S3ConnectionsPanel — list + CRUD chips for s3-compatible connections."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.infra.config_store import ConnectionEntry
from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.first_run_modal import S3CompatFormModal
from aws_tui.vm.chrome.confirm_vm import (
    ConfirmationVM,
    ConfirmPath,
    ConfirmRequest,
)
from aws_tui.vm.chrome.first_run_vm import S3CompatForm
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM


class _RowAccent(Static):
    """1-cell wide colored left rule for a connection row."""


class _AddButton(Button):
    """Pill-shaped 'Add s3-compatible connection' button."""


class _ChipEdit(Button):
    """Inline edit chip for a connection row."""


class _ChipDelete(Button):
    """Inline delete chip for a connection row."""


class S3ConnectionsPanel(Widget):
    """Renders the list of s3-compatible connections + CRUD chips."""

    DEFAULT_CSS = """
    S3ConnectionsPanel {
        height: 1fr;
        width: 1fr;
    }
    S3ConnectionsPanel > #panel-body {
        height: 1fr;
        padding: 0 1;
    }
    S3ConnectionsPanel .connection-row {
        height: 1;
        width: 1fr;
    }
    S3ConnectionsPanel .empty-state {
        align: center middle;
        height: 1fr;
        padding: 1 2;
    }
    S3ConnectionsPanel .empty-state Static {
        text-align: center;
    }
    """

    def __init__(self, *, vm: S3ConnectionsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm: S3ConnectionsVM = vm
        self._hub: MessageHub[Message] = hub
        # Cache the row count so we can detect "transitioned to/from
        # empty" without rebuilding identically.
        self._last_row_count: int = -1

    @property
    def vm(self) -> S3ConnectionsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Vertical(id="panel-body"):
            yield from self._render_body()

    def _render_body(self) -> ComposeResult:
        conns = self._vm.connections
        self._last_row_count = len(conns)
        if not conns:
            with Vertical(classes="empty-state"):
                yield Static("No S3-compatible connections configured yet.")
                yield Static("")
                yield Static("Add one to access MinIO, Wasabi, R2, etc.")
                yield Static("from the same panes you use for AWS S3.")
                yield Static("")
                yield _AddButton("+ Add s3-compatible connection", id="add-empty")
            return
        for c in conns:
            with Horizontal(classes="connection-row", id=f"row-{c.name}"):
                yield _RowAccent("▎", classes="row-accent")
                yield Static(c.name, classes="row-name")
                yield Static(c.endpoint_url or "", classes="row-endpoint")
                yield Static(c.region, classes="row-region")
                yield _ChipEdit("✎", id=f"edit-{c.name}", classes="row-chip-edit")
                yield _ChipDelete("✕", id=f"delete-{c.name}", classes="row-chip-delete")
        yield _AddButton("+ Add s3-compatible connection", id="add-populated")

    def refresh_rows(self) -> None:
        """Tear down + re-render the body container after a CRUD op."""
        body = self.query_one("#panel-body", Vertical)
        body.remove_children()
        for child in self._render_body():
            body.mount(child)

    @on(Button.Pressed, "#add-empty, #add-populated")
    async def _on_add(self, event: Button.Pressed) -> None:
        event.stop()
        result = await self.app.push_screen_wait(
            S3CompatFormModal(hub=self._hub, defaults=None, name_locked=False)
        )
        if result is None:
            return
        self._vm.add(_form_to_entry(result))
        self.refresh_rows()

    @on(Button.Pressed, ".row-chip-edit")
    async def _on_edit(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        name = btn_id.removeprefix("edit-")
        existing = next((c for c in self._vm.connections if c.name == name), None)
        if existing is None:
            return
        defaults = S3CompatForm(
            name=existing.name,
            endpoint_url=existing.endpoint_url or "",
            region=existing.region,
            access_key_id=existing.access_key_id or "",
            secret_access_key=existing.secret_access_key or "",
            force_path_style=existing.force_path_style,
            verify_tls=existing.verify_tls,
        )
        result = await self.app.push_screen_wait(
            S3CompatFormModal(hub=self._hub, defaults=defaults, name_locked=True)
        )
        if result is None:
            return
        self._vm.update(name, _form_to_entry(result))
        self.refresh_rows()

    @on(Button.Pressed, ".row-chip-delete")
    async def _on_delete(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        name = btn_id.removeprefix("delete-")
        confirm_vm = ConfirmationVM(hub=self._hub, dispatcher=self._vm.dispatcher)
        confirm_vm.construct()
        try:
            request = ConfirmRequest(
                title=f"Delete connection {name!r}?",
                paths=(ConfirmPath(label="Name", path=name),),
                body_lines=("This cannot be undone.",),
                confirm_label="Delete",
                cancel_label="Cancel",
                danger=True,
            )
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(confirm_vm, request, hub=self._hub)
            )
        finally:
            confirm_vm.dispose()
        if confirmed:
            self._vm.remove(name)
            self.refresh_rows()


def _form_to_entry(form: S3CompatForm) -> ConnectionEntry:
    return ConnectionEntry(
        name=form.name,
        kind="s3-compatible",
        region=form.region,
        endpoint_url=form.endpoint_url,
        access_key_id=form.access_key_id,
        secret_access_key=form.secret_access_key,
        force_path_style=form.force_path_style,
        verify_tls=form.verify_tls,
    )


__all__ = ["S3ConnectionsPanel"]
