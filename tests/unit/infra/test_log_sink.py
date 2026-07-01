"""Unit tests for the JSON-lines rotating log sink."""

from __future__ import annotations

import json
from collections.abc import Iterator
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
        "s3.failed",
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
