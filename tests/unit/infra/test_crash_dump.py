"""Tests for the CrashDump writer."""

from __future__ import annotations

import logging
import traceback
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy
from aws_tui.infra.crash_dump import CrashDump, _tail_text


def _make_exc() -> Exception:
    try:
        raise TypeError("kaboom")
    except TypeError as exc:
        return exc


def test_write_creates_file_with_expected_name(tmp_path: Path) -> None:
    dump = CrashDump(base_dir=tmp_path / "crash")
    # Microseconds suffix added in round-44 to prevent same-second
    # crash dumps from clobbering each other (a master crash plus a
    # cascading shutdown raise within ms losing the root-cause
    # report). The test now pins the seconds-and-microseconds shape.
    ts = datetime(2026, 6, 14, 10, 30, 45, 123456, tzinfo=UTC)
    path = dump.write(exc=_make_exc(), timestamp=ts)
    assert path.exists()
    assert path.name == "2026-06-14T10-30-45-123456.txt"
    assert path.parent == tmp_path / "crash"


def test_write_contains_traceback_and_marker(tmp_path: Path) -> None:
    dump = CrashDump(base_dir=tmp_path / "crash")
    path = dump.write(exc=_make_exc())
    text = path.read_text(encoding="utf-8")
    assert "aws-tui crash dump" in text
    assert "TypeError: kaboom" in text
    assert "== traceback ==" in text
    assert "== last user actions ==" in text
    assert "== log tail ==" in text


def test_write_redacts_exception_log_tail_and_actions(tmp_path: Path) -> None:
    log = tmp_path / "aws-tui.log"
    log.write_text(
        "endpoint=https://user:pass@example.com/bucket?X-Amz-Signature=abc123 "
        "Authorization: Bearer SECRETBEARER "
        "api_key=SECRETAPI private_key=SECRETPRIVATE "
        "secret_access_key=SECRET "
        "payload={'secret_access_key': 'REPRSECRET'} "
        '{"access_token": "JSONTOKEN", "password": "JSONPASS"} '
        'credentials = "TOMLCREDS"\n',
        encoding="utf-8",
    )
    try:
        raise RuntimeError("failed https://user:pass@example.com/bucket?token=tok123")
    except RuntimeError as exc:
        dump = CrashDump(base_dir=tmp_path / "crash")
        path = dump.write(
            exc=exc,
            log_path=log,
            action_ring=["open token=actiontok", "copy password=hunter2"],
        )

    text = path.read_text(encoding="utf-8")
    for leaked in [
        "user:pass",
        "abc123",
        "SECRET",
        "REPRSECRET",
        "SECRETBEARER",
        "SECRETAPI",
        "SECRETPRIVATE",
        "JSONTOKEN",
        "JSONPASS",
        "TOMLCREDS",
        "tok123",
        "actiontok",
        "hunter2",
    ]:
        assert leaked not in text
    assert "example.com" in text
    assert "[REDACTED]" in text


def test_rejected_sql_is_absent_from_exception_chains_logs_and_crash_dump(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "redaction_fixture"
    sql = f"SELECT '{secret}_TAIL_WITHOUT_QUOTE"
    caplog.set_level(logging.WARNING, logger="sqlglot")

    with pytest.raises(QueryRejectedError) as captured:
        ReadOnlySqlPolicy().validate(sql)

    exc = captured.value
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    short = CrashDump.short_traceback(exc, max_lines=100)
    path = CrashDump(base_dir=tmp_path / "crash").write(exc=exc)
    crash_text = path.read_text(encoding="utf-8")

    assert secret not in formatted
    assert secret not in short
    assert secret not in caplog.text
    assert secret not in crash_text


def test_write_appends_log_tail(tmp_path: Path) -> None:
    log = tmp_path / "aws-tui.log"
    log.write_text(
        "\n".join([f"line-{i}" for i in range(2000)]) + "\n",
        encoding="utf-8",
    )
    dump = CrashDump(base_dir=tmp_path / "crash")
    path = dump.write(exc=_make_exc(), log_path=log)
    text = path.read_text(encoding="utf-8")
    # only last 1000 lines included; line-999 absent, line-1000 present
    assert "line-1999" in text
    assert "line-1000" in text
    assert "line-0\n" not in text


def test_write_appends_tail_across_rotated_logs(tmp_path: Path) -> None:
    log = tmp_path / "aws-tui.log"
    (tmp_path / "aws-tui.log.2").write_text("oldest\n", encoding="utf-8")
    (tmp_path / "aws-tui.log.1").write_text("before-rollover\n", encoding="utf-8")
    log.write_text("current\n", encoding="utf-8")

    path = CrashDump(base_dir=tmp_path / "crash").write(exc=_make_exc(), log_path=log)
    text = path.read_text(encoding="utf-8")

    assert text.index("oldest") < text.index("before-rollover") < text.index("current")


def test_log_tail_reads_a_bounded_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "aws-tui.log"
    log.write_text("".join(f"line-{index:06d}-" + "x" * 80 + "\n" for index in range(80_000)))
    original_open = Path.open
    bytes_read = 0

    class _TrackingFile:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def __enter__(self) -> _TrackingFile:
            self._handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self._handle.__exit__(*args)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> object:
            nonlocal bytes_read
            data = self._handle.read(size)  # type: ignore[attr-defined]
            bytes_read += len(data if isinstance(data, bytes) else data.encode())
            return data

        def readlines(self) -> object:
            nonlocal bytes_read
            lines = self._handle.readlines()  # type: ignore[attr-defined]
            bytes_read += sum(
                len(line if isinstance(line, bytes) else line.encode()) for line in lines
            )
            return lines

        def __getattr__(self, name: str) -> object:
            return getattr(self._handle, name)

    def tracked_open(path: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(path, *args, **kwargs)
        return _TrackingFile(handle) if path == log else handle

    monkeypatch.setattr(Path, "open", tracked_open)

    tail = _tail_text(log, 1000)

    assert "line-079999" in tail
    assert "line-000000" not in tail
    assert bytes_read <= 1024 * 1024


def test_write_appends_action_tail(tmp_path: Path) -> None:
    actions = [f"act-{i}" for i in range(150)]
    dump = CrashDump(base_dir=tmp_path / "crash")
    path = dump.write(exc=_make_exc(), action_ring=actions)
    text = path.read_text(encoding="utf-8")
    # only last 100 entries
    assert "act-149" in text
    assert "act-50" in text
    assert "act-49" not in text


def test_short_traceback_caps_lines() -> None:
    exc = _make_exc()
    short = CrashDump.short_traceback(exc, max_lines=3)
    assert len(short.splitlines()) <= 3
    assert "TypeError" in short or "raise TypeError" in short


def test_missing_log_path_is_tolerated(tmp_path: Path) -> None:
    dump = CrashDump(base_dir=tmp_path / "crash")
    path = dump.write(exc=_make_exc(), log_path=tmp_path / "does-not-exist.log")
    assert path.exists()
