"""The durable-diagnostics permission layer, end to end.

Four chmod sites cooperate to keep aws-tui's cache subtree owner-only:
``paths.ensure_private_dir`` (0o700 on the directory), ``log_sink``'s
``_open``/``doRollover`` (0o600 on the active log and every rotated backup), and
``CrashDumpWriter`` (0o600 on each dump). A mutation sweep found ALL FOUR
unpinned — each could be deleted, or its mode widened, with the entire repo
suite still green.

They are one security property, not four unrelated calls: the log carries
endpoint URLs, request ids and profile names, and crash dumps embed the last
1000 log lines plus 100 user actions. On a shared host, losing any single one of
these makes that content readable by other local users, because the parent
directory is otherwise created at the ambient umask (0o755) and the files at
0o644.

Skipped on Windows, which does not enforce POSIX permission bits.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from aws_tui.infra.crash_dump import CrashDump
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.paths import ensure_private_dir

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX permission bits not enforced on Windows"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_ensure_private_dir_creates_owner_only_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "cache" / "log"

    ensure_private_dir(target)

    assert target.is_dir()
    assert _mode(target) == 0o700, (
        f"cache subdirectory is {_mode(target):o}, not owner-only; "
        "the log and crash dumps inside it become listable by other local users"
    )


def test_ensure_private_dir_tightens_an_existing_world_readable_directory(
    tmp_path: Path,
) -> None:
    """Idempotent AND corrective — an upgrade from a looser umask must tighten."""
    target = tmp_path / "cache"
    target.mkdir(parents=True)
    target.chmod(0o755)

    ensure_private_dir(target)

    assert _mode(target) == 0o700


def test_log_file_is_owner_only(tmp_path: Path) -> None:
    sink = LogSink(base_dir=tmp_path)
    try:
        sink.info("test.event")
        log_path = sink.path
        assert log_path.exists(), "precondition: the log file was created"
        assert _mode(log_path) == 0o600, (
            f"log file is {_mode(log_path):o}; it carries endpoint URLs, "
            "request ids and profile names"
        )
        assert _mode(log_path.parent) == 0o700
    finally:
        sink.close()


def test_rotated_log_backups_are_owner_only(tmp_path: Path) -> None:
    """Rotation creates new files; each must be tightened, not just the active one."""
    sink = LogSink(base_dir=tmp_path, max_bytes=512, backup_count=2)
    try:
        for index in range(400):
            sink.info("test.event", index=index, filler="x" * 64)
        backups = sorted(sink.path.parent.glob(f"{sink.path.name}.*"))
        assert backups, "precondition: rotation produced at least one backup"
        for backup in backups:
            assert _mode(backup) == 0o600, f"rotated backup {backup.name} is {_mode(backup):o}"
    finally:
        sink.close()


def test_crash_dump_is_owner_only(tmp_path: Path) -> None:
    writer = CrashDump(base_dir=tmp_path)

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        path = writer.write(exc=exc)

    assert path.exists()
    assert _mode(path) == 0o600, (
        f"crash dump is {_mode(path):o}; dumps embed recent log lines and user actions"
    )
    assert _mode(path.parent) == 0o700


def test_rotation_tightens_a_pre_existing_world_readable_backup(tmp_path: Path) -> None:
    """The backup chmod loop defends the UPGRADE case, not the steady state.

    In steady state every backup is already owner-only: ``_open`` sets 0o600 on
    the active file and ``doRollover`` renames it, and a rename preserves the
    mode. So the loop is only observable when a backup exists that was created
    before this handler chmod'd anything — a log directory left by an older
    aws-tui.

    Two earlier versions of this test could not fail. The first seeded the stale
    file in a "log/" subdirectory that ``LogSink`` never writes to. The second
    emitted so many lines that the stale file was rotated past ``backup_count``
    and deleted before the loop could matter. This one keeps the write volume
    low enough that the stale file survives, and finds it by CONTENT rather than
    by assuming which suffix it lands on.
    """
    stale_backup = tmp_path / "aws-tui.log.1"
    stale_backup.write_text("legacy-marker\n", encoding="utf-8")
    stale_backup.chmod(0o644)
    assert _mode(stale_backup) == 0o644, "precondition: the stale backup is world-readable"

    sink = LogSink(base_dir=tmp_path, max_bytes=512, backup_count=3)
    try:
        for index in range(8):
            sink.info("test.event", index=index, filler="x" * 64)
    finally:
        sink.close()

    survivors = [
        path
        for path in sorted(tmp_path.glob("aws-tui.log.*"))
        if "legacy-marker" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert survivors, (
        "the stale backup was rotated out of existence; this test cannot "
        "exercise the backup chmod loop"
    )
    for path in survivors:
        assert _mode(path) == 0o600, (
            f"{path.name} is {_mode(path):o}: rotation did not tighten a backup "
            "inherited from an older install"
        )
