"""ConnectionFormInline — inline form for adding / editing an
s3-compatible connection. Lifted from the deleted
``S3CompatFormModal``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.events import Click
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Input, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.first_run_vm import S3CompatForm

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    ("name", "Name", "minio-local", False),
    ("endpoint_url", "Endpoint URL", "http://localhost:9000", False),
    ("region", "Region", "us-east-1", False),
    ("access_key_id", "Access key ID", "", False),
    ("secret_access_key", "Secret access key", "", True),
)


def _validate_s3_form_value(field: str, value: str) -> str | None:
    """Return None if valid, else an error string suitable for tooltip.

    Rules:
    - ``name``: matches ``^[A-Za-z0-9_-]{1,32}$``
    - ``endpoint_url``: ``http://`` or ``https://``, non-empty netloc
    - ``region`` / ``access_key_id`` / ``secret_access_key``:
      non-empty after strip
    """
    stripped = value.strip()
    if field == "name":
        if not _NAME_RE.match(value):
            return "1-32 chars, alphanumeric + dash/underscore only"
        return None
    if field == "endpoint_url":
        if not stripped:
            return "required"
        try:
            parsed = urlparse(stripped)
        except ValueError:
            return "not a valid URL"
        if parsed.scheme not in ("http", "https"):
            return "must start with http:// or https://"
        if not parsed.netloc:
            return "missing host"
        return None
    if not stripped:
        return "required"
    return None


@dataclass
class _OpenContext:
    mode: Literal["add", "edit"]
    original_name: str | None


class ConnectionFormSubmitted(TextualMessage):
    """Emitted by ``ConnectionFormInline`` when the user clicks Save
    on a valid form."""

    def __init__(
        self,
        *,
        form: S3CompatForm,
        mode: Literal["add", "edit"],
        original_name: str | None,
    ) -> None:
        super().__init__()
        self.form: S3CompatForm = form
        self.mode: Literal["add", "edit"] = mode
        self.original_name: str | None = original_name


class ConnectionFormCancelled(TextualMessage):
    """Emitted by ``ConnectionFormInline`` when the user clicks Cancel
    or presses Esc inside the form."""


class ConnectionFormInline(Widget):
    """Inline form for s3-compatible connections.

    Hidden by default (``display: none``). Call ``open_for_add()`` or
    ``open_for_edit(name, defaults)`` to populate fields and show.

    Click Save → emits :class:`ConnectionFormSubmitted`.  The form does
    **not** close itself; the parent panel calls :meth:`close` only when
    the persistence step succeeds, so that errors can keep the form open.

    Click Cancel or Esc → emits :class:`ConnectionFormCancelled` and
    hides.
    """

    DEFAULT_CSS = """
    ConnectionFormInline {
        display: none;
        height: auto;
        width: 1fr;
    }
    ConnectionFormInline.-open {
        display: block;
    }
    ConnectionFormInline > Container {
        height: auto;
        padding: 1 2;
    }
    ConnectionFormInline .form-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    ConnectionFormInline .form-label {
        padding: 0 0 0 0;
    }
    ConnectionFormInline .form-fields {
        height: auto;
    }
    ConnectionFormInline .form-footer {
        height: 3;
        align: right middle;
    }
    """

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, *, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._hub: MessageHub[Message] = hub
        self._ctx: _OpenContext | None = None

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("New s3-compatible connection", classes="form-title", id="form-title")
            with Vertical(classes="form-fields"):
                for key, label, placeholder, secret in _FIELDS:
                    yield Static(label, classes="form-label")
                    yield Input(
                        value="",
                        placeholder=placeholder,
                        password=secret,
                        id=f"form-{key}",
                    )
            with Horizontal(classes="form-footer"):
                yield ModalButton("cancel", button_id="form-cancel-btn")
                yield ModalButton("save", button_id="form-save-btn", classes="-primary")

    # ── Public API ─────────────────────────────────────────────────────────

    def open_for_add(self) -> None:
        """Show the form in Add mode (all fields empty, name unlocked)."""
        self._ctx = _OpenContext(mode="add", original_name=None)
        self.query_one("#form-title", Static).update("New s3-compatible connection")
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            inp.value = ""
            inp.disabled = False
            inp.remove_class("-invalid")
        self._refresh_save_button()
        self.add_class("-open")
        # Focus the first field for keyboard convenience.
        self.query_one("#form-name", Input).focus()

    def open_for_edit(self, *, name: str, defaults: S3CompatForm) -> None:
        """Show the form in Edit mode (pre-filled, name locked)."""
        self._ctx = _OpenContext(mode="edit", original_name=name)
        self.query_one("#form-title", Static).update(f"Edit {name!r}")
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            default_val = getattr(defaults, key, "")
            inp.value = str(default_val) if default_val is not None else ""
            inp.disabled = key == "name"
            inp.remove_class("-invalid")
        self._refresh_save_button()
        self.add_class("-open")
        self.query_one("#form-endpoint_url", Input).focus()

    def close(self) -> None:
        """Hide the form and clear state."""
        self.remove_class("-open")
        self._ctx = None

    # ── Event handlers ─────────────────────────────────────────────────────

    @on(Input.Changed)
    def _on_input_changed(self, event: Input.Changed) -> None:
        field = event.input.id.removeprefix("form-") if event.input.id else ""
        if field not in {"name", "endpoint_url", "region", "access_key_id", "secret_access_key"}:
            return
        err = _validate_s3_form_value(field, event.value)
        if err is None:
            event.input.remove_class("-invalid")
        else:
            event.input.add_class("-invalid")
        self._refresh_save_button()

    def on_click(self, event: Click) -> None:
        # Walk up from the click target to find the ModalButton (if any).
        # ModalButton is a Static subclass (not Button), so @on(Button.Pressed)
        # won't fire. Use the same walk-up pattern as ConfirmModal.
        node: object | None = event.widget if hasattr(event, "widget") else None
        while node is not None:
            if isinstance(node, ModalButton):
                if node.button_id == "form-cancel-btn":
                    self.action_cancel()
                elif node.button_id == "form-save-btn":
                    self._submit()
                return
            node = getattr(node, "parent", None)

    def action_cancel(self) -> None:
        if self._ctx is None:
            return
        self.close()
        self.post_message(ConnectionFormCancelled())

    # ── Internal ───────────────────────────────────────────────────────────

    def _refresh_save_button(self) -> None:
        save_btn: ModalButton | None = None
        for btn in self.query(ModalButton):
            if btn.button_id == "form-save-btn":
                save_btn = btn
                break
        if save_btn is None:
            return
        invalid = False
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            if _validate_s3_form_value(key, inp.value) is not None:
                invalid = True
                break
        save_btn.disabled = invalid

    def mark_name_invalid(self, message: str) -> None:
        """Add ``-invalid`` to the name Input so the user sees the error.

        ``message`` is accepted for forward-compatibility (tooltip / status
        bar) but is not surfaced in the widget itself — callers should also
        show a toast with the message.
        """
        self.query_one("#form-name", Input).add_class("-invalid")

    def _submit(self) -> None:
        """Validate fields and post :class:`ConnectionFormSubmitted`.

        The form does **not** close itself here — the parent panel is
        responsible for calling :meth:`close` only when the persistence step
        succeeds.  On a duplicate-name or other persistence error the parent
        keeps the form open and surfaces an appropriate error message.
        """
        if self._ctx is None:
            return
        # Final validation pass — refuse if anything regressed.
        values: dict[str, str] = {}
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            if _validate_s3_form_value(key, inp.value) is not None:
                return  # Save button should have been disabled; defense in depth.
            values[key] = inp.value
        form = S3CompatForm(
            name=values["name"],
            endpoint_url=values["endpoint_url"],
            region=values["region"],
            access_key_id=values["access_key_id"],
            secret_access_key=values["secret_access_key"],
            force_path_style=True,
            verify_tls=True,
        )
        ctx = self._ctx
        self.post_message(
            ConnectionFormSubmitted(form=form, mode=ctx.mode, original_name=ctx.original_name)
        )


__all__ = [
    "ConnectionFormCancelled",
    "ConnectionFormInline",
    "ConnectionFormSubmitted",
]
