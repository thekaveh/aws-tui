"""Demo mode — runtime mock backing for all services.

Enabled by the ``AWS_TUI_DEMO`` env var (truthy values:
case-insensitive ``1`` / ``true`` / ``yes``) OR the ``--demo`` CLI
flag. When on, the composition root swaps real connections + service
clients for deterministic in-memory fakes — see
``docs/superpowers/specs/2026-06-28-demo-mode-design.md`` for the
overall design.

This package is consumed by ``aws_tui.app`` and ``aws_tui.composition``
only. Importing from ``aws_tui.{domain,infra,vm,services,ui}`` is
banned by ``scripts/check-layers.sh`` — the demo fakes live BELOW
those layers (they implement domain interfaces) so the import arrow
must point downward.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from typing import Final

DEMO_ENV_VAR: Final[str] = "AWS_TUI_DEMO"

_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes"})


def is_demo_mode_enabled(*, argv: Sequence[str] | None = None) -> bool:
    """Return True when either signal indicates demo mode.

    Two independent triggers:

    - Environment variable ``AWS_TUI_DEMO`` set to a truthy value
      (case-insensitive ``1`` / ``true`` / ``yes``).
    - The ``--demo`` long flag present in ``argv``.

    Either alone is sufficient; both being set is fine. The ``argv``
    parameter defaults to :data:`sys.argv` so callers don't have to
    thread it through; tests pass an explicit list.
    """
    env_value = os.environ.get(DEMO_ENV_VAR, "").strip().lower()
    if env_value in _TRUTHY:
        return True
    args = list(sys.argv if argv is None else argv)
    return "--demo" in args


__all__ = ["DEMO_ENV_VAR", "is_demo_mode_enabled"]
