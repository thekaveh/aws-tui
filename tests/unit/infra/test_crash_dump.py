"""Tests for the CrashDump writer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aws_tui.infra.crash_dump import CrashDump


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
