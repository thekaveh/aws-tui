"""Review a captured EMR run before requesting a fresh dashboard link."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, ClassVar, Literal

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Input, Static, Switch

from aws_tui.ui.widgets._worker import DeferredWorkerMixin
from aws_tui.ui.widgets.modal_button import ModalButton

if TYPE_CHECKING:
    from aws_tui.infra.browser import BrowserPort
    from aws_tui.vm.emr_serverless.dashboard_vm import DashboardTarget
    from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM

_ATTEMPT_ERROR = "Attempt must be a whole number greater than or equal to 1, or blank for latest."
_STALE = "The reviewed run is no longer current. Review the selected run again."


class DashboardModal(DeferredWorkerMixin, ModalScreen[None]):
    """Keep reviewed identity fixed; the coordinator owns all link delivery."""

    DEFAULT_CSS: ClassVar[str] = """
    DashboardModal { align: center middle; }
    DashboardModal > Container {
        width: 80;
        max-width: 90%;
        height: 22;
        max-height: 90%;
        padding: 1 2;
    }
    DashboardModal .modal-title { height: 1; }
    DashboardModal VerticalScroll { height: 1fr; }
    DashboardModal #emr-dashboard-summary { height: auto; margin-bottom: 1; }
    DashboardModal .modal-field-label { height: auto; }
    DashboardModal Input { margin-bottom: 1; }
    DashboardModal .system-logs-row { height: auto; }
    DashboardModal .system-logs-row Static { width: 1fr; height: auto; }
    DashboardModal .system-logs-row Switch { width: auto; }
    DashboardModal .modal-footer { height: 3; }
    DashboardModal #emr-dashboard-status { height: auto; max-height: 3; }
    """

    BINDINGS = [("escape", "cancel", "Close")]  # noqa: RUF012

    def __init__(
        self,
        page: EmrServerlessPageVM,
        *,
        browser: BrowserPort,
        copy_link: Callable[[str], Awaitable[None]],
        mode: Literal["open", "copy"] = "open",
        on_closed_open: Callable[[DashboardTarget], None] | None = None,
    ) -> None:
        super().__init__()
        target = page.dashboard_target()
        assert target is not None
        self._page = page
        self._target = target
        self._browser = browser
        self._copy_link = copy_link
        self._mode = mode
        self._working = False
        self._invalidated = False
        self._dismissed_review = False
        self._on_closed_open = on_closed_open
        self._closed_open_reported = False

    @property
    def target(self) -> DashboardTarget:
        return self._target

    def compose(self) -> ComposeResult:
        target = self._target
        with Container():
            yield Static("EMR Serverless dashboard", classes="modal-title", markup=False)
            with VerticalScroll():
                yield Static(
                    f"Source: {target.source.label}\n"
                    f"Application: {target.application_name} ({target.application_id})\n"
                    f"Run: {target.job_run_id}\n"
                    f"Type: {target.application_type}\n\n"
                    "Links are valid for one hour. Open and Copy each request a fresh link.",
                    id="emr-dashboard-summary",
                    markup=False,
                )
                yield Static(
                    "Attempt (blank for latest)", classes="modal-field-label", markup=False
                )
                yield Input(placeholder="Latest", id="emr-dashboard-attempt")
                with Horizontal(classes="system-logs-row"):
                    yield Static("Access system profile logs", markup=False)
                    yield Switch(False, id="emr-dashboard-system-logs")
            yield Static("", id="emr-dashboard-status", markup=False)
            with Horizontal(classes="modal-footer"):
                for name, label in (("open", "Open"), ("copy", "Copy"), ("cancel", "Close")):
                    button = ModalButton(
                        label, button_id=name, classes="-primary" if name == self._mode else ""
                    )
                    button.id = f"emr-dashboard-{name}"
                    yield button

    def on_mount(self) -> None:
        if not self._page.dashboards.available(self._target):
            self.invalidate()
        self.query_one(f"#emr-dashboard-{self._mode}").focus()

    def _status(self, message: str) -> None:
        if self.is_attached and not self._dismissed_review:
            self.query_one("#emr-dashboard-status", Static).update(message)

    def _sync_controls(self) -> None:
        if self.is_attached and not self._dismissed_review:
            for name in ("open", "copy"):
                self.query_one(f"#emr-dashboard-{name}").disabled = (
                    self._working or self._invalidated
                )
            self.query_one("#emr-dashboard-attempt", Input).disabled = self._working
            self.query_one("#emr-dashboard-system-logs", Switch).disabled = self._working

    def invalidate(self) -> None:
        """Fence an old review when its page or selection loses ownership."""
        self._invalidated = True
        self._page.dashboards.cancel()
        self._sync_controls()
        self._status(_STALE)

    def action_open(self) -> None:
        self._start("open")

    def action_copy(self) -> None:
        self._start("copy")

    def _start(self, mode: Literal["open", "copy"]) -> None:
        if self._working or self._dismissed_review or self._invalidated:
            return
        if not self._page.dashboards.available(self._target):
            self.invalidate()
            return
        text = self.query_one("#emr-dashboard-attempt", Input).value.strip()
        try:
            attempt = int(text) if text else None
            if attempt is not None and (not text.isascii() or not text.isdecimal() or attempt < 1):
                raise ValueError
        except ValueError:
            self._status(_ATTEMPT_ERROR)
            return
        logs = self.query_one("#emr-dashboard-system-logs", Switch).value
        # Admission is synchronous so two queued button events cannot both
        # enqueue a worker before either has started.
        self._working = True
        self._sync_controls()
        self._status("Requesting a fresh dashboard link…")
        self._run_lifecycle_worker(
            lambda: self._perform(mode, attempt, logs),
            group="emr-dashboard-delivery",
            exclusive=False,
        )

    async def _perform(
        self, mode: Literal["open", "copy"], attempt: int | None, logs: bool
    ) -> None:
        message = ""
        opened = False
        try:
            if self._dismissed_review or self._invalidated:
                return
            outcome = await self._page.dashboards.perform(
                self._target,
                mode=mode,
                attempt=attempt,
                access_system_profile_logs=logs,
                browser=self._browser,
                copy_link=self._copy_link,
            )
            message = outcome.message
            opened = outcome.status == "opened"
        except Exception:
            # Unexpected failures may contain a signed link. Keep diagnostics
            # value-free even when an injected coordinator fails unexpectedly.
            message = "Dashboard delivery failed. Retry the reviewed request."
        finally:
            self._working = False
            if not self._page.dashboards.available(self._target) and not self._dismissed_review:
                self.invalidate()
            self._sync_controls()
        if opened and self._dismissed_review:
            if not self._closed_open_reported:
                self._closed_open_reported = True
                if self._on_closed_open is not None:
                    # Callback failures must not surface private values in
                    # Textual crash diagnostics after the review closes.
                    with suppress(Exception):
                        self._on_closed_open(self._target)
            return
        if not self._invalidated:
            self._status(message)
        elif opened:
            # An admitted OS request cannot be recalled. Keep its actual
            # outcome visible for the reviewed target alongside ownership loss.
            self._status(f"{message} {_STALE}")

    def action_cancel(self) -> None:
        self._dismissed_review = True
        self._page.dashboards.cancel()
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._dismissed_review = True
        self._working = False
        self._page.dashboards.cancel()

    def on_click(self, event: Click) -> None:
        node: object | None = event.widget
        while node is not None:
            if isinstance(node, ModalButton):
                event.stop()
                if node.disabled:
                    return
                if node.button_id == "open":
                    self.action_open()
                elif node.button_id == "copy":
                    self.action_copy()
                else:
                    self.action_cancel()
                return
            node = getattr(node, "parent", None)


__all__ = ["DashboardModal"]
