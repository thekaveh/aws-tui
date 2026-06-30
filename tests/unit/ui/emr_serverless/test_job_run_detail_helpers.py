"""Tests for ``JobRunDetailPane`` pure helpers + placeholder rendering.

Covers H3 (args/spark/multiline_kv) and H6 (placeholder text) of the
Pass-1 test-review gaps. The helpers fix a PR #80 user-reported bug
("Args were unreadable on a single line"); they are pure functions
with docstring examples so a refactor that silently changes the
output gets caught here even when the snapshot still passes.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import (
    JobRunDetailPane,
    _multiline_kv,
    _pair_args,
    _split_spark_params,
)
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState

# ── _pair_args ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        # Empty input → empty output.
        ([], []),
        # Single bare flag with no following value (end of list).
        (["--verbose"], ["--verbose"]),
        # --option value pair.
        (["--debug", "true"], ["--debug true"]),
        # --flag followed by --flag stays separate (no value).
        (["--verbose", "--debug"], ["--verbose", "--debug"]),
        # Mixed: flag, pair, pair.
        (
            ["--debug", "true", "--in", "s3://x", "--verbose", "--out", "s3://y"],
            ["--debug true", "--in s3://x", "--verbose", "--out s3://y"],
        ),
        # Positional (non-`--`) arg stays on its own line.
        (["positional"], ["positional"]),
        # Positional followed by --flag value still pairs the flag.
        (["pos", "--in", "x"], ["pos", "--in x"]),
    ],
)
def test_pair_args(args: list[str], expected: list[str]) -> None:
    assert _pair_args(args) == expected


# ── _split_spark_params ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Empty / None → empty list (so _multiline_kv falls back to "—").
        (None, []),
        ("", []),
        # Single --conf k=v pair.
        ("--conf spark.executor.memory=4g", ["--conf spark.executor.memory=4g"]),
        # Two --conf entries on one line.
        (
            "--conf spark.executor.instances=8 --conf spark.executor.memory=4g",
            [
                "--conf spark.executor.instances=8",
                "--conf spark.executor.memory=4g",
            ],
        ),
        # Mixed --conf and --other-option.
        (
            "--conf a=1 --class com.acme.Job",
            ["--conf a=1", "--class com.acme.Job"],
        ),
    ],
)
def test_split_spark_params(raw: str | None, expected: list[str]) -> None:
    assert _split_spark_params(raw) == expected


# ── _multiline_kv ─────────────────────────────────────────────────────────────


def test_multiline_kv_empty_collapses_to_single_em_dash_row() -> None:
    """When the value list is empty the helper falls back to a
    single ``key  —`` row so the detail pane stays visually balanced."""
    assert _multiline_kv("Args", []) == ["Args          —"]


def test_multiline_kv_single_value_emits_header_plus_one_indented_row() -> None:
    result = _multiline_kv("Args", ["--debug true"])
    assert len(result) == 2
    # Header is the bare 12-char-padded key.
    assert result[0] == "Args        "
    # Indent is 14 spaces (12-char key column + 2-space gap).
    assert result[1] == " " * 14 + "--debug true"


def test_multiline_kv_many_values_keeps_header_plus_one_row_each() -> None:
    values = ["--in s3://bucket/in", "--out s3://bucket/out", "--partitions 200"]
    result = _multiline_kv("Args", values)
    assert len(result) == 1 + len(values)
    assert result[0] == "Args        "
    indent = " " * 14
    for i, v in enumerate(values, start=1):
        assert result[i] == indent + v


def test_multiline_kv_long_key_still_pads_to_12() -> None:
    """The header is left-padded to 12 chars regardless of key length;
    the docstring example explicitly shows ``Args        `` (12 chars)."""
    result = _multiline_kv("X", ["v"])
    assert result[0] == "X" + " " * 11  # 1 + 11 = 12 total


# ── H6: placeholder rendering through the widget ──────────────────────────────


def _make_detail_vm() -> JobRunDetailVM:
    """A real ``JobRunDetailVM`` with a stub client. We only drive
    state setters; ``refresh()`` is never invoked here."""

    class _NoCallClient:
        async def get_job_run(self, application_id: str, job_run_id: str) -> object:
            raise AssertionError("placeholder tests must not hit the client")

    hub: MessageHub[Message] = MessageHub()
    vm = JobRunDetailVM(client=_NoCallClient(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


async def _render_pane_with_state(vm: JobRunDetailVM, state: PaneState) -> str:
    """Mount the pane in a temporary Textual app and return the
    rendered placeholder text. Bypasses the VM's set_target +
    refresh dance by writing the private ``_state`` directly — we're
    testing the widget's branch on ``vm.state``, not how the VM
    enters that state."""

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield JobRunDetailPane(vm, id="pane")

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Flip state AFTER mount + initial refresh, then ask the pane to redraw.
        vm._state = state  # type: ignore[attr-defined]
        pane = app.query_one(JobRunDetailPane)
        pane._refresh()  # type: ignore[attr-defined]
        await pilot.pause()
        statics = list(pane.query(Static))
        # The placeholder is the only child Static when state is non-IDLE.
        return " | ".join(str(s.render()) for s in statics)


async def test_no_run_selected_placeholder_rendered_when_detail_none() -> None:
    """Default state — no run selected — shows the ``(no run selected)``
    placeholder."""
    vm = _make_detail_vm()
    rendered = await _render_pane_with_state(vm, PaneState.IDLE)
    assert "(no run selected)" in rendered


async def test_unreachable_placeholder_uses_vm_error_text_when_present() -> None:
    """The UNREACHABLE branch prefers the VM's ``error_text`` over the
    default fallback string. Driven via ``map_provider_error`` in
    ``refresh()`` — here we set the state directly."""
    vm = _make_detail_vm()
    vm._error_text = "custom unreachable text"  # type: ignore[attr-defined]
    rendered = await _render_pane_with_state(vm, PaneState.UNREACHABLE)
    assert "custom unreachable text" in rendered


async def test_unreachable_placeholder_falls_back_when_error_text_missing() -> None:
    """If the VM has no ``error_text`` (defensive: shouldn't happen
    in practice), the pane falls back to the canned 'endpoint
    unreachable' string."""
    vm = _make_detail_vm()
    # error_text stays None.
    rendered = await _render_pane_with_state(vm, PaneState.UNREACHABLE)
    assert "endpoint unreachable" in rendered
    assert "press r to retry" in rendered


async def test_auth_required_placeholder_rendered() -> None:
    """``PaneState.AUTH_REQUIRED`` → ``authentication required — aws
    sso login`` placeholder so the user knows to re-auth."""
    vm = _make_detail_vm()
    rendered = await _render_pane_with_state(vm, PaneState.AUTH_REQUIRED)
    assert "authentication required" in rendered
    assert "aws sso login" in rendered


async def test_loading_placeholder_rendered() -> None:
    """The LOADING state shows a ``loading…`` placeholder while a
    refresh is in flight."""
    vm = _make_detail_vm()
    rendered = await _render_pane_with_state(vm, PaneState.LOADING)
    assert "loading" in rendered
