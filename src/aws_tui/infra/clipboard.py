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
:func:`shutil.which` and spawn *the path it returned* rather than the bare
name, argv lists only, never ``shell=True``, an explicit timeout,
``check=False``, and both streams to ``DEVNULL``.
"""

from __future__ import annotations

import ntpath
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

# POSIX helpers get the filesystem's own bytes. A payload here is a path,
# and ``Path.iterdir`` decodes names that are not valid UTF-8 with
# ``surrogateescape`` (``domain/local_fs.py``), so a POSIX file whose name
# holds raw high bytes arrives as a ``str`` carrying lone surrogates.
# ``surrogateescape`` on the way out hands those exact bytes back, making
# the paste byte-identical to the name on disk instead of an error.
_POSIX_ENCODING: Final[str] = "utf-8"
_POSIX_ERRORS: Final[str] = "surrogateescape"

# Windows. UTF-8 is wrong for clip.exe; UTF-16LE with a leading BOM is the
# encoding the known-good PowerShell recipe produces, because .NET emits the
# codec preamble. NOBODY HERE HAS OBSERVED THIS: no macOS or Linux box and
# no test in this suite can drive clip.exe, so treat it as reasoned, not
# measured. The BOM is the weakly dominant guess — if clip.exe keys on the
# preamble it prevents a silent truncation at the first embedded NUL; if it
# keys on ``IsTextUnicode`` the signature flag makes detection definitive
# rather than statistical; and if it only ever does a codepage conversion
# then the output was already wrong and a stray visible glyph at least fails
# loudly. See the clipboard step in ``docs/RELEASING.md``.
_WINDOWS_ENCODING: Final[str] = "utf-16-le"
_WINDOWS_BOM: Final[str] = "\ufeff"

# ``%SystemRoot%`` is always set on Windows; the literal is the last resort.
_WINDOWS_SYSTEM_ROOT: Final[str] = "C:\\Windows"

# No helper was found, or the session has no display to own a clipboard.
_NO_MECHANISM: Final[str] = "none"

# A non-zero exit is not an exception here (``check=False``), but it is the
# condition ``subprocess`` names ``CalledProcessError``. Reporting that name
# keeps ``error_type`` a class name and nothing else.
_NONZERO_EXIT: Final[str] = "CalledProcessError"


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    """What one clipboard write actually did.

    ``mechanism`` is the helper this write was handed to — ``"pbcopy"``,
    ``"clip"``, ``"wl-copy"``, ``"xclip"``, ``"xsel"`` — or ``"none"`` when
    there was nothing to run. It names the helper even when the failure
    happened before the spawn (an unencodable payload), because "which
    helper would have been used" is what the caller has to report.

    ``error_type`` is an exception class name and never, under any
    circumstance, the payload: the whole point of a clipboard is that its
    contents are the user's, and a copied secret must not land in a log
    file.
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
    # Fixed argv, no shell, and argv[0] is the absolute path _resolve()
    # took from which() — the spawn does not re-search PATH for a bare name.
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
        try:
            # The encode is INSIDE the try on purpose. UnicodeEncodeError is a
            # ValueError, not an OSError, so an unencodable payload used to
            # escape write() and break the "always returns a result" contract
            # — and its message quotes a character of the payload, which is
            # exactly what this module refuses to disclose.
            payload = self._encode(text)
            returncode = self._run(argv, payload)
        except (subprocess.TimeoutExpired, OSError, UnicodeEncodeError) as exc:
            # FileNotFoundError and PermissionError are OSError subclasses;
            # ``type(exc).__name__`` keeps each one's precise name and never
            # the exception's message.
            return ClipboardResult(ok=False, mechanism=mechanism, error_type=type(exc).__name__)
        if returncode != 0:
            return ClipboardResult(ok=False, mechanism=mechanism, error_type=_NONZERO_EXIT)
        return ClipboardResult(ok=True, mechanism=mechanism)

    def _encode(self, text: str) -> bytes:
        """Return the bytes to feed the helper on this platform."""
        if self._platform == "win32":
            return (_WINDOWS_BOM + text).encode(_WINDOWS_ENCODING)
        return text.encode(_POSIX_ENCODING, errors=_POSIX_ERRORS)

    def _resolve(self) -> tuple[str, list[str]] | None:
        """Return the first installed candidate, or ``None`` for none.

        The path :func:`shutil.which` returned becomes ``argv[0]``, so the
        spawn runs the exact file the probe accepted. Passing the bare name
        on would make the OS repeat a *different* search at spawn time —
        a TOCTOU the comment in :func:`_run` used to claim was closed.
        """
        for mechanism, argv in self._candidates():
            resolved = self._which(argv[0])
            if resolved is not None:
                return mechanism, [resolved, *argv[1:]]
        return None

    def _candidates(self) -> tuple[tuple[str, list[str]], ...]:
        """Return the helpers worth probing on this platform and session."""
        if self._platform == "darwin":
            return (("pbcopy", ["pbcopy"]),)
        if self._platform == "win32":
            return (("clip", [self._windows_clip_path()]),)
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

    def _windows_clip_path(self) -> str:
        """Return the absolute ``clip.exe`` under ``%SystemRoot%\\System32``.

        Never the bare name ``"clip"``. :func:`shutil.which` inserts
        ``os.curdir`` at the front of the search path on win32, and
        ``CreateProcess`` with a NULL ``lpApplicationName`` likewise searches
        the current directory before ``System32`` — so a bare name would let
        a ``clip.exe`` sitting in whatever directory aws-tui happened to be
        launched from receive the payload (CWE-426, untrusted search path),
        and the payload is on occasion a credential or a private URI.
        Naming the directory takes both the CWD and the ambient ``PATH`` out
        of the probe *and* the spawn: :func:`shutil.which` given a path with
        a directory part searches that one directory and nothing else.
        """
        system_root = self._environ.get("SystemRoot") or _WINDOWS_SYSTEM_ROOT
        return ntpath.join(system_root, "System32", "clip.exe")


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
