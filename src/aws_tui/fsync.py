"""Durability helper shared by journal and config writers."""

from __future__ import annotations

import os
from pathlib import Path


def fsync_directory(path: Path) -> None:
    """Flush a directory entry to disk so a rename survives a crash.

    POSIX only: Windows has no directory fsync, and the call is a no-op there.
    Failures are ignored because the rename has already happened; this only
    narrows the window in which it could be lost.
    """
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
