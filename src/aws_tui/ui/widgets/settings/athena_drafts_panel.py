from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Button, Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.athena.drafts_modal import ask_draft_confirmation
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.chrome.confirm_vm import ConfirmRequest


class AthenaDraftsPanel(DeferredWorkerMixin, Widget):
    DEFAULT_CSS: ClassVar[str] = (
        "AthenaDraftsPanel { height: auto; } AthenaDraftsPanel Static { height: auto; }"
    )

    def __init__(self, vm: AthenaDraftsVM, *, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub
        self._subscription: DisposableBase | None = None
        self._pending = False

    def compose(self) -> ComposeResult:
        yield Static(str(self._vm.directory), id="athena-drafts-path", markup=False)
        yield Static(
            "Keep the latest SQL for each query context on this device. "
            "Up to 50 drafts and 8 MiB total. SQL is stored as plaintext; "
            "results and credentials are not retained.",
            id="athena-drafts-retention",
            markup=False,
        )
        button = Button(
            "Disable and delete drafts" if self._vm.enabled else "Enable local SQL drafts",
            id="athena-drafts-toggle",
            flat=True,
        )
        button.disabled = self._vm.read_only or self._vm.busy
        yield button
        cleanup = Button("Retry draft cleanup", id="athena-drafts-cleanup", flat=True)
        cleanup.display = self._vm.cleanup_required
        yield cleanup
        yield Static("", id="athena-drafts-setting-status", markup=False)

    def on_mount(self) -> None:
        self._subscription = self._vm.on_property_changed.subscribe(self._on_vm_changed)
        self.call_after_refresh(self._refresh)

    def _on_vm_changed(self, _name: str) -> None:
        if self.is_mounted and self.is_attached and self.is_running:
            self.call_after_refresh(self._refresh)

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()

    def _refresh(self) -> None:
        if not self.is_mounted or not self.is_attached or not self.is_running:
            return
        button = self.query_one("#athena-drafts-toggle", Button)
        button.label = (
            "Disable and delete drafts" if self._vm.enabled else "Enable local SQL drafts"
        )
        button.disabled = self._vm.read_only or self._vm.busy or self._pending
        cleanup = self.query_one("#athena-drafts-cleanup", Button)
        cleanup.display = self._vm.cleanup_required
        cleanup.disabled = self._vm.busy or self._pending or self._vm.read_only
        status = "Unavailable in demo mode" if self._vm.read_only else self._vm.error_text
        self.query_one("#athena-drafts-setting-status", Static).update(status or "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if (
            event.button.id not in {"athena-drafts-toggle", "athena-drafts-cleanup"}
            or self._pending
            or self._vm.busy
        ):
            return
        event.stop()
        self._pending = True
        self._refresh()
        cleanup_only = event.button.id == "athena-drafts-cleanup"
        self._run_lifecycle_worker(
            lambda: self._toggle(cleanup_only), group="athena-drafts-setting"
        )

    async def _toggle(self, cleanup_only: bool) -> None:
        try:
            if cleanup_only:
                await self._vm.set_enabled(False)
                return
            enabled = self._vm.enabled
            if enabled and not await ask_draft_confirmation(
                self,
                drafts=self._vm,
                hub=self._hub,
                request=ConfirmRequest(
                    title="Disable and delete local drafts?",
                    body_lines=("All local Athena SQL draft records will be deleted.",),
                    confirm_label="Disable and delete",
                    danger=True,
                ),
            ):
                return
            await self._vm.set_enabled(not enabled)
        finally:
            self._pending = False
            if self.is_mounted:
                self._refresh()
