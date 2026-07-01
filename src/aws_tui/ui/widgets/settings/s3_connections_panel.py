"""S3ConnectionsPanel — list + CRUD chips for s3-compatible connections."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.confirm_modal import ConfirmModal
from aws_tui.ui.widgets.settings.connection_form import (
    ConnectionFormCancelled,
    ConnectionFormInline,
    ConnectionFormSubmitted,
)
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
    /* Auto-size to content (rows + optional inline form). The earlier
       ``1fr`` posture stretched the panel to absorb whatever
       VerticalScroll handed it, leaving a tall blank region when the
       user hadn't added any connections yet. With the post-PR-67
       per-Collapsible borders on the Settings page, the surrounding
       Collapsible now gives the panel a proper visual frame; the
       panel itself just needs to fit its content. The form-open
       case sizes naturally tall — VerticalScroll above will scroll
       to reach the Save button when total content exceeds the
       viewport. */
    S3ConnectionsPanel {
        height: auto;
        width: 1fr;
    }
    S3ConnectionsPanel > #panel-body {
        height: auto;
        padding: 0 1;
    }
    S3ConnectionsPanel .connection-row {
        height: 1;
        width: 1fr;
    }
    S3ConnectionsPanel .empty-state {
        height: auto;
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
        # Index-keyed map from row id slug to connection name. The
        # name itself can't go into the widget id because Textual
        # validates against ``^[a-zA-Z_-][a-zA-Z0-9_-]*$`` and would
        # raise ``BadIdentifier`` on a config.toml-loaded name
        # containing ``[``, ``.``, whitespace, or any non-ASCII
        # character. Rebuilt on every render in ``_render_children``.
        self._id_to_name: dict[str, str] = {}

    @property
    def vm(self) -> S3ConnectionsVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Vertical(*self._render_children(), id="panel-body")
        yield ConnectionFormInline(hub=self._hub)

    def _render_children(self) -> list[Widget]:
        """Build the body's children as constructed widget instances.

        Safe to call OUTSIDE compose() (no context-manager state required).
        """
        conns = self._vm.connections
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
        # Rebuild the id → name map. Use index-based slugs because
        # connection names can come from a config.toml that allows
        # quoted bracketed keys (``[connections."minio[v2]"]``) or
        # any other character Textual's ``^[a-zA-Z_-][a-zA-Z0-9_-]*$``
        # widget-id grammar rejects. Without this the Settings page
        # crashes on mount before the user can even see the row to
        # repair the bad name.
        self._id_to_name = {}
        for idx, c in enumerate(conns):
            self._id_to_name[str(idx)] = c.name
            # ``markup=False`` on every user-controlled field. The
            # connection-form validator gates ``c.name`` against
            # ``[A-Za-z0-9_-]{1,32}`` so brackets are blocked there,
            # but ``endpoint_url`` and ``region`` are not similarly
            # restricted, and ``c.name`` itself can arrive from a
            # hand-edited config.toml. Defensive guard.
            children.append(
                Horizontal(
                    _RowAccent("▎", classes="row-accent"),
                    Static(c.name, classes="row-name", markup=False),
                    Static(c.endpoint_url or "", classes="row-endpoint", markup=False),
                    Static(c.region, classes="row-region", markup=False),
                    _ChipEdit("✎", id=f"edit-{idx}", classes="row-chip-edit"),
                    _ChipDelete("✕", id=f"delete-{idx}", classes="row-chip-delete"),
                    classes="connection-row",
                    id=f"row-{idx}",
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
        self._on_add_clicked()

    def _on_add_clicked(self) -> None:
        form = self.query_one(ConnectionFormInline)
        form.open_for_add()

    @on(Button.Pressed, ".row-chip-edit")
    def _on_edit(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        slug = btn_id.removeprefix("edit-")
        # Map back through ``_id_to_name`` because the button id is
        # an index slug (Textual widget-id grammar rejects names
        # containing brackets / dots / whitespace, so we can't
        # round-trip the name through the id).
        name = self._id_to_name.get(slug)
        if name is None:
            return
        self._on_edit_clicked(name)

    def _on_edit_clicked(self, name: str) -> None:
        existing = self._vm.find_by_name(name)
        if existing is None:
            return
        defaults = S3CompatForm(
            name=existing.name,
            endpoint_url=existing.endpoint_url or "",
            region=existing.region,
            access_key_id=existing.access_key_id or "",
            secret_access_key=existing.secret_access_key or "",
            session_token=existing.session_token,
            force_path_style=existing.force_path_style,
            verify_tls=existing.verify_tls,
        )
        form = self.query_one(ConnectionFormInline)
        form.open_for_edit(name=name, defaults=defaults)

    @on(Button.Pressed, ".row-chip-delete")
    def _on_delete(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""
        slug = btn_id.removeprefix("delete-")
        # Same id-slug → name mapping as the edit handler.
        name = self._id_to_name.get(slug)
        if name is None:
            return
        self._do_delete(name)

    @work(exclusive=True, group="s3-connections-delete")
    async def _do_delete(self, name: str) -> None:
        # exclusive=True + group serializes rapid double-clicks on the
        # same (or different) delete chip — without it, two concurrent
        # workers can both pass the confirm dialog and the second's
        # vm.remove() crashes with a missing-entry error.
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
        if not confirmed:
            return
        try:
            self._vm.remove(name)
        except Exception as exc:
            # The connection vanished between the dialog opening and our
            # remove() call (concurrent edit, file corruption, etc.).
            # Surface the failure rather than letting it crash the worker.
            self._surface_error_toast(
                f"Could not delete {name!r}: {exc}",
                toast_id=f"delete-error-{name}",
            )
            return
        await self.refresh_rows()

    def _surface_error_toast(self, text: str, toast_id: str) -> None:
        """Show an ERROR-level toast if the app context is available.

        Uses the public ``app_ctx`` property instead of reaching into
        ``_app_ctx``. The ``hasattr`` gate still covers tests / harnesses
        that mount this panel under a vanilla Textual ``App`` without
        the ``AwsTuiApp`` wrapper.
        """
        if hasattr(self.app, "app_ctx"):
            from aws_tui.ui import notifications

            notifications.error(
                self.app.app_ctx.root_vm.chrome.toast_stack,
                subject="Settings",
                message=text,
                toast_id=toast_id,
            )

    async def on_connection_form_submitted(self, event: ConnectionFormSubmitted) -> None:
        """Handle a Save from the inline form.

        The form stays open until *this* handler decides the persistence
        step succeeded.  On success we call ``form.close()`` then refresh
        the row list.  On failure we keep the form open and surface an
        error (mark name field + toast).

        Async so we can ``await self.refresh_rows()`` directly; using
        ``run_worker`` would silently swallow any exception from the
        refresh (e.g., panel not yet mounted) and the user would see
        the form close without the list updating.
        """
        form = self.query_one(ConnectionFormInline)
        entry = self._vm.entry_from_form(event.form)
        if event.mode == "add":
            try:
                self._vm.add(entry)
            except ValueError:
                # Duplicate name — keep form open, mark the field invalid.
                form.mark_name_invalid()
                self._surface_error_toast(
                    f"Connection {event.form.name!r} already exists.",
                    toast_id=f"duplicate-{event.form.name}",
                )
                return
            except Exception as exc:
                # ConfigError (invalid kind), OSError (disk full / read-only),
                # or anything else from ConfigStore.save(). Keep the form
                # open so the user doesn't lose their entered values; surface
                # the error so they understand why it didn't save.
                # ``clear_submitting()`` re-arms the re-entrancy flag so the
                # user can retry after fixing the underlying problem.
                form.clear_submitting()
                self._surface_error_toast(
                    f"Could not save {event.form.name!r}: {exc}",
                    toast_id=f"add-error-{event.form.name}",
                )
                return
        else:  # "edit"
            assert event.original_name is not None
            try:
                self._vm.update(event.original_name, entry)
            except Exception as exc:
                form.clear_submitting()
                self._surface_error_toast(
                    f"Could not save {event.form.name!r}: {exc}",
                    toast_id=f"edit-error-{event.form.name}",
                )
                return
        # Persistence succeeded — close the form and refresh the list.
        form.close()
        await self.refresh_rows()

    def on_connection_form_cancelled(self, event: ConnectionFormCancelled) -> None:
        """Form closed itself on cancel; nothing to do here."""
        return


__all__ = ["S3ConnectionsPanel"]
