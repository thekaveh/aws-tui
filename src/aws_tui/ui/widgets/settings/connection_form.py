"""ConnectionFormInline — inline form for adding / editing an
s3-compatible connection. Lifted from the deleted
``S3CompatFormModal``.

Round-3 directive §9.bis.11: the form's validation state is owned
by :class:`S3ConnectionFormVM` (composes
:class:`vmx.FormVM[S3CompatForm]`), not by a private View-side
helper. The widget consumes the VM's
``can_submit`` / ``errors`` / ``model`` surface and routes
``Input.Changed`` through ``form_vm.set_field`` so the
validators (field-presence + name-format + URL-format +
endpoint-IFF-force-path-style cross-field) live in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
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
from aws_tui.vm.settings.s3_connection_form_vm import S3ConnectionFormVM

if TYPE_CHECKING:
    from reactivex.abc import DisposableBase

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    ("name", "Name", "minio-local", False),
    ("endpoint_url", "Endpoint URL", "http://localhost:9000", False),
    ("region", "Region", "us-east-1", False),
    ("access_key_id", "Access key ID", "", False),
    ("secret_access_key", "Secret access key", "", True),
    ("session_token", "Session token", "", True),
)


def _validate_name(form: S3CompatForm) -> str | None:
    """Name must match the connection-id regex (1-32 chars,
    alphanumeric + dash/underscore). Surfaced as a per-field
    validator on :class:`S3ConnectionFormVM`."""
    if not _NAME_RE.match(form.name):
        return "1-32 chars, alphanumeric + dash/underscore only"
    return None


def _validate_endpoint_url(form: S3CompatForm) -> str | None:
    """``endpoint_url`` must be a syntactically valid
    ``http://`` or ``https://`` URL with a non-empty netloc.
    Field-presence is enforced by :class:`S3ConnectionFormVM`'s
    built-in non-empty validators; this validator runs only when the
    field is non-empty (regression: format wins over presence)."""
    stripped = form.endpoint_url.strip()
    if not stripped:
        return None  # field-presence validator already flagged it
    try:
        parsed = urlparse(stripped)
    except ValueError:
        return "not a valid URL"
    if parsed.scheme not in ("http", "https"):
        return "must start with http:// or https://"
    if not parsed.netloc:
        return "missing host"
    try:
        _ = parsed.port
    except ValueError:
        return "invalid port"
    if parsed.username or parsed.password:
        return "must not include username or password"
    if parsed.query or parsed.fragment:
        return "must not include query or fragment"
    return None


async def _noop_persister(_m: S3CompatForm) -> None:
    """The widget's S3ConnectionFormVM persister is a no-op — the
    actual persistence (config-store write + hub publish) is the
    parent panel's job. We only use the VM for its validation +
    can_submit surface."""
    pass


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

    Validation: composes :class:`S3ConnectionFormVM` internally
    (round-3 directive §9.bis.11). The form VM is NOT exposed
    publicly; consumers see the same ``ConnectionFormSubmitted`` /
    ``ConnectionFormCancelled`` message surface as before. Each
    ``Input.Changed`` calls ``form_vm.set_field``; the save button is
    gated on ``form_vm.can_submit``.
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
        # Form VM: composes VMx FormVM over S3CompatForm. A
        # blank model on construction; reset to actual values via
        # ``set_model`` when the form opens for add/edit. The form
        # is recreated each open so the snapshot/dirty state is
        # fresh — under round-3 the VM owns dirty tracking, and
        # form re-opens are conceptually new edit sessions.
        self._form_vm: S3ConnectionFormVM = self._build_form_vm()
        self._errors_sub: DisposableBase | None = None
        # Re-entrancy guard for _submit — see _submit docstring.
        self._submitting: bool = False

    @staticmethod
    def _build_form_vm() -> S3ConnectionFormVM:
        return S3ConnectionFormVM(
            initial=S3CompatForm(
                name="",
                endpoint_url="",
                region="",
                access_key_id="",
                secret_access_key="",
                force_path_style=True,
                verify_tls=True,
            ),
            persister=_noop_persister,
            strict=False,
        )

    def _install_view_validators(self) -> None:
        """Layer the View-specific format validators (name-regex
        and URL-format) on top of the VM's built-in field-presence
        + cross-field validators. Uses the form VM's public
        ``add_field_validator`` — no reach-through into the
        inner VMx :class:`FormVM` (round-3 directive
        §9.bis.11: composed primitives stay private)."""
        self._form_vm.add_field_validator("name", _validate_name)
        self._form_vm.add_field_validator("endpoint_url", _validate_endpoint_url)

    def compose(self) -> ComposeResult:
        with Container():
            # ``markup=False`` — open_for_edit() later calls update()
            # with ``f"Edit {name!r}"``. The form regex (_NAME_RE) blocks
            # brackets at SUBMISSION, but ConfigStore loads the existing
            # config.toml without re-applying that regex, so a
            # hand-edited or migrated file can round-trip ``minio[v2]``
            # back into the form. The bracketed name would crash the
            # Rich markup parser when the Edit-mode title updates.
            yield Static(
                "New s3-compatible connection",
                classes="form-title",
                id="form-title",
                markup=False,
            )
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

    def on_mount(self) -> None:
        self._install_view_validators()
        self._errors_sub = self._form_vm.on_errors_changed.subscribe(
            on_next=self._on_errors_changed
        )

    def on_unmount(self) -> None:
        if self._errors_sub is not None:
            self._errors_sub.dispose()
            self._errors_sub = None
        self._form_vm.dispose()

    # ── Public API ─────────────────────────────────────────────────────────

    def open_for_add(self) -> None:
        """Show the form in Add mode (all fields empty, name unlocked)."""
        self._ctx = _OpenContext(mode="add", original_name=None)
        self._submitting = False
        self.query_one("#form-title", Static).update("New s3-compatible connection")
        # Reset the form VM to a blank model. Use set_model so the
        # snapshot also resets; that way ``is_dirty`` is False on
        # the empty form.
        self._reset_form_vm(
            S3CompatForm(
                name="",
                endpoint_url="",
                region="",
                access_key_id="",
                secret_access_key="",
                force_path_style=True,
                verify_tls=True,
            )
        )
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
        self._submitting = False
        self.query_one("#form-title", Static).update(f"Edit {name!r}")
        self._reset_form_vm(defaults)
        for key, _, _, _ in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            default_val = getattr(defaults, key, "")
            inp.value = str(default_val) if default_val is not None else ""
            inp.disabled = key == "name"
            inp.remove_class("-invalid")
        self._refresh_save_button()
        # Apply error markers from any initial validation pass.
        self._apply_error_classes()
        self.add_class("-open")
        self.query_one("#form-endpoint_url", Input).focus()

    def close(self) -> None:
        """Hide the form and clear state."""
        self.remove_class("-open")
        self._submitting = False
        self._ctx = None

    def _reset_form_vm(self, model: S3CompatForm) -> None:
        """Dispose the prior form VM and build a fresh one with
        ``model`` as both the working and snapshot values, then
        re-install validators + the errors subscription."""
        if self._errors_sub is not None:
            self._errors_sub.dispose()
            self._errors_sub = None
        self._form_vm.dispose()
        self._form_vm = S3ConnectionFormVM(
            initial=model,
            persister=_noop_persister,
            strict=False,
        )
        self._install_view_validators()
        self._errors_sub = self._form_vm.on_errors_changed.subscribe(
            on_next=self._on_errors_changed
        )

    # ── Event handlers ─────────────────────────────────────────────────────

    @on(Input.Changed)
    def _on_input_changed(self, event: Input.Changed) -> None:
        field = event.input.id.removeprefix("form-") if event.input.id else ""
        if field not in {
            "name",
            "endpoint_url",
            "region",
            "access_key_id",
            "secret_access_key",
            "session_token",
        }:
            return
        # Drive the form VM — re-runs all validators and updates
        # ``errors`` / ``can_submit`` reactively.
        self._form_vm.set_field(field, event.value)
        # Per-field error class still applied directly because the
        # focus event for THIS input may have already fired
        # by the time the errors subscription runs; setting the
        # class here keeps the per-keystroke feedback tight.
        err = self._form_vm.errors.get(field)
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

    def _on_errors_changed(self, errors: dict[str, str]) -> None:
        """Subscription callback: paint ``-invalid`` markers on the
        affected inputs whenever the form VM's errors map flips."""
        self._apply_error_classes()
        self._refresh_save_button()

    def _apply_error_classes(self) -> None:
        errors = self._form_vm.errors
        for key, _, _, _ in _FIELDS:
            try:
                inp = self.query_one(f"#form-{key}", Input)
            except Exception:
                continue
            if key in errors:
                inp.add_class("-invalid")
            else:
                inp.remove_class("-invalid")

    def _refresh_save_button(self) -> None:
        save_btn: ModalButton | None = None
        for btn in self.query(ModalButton):
            if btn.button_id == "form-save-btn":
                save_btn = btn
                break
        if save_btn is None:
            return
        # The VM's ``can_submit`` reflects "all validators pass" —
        # under non-strict mode (this widget's choice) the form does
        # NOT also require is_dirty. Pristine-but-valid stays
        # enabled, preserving the prior "save always available when
        # all fields valid" semantics the form had pre-round-3.
        save_btn.disabled = self._form_vm.has_errors

    def mark_name_invalid(self) -> None:
        """Add ``-invalid`` to the name Input so the user sees the error.

        Callers should also surface the specific error message (e.g. via
        a toast) — this method only paints the field; it does not show
        the reason.

        Also clears the ``_submitting`` re-entrancy flag so the user
        can correct the name and re-submit. The parent panel signals
        a persistence failure by calling this method instead of
        :meth:`close`, so this is the single seam where the form
        learns "your last submit failed; you can try again".
        """
        self.query_one("#form-name", Input).add_class("-invalid")
        self._submitting = False

    def clear_submitting(self) -> None:
        """Clear the ``_submitting`` re-entrancy flag.

        For failure paths that don't go through :meth:`mark_name_invalid`
        — generic persistence errors (disk full, permission denied)
        where the name itself is fine but the save couldn't complete.
        The parent panel calls this before surfacing the toast so the
        user can edit field values and retry.
        """
        self._submitting = False

    def _submit(self) -> None:
        """Validate fields and post :class:`ConnectionFormSubmitted`.

        The form does **not** close itself here — the parent panel is
        responsible for calling :meth:`close` only when the persistence step
        succeeds.  On a duplicate-name or other persistence error the parent
        keeps the form open and surfaces an appropriate error message.

        Under round-3 the gate is ``self._form_vm.has_errors`` rather
        than a re-run of a private validator function. The Input
        values are pulled into the form VM via ``set_field`` on every
        keystroke, so the VM's ``model`` is always live with the
        latest values; the message carries that model directly.

        Re-entrancy guard: a second click (or Enter / Space repeat)
        between this method posting the message and the panel's
        async handler calling :meth:`close` would queue a SECOND
        ConnectionFormSubmitted. The panel processes them in order,
        so M1 successfully writes the config + closes + refreshes,
        then M2 lands on a now-closed form, the duplicate check
        fires, and the user sees a spurious "already exists" error
        toast for the entry they just successfully created. The
        ``_submitting`` flag short-circuits the second post; the
        panel clears it via :meth:`close` and :meth:`open_for_add`
        / :meth:`open_for_edit` so the next form interaction starts
        fresh.
        """
        if self._ctx is None:
            return
        if self._form_vm.has_errors:
            # Defense in depth — the save button should already be
            # disabled, but the form VM's errors map is the canonical
            # gate so we re-check here.
            return
        if self._submitting:
            return
        self._submitting = True
        # Pull the live model from the form VM. set_field has been
        # threading every keystroke into the working model, so it's
        # current — just hand it off. force_path_style=True and
        # verify_tls=True from the initial construction survive
        # untouched because no Input writes to those fields.
        model = self._form_vm.model
        ctx = self._ctx
        self.post_message(
            ConnectionFormSubmitted(form=model, mode=ctx.mode, original_name=ctx.original_name)
        )


# Keep the legacy helper exported so tests importing it still work;
# implementation now delegates to the form VM's validators when run
# through the widget. Tests that import the function directly get
# the standalone regex/URL behavior unchanged.
def _validate_s3_form_value(field: str, value: str) -> str | None:
    """Standalone validator preserved for tests that import it.

    The widget no longer uses this directly — it composes
    :class:`S3ConnectionFormVM` and adds the same validators via
    ``add_field_validator``. Tests that called this function
    against arbitrary (field, value) pairs continue to work
    unchanged.
    """
    stripped = value.strip()
    if field == "name":
        return None if _NAME_RE.match(value) else "1-32 chars, alphanumeric + dash/underscore only"
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
        try:
            _ = parsed.port
        except ValueError:
            return "invalid port"
        if parsed.username or parsed.password:
            return "must not include username or password"
        if parsed.query or parsed.fragment:
            return "must not include query or fragment"
        return None
    if field == "session_token":
        return None
    if not stripped:
        return "required"
    return None


__all__ = [
    "ConnectionFormCancelled",
    "ConnectionFormInline",
    "ConnectionFormSubmitted",
]
