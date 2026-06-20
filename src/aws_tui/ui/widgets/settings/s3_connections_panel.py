"""S3ConnectionsPanel — list + CRUD chips for s3-compatible connections."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static
from vmx import Message, MessageHub

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
        yield Vertical(*self._render_children(), id="panel-body")

    def _render_children(self) -> list[Widget]:
        """Build the body's children as constructed widget instances.

        Safe to call OUTSIDE compose() (no context-manager state required).
        """
        conns = self._vm.connections
        self._last_row_count = len(conns)
        if not conns:
            return [
                Vertical(
                    Static("No S3-compatible connections configured yet."),
                    Static(""),
                    Static("Add one to access MinIO, Wasabi, R2, etc."),
                    Static("from the same panes you use for AWS S3."),
                    Static(""),
                    _AddButton("+ Add s3-compatible connection", id="add-empty"),
                    classes="empty-state",
                )
            ]
        children: list[Widget] = []
        for c in conns:
            children.append(
                Horizontal(
                    _RowAccent("▎", classes="row-accent"),
                    Static(c.name, classes="row-name"),
                    Static(c.endpoint_url or "", classes="row-endpoint"),
                    Static(c.region, classes="row-region"),
                    _ChipEdit("✎", id=f"edit-{c.name}", classes="row-chip-edit"),
                    _ChipDelete("✕", id=f"delete-{c.name}", classes="row-chip-delete"),
                    classes="connection-row",
                    id=f"row-{c.name}",
                )
            )
        children.append(_AddButton("+ Add s3-compatible connection", id="add-populated"))
        return children

    async def refresh_rows(self) -> None:
        """Tear down + re-render the body container after a CRUD op.

        ``remove_children()`` returns an ``AwaitRemove``; awaiting it
        ensures the old widgets are fully unmounted before the new ones
        are mounted, preventing ``DuplicateIds`` errors.
        """
        body = self.query_one("#panel-body", Vertical)
        await body.remove_children()
        await body.mount_all(self._render_children())

    @on(Button.Pressed, "#add-empty, #add-populated")
    def _on_add(self, event: Button.Pressed) -> None:
        event.stop()
        self._do_add()

    @work(exclusive=False)
    async def _do_add(self) -> None:
        result = await self.app.push_screen_wait(
            S3CompatFormModal(hub=self._hub, defaults=None, name_locked=False)
        )
        if result is None:
            return
        self._vm.add(self._vm.entry_from_form(result))
        await self.refresh_rows()

    @on(Button.Pressed, ".row-chip-edit")
    def _on_edit(self, event: Button.Pressed) -> None:
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
        self._do_edit(name, defaults)

    @work(exclusive=False)
    async def _do_edit(self, name: str, defaults: S3CompatForm) -> None:
        result = await self.app.push_screen_wait(
            S3CompatFormModal(hub=self._hub, defaults=defaults, name_locked=True)
        )
        if result is None:
            return
        self._vm.update(name, self._vm.entry_from_form(result))
        await self.refresh_rows()

    @on(Button.Pressed, ".row-chip-delete")
    def _on_delete(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        name = btn_id.removeprefix("delete-")
        self._do_delete(name)

    @work(exclusive=False)
    async def _do_delete(self, name: str) -> None:
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
            await self.refresh_rows()


__all__ = ["S3ConnectionsPanel"]
