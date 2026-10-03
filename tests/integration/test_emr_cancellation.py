"""Real cancellation entry points and lifecycle with synthetic provider reads."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from textual.events import Key
from textual.widgets import Input, Static

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.domain.filesystem import (
    NotFoundError,
    PermissionDeniedError,
    ProviderUnreachableError,
    ThrottledError,
)
from aws_tui.infra.aws_session import TokenState
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.services.s3 import S3Service
from aws_tui.ui.widgets.command_palette import CommandPalette
from aws_tui.ui.widgets.confirm_modal import ConfirmModal, TextualDialogService
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.hint_legend import HintLegend
from aws_tui.ui.widgets.settings.connection_form import ConnectionFormInline
from aws_tui.ui.widgets.toast import Toast
from tests.helpers import drain_workers, focus_and_settle, wait_until


class RecordingEmr(InMemoryEmr):
    """Acknowledgement deliberately does not alter the backend's read records."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.cleaned = asyncio.Event()
        self.read_release = asyncio.Event()
        self.read_release.set()
        self.read_entered = asyncio.Event()
        self.failure: Exception | None = None

    @property
    def cancel_calls(self) -> list[tuple[object, ...]]:
        return [args for method, args in self.calls if method == "cancel_job_run"]

    async def cancel_job_run(self, application_id: str, job_run_id: str) -> None:
        self.calls.append(("cancel_job_run", (application_id, job_run_id)))
        self.entered.set()
        try:
            await self.release.wait()
            if self.failure:
                raise self.failure
        finally:
            self.cleaned.set()

    async def get_job_run(self, application_id: str, job_run_id: str):  # type: ignore[no-untyped-def]
        self.read_entered.set()
        await self.read_release.wait()
        return await super().get_job_run(application_id, job_run_id)

    def transition(self, state: JobRunState) -> None:
        detail = self._details[("a1", "r1")]
        self._details[("a1", "r1")] = replace(
            detail, state=state, updated_at=detail.updated_at + timedelta(seconds=1)
        )
        summary = self._runs["a1"]["r1"]
        self._runs["a1"]["r1"] = replace(
            summary, state=state, updated_at=summary.updated_at + timedelta(seconds=1)
        )


@asynccontextmanager
async def cancellation_app(tmp_path: Path, *, overlay: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text(
        '[connections.active]\nkind="aws"\nprofile="dev"\nregion="us-east-1"\n'
        '[connections.other]\nkind="aws"\nprofile="other"\nregion="us-west-2"\n'
        '[defaults]\nconnection="active"\n'
        + (f'[keybindings]\n"emr.cancel"="{overlay["emr.cancel"]}"\n' if overlay else "")
    )
    ctx = build_app_context(config_dir=config, cache_dir=tmp_path / "cache")
    fake = RecordingEmr()
    other = RecordingEmr()
    clients = {"active": fake, "other": other}
    for app_id in ("a1", "a2"):
        fake.add_application(app_id=app_id, name=app_id)
        for run_id in ("r1", "r2"):
            fake.add_job_run(application_id=app_id, job_run_id=run_id, state=JobRunState.RUNNING)
            fake.add_job_run_detail(application_id=app_id, job_run_id=run_id)
        other.add_application(app_id=app_id, name=app_id)
        for run_id in ("r1", "r2"):
            other.add_job_run(application_id=app_id, job_run_id=run_id, state=JobRunState.RUNNING)
            other.add_job_run_detail(application_id=app_id, job_run_id=run_id)
    fs = InMemoryFS()
    for svc in ctx.root_vm._registry.all():
        if isinstance(svc, EmrServerlessService):
            svc._client_factory = lambda conn: clients[conn.name]
            svc._logs_client_factory = lambda conn: clients[conn.name].make_logs_client()
        if isinstance(svc, S3Service):
            svc._s3_fs_factory = lambda _conn: fs
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(180, 48)) as pilot:
        await drain_workers(app)
        ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
        await drain_workers(app)
        await wait_until(lambda: bool(app.query(EmrServerlessPage)), what="EMR mounted")
        page = app.query_one(EmrServerlessPage)
        setup = ctx.root_vm.content_host._setup_task
        if setup is not None:
            await setup
        await page.vm.select_application("a1")
        await page.vm.select_job_run("r1")
        await focus_and_settle(page.left_pane)
        await pilot.pause()
        yield app, pilot, page, fake
        assert other.cancel_calls == [], "replacement source never receives an old request"


def toast_text(app: AwsTuiApp) -> str:
    return "\n".join(t.model.text for t in app.app_ctx.root_vm.chrome.toast_stack.toasts)


def cancel_hint(app: AwsTuiApp):  # type: ignore[no-untyped-def]
    return next(
        a for a in app.app_ctx.root_vm.chrome.hint_legend.actions if a.action_id == "emr.cancel"
    )


def palette_ids(app: AwsTuiApp) -> set[str]:
    return {e.id for e in app.app_ctx.command_palette_vm.filtered_entries}


async def open_confirmation(app, pilot, key="x"):  # type: ignore[no-untyped-def]
    await pilot.press(key)
    await wait_until(
        lambda: isinstance(app.screen, ConfirmModal), what="EMR cancel confirmation", timeout=2
    )
    assert app.screen.request.title == "Cancel EMR job run?"


@pytest.mark.asyncio
async def test_danger_dismiss_accept_repeat_and_controlled_polls(tmp_path: Path) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        await open_confirmation(app, pilot)
        paths = {p.label: p.path for p in app.screen.request.paths}
        assert paths["Application"] == "a1"
        assert paths["Run"] == "r1"
        assert "active" in paths["Source"]
        assert "dev" in paths["Source"]
        assert "us-east-1" in paths["Source"]
        assert app.screen.request.danger
        assert app.screen.request.confirm_label == "Request cancellation"
        assert app.screen.request.cancel_label == "Keep running"
        assert app.screen._focused_button_id == "cancel"
        await pilot.press("escape")
        await drain_workers(app)
        assert fake.cancel_calls == []
        # Queue three real keys before workers get their turn.
        for _ in range(3):
            app.post_message(Key("x", "x"))
        await wait_until(
            lambda: isinstance(app.screen, ConfirmModal), what="queued keys confirmation"
        )
        fake.release.clear()
        await pilot.press("right", "enter")
        await fake.entered.wait()
        await pilot.press("x", "x")
        assert fake.cancel_calls == [("a1", "r1")]
        assert page.vm.cancel_busy
        assert not fake.cleaned.is_set(), "repeated keys did not replace the live client call"
        assert len([s for s in app.screen_stack if isinstance(s, ConfirmModal)]) <= 1
        assert not cancel_hint(app).enabled
        fake.release.set()
        await drain_workers(app)
        assert "cancellation requested" in toast_text(app)
        await pilot.pause()
        assert any("cancellation requested" in str(t.render()) for t in app.query(Toast))
        assert page.vm.job_run_detail.detail.state is JobRunState.RUNNING
        assert (
            next(r for r in page.vm.job_runs.runs if r.job_run_id == "r1").state
            is JobRunState.RUNNING
        )
        # The existing poller cannot expose a new state before its read completes.
        for state in (JobRunState.CANCELLING, JobRunState.CANCELLED):
            fake.transition(state)
            fake.read_entered.clear()
            fake.read_release.clear()
            page._tick_detail()
            await fake.read_entered.wait()
            assert page.vm.job_run_detail.detail.state is not state
            fake.read_release.set()
            await drain_workers(app)
            await pilot.pause()
            assert page.vm.job_run_detail.detail.state is state
            rendered = "\n".join(str(w.render()) for w in page.right_detail.query(Static))
            assert state.value in rendered
            assert not cancel_hint(app).enabled
            assert "emr.cancel" not in palette_ids(app)
        reads = len([c for c in fake.calls if c[0] == "get_job_run"])
        page._tick_detail()
        await drain_workers(app)
        assert len([c for c in fake.calls if c[0] == "get_job_run"]) == reads
        assert fake.cancel_calls == [("a1", "r1")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED, JobRunState.CANCELLING, None],
)
async def test_ineligible_reason_has_no_callable_offer(
    tmp_path: Path, state: JobRunState | None
) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        if state is None:
            fake._runs["a1"].clear()
            await page.vm.refresh_job_runs()
        else:
            fake.transition(state)
            await page.vm.refresh_job_run_detail()
        app._populate_command_palette()
        app.app_ctx.command_palette_vm.set_active_service("emr-serverless")
        assert not cancel_hint(app).enabled
        assert "emr.cancel" not in palette_ids(app)
        await pilot.press("x")
        await drain_workers(app)
        assert fake.cancel_calls == []
        assert not isinstance(app.screen, ConfirmModal)
        assert (state.value if state else "select an EMR job run") in toast_text(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["dispose", "source"])
async def test_owner_teardown_during_confirmation_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        original_mount = ConfirmModal.on_mount
        original_shutdown = page.vm.shutdown
        shutdown_entered = asyncio.Event()
        mounted = asyncio.Event()
        owned_tasks: list[asyncio.Task] = []
        replacements: list[asyncio.Task] = []

        async def shutdown_at_mount() -> None:
            shutdown_entered.set()
            await original_shutdown()

        async def teardown_on_mount(modal: ConfirmModal) -> None:
            original_mount(modal)
            assert modal is app.screen
            assert not modal._mounted_event.is_set()
            assert app.app_ctx.confirm_vm.is_open
            assert page.vm.cancel_busy
            owned_tasks.extend(page.vm._operations.tasks)
            assert len(owned_tasks) == 1
            # The real operation is suspended in ask -> present -> AwaitMount,
            # before wait_result can begin. No fake dialog or mount is used.
            presentation = owned_tasks[0].get_coro().cr_await.cr_await
            assert presentation.cr_code is TextualDialogService.present.__code__
            assert presentation.cr_await is not None
            if change == "dispose":
                page.vm.dispose()
            else:
                replacements.append(
                    asyncio.create_task(
                        app.app_ctx.root_vm.switch_connection_and_service(
                            app.app_ctx.connection_resolver.resolve("other"),
                            TokenState.CONNECTED,
                            "emr-serverless",
                        )
                    )
                )
                await shutdown_entered.wait()
            assert owned_tasks[0].cancelling()
            mounted.set()

        monkeypatch.setattr(page.vm, "shutdown", shutdown_at_mount)
        monkeypatch.setattr(ConfirmModal, "on_mount", teardown_on_mount)
        await pilot.press("x")
        await asyncio.wait_for(mounted.wait(), timeout=15)
        for replacement in replacements:
            await replacement
        await drain_workers(app)
        await pilot.pause()
        assert all(task.cancelled() for task in owned_tasks)
        assert page.vm._operations.tasks == set()
        assert not page.vm.cancel_busy
        assert not app.app_ctx.confirm_vm.is_open
        assert fake.cancel_calls == []
        assert not any(isinstance(screen, ConfirmModal) for screen in app.screen_stack)
        assert app.screen.focused is not None
        assert app.screen.focused.screen is app.screen
        # Real keyboard routing remains usable without dismissing the old modal.
        await pilot.press("ctrl+k")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["run", "application", "source", "unmount"])
@pytest.mark.parametrize("during", ["confirmation", "request", "error"])
async def test_owner_changes_never_redirect_or_toast(
    tmp_path: Path, change: str, during: str
) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        await open_confirmation(app, pilot)
        requested = during != "confirmation"
        if during == "error":
            fake.failure = PermissionDeniedError("RAW_SECRET_RESPONSE")
        if requested:
            fake.release.clear()
            await pilot.press("right", "enter")
            await fake.entered.wait()
        if change == "run":
            await page.vm.select_job_run("r2")
        elif change == "application":
            await page.vm.select_application("a2")
        elif change == "source":
            await app.app_ctx.root_vm.switch_connection_and_service(
                app.app_ctx.connection_resolver.resolve("other"),
                TokenState.CONNECTED,
                "emr-serverless",
            )
            setup = app.app_ctx.root_vm.content_host._setup_task
            if setup is not None:
                await setup
            replacement = app.app_ctx.root_vm.content_host.current
            assert replacement.source.connection_key == ("other", "us-west-2")
            assert replacement.job_runs.selected_id == "r1"
        else:
            await page.remove()
        if change in {"source", "unmount"}:
            if requested:
                await wait_until(fake.cleaned.is_set, what="owned client cancellation cleanup")
            await drain_workers(app)
            assert not isinstance(app.screen, ConfirmModal)
        if during == "confirmation" and isinstance(app.screen, ConfirmModal):
            await pilot.press("right", "enter")
        fake.release.set()
        await drain_workers(app)
        assert fake.cancel_calls == ([("a1", "r1")] if requested else [])
        assert "cancellation requested" not in toast_text(app)
        assert not isinstance(app.screen, ConfirmModal)
        assert not page.vm.cancel_busy
        assert "permission denied" not in toast_text(app)
        assert "RAW_SECRET_RESPONSE" not in toast_text(app)
        if requested:
            assert fake.cleaned.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            PermissionDeniedError,
            "CancelJobRun permission denied; check emr-serverless:CancelJobRun permission",
        ),
        (NotFoundError, "application or job run was not found; refresh the selected job run"),
        (
            ThrottledError,
            "EMR Serverless throttled the cancellation request; wait before a deliberate retry",
        ),
        (
            ProviderUnreachableError,
            "cancellation outcome is unconfirmed because of a network failure; refresh job state before a deliberate retry",
        ),
    ],
)
async def test_category_feedback_is_fixed_and_single_request(
    tmp_path: Path, error: type[Exception], message: str
) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        fake.failure = error("RAW_SECRET_RESPONSE")
        await open_confirmation(app, pilot)
        await pilot.press("right", "enter")
        await drain_workers(app)
        assert message in toast_text(app)
        assert "RAW_SECRET_RESPONSE" not in toast_text(app)
        assert fake.cancel_calls == [("a1", "r1")]
        assert fake.cleaned.is_set()
        assert not page.vm.cancel_busy
        assert page.vm.job_run_detail.detail.state is JobRunState.RUNNING


@pytest.mark.asyncio
async def test_palette_dismisses_before_confirm_and_updates_while_open(tmp_path: Path) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        await pilot.press("ctrl+k")
        assert isinstance(app.screen, CommandPalette)
        assert "emr.cancel" in palette_ids(app)
        field = app.screen.query_one(Input)
        field.value = "Cancel selected EMR job run"
        await pilot.pause()
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="palette cancellation")
        assert not any(isinstance(s, CommandPalette) for s in app.screen_stack)
        await pilot.press("escape")
        await drain_workers(app)
        await pilot.press("ctrl+k")
        fake.transition(JobRunState.CANCELLED)
        await page.vm.refresh_job_run_detail()
        await pilot.pause()
        assert "emr.cancel" not in palette_ids(app)
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape")
        assert fake.cancel_calls == []


@pytest.mark.asyncio
async def test_hint_click_routes_same_command(tmp_path: Path) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, _page, fake):
        legend = app.query_one(HintLegend)
        chip = next(w for w in legend.query(".hint-chip") if w.action.action_id == "emr.cancel")
        assert chip.action.enabled
        await pilot.click(chip)
        await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="hint cancellation")
        await pilot.press("right", "enter")
        await drain_workers(app)
        assert fake.cancel_calls == [("a1", "r1")]


@pytest.mark.asyncio
async def test_remap_and_modal_input_preserve_printable_keys(tmp_path: Path) -> None:
    async with cancellation_app(tmp_path, overlay={"emr.cancel": "z"}) as (app, pilot, _page, fake):
        await pilot.press("x")
        await drain_workers(app)
        assert not isinstance(app.screen, ConfirmModal)
        assert cancel_hint(app).key_label == "z"
        await pilot.press("ctrl+k")
        field = app.screen.query_one(Input)
        await pilot.press("x", "z")
        assert field.value == "xz"
        assert fake.cancel_calls == []
        await pilot.press("escape")
        await open_confirmation(app, pilot, "z")
        await pilot.press("escape")
        await drain_workers(app)
        app.app_ctx.root_vm.services_menu.switch_service_command.execute("settings")
        await drain_workers(app)
        await pilot.press("x", "z")
        assert not isinstance(app.screen, ConfirmModal)
        assert fake.cancel_calls == []
        form = app.query_one(ConnectionFormInline)
        form.open_for_add()
        await focus_and_settle(app.query_one("#form-name", Input))
        await pilot.press("x", "z")
        assert app.query_one("#form-name", Input).value == "xz"
        assert fake.cancel_calls == []
        await pilot.press("escape")
        app.app_ctx.root_vm.services_menu.switch_service_command.execute("s3")
        await drain_workers(app)
        await pilot.press("x", "z")
        assert not isinstance(app.screen, ConfirmModal)
        assert fake.cancel_calls == []


@pytest.mark.asyncio
async def test_busy_transition_removes_entry_from_open_palette(tmp_path: Path) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, page, fake):
        await pilot.press("ctrl+k")
        assert isinstance(app.screen, CommandPalette)
        assert "emr.cancel" in palette_ids(app)
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_confirmation(_request):  # type: ignore[no-untyped-def]
            entered.set()
            await release.wait()
            return False

        task = asyncio.create_task(page.vm.cancel_selected_run(held_confirmation))
        try:
            await entered.wait()
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            assert "emr.cancel" not in palette_ids(app)
            assert not cancel_hint(app).enabled
        finally:
            release.set()
            await task
        assert "emr.cancel" in palette_ids(app)
        assert fake.cancel_calls == []
        await pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.parametrize("adoption", ["recovery", "initial_mount", "navigation_mount"])
@pytest.mark.parametrize(
    ("prior_state", "prepared_state"),
    [
        (JobRunState.CANCELLED, JobRunState.RUNNING),
        (JobRunState.RUNNING, JobRunState.CANCELLED),
    ],
)
async def test_prepared_emr_adoption_refreshes_current_owner_availability(
    tmp_path: Path,
    adoption: str,
    prior_state: JobRunState,
    prepared_state: JobRunState,
) -> None:
    async with cancellation_app(tmp_path) as (app, pilot, prior_page, fake):
        fake.transition(prior_state)
        await prior_page.vm.refresh_job_run_detail()
        prior_eligible = prior_state is JobRunState.RUNNING
        assert prior_page.vm.can_cancel_selected_run() is prior_eligible
        assert cancel_hint(app).enabled is prior_eligible
        # Populate the real palette before preparing the replacement, so its
        # idempotent population cannot conceal a missing adoption refresh.
        await pilot.press("ctrl+k")
        assert isinstance(app.screen, CommandPalette)
        assert ("emr.cancel" in palette_ids(app)) is prior_eligible
        await pilot.press("escape")
        assert app._command_palette_populated

        fake.transition(prepared_state)
        connection = prior_page.vm.connection
        recovery = await app._stage_service_credential_recovery(
            resolved=connection,
            service_id="emr-serverless",
            hosted=prior_page.vm,
        )
        assert recovery is not None
        prepared = recovery.vm
        eligible = prepared_state is JobRunState.RUNNING
        assert prepared.can_cancel_selected_run() is eligible
        assert app.app_ctx.root_vm.content_host.current is prior_page.vm
        assert cancel_hint(app).enabled is prior_eligible
        read_calls = list(fake.calls)

        if adoption == "recovery":
            await app._commit_staged_service_recovery(
                recovery, connection=connection, service_id="emr-serverless"
            )
        else:
            await app.app_ctx.root_vm.adopt_prepared_service_vm(
                connection, TokenState.CONNECTED, "emr-serverless", prepared
            )
            recovery.commit_selection()
            if adoption == "initial_mount":
                assert await app._mount_initial_service_view()
            else:
                assert await app._mount_service_view("emr-serverless")
        await drain_workers(app)
        await pilot.pause()

        current_page = app.query_one(EmrServerlessPage)
        assert current_page.vm is prepared
        assert app.app_ctx.root_vm.content_host.current is prepared
        assert prepared.source.connection_key == ("active", "us-east-1")
        assert prepared.job_runs.selected_id == "r1"
        assert (
            prepared.can_cancel_selected_run(),
            cancel_hint(app).enabled,
            "emr.cancel" in palette_ids(app),
        ) == (eligible, eligible, eligible)
        # Adoption of an already-read VM needs no subsequent provider read or
        # property notification to project the mounted owner's availability.
        assert list(fake.calls) == read_calls
        await pilot.press("ctrl+k")
        assert isinstance(app.screen, CommandPalette)
        assert ("emr.cancel" in palette_ids(app)) is eligible
        await pilot.press("escape")
        assert fake.cancel_calls == []
