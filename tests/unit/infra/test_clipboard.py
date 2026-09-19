"""Unit tests for the native clipboard port.

Nothing here ever spawns a real helper. ``pbcopy``, ``clip``, ``wl-copy``,
``xclip`` and ``xsel`` are all reached through injected fakes, so every
platform's resolution is covered from any one machine and a headless CI
runner that happens to ship ``xclip`` cannot hang a test for two seconds
per copy.
"""

from __future__ import annotations

import logging
import ntpath
import posixpath
import subprocess
from typing import Any

import pytest

from aws_tui.infra import clipboard as clipboard_module
from aws_tui.infra.clipboard import (
    ClipboardPort,
    ClipboardResult,
    InMemoryClipboard,
    NativeClipboard,
)

SECRET = "s3://bucket/kéy-with-ünicode"

# The absolute path the win32 branch must probe and spawn. A bare "clip"
# would be searched for in the current directory first, by shutil.which and
# by CreateProcess alike.
CLIP = "C:\\Windows\\System32\\clip.exe"
WINDOWS_ENVIRON = {"SystemRoot": "C:\\Windows"}

# A POSIX file name whose bytes are not valid UTF-8. ``Path.iterdir`` decodes
# it with surrogateescape, so this is the exact ``str`` a pane VM hands the
# port for a file called ``caf<0xe9>.txt`` on ext4, NFS or an old archive.
SURROGATE_PATH = "/tmp/caf\udce9.txt"
SURROGATE_BYTES = b"/tmp/caf\xe9.txt"

# A lone HIGH surrogate — outside the U+DC80..U+DCFF window surrogateescape
# can round-trip, so no encoder on any platform accepts it.
UNENCODABLE = "/tmp/\ud83d-broken.txt"


class FakeWhich:
    """Resolves only the named helpers; records every probe.

    Mirrors the one ``shutil.which`` rule the win32 candidate leans on: a
    name carrying a directory part is looked up in that directory alone and
    never through ``PATH`` — nor, on win32, through ``os.curdir``.
    """

    def __init__(self, *installed: str) -> None:
        self.installed = set(installed)
        self.probes: list[str] = []

    def __call__(self, name: str) -> str | None:
        self.probes.append(name)
        if name not in self.installed:
            return None
        if ntpath.dirname(name) or posixpath.dirname(name):
            return name
        return f"/usr/bin/{name}"


class FakeRun:
    """Stands in for the subprocess seam; records every invocation."""

    def __init__(self, *, returncode: int = 0, raises: BaseException | None = None) -> None:
        self.returncode = returncode
        self.raises = raises
        self.calls: list[tuple[list[str], bytes]] = []

    def __call__(self, argv: list[str], payload: bytes) -> int:
        self.calls.append((argv, payload))
        if self.raises is not None:
            raise self.raises
        return self.returncode


def _port(
    *,
    platform: str,
    which: FakeWhich,
    run: FakeRun,
    environ: dict[str, str] | None = None,
) -> NativeClipboard:
    return NativeClipboard(
        which=which,
        run=run,
        platform=platform,
        environ={} if environ is None else environ,
    )


class TestCandidateResolution:
    def test_darwin_uses_pbcopy_with_utf8(self) -> None:
        which, run = FakeWhich("pbcopy"), FakeRun()
        result = _port(platform="darwin", which=which, run=run).write(SECRET)
        assert result == ClipboardResult(ok=True, mechanism="pbcopy")
        assert run.calls == [(["/usr/bin/pbcopy"], SECRET.encode("utf-8"))]

    def test_darwin_ignores_a_display_variable(self) -> None:
        # A macOS session under X11 forwarding still owns a real clipboard.
        which, run = FakeWhich("pbcopy", "xclip"), FakeRun()
        port = _port(platform="darwin", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "pbcopy"

    def test_win32_uses_clip_with_bom_prefixed_utf16le(self) -> None:
        # What this actually pins is the ENCODING SELECTION: BOM-prefixed
        # UTF-16LE rather than UTF-8. It does NOT and cannot demonstrate what
        # clip.exe does with those bytes — nothing on a macOS or Linux box,
        # and no test in this suite, can drive clip.exe. The Windows leg of
        # the manual smoke list in docs/RELEASING.md is the only real check.
        which, run = FakeWhich(CLIP), FakeRun()
        port = _port(platform="win32", which=which, run=run, environ=WINDOWS_ENVIRON)
        assert port.write(SECRET) == ClipboardResult(ok=True, mechanism="clip")
        argv, payload = run.calls[0]
        assert argv == [CLIP]
        assert payload.startswith(b"\xff\xfe")
        assert payload == ("\ufeff" + SECRET).encode("utf-16-le")
        assert payload != SECRET.encode("utf-8")
        assert payload.decode("utf-16").removeprefix("\ufeff") == SECRET

    def test_win32_probes_and_spawns_clip_only_out_of_system32(self) -> None:
        # CWE-426. shutil.which prepends os.curdir to the search path on
        # win32 and CreateProcess with a NULL lpApplicationName searches the
        # current directory too, so a bare "clip" would let a clip.exe in the
        # launch directory receive the payload. Naming the directory takes
        # both the CWD and the ambient PATH out of the probe and the spawn.
        which, run = FakeWhich(CLIP), FakeRun()
        port = _port(platform="win32", which=which, run=run, environ=WINDOWS_ENVIRON)
        assert port.write(SECRET).ok is True
        assert which.probes == [CLIP]
        assert run.calls[0][0] == [CLIP]
        assert all(ntpath.dirname(probe) for probe in which.probes)

    def test_win32_follows_a_relocated_system_root(self) -> None:
        relocated = "D:\\WinNT\\System32\\clip.exe"
        which, run = FakeWhich(relocated), FakeRun()
        port = _port(platform="win32", which=which, run=run, environ={"SystemRoot": "D:\\WinNT"})
        assert port.write(SECRET).ok is True
        assert which.probes == [relocated]

    def test_the_path_which_resolved_is_what_gets_spawned(self) -> None:
        # The probe and the spawn must agree on one file. Handing the bare
        # name to subprocess.run would make the OS repeat a different search.
        which, run = FakeWhich("xclip"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).ok is True
        assert which.probes == ["xclip"]
        assert run.calls[0][0][0] == "/usr/bin/xclip"

    def test_wayland_prefers_wl_copy(self) -> None:
        which, run = FakeWhich("wl-copy", "xclip"), FakeRun()
        port = _port(
            platform="linux",
            which=which,
            run=run,
            environ={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
        )
        assert port.write(SECRET).mechanism == "wl-copy"
        assert run.calls == [(["/usr/bin/wl-copy"], SECRET.encode("utf-8"))]

    def test_wayland_without_wl_copy_falls_through_to_xclip(self) -> None:
        which, run = FakeWhich("xclip"), FakeRun()
        port = _port(
            platform="linux",
            which=which,
            run=run,
            environ={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
        )
        assert port.write(SECRET).mechanism == "xclip"
        assert run.calls == [
            (["/usr/bin/xclip", "-selection", "clipboard"], SECRET.encode("utf-8"))
        ]

    def test_x11_uses_xclip_with_the_clipboard_selection(self) -> None:
        which, run = FakeWhich("xclip", "xsel"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "xclip"
        assert run.calls == [
            (["/usr/bin/xclip", "-selection", "clipboard"], SECRET.encode("utf-8"))
        ]

    def test_x11_falls_back_to_xsel(self) -> None:
        which, run = FakeWhich("xsel"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "xsel"
        assert run.calls == [(["/usr/bin/xsel", "-ib"], SECRET.encode("utf-8"))]

    def test_wayland_only_never_probes_the_x11_helpers(self) -> None:
        which, run = FakeWhich("wl-copy"), FakeRun()
        port = _port(
            platform="linux",
            which=which,
            run=run,
            environ={"WAYLAND_DISPLAY": "wayland-0"},
        )
        assert port.write(SECRET).mechanism == "wl-copy"
        assert which.probes == ["wl-copy"]

    def test_headless_reports_none_without_probing_or_spawning(self) -> None:
        # An installed xclip with no DISPLAY blocks until the timeout, so a
        # headless runner must not reach the probe at all.
        which, run = FakeWhich("wl-copy", "xclip", "xsel"), FakeRun()
        result = _port(platform="linux", which=which, run=run, environ={}).write(SECRET)
        assert result == ClipboardResult(ok=False, mechanism="none", error_type=None)
        assert which.probes == []
        assert run.calls == []

    def test_empty_display_string_counts_as_headless(self) -> None:
        which, run = FakeWhich("xclip"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ""})
        assert port.write(SECRET).mechanism == "none"
        assert run.calls == []

    def test_missing_helper_reports_none_without_spawning(self) -> None:
        which, run = FakeWhich(), FakeRun()
        result = _port(platform="darwin", which=which, run=run).write(SECRET)
        assert result == ClipboardResult(ok=False, mechanism="none", error_type=None)
        assert which.probes == ["pbcopy"]
        assert run.calls == []

    def test_unknown_posix_platform_still_resolves_by_session(self) -> None:
        which, run = FakeWhich("xclip"), FakeRun()
        port = _port(platform="freebsd14", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "xclip"


class TestFailureMapping:
    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (FileNotFoundError(2, "No such file or directory"), "FileNotFoundError"),
            (PermissionError(13, "Permission denied"), "PermissionError"),
            (subprocess.TimeoutExpired(cmd=["pbcopy"], timeout=2.0), "TimeoutExpired"),
            (OSError("generic failure"), "OSError"),
        ],
    )
    def test_exceptions_map_to_their_class_name(self, raised: BaseException, expected: str) -> None:
        which, run = FakeWhich("pbcopy"), FakeRun(raises=raised)
        result = _port(platform="darwin", which=which, run=run).write(SECRET)
        assert result == ClipboardResult(ok=False, mechanism="pbcopy", error_type=expected)

    def test_non_zero_exit_is_a_failure(self) -> None:
        which, run = FakeWhich("xsel"), FakeRun(returncode=1)
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET) == ClipboardResult(
            ok=False, mechanism="xsel", error_type="CalledProcessError"
        )

    def test_failure_keeps_the_mechanism_that_was_tried(self) -> None:
        which, run = FakeWhich(CLIP), FakeRun(raises=PermissionError())
        port = _port(platform="win32", which=which, run=run, environ=WINDOWS_ENVIRON)
        result = port.write(SECRET)
        assert result.mechanism == "clip"
        assert result.ok is False

    @pytest.mark.parametrize(
        ("platform", "environ", "installed"),
        [
            ("darwin", {}, "pbcopy"),
            ("win32", WINDOWS_ENVIRON, CLIP),
            ("linux", {"DISPLAY": ":0"}, "xclip"),
        ],
    )
    def test_an_unencodable_payload_returns_a_result_instead_of_raising(
        self, platform: str, environ: dict[str, str], installed: str
    ) -> None:
        # UnicodeEncodeError is a ValueError, NOT an OSError, so it used to
        # escape write() entirely and break the contract that this port
        # always reports what happened. Task 6 awaits write() inside an async
        # action handler with no try of its own — an escape there is a
        # Textual crash report, on the one path whose whole purpose is to
        # stop copy lying.
        which, run = FakeWhich(installed), FakeRun()
        port = _port(platform=platform, which=which, run=run, environ=environ)
        result = port.write(UNENCODABLE)
        assert result.ok is False
        assert result.error_type == "UnicodeEncodeError"
        assert result.mechanism != "none"
        assert run.calls == []

    def test_posix_hands_over_the_filesystem_bytes_byte_for_byte(self) -> None:
        # A path from Path.iterdir() whose name is not valid UTF-8 arrives
        # carrying lone surrogates. surrogateescape gives the original bytes
        # back, so the paste matches the name on disk instead of failing.
        which, run = FakeWhich("xclip"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SURROGATE_PATH) == ClipboardResult(ok=True, mechanism="xclip")
        assert run.calls[0][1] == SURROGATE_BYTES

    def test_win32_reports_a_surrogate_path_it_cannot_encode(self) -> None:
        # utf-16-le has no surrogateescape handler, so the POSIX rescue does
        # not exist here. The honest outcome is a result, not an exception.
        which, run = FakeWhich(CLIP), FakeRun()
        port = _port(platform="win32", which=which, run=run, environ=WINDOWS_ENVIRON)
        assert port.write(SURROGATE_PATH) == ClipboardResult(
            ok=False, mechanism="clip", error_type="UnicodeEncodeError"
        )


class TestPayloadIsNeverDisclosed:
    @pytest.mark.parametrize(
        "run",
        [
            FakeRun(),
            FakeRun(returncode=1),
            FakeRun(raises=PermissionError(13, "Permission denied")),
            FakeRun(raises=subprocess.TimeoutExpired(cmd=["pbcopy"], timeout=2.0)),
        ],
    )
    def test_no_outcome_puts_the_payload_in_a_log_record(
        self, run: FakeRun, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        result = _port(platform="darwin", which=FakeWhich("pbcopy"), run=run).write(SECRET)
        assert SECRET not in caplog.text
        assert all(SECRET not in record.getMessage() for record in caplog.records)
        # The result itself is what the caller logs, so it must be clean too.
        assert SECRET not in repr(result)
        assert result.error_type in (
            None,
            "CalledProcessError",
            "PermissionError",
            "TimeoutExpired",
        )

    def test_an_encode_failure_discloses_no_fragment_of_the_payload(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # UnicodeEncodeError's own message quotes the offending character and
        # its position. Only the class name may survive into the result.
        caplog.set_level(logging.DEBUG)
        which, run = FakeWhich("pbcopy"), FakeRun()
        result = _port(platform="darwin", which=which, run=run).write(UNENCODABLE)
        assert result.error_type == "UnicodeEncodeError"
        assert "\ud83d" not in repr(result)
        assert "broken" not in repr(result)
        assert caplog.text == ""

    def test_error_type_carries_no_exception_message(self) -> None:
        which = FakeWhich("pbcopy")
        run = FakeRun(raises=PermissionError(13, f"cannot write {SECRET}"))
        result = _port(platform="darwin", which=which, run=run).write(SECRET)
        assert result.error_type == "PermissionError"


class TestSubprocessContract:
    def test_the_real_runner_obeys_the_subprocess_rules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_subprocess_run(argv: Any, **kwargs: Any) -> Any:
            captured["argv"] = argv
            captured["kwargs"] = kwargs

            class _Completed:
                returncode = 0

            return _Completed()

        monkeypatch.setattr(clipboard_module.subprocess, "run", fake_subprocess_run)
        # Real ``run`` seam, faked helper lookup: nothing is spawned.
        port = NativeClipboard(which=FakeWhich("pbcopy"), platform="darwin", environ={})
        assert port.write(SECRET).ok is True

        assert captured["argv"] == ["/usr/bin/pbcopy"]
        kwargs = captured["kwargs"]
        assert kwargs["input"] == SECRET.encode("utf-8")
        assert kwargs["timeout"] == pytest.approx(2.0)
        assert kwargs["check"] is False
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert "shell" not in kwargs

    def test_a_default_port_probes_with_shutil_which(self, monkeypatch: pytest.MonkeyPatch) -> None:
        probed: list[str] = []

        def fake_which(name: str) -> str | None:
            probed.append(name)
            return None

        monkeypatch.setattr(clipboard_module.shutil, "which", fake_which)
        result = NativeClipboard(platform="darwin", environ={}).write(SECRET)
        assert probed == ["pbcopy"]
        assert result.mechanism == "none"


class TestInMemoryClipboard:
    def test_records_every_payload_in_order(self) -> None:
        fake = InMemoryClipboard()
        fake.write("first")
        fake.write("second")
        assert fake.writes == ["first", "second"]
        assert fake.last == "second"

    def test_last_is_none_before_any_write(self) -> None:
        assert InMemoryClipboard().last is None

    def test_reports_success_by_default(self) -> None:
        assert InMemoryClipboard().write(SECRET) == ClipboardResult(ok=True, mechanism="in-memory")

    def test_can_be_configured_to_fail(self) -> None:
        fake = InMemoryClipboard(ok=False, mechanism="xclip", error_type="TimeoutExpired")
        assert fake.write(SECRET) == ClipboardResult(
            ok=False, mechanism="xclip", error_type="TimeoutExpired"
        )
        assert fake.writes == [SECRET]


class TestPortShape:
    @pytest.mark.parametrize("port", [NativeClipboard(), InMemoryClipboard()])
    def test_implementations_satisfy_the_protocol(self, port: ClipboardPort) -> None:
        assert isinstance(port, ClipboardPort)

    def test_result_is_frozen_and_slotted(self) -> None:
        result = ClipboardResult(ok=True, mechanism="pbcopy")
        assert result.error_type is None
        assert not hasattr(result, "__dict__")
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]
