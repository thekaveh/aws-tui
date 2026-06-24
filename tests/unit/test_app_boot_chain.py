"""Unit tests for the boot-time fallback-chain narration helpers
introduced after the post-PR-70 user request: "show toast
notifications when each source is about to be tried. And if that
source turns out not available, we show another toast that it's
not or that it failed, and then show another one about trying the
next option and so on".

These tests stay at the unit tier — exercising the chain builder,
the friendly-string mappings, and the stable toast-id contract
without booting the full app. End-to-end coverage of the live
chain (slow boto retry path → local fallback) sits in
``tests/integration/test_settings_flow.py``.
"""

from __future__ import annotations

from aws_tui.app import AwsTuiApp
from aws_tui.infra.connection_resolver import Connection


def _aws(name: str) -> Connection:
    return Connection(
        name=name,
        kind="aws",
        region="us-east-1",
        source="config",
        profile=name,
    )


def _s3(name: str) -> Connection:
    return Connection(
        name=name,
        kind="s3-compatible",
        region="us-east-1",
        source="config",
        endpoint_url=f"http://{name}.example:9000",
        access_key_id="AKIA",
        secret_access_key="SECRET",
        force_path_style=True,
    )


class _StubResolver:
    def __init__(self, conns: list[Connection]) -> None:
        self._conns = list(conns)

    def list(self) -> list[Connection]:
        return list(self._conns)


class _StubLogSink:
    def info(self, *_a: object, **_kw: object) -> None:  # pragma: no cover
        pass

    def error(self, *_a: object, **_kw: object) -> None:  # pragma: no cover
        pass


class _StubCtx:
    def __init__(self, conns: list[Connection]) -> None:
        self.connection_resolver = _StubResolver(conns)
        self.log_sink = _StubLogSink()


def _app_with(initial: Connection, others: list[Connection]) -> AwsTuiApp:
    """Bypass ``AwsTuiApp.__init__`` so we can drive ``_build_attempt_chain``
    in isolation. ``_build_attempt_chain`` only reads
    ``self._app_ctx.connection_resolver`` + ``self._app_ctx.log_sink``,
    so a tiny ``_StubCtx`` is enough.
    """
    app = AwsTuiApp.__new__(AwsTuiApp)
    app._app_ctx = _StubCtx(others)  # type: ignore[attr-defined]
    return app


# ── _build_attempt_chain ────────────────────────────────────────────────────


def test_chain_pins_initial_first_even_when_listed_later() -> None:
    initial = _aws("dev-sso")
    others = [_s3("minio"), _aws("prod"), _aws("dev-sso")]  # initial listed last
    app = _app_with(initial, others)
    chain = app._build_attempt_chain(initial)
    assert [c.name for c in chain] == ["dev-sso", "minio", "prod"]


def test_chain_dedupes_initial_against_resolver_list() -> None:
    initial = _aws("only")
    app = _app_with(initial, [_aws("only")])
    chain = app._build_attempt_chain(initial)
    assert [c.name for c in chain] == ["only"]


def test_chain_uses_kind_plus_name_as_identity() -> None:
    # Same name across different kinds is two distinct candidates.
    initial = _aws("shared")
    app = _app_with(initial, [_s3("shared")])
    chain = app._build_attempt_chain(initial)
    assert [(c.kind, c.name) for c in chain] == [("aws", "shared"), ("s3-compatible", "shared")]


def test_chain_survives_resolver_failure() -> None:
    initial = _aws("alpha")
    app = _app_with(initial, [])

    def _explode() -> list[Connection]:
        raise RuntimeError("config corrupted")

    app._app_ctx.connection_resolver.list = _explode  # type: ignore[assignment]
    chain = app._build_attempt_chain(initial)
    # Initial still gets tried — degrading to a single-candidate chain.
    assert [c.name for c in chain] == ["alpha"]


# ── Stable string contracts (used in toast text + log keys) ─────────────────


def test_friendly_kind_labels() -> None:
    assert AwsTuiApp._friendly_kind("aws") == "AWS"
    assert AwsTuiApp._friendly_kind("s3-compatible") == "S3-compatible"
    # Unknown kinds round-trip — no opaque renaming.
    assert AwsTuiApp._friendly_kind("ftp") == "ftp"


def test_friendly_outcome_labels_cover_every_terminal_pane_state() -> None:
    # Every outcome string the chain narrator can produce must map.
    for outcome in (
        "aws-sso-expired",
        "aws-no-creds",
        "unreachable",
        "auth-required",
        "forbidden",
        "timeout",
        "error",
    ):
        assert AwsTuiApp._friendly_outcome(outcome) != outcome, outcome


def test_toast_ids_are_stable_across_phases() -> None:
    # The chain raises the attempt toast, then dismisses it before
    # raising the outcome toast — same connection but DISTINCT ids so
    # the outcome doesn't overwrite an in-flight attempt by id-collision.
    conn = _aws("dev")
    assert AwsTuiApp._attempt_toast_id(conn) != AwsTuiApp._outcome_toast_id(conn)
    # Ids stay deterministic across calls so dismiss/raise can target
    # the same entry without bookkeeping.
    assert AwsTuiApp._attempt_toast_id(conn) == AwsTuiApp._attempt_toast_id(conn)
