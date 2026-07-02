"""JSON-lines log sink with rotation.

Wraps :class:`logging.handlers.RotatingFileHandler` so that every call writes
exactly one JSON line containing an ISO-8601 timestamp, the level, the event
name, and arbitrary structured fields supplied as keyword arguments.

The sink is owned at the app boundary (composed by :class:`RootVM` at startup
in M3); it is the only writer to its base directory.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final

from aws_tui.infra.redaction import redact_mapping, redact_text

_LOGGER_NAME: Final[str] = "aws_tui"
_FILE_NAME: Final[str] = "aws-tui.log"
_DEFAULT_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB
_DEFAULT_BACKUP_COUNT: Final[int] = 5
_STANDARD_LOG_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message"}


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
            "event": redact_text(record.getMessage()),
        }
        extra = getattr(record, "json_fields", None)
        if isinstance(extra, dict):
            payload.update(redact_mapping(extra))
        else:
            stdlib_extra = {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_ATTRS
            }
            if stdlib_extra:
                payload.update(redact_mapping(stdlib_extra))
        return json.dumps(payload, default=str, separators=(",", ":"))


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """``RotatingFileHandler`` that tightens the active log file and its
    rotated backups to ``0o600``.

    Log lines can carry endpoint URLs, request IDs, and structured
    error context that shouldn't be readable by other local users on
    shared systems. The parent directory is already chmod'd ``0o700``
    by :func:`ensure_private_dir`; this brings the files themselves in
    line. Best-effort: filesystems without POSIX permission bits
    silently no-op the chmod (matches the crash-dump posture from the
    second loop).
    """

    @staticmethod
    def _chmod_owner_only(path: Path) -> None:
        with contextlib.suppress(OSError, NotImplementedError):
            path.chmod(0o600)

    def _open(self):  # type: ignore[no-untyped-def]
        # Untyped to match ``logging.FileHandler._open``'s actual
        # return — the stdlib's annotation is ``TextIOWrapper`` (a
        # private generic that's awkward to spell), and we're only
        # forwarding the value.
        stream = super()._open()
        self._chmod_owner_only(Path(self.baseFilename))
        return stream

    def doRollover(self) -> None:
        super().doRollover()
        # ``doRollover`` creates a fresh ``baseFilename`` and renames the
        # prior one to ``.1`` (shifting any pre-existing backups). Both
        # the new file and every existing backup need owner-only bits.
        self._chmod_owner_only(Path(self.baseFilename))
        for i in range(1, self.backupCount + 1):
            backup = Path(f"{self.baseFilename}.{i}")
            if backup.exists():
                self._chmod_owner_only(backup)


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
        capture_stdlib: bool = False,
    ) -> None:
        if base_dir is None:
            from aws_tui.infra.paths import cache_home

            base_dir = cache_home() / "log"
        # 0o700 matches ConfigStore.save — log lines can carry endpoint
        # URLs and request IDs that shouldn't be readable by other
        # local users on shared systems.
        from aws_tui.infra.paths import ensure_private_dir

        ensure_private_dir(base_dir)

        self._base_dir: Path = base_dir
        self._log_path: Path = base_dir / _FILE_NAME
        self._handler: RotatingFileHandler = _PrivateRotatingFileHandler(
            self._log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        self._handler.setFormatter(_JsonLineFormatter())
        # Each LogSink owns its own logger instance, isolated so test runs and
        # concurrent app instances don't fight over global handler state.
        #
        # Use uuid4 instead of ``id(self)``: id() is the CPython
        # memory address — Python's logging module caches every
        # logger by name in Logger.manager.loggerDict with a STRONG
        # reference, so the entry survives GC of the LogSink. Across
        # a long-running session (or a test suite cycling sinks)
        # the registry grows monotonically and every dead LogSink's
        # logger leaks. uuid4 collisions are astronomically
        # improbable so the prior "reset handlers in case the id
        # was reused" defence is no longer needed either.
        self._logger: logging.Logger = logging.getLogger(f"{_LOGGER_NAME}.{uuid.uuid4().hex}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._logger.addHandler(self._handler)
        self._stdlib_logger: logging.Logger | None = None
        self._stdlib_previous_level: int | None = None
        self._stdlib_previous_propagate: bool | None = None
        if capture_stdlib:
            self._stdlib_logger = logging.getLogger(_LOGGER_NAME)
            self._stdlib_previous_level = self._stdlib_logger.level
            self._stdlib_previous_propagate = self._stdlib_logger.propagate
            self._stdlib_logger.setLevel(logging.DEBUG)
            self._stdlib_logger.propagate = False
            self._stdlib_logger.addHandler(self._handler)
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
        if self._stdlib_logger is not None:
            self._stdlib_logger.removeHandler(self._handler)
            if self._stdlib_previous_level is not None:
                self._stdlib_logger.setLevel(self._stdlib_previous_level)
            if self._stdlib_previous_propagate is not None:
                self._stdlib_logger.propagate = self._stdlib_previous_propagate
        self._handler.close()
        # Release the logger from the module-level registry too.
        # ``Logger.manager.loggerDict`` holds a STRONG reference per
        # named logger that survives GC of the LogSink wrapper —
        # the R46 uuid switch only stopped id-reuse collisions, the
        # registry still grew monotonically across the process
        # lifetime (test suites cycling sinks, long-running app
        # sessions). The uuid4 name guarantees no other code
        # references it, so this del is safe.
        with contextlib.suppress(KeyError):
            del logging.Logger.manager.loggerDict[self._logger.name]
        self._closed = True


__all__ = ["LogSink"]
