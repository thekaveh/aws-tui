# ruff: noqa: RUF001 — toast glyphs are Unicode look-alikes
# of ASCII chars (U+203A, U+2026, U+2713, U+26A0, U+2716); chosen
# deliberately for visual distinction and these assertions
# intentionally use the same chars.
"""Unit tests for :mod:`aws_tui.ui.notifications` — the unified
toast taxonomy + grammar introduced post-PR-74 per the user's ask:

  "go through all current notifications we currently have,
  categorize them into different classes, and then make sure they
  are formatted, stylized and presented consistently with
  consistent countdowns and maybe even consistent emojis based on
  their categories."

These tests pin the contract so a future migration of a call site
to a different helper changes user-visible behaviour deliberately,
not by accident.
"""

from __future__ import annotations

from typing import Any

import pytest

from aws_tui.ui import notifications
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel


class _StubStack:
    """Captures every raised toast for assertions, no Textual mount."""

    def __init__(self) -> None:
        self.raised: list[ToastModel] = []

    def raise_toast(self, model: ToastModel) -> Any:
        self.raised.append(model)
        return object()


# ── Per-class glyph + level + timeout ──────────────────────────────────────


def test_announce_uses_info_level_announce_glyph_and_3s_timeout() -> None:
    stack = _StubStack()
    notifications.announce(stack, subject="Theme", message="switched to carbon")  # type: ignore[arg-type]
    [m] = stack.raised
    assert m.level is ToastLevel.INFO
    assert m.sticky is False
    assert m.timeout_seconds == 3.0
    assert m.text.startswith("›")
    assert "[b]Theme:[/]" in m.text
    assert "switched to carbon" in m.text


def test_success_uses_success_level_check_glyph_and_3s_timeout() -> None:
    stack = _StubStack()
    notifications.success(stack, subject="Connection", message="dev-sso connected")  # type: ignore[arg-type]
    [m] = stack.raised
    assert m.level is ToastLevel.SUCCESS
    assert m.timeout_seconds == 3.0
    assert m.text.startswith("✓")


def test_advise_uses_warning_level_warn_glyph_and_8s_timeout() -> None:
    stack = _StubStack()
    notifications.advise(stack, subject="Source", message="dev-sso unavailable")  # type: ignore[arg-type]
    [m] = stack.raised
    assert m.level is ToastLevel.WARNING
    assert m.timeout_seconds == 8.0
    assert m.text.startswith("⚠")


def test_error_uses_error_level_x_glyph_and_30s_timeout_not_sticky() -> None:
    """User asked for "consistent countdowns" — even error toasts
    get a deadline so the chrome doesn't pin forever. 30 s is long
    enough to read + react; the durable record is in the log."""
    stack = _StubStack()
    notifications.error(stack, subject="Mount", message="S3 view failed")  # type: ignore[arg-type]
    [m] = stack.raised
    assert m.level is ToastLevel.ERROR
    assert m.sticky is False, "error toasts should auto-dismiss per the consistency plan"
    assert m.timeout_seconds == 30.0
    assert m.text.startswith("✖")


def test_progress_is_sticky_with_ellipsis_glyph() -> None:
    stack = _StubStack()
    notifications.progress(stack, key="boot-1", subject="Connection", message="trying dev-sso")  # type: ignore[arg-type]
    [m] = stack.raised
    assert m.level is ToastLevel.INFO
    assert m.sticky is True
    assert m.timeout_seconds is None
    assert m.text.startswith("…")
    assert m.id == "progress-boot-1"


# ── Grammar ────────────────────────────────────────────────────────────────


def test_canonical_grammar_glyph_subject_message() -> None:
    stack = _StubStack()
    notifications.announce(stack, subject="Theme", message="switched to nord")  # type: ignore[arg-type]
    [m] = stack.raised
    # Canonical: ``<glyph>  [b]<subject>:[/] <message>`` — two spaces
    # after the glyph give the subject breathing room from the
    # leading icon (single-space looked cramped in the dark themes).
    assert m.text == "›  [b]Theme:[/] switched to nord"


def test_action_call_out_uses_em_dash() -> None:
    stack = _StubStack()
    notifications.advise(
        stack,  # type: ignore[arg-type]
        subject="Fallback",
        message="all sources unavailable",
        action="press r to retry",
    )
    [m] = stack.raised
    assert m.text.endswith(" — press r to retry")


def test_progress_does_not_carry_action_in_grammar() -> None:
    """Action call-outs only belong on terminal outcomes
    (announce / success / advise / error). A live progress toast
    that says "trying X — and also press r" reads as nonsense."""
    # Helper doesn't accept ``action`` — confirm via signature.
    import inspect

    sig = inspect.signature(notifications.progress)
    assert "action" not in sig.parameters


# ── Toast id stability ─────────────────────────────────────────────────────


def test_same_message_renders_same_id_for_dedupe() -> None:
    """The stack id-collision handling replaces an earlier toast
    with the same id rather than stacking duplicates. Same-subject +
    same-message must therefore produce the same id so a re-raise
    naturally dedupes."""
    a = _StubStack()
    b = _StubStack()
    notifications.advise(a, subject="Source", message="skipped foo")  # type: ignore[arg-type]
    notifications.advise(b, subject="Source", message="skipped foo")  # type: ignore[arg-type]
    assert a.raised[0].id == b.raised[0].id


def test_explicit_toast_id_overrides_auto() -> None:
    stack = _StubStack()
    notifications.error(stack, subject="Mount", message="X", toast_id="mount-service-failed")  # type: ignore[arg-type]
    [m] = stack.raised
    assert m.id == "mount-service-failed"


# ── Subject typing ─────────────────────────────────────────────────────────


def test_subject_literal_includes_every_call_site_subject() -> None:
    """If a call site uses a new subject the literal must list it,
    otherwise mypy will reject. This test pins the public set so a
    drift becomes a deliberate change."""
    from typing import get_args

    assert set(get_args(notifications.Subject)) == {
        "Theme",
        "Connection",
        "Source",
        "Fallback",
        "Mount",
        "Transfer",
        "Settings",
        "Auth",
    }


# ── Edge cases ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("helper", "expected_glyph"),
    [
        (notifications.announce, "›"),
        (notifications.success, "✓"),
        (notifications.advise, "⚠"),
        (notifications.error, "✖"),
    ],
)
def test_glyphs_are_distinct_one_per_terminal_class(helper: Any, expected_glyph: str) -> None:
    stack = _StubStack()
    helper(stack, subject="Theme", message="m")
    assert stack.raised[0].text.startswith(expected_glyph)
