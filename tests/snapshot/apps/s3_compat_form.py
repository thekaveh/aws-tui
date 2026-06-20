"""Test apps for S3CompatFormModal add, edit, and validation snapshots."""

from __future__ import annotations

from typing import cast

from textual.app import App, ComposeResult
from textual.widgets import Input, Static
from vmx import MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.first_run_modal import S3CompatFormModal
from aws_tui.vm.chrome.first_run_vm import S3CompatForm


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


_FILLED = S3CompatForm(
    name="minio-local",
    endpoint_url="http://localhost:9000",
    region="us-east-1",
    access_key_id="AKIATEST",
    secret_access_key="SECRETTEST",
    force_path_style=True,
    verify_tls=True,
)

_INVALID = S3CompatForm(
    name="bad name",  # space → invalid
    endpoint_url="ftp://wrong",  # wrong scheme → invalid
    region="",  # empty → invalid
    access_key_id="",  # empty → invalid
    secret_access_key="",  # empty → invalid
)


class S3FormAddApp(App[None]):
    """S3CompatFormModal in add mode (empty, no defaults)."""

    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)")

    async def on_mount(self) -> None:
        await self.push_screen(S3CompatFormModal(hub=self._hub, defaults=None, name_locked=False))


class S3FormEditApp(App[None]):
    """S3CompatFormModal in edit mode (pre-filled, name locked)."""

    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)")

    async def on_mount(self) -> None:
        await self.push_screen(S3CompatFormModal(hub=self._hub, defaults=_FILLED, name_locked=True))


class S3FormValidationErrorsApp(App[None]):
    """S3CompatFormModal with all five fields in an invalid state."""

    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._hub = cast("MessageHub[Message]", MessageHub())

    def compose(self) -> ComposeResult:
        yield Static("aws-tui main view (behind modal)")

    async def on_mount(self) -> None:
        screen = S3CompatFormModal(hub=self._hub, defaults=_INVALID, name_locked=False)
        await self.push_screen(screen)
        # The -invalid class is only applied on Input.Changed events; post a
        # synthetic change for each field to drive the modal's own validation
        # handler so the snapshot reflects the full error state.
        for key in ("name", "endpoint_url", "region", "access_key_id", "secret_access_key"):
            inp = screen.query_one(f"#form-{key}", Input)
            inp.post_message(Input.Changed(inp, inp.value))


__all__ = ["S3FormAddApp", "S3FormEditApp", "S3FormValidationErrorsApp"]
