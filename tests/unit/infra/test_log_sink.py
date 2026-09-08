"""Unit tests for the JSON-lines rotating log sink."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from aws_tui.infra.log_sink import LogSink


@pytest.fixture
def sink(tmp_path: Path) -> Iterator[LogSink]:
    s = LogSink(base_dir=tmp_path)
    yield s
    s.close()


def _read_log_lines(tmp_path: Path) -> list[dict[str, object]]:
    log_path = tmp_path / "aws-tui.log"
    text = log_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_log_dir_created_when_missing(tmp_path: Path) -> None:
    sub = tmp_path / "nested" / "logs"
    s = LogSink(base_dir=sub)
    try:
        s.info("startup")
        s.flush()
        assert sub.is_dir()
        assert (sub / "aws-tui.log").is_file()
    finally:
        s.close()


def test_info_writes_one_json_line(sink: LogSink, tmp_path: Path) -> None:
    sink.info("hello", user="kaveh", count=3)
    sink.flush()
    lines = _read_log_lines(tmp_path)
    assert len(lines) == 1
    record = lines[0]
    assert record["event"] == "hello"
    assert record["level"] == "INFO"
    assert record["user"] == "kaveh"
    assert record["count"] == 3
    assert "ts" in record
    # ISO8601 with timezone indicator (Z or +/-HH:MM)
    ts = str(record["ts"])
    assert ts.endswith("Z") or "+" in ts or ts.count("-") >= 3


def test_secret_fields_and_url_credentials_are_redacted(sink: LogSink, tmp_path: Path) -> None:
    sink.warning(
        "s3.failed secret_access_key=EVENTSECRET "
        "Authorization: Bearer SECRETBEARER "
        "api_key=SECRETAPI private_key=SECRETPRIVATE "
        "payload={'secret_access_key': 'REPRSECRET'} "
        "https://user:eventpass@example.com/bucket?X-Amz-Signature=eventsig",
        endpoint_url="https://user:pass@example.com/bucket?X-Amz-Signature=abc123",
        secret_access_key="SECRET",
        access_token="TOKEN",
        nested={
            "AuthorizationToken": "NESTED",
            "items": [{"password": "HUNTER2"}],
        },
    )
    sink.flush()

    raw = (tmp_path / "aws-tui.log").read_text(encoding="utf-8")
    assert "SECRET" not in raw
    assert "TOKEN" not in raw
    assert "NESTED" not in raw
    assert "HUNTER2" not in raw
    assert "user:pass" not in raw
    assert "EVENTSECRET" not in raw
    assert "SECRETBEARER" not in raw
    assert "SECRETAPI" not in raw
    assert "SECRETPRIVATE" not in raw
    assert "REPRSECRET" not in raw
    assert "user:eventpass" not in raw
    assert "eventsig" not in raw
    assert "abc123" not in raw
    assert "example.com" in raw
    assert "[REDACTED]" in raw


def test_all_levels_round_trip(sink: LogSink, tmp_path: Path) -> None:
    sink.debug("dbg")
    sink.info("nfo")
    sink.warning("warn")
    sink.error("err", code=500)
    sink.flush()
    lines = _read_log_lines(tmp_path)
    assert {entry["level"] for entry in lines} == {"DEBUG", "INFO", "WARNING", "ERROR"}


def test_rotation_creates_backup(tmp_path: Path) -> None:
    s = LogSink(base_dir=tmp_path, max_bytes=512, backup_count=3)
    try:
        # Each call writes ~80 bytes of JSON; 100 of them safely exceeds 512.
        for i in range(100):
            s.info("entry", index=i, padding="x" * 32)
        s.flush()
    finally:
        s.close()
    # After rotation, at least one backup file should exist.
    backups = list(tmp_path.glob("aws-tui.log.*"))
    assert backups, "expected at least one rotated backup file"


def test_close_is_idempotent(tmp_path: Path) -> None:
    s = LogSink(base_dir=tmp_path)
    s.info("hi")
    s.close()
    s.close()  # second close must not raise


def test_capture_stdlib_aws_tui_warnings_when_enabled(tmp_path: Path) -> None:
    s = LogSink(base_dir=tmp_path, capture_stdlib=True)
    try:
        logging.getLogger("aws_tui.infra.aws_session").warning(
            "aws_session.aclose_failed",
            extra={
                "error": "secret_access_key=SHOULD_NOT_LEAK",
                "error_type": "RuntimeError",
            },
        )
        s.flush()
    finally:
        s.close()

    raw = (tmp_path / "aws-tui.log").read_text(encoding="utf-8")
    assert "SHOULD_NOT_LEAK" not in raw
    lines = _read_log_lines(tmp_path)
    assert lines[-1]["event"] == "aws_session.aclose_failed"
    assert lines[-1]["level"] == "WARNING"
    assert lines[-1]["error"] == "secret_access_key=[REDACTED]"
    assert lines[-1]["error_type"] == "RuntimeError"


def test_capture_stdlib_exception_serializes_redacted_traceback(tmp_path: Path) -> None:
    s = LogSink(base_dir=tmp_path, capture_stdlib=True)
    try:
        try:
            raise RuntimeError("Authorization: Bearer TRACE_SECRET")
        except RuntimeError:
            logging.getLogger("aws_tui.background").exception("background.failed")
        s.flush()
    finally:
        s.close()

    raw = (tmp_path / "aws-tui.log").read_text(encoding="utf-8")
    assert "TRACE_SECRET" not in raw
    record = _read_log_lines(tmp_path)[-1]
    assert record["event"] == "background.failed"
    assert "RuntimeError: Authorization: Bearer [REDACTED]" in str(record["traceback"])


@pytest.mark.parametrize("close_first", ["first", "second"])
def test_overlapping_stdlib_captures_restore_logger_after_final_close(
    tmp_path: Path,
    close_first: str,
) -> None:
    logger = logging.getLogger("aws_tui")
    previous_level = logger.level
    previous_propagate = logger.propagate
    previous_handlers = list(logger.handlers)
    logger.setLevel(logging.WARNING)
    logger.propagate = True

    first = LogSink(base_dir=tmp_path / "first", capture_stdlib=True)
    second = LogSink(base_dir=tmp_path / "second", capture_stdlib=True)
    closing, survivor, survivor_dir = (
        (first, second, tmp_path / "second")
        if close_first == "first"
        else (second, first, tmp_path / "first")
    )
    try:
        closing.close()
        logging.getLogger("aws_tui.overlap").info("survivor-record")
        survivor.flush()
        assert _read_log_lines(survivor_dir)[-1]["event"] == "survivor-record"

        survivor.close()
        assert logger.level == logging.WARNING
        assert logger.propagate is True
        assert logger.handlers == previous_handlers
    finally:
        first.close()
        second.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_non_serializable_values_are_redacted_before_stringification(
    sink: LogSink, tmp_path: Path
) -> None:
    """The ``default=`` fallback must not bypass redaction.

    ``redact_mapping`` returns any value that is not a str/Mapping/Sequence
    untouched, and ``json.dumps(default=str)`` then stringified it AFTER
    redaction — so an object whose repr carried a credential reached the durable
    log verbatim.
    """

    @dataclass
    class _Credentials:
        aws_secret_access_key: str = "wJalrXUtnFEMISECRET"

    sink.info("boot", connection=_Credentials(), nested={"inner": _Credentials()})

    written = (tmp_path / "aws-tui.log").read_text(encoding="utf-8")

    assert "wJalrXUtnFEMISECRET" not in written
    assert "[REDACTED]" in written
