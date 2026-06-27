"""Snapshot harness for :class:`JobRunCloneModal`.

A vanilla Textual app that mounts a single pre-populated clone
modal so the per-theme snapshot pins the form layout + button
strip across all 10 themes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.containers import Container
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal
from aws_tui.vm.emr_serverless.clone_vm import JobRunCloneVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


_FIXED_TS = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)


def _detail() -> JobRunDetail:
    return JobRunDetail(
        application_id="00abc",
        job_run_id="r-001",
        name="nightly-2026-06-25",
        state=JobRunState.SUCCESS,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
        entry_point="s3://my-bucket/jobs/etl.py",
        entry_point_arguments=(
            "--input",
            "s3://my-bucket/raw/",
            "--output",
            "s3://my-bucket/curated/",
        ),
        spark_submit_parameters="--conf spark.executor.instances=8",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
        s3_monitoring_log_uri=None,
    )


class EmrCloneModalApp(App[None]):
    """Renders the EMR clone modal pre-populated from a detail
    fixture. Used by the per-theme snapshot tier so the form
    layout + button strip is pinned across all 10 themes."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self.CSS = _load_css(theme)
        self._theme = theme
        hub: MessageHub[Message] = MessageHub()
        self._hub = hub
        self._fake = _InMemoryEmr()
        self._fake.add_application(app_id="00abc", name="etl")
        self._vm = JobRunCloneVM(_detail(), client=self._fake, hub=hub, dispatcher=NULL_DISPATCHER)
        self._vm.construct()

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        await self.push_screen(JobRunCloneModal(self._vm, hub=self._hub))


__all__ = ["EmrCloneModalApp"]
