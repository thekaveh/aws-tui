"""Crash-dump writer for unhandled exceptions.

Per spec §7.9 + §7.10, an unhandled exception writes a single file at
``~/.cache/aws-tui/crash/<ts>.txt`` containing:

- the full traceback,
- up to the last 1000 lines of the JSON-lines log file,
- up to the last 100 user-action records (supplied by the caller as a
  small in-memory ring buffer).

The module is deliberately I/O-only and free of VM / UI references so it
sits cleanly in the infra layer.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Final

_LOG_TAIL_LINES: Final[int] = 1000
_ACTION_TAIL_LINES: Final[int] = 100


def _default_crash_dir() -> Path:
    # Lazy import to keep ``Path.home()`` evaluation out of module-load
    # time (matters for tests that monkey-patch ``HOME`` or
    # ``platformdirs.user_cache_dir`` before importing this module).
    from aws_tui.infra.paths import cache_home

    return cache_home() / "crash"


def _filename_for(ts: datetime) -> str:
    """Return the filename for a crash dump created at ``ts``.

    Colons would render as drive separators on a few corner-case
    filesystems, so we use ``YYYY-MM-DDTHH-MM-SS`` (no ``:``).
    """
    return ts.strftime("%Y-%m-%dT%H-%M-%S") + ".txt"


def _short_traceback(tb_lines: list[str], *, max_lines: int = 5) -> str:
    """Pick the first ``max_lines`` non-blank lines from a traceback render."""
    out: list[str] = []
    for line in tb_lines:
        line = line.rstrip()
        if not line:
            continue
        out.append(line)
        if len(out) >= max_lines:
            break
    return "\n".join(out)


class CrashDump:
    """Writer for ``~/.cache/aws-tui/crash/<ts>.txt`` dumps.

    The class is a thin formatter; it exists as a class (rather than a
    bare function) so tests can inject the base directory and so the
    composition root can reuse a single instance.
    """

    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._dir = base_dir if base_dir is not None else _default_crash_dir()
        # 0o700: crash dumps embed the last 1000 log lines + the last
        # 100 user actions, which may include endpoint URLs and
        # presigned-URL query strings. Match ConfigStore.save's
        # defense-in-depth on the parent directory.
        from aws_tui.infra.paths import ensure_private_dir

        ensure_private_dir(self._dir)

    @property
    def base_dir(self) -> Path:
        return self._dir

    def write(
        self,
        *,
        exc: BaseException,
        log_path: Path | None = None,
        action_ring: Iterable[str] | None = None,
        timestamp: datetime | None = None,
    ) -> Path:
        """Write the dump and return its path.

        Parameters
        ----------
        exc:
            The unhandled exception.
        log_path:
            Optional path to the JSON-lines log file; up to the last 1000
            lines are appended verbatim.
        action_ring:
            Optional iterable of user-action strings; up to the last 100
            entries are appended verbatim.
        timestamp:
            Override for the dump's filename timestamp (used by tests).
        """
        ts = timestamp if timestamp is not None else datetime.now(UTC)
        path = self._dir / _filename_for(ts)
        tb: TracebackType | None = exc.__traceback__
        tb_text = "".join(traceback.format_exception(type(exc), exc, tb))
        log_tail = _tail_text(log_path, _LOG_TAIL_LINES) if log_path is not None else ""
        action_tail = _format_actions(action_ring, _ACTION_TAIL_LINES) if action_ring else ""

        body = (
            f"aws-tui crash dump\n"
            f"timestamp: {ts.isoformat()}\n"
            f"exception: {type(exc).__name__}: {exc}\n"
            f"\n"
            f"== traceback ==\n"
            f"{tb_text.rstrip()}\n"
            f"\n"
            f"== last user actions ==\n"
            f"{action_tail.rstrip()}\n"
            f"\n"
            f"== log tail ==\n"
            f"{log_tail.rstrip()}\n"
        )
        path.write_text(body, encoding="utf-8")
        return path

    @staticmethod
    def short_traceback(exc: BaseException, *, max_lines: int = 5) -> str:
        """Render up to ``max_lines`` of formatted traceback for a modal preview."""
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        flat: list[str] = []
        for chunk in tb_lines:
            flat.extend(chunk.splitlines())
        return _short_traceback(flat, max_lines=max_lines)


def _tail_text(path: Path, max_lines: int) -> str:
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    return "".join(lines[-max_lines:])


def _format_actions(actions: Iterable[str], max_lines: int) -> str:
    items = list(actions)
    if len(items) > max_lines:
        items = items[-max_lines:]
    return "\n".join(items)


__all__ = ["CrashDump"]
