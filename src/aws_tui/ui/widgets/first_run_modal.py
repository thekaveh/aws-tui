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

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static
from vmx import Message, MessageHub

from aws_tui.vm.chrome.first_run_vm import FirstRunAction, FirstRunVM, S3CompatForm


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
                yield Button("add aws", id="first-run-aws-btn")
                yield Button("add s3-compatible", id="first-run-s3-btn")
                yield Button("skip", id="first-run-skip-btn")

    def action_add_aws(self) -> None:
        self._vm.add_aws_command.execute()
        self.dismiss(FirstRunAction.ADD_AWS)

    def action_add_s3_compat(self) -> None:
        self._vm.add_s3_compat_command.execute()
        self.dismiss(FirstRunAction.ADD_S3_COMPAT)

    def action_skip(self) -> None:
        self._vm.skip_command.execute()
        self.dismiss(FirstRunAction.SKIP)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "first-run-aws-btn":
            self.action_add_aws()
        elif event.button.id == "first-run-s3-btn":
            self.action_add_s3_compat()
        elif event.button.id == "first-run-skip-btn":
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
    ) -> None:
        super().__init__()
        self._hub: MessageHub[Message] = hub
        self._defaults: S3CompatForm | None = defaults

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
                    )
            with Horizontal(classes="modal-footer"):
                yield Button("cancel", id="form-cancel-btn")
                yield Button("save", id="form-save-btn")

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-cancel-btn":
            self.action_cancel()
        elif event.button.id == "form-save-btn":
            self.action_submit()


__all__ = ["FirstRunModal", "S3CompatFormModal"]
