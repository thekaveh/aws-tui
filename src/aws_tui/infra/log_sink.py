"""JSON-lines log sink with rotation.

Wraps :class:`logging.handlers.RotatingFileHandler` so that every call writes
exactly one JSON line containing an ISO-8601 timestamp, the level, the event
name, and arbitrary structured fields supplied as keyword arguments.

The sink is owned at the app boundary (composed by :class:`RootVM` at startup
in M3); it is the only writer to its base directory.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final

_LOGGER_NAME: Final[str] = "aws_tui"
_FILE_NAME: Final[str] = "aws-tui.log"
_DEFAULT_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB
_DEFAULT_BACKUP_COUNT: Final[int] = 5


class _JsonLineFormatter(logging.Formatter):
    """Formatter that emits one JSON object per log record.

    The structured payload is attached to the record as the attribute
    ``json_fields``; the formatter merges in ``ts``, ``level``, and ``event``
    and serializes the result.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        extra = getattr(record, "json_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str, separators=(",", ":"))


class LogSink:
    """JSON-lines log writer with rotation.

    Default location: ``~/.cache/aws-tui/log/aws-tui.log`` rotated at 5 MiB
    across 5 backups. The directory is created on init if missing.
    """

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        backup_count: int = _DEFAULT_BACKUP_COUNT,
    ) -> None:
        if base_dir is None:
            base_dir = Path.home() / ".cache" / "aws-tui" / "log"
        base_dir.mkdir(parents=True, exist_ok=True)

        self._base_dir: Path = base_dir
        self._log_path: Path = base_dir / _FILE_NAME
        self._handler: RotatingFileHandler = RotatingFileHandler(
            self._log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        self._handler.setFormatter(_JsonLineFormatter())
        # Each LogSink owns its own logger instance, isolated so test runs and
        # concurrent app instances don't fight over global handler state.
        self._logger: logging.Logger = logging.getLogger(f"{_LOGGER_NAME}.{id(self)}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        # Reset handlers in case this id was reused.
        for existing in list(self._logger.handlers):
            self._logger.removeHandler(existing)
        self._logger.addHandler(self._handler)
        self._closed: bool = False

    @property
    def path(self) -> Path:
        """The active log file path."""
        return self._log_path

    def _log(self, level: int, event: str, fields: dict[str, Any]) -> None:
        if self._closed:
            return
        self._logger.log(level, event, extra={"json_fields": fields})

    def debug(self, event: str, **fields: object) -> None:
        self._log(logging.DEBUG, event, dict(fields))

    def info(self, event: str, **fields: object) -> None:
        self._log(logging.INFO, event, dict(fields))

    def warning(self, event: str, **fields: object) -> None:
        self._log(logging.WARNING, event, dict(fields))

    def error(self, event: str, **fields: object) -> None:
        self._log(logging.ERROR, event, dict(fields))

    def flush(self) -> None:
        """Force the underlying handler to flush its buffer."""
        if self._closed:
            return
        self._handler.flush()

    def close(self) -> None:
        """Close the handler. Idempotent."""
        if self._closed:
            return
        self._handler.flush()
        self._logger.removeHandler(self._handler)
        self._handler.close()
        self._closed = True


__all__ = ["LogSink"]
