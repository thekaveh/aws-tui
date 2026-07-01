# ruff: noqa: RUF001, RUF002, RUF003 — the glyph palette uses Unicode
# characters that look similar to ASCII (e.g. U+203A vs U+003E) and
# the ambiguity is intentional. Each glyph is documented next to its
# constant below.
"""Unified notification helpers.

Single source of truth for the **toast taxonomy** the user asked
for after PR #74:

  "I now need you to further standardize all toast notifications:
  go through all current notifications we currently have,
  categorize them into different classes, and then make sure they
  are formatted, stylized and presented consistently with
  consistent countdowns and maybe even consistent emojis based on
  their categories."

The plan lives in
``docs/superpowers/plans/2026-06-24-notification-consistency.md``
(§2 taxonomy, §3 grammar, §5 timeout policy). The five open
questions at the end of the plan are answered as the **defaults
in this file** — change them here and every call site updates.

Every toast in aws-tui should go through one of these helpers
instead of constructing :class:`~aws_tui.vm.chrome.toast_vm.ToastModel`
directly. The mypy ``Subject`` literal makes a typo
(``subject="Conection"``) a build error.

Classes:

- :func:`announce` — INFO, 3 s, glyph ``›``. "X happened, no action."
  (``›`` is U+203A, single right-pointing angle quotation mark.)
- :func:`progress` — INFO, sticky, glyph ``…``. "X is happening,
  caller dismisses on outcome."
- :func:`success`  — SUCCESS, 3 s, glyph ``✓``. "X you asked for
  completed."
- :func:`advise`   — WARNING, 8 s, glyph ``⚠``. "X went sideways
  but we coped; you may need to act."
- :func:`error`    — ERROR, 30 s (not sticky — user asked for a
  countdown), glyph ``✖``. "X failed, see log."
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, Literal

from rich.markup import escape as _markup_escape

from aws_tui.infra.redaction import redact_text
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel

if TYPE_CHECKING:
    from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
    from aws_tui.vm.chrome.toast_vm import ToastVM


Subject = Literal[
    "Theme",
    "Connection",
    "Source",
    "Fallback",
    "Mount",
    "Transfer",
    "Settings",
    "Auth",
    "Job",
]
"""Canonical subject vocabulary.

A new subject means adding to this literal AND deciding it's
durable enough to warrant a new channel. Until then, reuse the
nearest existing subject."""


# ── Class → (glyph, timeout, ToastLevel) ────────────────────────────────────
_ANNOUNCE_GLYPH: Final[str] = "›"  # ›, single right-pointing angle quotation mark
_PROGRESS_GLYPH: Final[str] = "…"
_SUCCESS_GLYPH: Final[str] = "✓"
_ADVISE_GLYPH: Final[str] = "⚠"
_ERROR_GLYPH: Final[str] = "✖"

_ANNOUNCE_TIMEOUT: Final[float] = 3.0
_SUCCESS_TIMEOUT: Final[float] = 3.0
_ADVISE_TIMEOUT: Final[float] = 8.0
# User: "consistent countdowns" — even errors get a deadline so
# the chrome doesn't pin forever. 30 s is enough to read + react;
# the durable record is in the log.
_ERROR_TIMEOUT: Final[float] = 30.0


# ── Internal: canonical text grammar ────────────────────────────────────────


def _format(glyph: str, subject: str, message: str, action: str | None) -> str:
    """Single canonical text shape:

        ``<glyph>  [b]<subject>:[/] <message>[ — <action>]``

    Two spaces after the glyph give the subject visible breathing
    room from the leading icon; one space after the colon keeps
    the rest readable. Rich markup is parsed by ``Toast.render``
    (post-PR-75) so ``[b]…[/]`` becomes bold.

    ``message`` and ``action`` are redacted, then escaped via
    ``rich.markup.escape`` so user / exception text containing
    ``[…]`` (``copy failed: [Errno 13] Permission denied`` etc.)
    doesn't crash the toast render with MarkupError or leak secrets.
    The ``[b]…[/]`` literal we inject around ``subject`` is the only
    intentional markup; subject itself comes from a small closed enum
    of callers and stays raw.
    """
    safe_message = redact_text(message)
    body = f"{glyph}  [b]{subject}:[/] {_markup_escape(safe_message)}"
    if action:
        body += f" — {_markup_escape(redact_text(action))}"
    return body


def _stable_id(prefix: str, subject: str, message: str) -> str:
    """Stable-but-collision-resistant id when the caller doesn't
    care about dedupe. Same prefix + same text = same id, so a
    re-raise replaces the earlier toast via the stack's id-collision
    handling rather than stacking duplicates.

    Uses ``hashlib.blake2b`` instead of ``hash() & 0xFFFFFF`` —
    Python's ``hash`` is salted per-process AND the 24-bit mask
    crosses 50% birthday-collision at ~4,800 unique messages within
    the same prefix+subject bucket (1% at ~580), meaning unrelated
    fresh toasts can silently replace earlier still-visible ones.
    blake2b 64-bit digest gives collision-resistant ids without
    per-process variance.
    """
    digest = hashlib.blake2b(message.encode("utf-8"), digest_size=8).hexdigest()
    return f"{prefix}-{subject.lower()}-{digest}"


# ── Public helpers ──────────────────────────────────────────────────────────


def announce(
    stack: ToastStackVM,
    *,
    subject: Subject,
    message: str,
    action: str | None = None,
    toast_id: str | None = None,
) -> ToastVM:
    """Benign event just happened. No action needed."""
    return stack.raise_toast(
        ToastModel(
            id=toast_id or _stable_id("announce", subject, message),
            text=_format(_ANNOUNCE_GLYPH, subject, message, action),
            level=ToastLevel.INFO,
            sticky=False,
            timeout_seconds=_ANNOUNCE_TIMEOUT,
            action_label=None,
            action_action=None,
        )
    )


def progress(
    stack: ToastStackVM,
    *,
    key: str,
    subject: Subject,
    message: str,
) -> ToastVM:
    """In-flight operation. Sticky — caller dismisses on outcome
    via ``stack.dismiss(toast_id)`` or by raising a new toast with
    the same id."""
    return stack.raise_toast(
        ToastModel(
            id=f"progress-{key}",
            text=_format(_PROGRESS_GLYPH, subject, message, None),
            level=ToastLevel.INFO,
            sticky=True,
            timeout_seconds=None,
            action_label=None,
            action_action=None,
        )
    )


def success(
    stack: ToastStackVM,
    *,
    subject: Subject,
    message: str,
    action: str | None = None,
    toast_id: str | None = None,
) -> ToastVM:
    """User-initiated operation completed cleanly."""
    return stack.raise_toast(
        ToastModel(
            id=toast_id or _stable_id("success", subject, message),
            text=_format(_SUCCESS_GLYPH, subject, message, action),
            level=ToastLevel.SUCCESS,
            sticky=False,
            timeout_seconds=_SUCCESS_TIMEOUT,
            action_label=None,
            action_action=None,
        )
    )


def advise(
    stack: ToastStackVM,
    *,
    subject: Subject,
    message: str,
    action: str | None = None,
    toast_id: str | None = None,
) -> ToastVM:
    """Heads-up: something is off but we coped. User may need to
    act later."""
    return stack.raise_toast(
        ToastModel(
            id=toast_id or _stable_id("advise", subject, message),
            text=_format(_ADVISE_GLYPH, subject, message, action),
            level=ToastLevel.WARNING,
            sticky=False,
            timeout_seconds=_ADVISE_TIMEOUT,
            action_label=None,
            action_action=None,
        )
    )


def error(
    stack: ToastStackVM,
    *,
    subject: Subject,
    message: str,
    action: str | None = None,
    toast_id: str | None = None,
) -> ToastVM:
    """Operation failed. User must act or read the log."""
    return stack.raise_toast(
        ToastModel(
            id=toast_id or _stable_id("error", subject, message),
            text=_format(_ERROR_GLYPH, subject, message, action),
            level=ToastLevel.ERROR,
            sticky=False,
            timeout_seconds=_ERROR_TIMEOUT,
            action_label=None,
            action_action=None,
        )
    )


__all__ = [
    "Subject",
    "advise",
    "announce",
    "error",
    "progress",
    "success",
]
