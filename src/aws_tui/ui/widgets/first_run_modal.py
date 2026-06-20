"""FirstRunModal + S3CompatFormModal screens.

The first-run modal is the entrypoint for an aws-tui session with no
known connections (neither in ``config.toml`` nor in ``~/.aws/``). It
offers three buttons mapping to :class:`FirstRunAction`:

- ``add aws``        — composition root shells out to ``aws configure sso``
- ``add s3-compat``  — composition root pushes :class:`S3CompatFormModal`
- ``skip``           — proceed to main screen

:class:`S3CompatFormModal` is the in-TUI form that collects the fields
needed to write a new ``[connections.<name>]`` block of
``kind = "s3-compatible"``.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets.modal_button import ModalButton
from aws_tui.vm.chrome.first_run_vm import FirstRunAction, FirstRunVM, S3CompatForm

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _validate_s3_form_value(field: str, value: str) -> str | None:
    """Return None if the value is valid for the field, else an
    error message suitable for tooltip display.

    Validation rules per the design spec:
    - ``name``: matches ``^[A-Za-z0-9_-]{1,32}$``
    - ``endpoint_url``: starts with ``http://`` or ``https://``, has
      a non-empty netloc
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
    # region, access_key_id, secret_access_key — required, non-empty
    if not stripped:
        return "required"
    return None


class FirstRunModal(ModalScreen[FirstRunAction]):
    """The empty-config welcome modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "skip", "Skip"),
        ("a", "add_aws", "Add AWS"),
        ("s", "add_s3_compat", "Add S3-compat"),
        ("k", "skip", "Skip"),
    ]

    def __init__(
        self,
        vm: FirstRunVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: FirstRunVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> FirstRunVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("welcome to aws-tui", classes="modal-title")
            yield Static(
                "no AWS or S3-compatible connections configured.",
                classes="modal-body",
            )
            yield Static(
                "choose how to get started:",
                classes="modal-body",
            )
            yield Static(
                "  add aws profile  (runs 'aws configure sso' in your terminal)",
                classes="modal-body first-run-bullet",
            )
            yield Static(
                "  add s3-compatible (in-TUI form for MinIO, R2, etc.)",
                classes="modal-body first-run-bullet",
            )
            yield Static(
                "  skip for now (you can add later via : connection add)",
                classes="modal-body first-run-bullet",
            )
            with Horizontal(classes="modal-footer"):
                yield ModalButton("add aws", button_id="first-run-aws-btn", classes="-primary")
                yield ModalButton("add s3-compatible", button_id="first-run-s3-btn")
                yield ModalButton("skip", button_id="first-run-skip-btn")

    def action_add_aws(self) -> None:
        self._vm.add_aws_command.execute()
        self.dismiss(FirstRunAction.ADD_AWS)

    def action_add_s3_compat(self) -> None:
        self._vm.add_s3_compat_command.execute()
        self.dismiss(FirstRunAction.ADD_S3_COMPAT)

    def action_skip(self) -> None:
        self._vm.skip_command.execute()
        self.dismiss(FirstRunAction.SKIP)

    def on_click(self, event: Click) -> None:
        target = event.widget
        button_id = getattr(target, "button_id", None)
        if button_id == "first-run-aws-btn":
            self.action_add_aws()
        elif button_id == "first-run-s3-btn":
            self.action_add_s3_compat()
        elif button_id == "first-run-skip-btn":
            self.action_skip()


_FIELDS = (
    ("name", "Name", "minio-local", False),
    ("endpoint_url", "Endpoint URL", "http://localhost:9000", False),
    ("region", "Region", "us-east-1", False),
    ("access_key_id", "Access key ID", "", False),
    ("secret_access_key", "Secret access key", "", True),
)


class S3CompatFormModal(ModalScreen[S3CompatForm | None]):
    """In-TUI form for adding an S3-compatible connection."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "cancel", "Cancel"),
        ("enter", "submit", "Submit"),
    ]

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        defaults: S3CompatForm | None = None,
        name_locked: bool = False,
    ) -> None:
        super().__init__()
        self._hub: MessageHub[Message] = hub
        self._defaults: S3CompatForm | None = defaults
        self._name_locked: bool = name_locked

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("add s3-compatible connection", classes="modal-title")
            with Vertical(classes="form-fields"):
                for key, label, placeholder, secret in _FIELDS:
                    default = ""
                    if self._defaults is not None:
                        default = str(getattr(self._defaults, key, ""))
                    yield Static(label, classes="form-label")
                    yield Input(
                        value=default,
                        placeholder=placeholder,
                        password=secret,
                        id=f"form-{key}",
                        disabled=(self._name_locked and key == "name"),
                    )
            with Horizontal(classes="modal-footer"):
                yield ModalButton("cancel", button_id="form-cancel-btn")
                yield ModalButton("save", button_id="form-save-btn", classes="-primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        values: dict[str, str] = {}
        for key, _label, _placeholder, _secret in _FIELDS:
            inp = self.query_one(f"#form-{key}", Input)
            values[key] = inp.value
        form = S3CompatForm(
            name=values["name"].strip(),
            endpoint_url=values["endpoint_url"].strip(),
            region=values["region"].strip(),
            access_key_id=values["access_key_id"],
            secret_access_key=values["secret_access_key"],
            force_path_style=True,
            verify_tls=True,
        )
        if not form.is_valid():
            return
        self.dismiss(form)

    def on_mount(self) -> None:
        # Initial sync: if defaults were passed, validation has already
        # run via compose; otherwise the empty form is invalid and the
        # save button must reflect that.
        self._refresh_save_button()

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

    def _refresh_save_button(self) -> None:
        """Disable the save button if any required field is invalid."""
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

    def on_click(self, event: Click) -> None:
        target = event.widget
        button_id = getattr(target, "button_id", None)
        if button_id == "form-cancel-btn":
            self.action_cancel()
        elif button_id == "form-save-btn":
            self.action_submit()


__all__ = ["FirstRunModal", "S3CompatFormModal"]
