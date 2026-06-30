# src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py
"""JobRunDetailPane — RIGHT pane of the EMR page.

PR-A renders the static detail (state, timings, IAM, entry point,
args, Spark params). PR-B adds the log surface below the KV table
as a child widget; PR-A leaves the bottom empty so PR-B's layout
slot is reserved."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.domain.emr_serverless import JobRunDetail
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_TERMINAL_GLYPH: dict[str, str] = {
    "SUCCESS": "✓",
    "FAILED": "✗",
    "CANCELLED": "⊘",
    "CANCELLING": "⊘",
    "RUNNING": "●",
    "PENDING": "⏸",
}


class JobRunDetailPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = """
    JobRunDetailPane {
        height: 1fr;
        layout: vertical;
    }
    JobRunDetailPane > VerticalScroll {
        height: 1fr;
    }
    JobRunDetailPane .detail-row {
        height: auto;
        padding: 0 1;
    }
    JobRunDetailPane .detail-key {
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = []

    def __init__(
        self,
        vm: JobRunDetailVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunDetailVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="detail-body")

    def on_mount(self) -> None:
        self.border_title = "detail"
        self._refresh()
        # Round-3 / PR #103 retirement: subscribe to the VM's
        # per-instance Observable instead of filtering the shared
        # hub by sender_object.
        self._sub = self._vm.on_property_changed.subscribe(on_next=self._on_vm_property_changed)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_vm_property_changed(self, prop: str) -> None:
        """Round-3 directive: per-VM Observable subscription. The
        cross-VM `state` collisions PR #103 hub-filter was guarding
        against can't reach here because this Subject is scoped to
        JobRunDetailVM only."""
        if prop in {"detail", "state"}:
            self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            body = self.query_one("#detail-body", VerticalScroll)
        except Exception:
            return
        body.remove_children()
        state = self._vm.state
        d = self._vm.detail
        # All rows that render AWS-returned content are mounted
        # with ``markup=False`` so square brackets in user-supplied
        # job-run fields (entry_point, args, spark params, IAM ARN,
        # error/state-details text) are NOT parsed as Rich markup
        # tags. Pre-fix the user hit a crash when a failed job's
        # error text contained ``[ContainerError(ContainerGroupId=
        # cecf82cd-c71a-…)]`` — Rich's parser saw the leading ``[``,
        # tried to read the GUID as a tag value, and failed with
        # ``MarkupError: Expected markup value (found '-c71a-…')``
        # in the compositor reflow path, crashing the whole app.
        # The detail rows are plain text only; we never use markup
        # here, so disabling it is a safe defensive default.
        if state is PaneState.LOADING:
            body.mount(Static("loading…", classes="detail-placeholder", markup=False))
            return
        if state is PaneState.UNREACHABLE:
            body.mount(
                Static(
                    self._vm.error_text or "endpoint unreachable — press r to retry",
                    classes="detail-placeholder",
                    markup=False,
                )
            )
            return
        if state is PaneState.AUTH_REQUIRED:
            body.mount(
                Static(
                    "authentication required — aws sso login --profile <X>",
                    classes="detail-placeholder",
                    markup=False,
                )
            )
            return
        if d is None:
            body.mount(Static("(no run selected)", classes="detail-placeholder", markup=False))
            return
        body.mount(Static(_format_kv("State", _state_label(d)), classes="detail-row", markup=False))
        body.mount(
            Static(
                _format_kv("Started", d.created_at.strftime("%Y-%m-%d %H:%M:%S")),
                classes="detail-row",
                markup=False,
            )
        )
        body.mount(
            Static(
                _format_kv(
                    "Duration",
                    f"{d.duration_ms // 1000} s" if d.duration_ms is not None else "—",
                ),
                classes="detail-row",
                markup=False,
            )
        )
        body.mount(
            Static(
                _format_kv("IAM", d.execution_role_arn or "—"),
                classes="detail-row",
                markup=False,
            )
        )
        body.mount(
            Static(
                _format_kv("Entry point", d.entry_point or "—"),
                classes="detail-row",
                markup=False,
            )
        )
        # Args + Spark are typically the longest values in a job-run
        # detail. Per user feedback the previous single-line ``Args
        # --in s3://… --out s3://… --partitions 200`` was unreadable
        # on the EMR right pane, so each argument and each
        # ``--conf k=v`` gets its own indented line below a single
        # key header.
        for line in _multiline_kv("Args", _pair_args(list(d.entry_point_arguments))):
            body.mount(Static(line, classes="detail-row", markup=False))
        for line in _multiline_kv("Spark", _split_spark_params(d.spark_submit_parameters)):
            body.mount(Static(line, classes="detail-row", markup=False))


def _state_label(d: JobRunDetail) -> str:
    glyph = _TERMINAL_GLYPH.get(d.state.value, "?")
    return f"{glyph} {d.state.value}"


def _format_kv(key: str, value: str) -> str:
    return f"{key:<12}  {value}"


def _multiline_kv(key: str, values: list[str]) -> list[str]:
    """Render a list-valued KV as a header line + one indented row
    per value. Empty list collapses to a single ``key  —`` row.

    >>> _multiline_kv("Args", ["--in", "s3://bucket/in"])
    ['Args        ', '              --in', '              s3://bucket/in']
    """
    if not values:
        return [_format_kv(key, "—")]
    header = f"{key:<12}"
    indent = " " * 14  # 12-char key column + 2-space gap
    return [header] + [f"{indent}{v}" for v in values]


def _pair_args(args: list[str]) -> list[str]:
    """Group a flat positional-arg list into ``--option value`` pairs.

    Job-run arguments come from boto as a flat tuple
    (e.g. ``("--debug", "true", "--input", "s3://bucket/in/")``).
    User feedback after PR #80: rendering each element on its own
    line splits ``--debug`` from its value ``true`` — unreadable.

    Heuristic: an arg that starts with ``--`` AND is followed by
    a non-``--`` arg is treated as the option's value and rendered
    on the same line. ``--flag`` followed by another ``--flag``
    stays on its own line (boolean flag with no value). Positional
    args that don't start with ``--`` also stay on their own line.

    >>> _pair_args(["--debug", "true", "--in", "s3://x", "--verbose", "--out", "s3://y"])
    ['--debug true', '--in s3://x', '--verbose', '--out s3://y']
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        cur = args[i]
        if cur.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            out.append(f"{cur} {args[i + 1]}")
            i += 2
        else:
            out.append(cur)
            i += 1
    return out


def _split_spark_params(raw: str | None) -> list[str]:
    """Split a Spark submit parameter string into one element per
    ``--conf k=v`` (or other ``--option`` chunk).

    EMR returns the params as one space-joined string, e.g.
    ``"--conf spark.executor.instances=8 --conf spark.executor.memory=4g"``.
    Splitting on ``" --"`` and reattaching the leading ``--`` gives
    one line per option.

    Returns ``[]`` when the input is empty/None so
    :func:`_multiline_kv` falls back to the single-line ``—`` form.
    """
    if not raw:
        return []
    pieces = raw.split(" --")
    return [pieces[0]] + [f"--{p}" for p in pieces[1:]]


__all__ = ["JobRunDetailPane"]
