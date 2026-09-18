"""Native OS clipboard port.

The TUI writes OSC 52 to the terminal, but OSC 52 delivery is
unobservable: no terminal acknowledges it and most refuse the read-back
query, so a copy that reaches nothing looks exactly like a copy that
worked. This port is the second, *checkable* channel — it spawns the
platform's clipboard helper and reports what actually happened, so the
caller can stop claiming "copied" on faith.

:class:`ClipboardPort` is the protocol callers depend on. Two concrete
implementations:

* :class:`NativeClipboard` — spawns ``pbcopy`` / ``clip`` /
  ``wl-copy`` / ``xclip`` / ``xsel`` depending on platform and session.
* :class:`InMemoryClipboard` — records every payload; the test fake.

All three live in this one module on purpose. ``infra/`` may not import
``aws_tui.domain``/``vm``/``ui``/``services`` or textual
(``scripts/check-layers.sh``), so the protocol has nowhere else to live —
the same shape as :mod:`aws_tui.infra.keychain`. For the same reason the
port never touches ``App.copy_to_clipboard``: the OSC 52 write stays in
the app layer.

This is the first subprocess in ``src/aws_tui``, so the rules it follows
are the convention for any that come later: probe with
:func:`shutil.which`, argv lists only, never ``shell=True``, an explicit
timeout, ``check=False``, and both streams to ``DEVNULL``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

# Generous for pbcopy (~2 ms) and for clip.exe on a loaded Windows CI
# runner, short enough that a hung xclip cannot look like a freeze.
_TIMEOUT_SECONDS: Final[float] = 2.0

# clip.exe mangles non-ASCII fed as UTF-8; it reads UTF-16LE correctly.
# Windows-only, and therefore not observable on a macOS or Linux box.
_WINDOWS_ENCODING: Final[str] = "utf-16-le"

# No helper was found, or the session has no display to own a clipboard.
_NO_MECHANISM: Final[str] = "none"

# A non-zero exit is not an exception here (``check=False``), but it is the
# condition ``subprocess`` names ``CalledProcessError``. Reporting that name
# keeps ``error_type`` a class name and nothing else.
_NONZERO_EXIT: Final[str] = "CalledProcessError"


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    """What one clipboard write actually did.

    ``mechanism`` is the helper that ran — ``"pbcopy"``, ``"clip"``,
    ``"wl-copy"``, ``"xclip"``, ``"xsel"`` — or ``"none"`` when there was
    nothing to run. ``error_type`` is an exception class name and never,
    under any circumstance, the payload: the whole point of a clipboard is
    that its contents are the user's, and a copied secret must not land in
    a log file.
    """

    ok: bool
    mechanism: str
    error_type: str | None = None


@runtime_checkable
class ClipboardPort(Protocol):
    """Write text to the OS clipboard and say whether it landed.

    Synchronous: the implementation spawns a process, so callers on an
    event loop must offload it (``anyio.to_thread.run_sync``).
    """

    def write(self, text: str) -> ClipboardResult: ...


def _which(name: str) -> str | None:
    """Return the resolved helper path, or ``None`` when it is absent."""
    return shutil.which(name)


def _run(argv: list[str], payload: bytes) -> int:
    """Feed ``payload`` to ``argv`` on stdin and return its exit status.

    Argv list, no shell, bounded wait, no exception on a non-zero exit,
    and both output streams discarded so a chatty helper can never
    corrupt the TUI's own terminal.
    """
    # Fixed argv, no shell, and the executable was resolved by which().
    completed = subprocess.run(
        argv,
        input=payload,
        timeout=_TIMEOUT_SECONDS,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode


class NativeClipboard:
    """Spawns the platform clipboard helper.

    The seams (``which``, ``run``, ``platform``, ``environ``) exist so
    tests can cover every platform's resolution from one machine without
    ever spawning a real helper — CI runs Linux, macOS and Windows, and a
    real ``xclip`` on a headless runner would hang until the timeout.
    """

    __slots__ = ("_environ", "_platform", "_run", "_which")

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = _which,
        run: Callable[[list[str], bytes], int] = _run,
        platform: str = sys.platform,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self._which = which
        self._run = run
        self._platform = platform
        self._environ = environ

    def write(self, text: str) -> ClipboardResult:
        """Hand ``text`` to the first available helper."""
        candidate = self._resolve()
        if candidate is None:
            return ClipboardResult(ok=False, mechanism=_NO_MECHANISM)
        mechanism, argv = candidate
        encoding = _WINDOWS_ENCODING if self._platform == "win32" else "utf-8"
        payload = text.encode(encoding)
        try:
            returncode = self._run(argv, payload)
        except (subprocess.TimeoutExpired, OSError) as exc:
            # FileNotFoundError and PermissionError are OSError subclasses;
            # ``type(exc).__name__`` keeps each one's precise name.
            return ClipboardResult(ok=False, mechanism=mechanism, error_type=type(exc).__name__)
        if returncode != 0:
            return ClipboardResult(ok=False, mechanism=mechanism, error_type=_NONZERO_EXIT)
        return ClipboardResult(ok=True, mechanism=mechanism)

    def _resolve(self) -> tuple[str, list[str]] | None:
        """Return the first installed candidate, or ``None`` for none."""
        for mechanism, argv in self._candidates():
            if self._which(argv[0]) is not None:
                return mechanism, argv
        return None

    def _candidates(self) -> tuple[tuple[str, list[str]], ...]:
        """Return the helpers worth probing on this platform and session."""
        if self._platform == "darwin":
            return (("pbcopy", ["pbcopy"]),)
        if self._platform == "win32":
            return (("clip", ["clip"]),)
        # Everything else is a POSIX desktop, where the clipboard belongs to
        # the display server. With neither variable set there is no display
        # to write to (ssh, tmux-over-ssh, a CI runner) — return nothing and
        # spawn nothing, because an installed xclip with no DISPLAY blocks
        # until the timeout on every single copy.
        wayland: tuple[tuple[str, list[str]], ...] = (
            (("wl-copy", ["wl-copy"]),) if self._environ.get("WAYLAND_DISPLAY") else ()
        )
        x11: tuple[tuple[str, list[str]], ...] = (
            (
                ("xclip", ["xclip", "-selection", "clipboard"]),
                ("xsel", ["xsel", "-ib"]),
            )
            if self._environ.get("DISPLAY")
            else ()
        )
        return wayland + x11


class InMemoryClipboard:
    """Test fake. Records every payload and returns a canned result."""

    def __init__(
        self,
        *,
        ok: bool = True,
        mechanism: str = "in-memory",
        error_type: str | None = None,
    ) -> None:
        self.writes: list[str] = []
        self.ok = ok
        self.mechanism = mechanism
        self.error_type = error_type

    @property
    def last(self) -> str | None:
        """The most recent payload, or ``None`` if nothing was written."""
        return self.writes[-1] if self.writes else None

    def write(self, text: str) -> ClipboardResult:
        self.writes.append(text)
        return ClipboardResult(ok=self.ok, mechanism=self.mechanism, error_type=self.error_type)


__all__ = [
    "ClipboardPort",
    "ClipboardResult",
    "InMemoryClipboard",
    "NativeClipboard",
]
