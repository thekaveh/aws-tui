"""Unit tests for the native clipboard port.

Nothing here ever spawns a real helper. ``pbcopy``, ``clip``, ``wl-copy``,
``xclip`` and ``xsel`` are all reached through injected fakes, so every
platform's resolution is covered from any one machine and a headless CI
runner that happens to ship ``xclip`` cannot hang a test for two seconds
per copy.
"""

from __future__ import annotations

import logging
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


class FakeWhich:
    """Resolves only the named helpers; records every probe."""

    def __init__(self, *installed: str) -> None:
        self.installed = set(installed)
        self.probes: list[str] = []

    def __call__(self, name: str) -> str | None:
        self.probes.append(name)
        return f"/usr/bin/{name}" if name in self.installed else None


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
        assert run.calls == [(["pbcopy"], SECRET.encode("utf-8"))]

    def test_darwin_ignores_a_display_variable(self) -> None:
        # A macOS session under X11 forwarding still owns a real clipboard.
        which, run = FakeWhich("pbcopy", "xclip"), FakeRun()
        port = _port(platform="darwin", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "pbcopy"

    def test_win32_uses_clip_with_utf16le(self) -> None:
        # clip.exe mangles non-ASCII fed as UTF-8. This is the only guard
        # for that, and it is unobservable on a macOS or Linux box.
        which, run = FakeWhich("clip"), FakeRun()
        result = _port(platform="win32", which=which, run=run).write(SECRET)
        assert result == ClipboardResult(ok=True, mechanism="clip")
        argv, payload = run.calls[0]
        assert argv == ["clip"]
        assert payload == SECRET.encode("utf-16-le")
        assert payload != SECRET.encode("utf-8")
        assert payload.decode("utf-16-le") == SECRET

    def test_wayland_prefers_wl_copy(self) -> None:
        which, run = FakeWhich("wl-copy", "xclip"), FakeRun()
        port = _port(
            platform="linux",
            which=which,
            run=run,
            environ={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
        )
        assert port.write(SECRET).mechanism == "wl-copy"
        assert run.calls == [(["wl-copy"], SECRET.encode("utf-8"))]

    def test_wayland_without_wl_copy_falls_through_to_xclip(self) -> None:
        which, run = FakeWhich("xclip"), FakeRun()
        port = _port(
            platform="linux",
            which=which,
            run=run,
            environ={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
        )
        assert port.write(SECRET).mechanism == "xclip"
        assert run.calls == [(["xclip", "-selection", "clipboard"], SECRET.encode("utf-8"))]

    def test_x11_uses_xclip_with_the_clipboard_selection(self) -> None:
        which, run = FakeWhich("xclip", "xsel"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "xclip"
        assert run.calls == [(["xclip", "-selection", "clipboard"], SECRET.encode("utf-8"))]

    def test_x11_falls_back_to_xsel(self) -> None:
        which, run = FakeWhich("xsel"), FakeRun()
        port = _port(platform="linux", which=which, run=run, environ={"DISPLAY": ":0"})
        assert port.write(SECRET).mechanism == "xsel"
        assert run.calls == [(["xsel", "-ib"], SECRET.encode("utf-8"))]

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
        which, run = FakeWhich("clip"), FakeRun(raises=PermissionError())
        result = _port(platform="win32", which=which, run=run).write(SECRET)
        assert result.mechanism == "clip"
        assert result.ok is False


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

        assert captured["argv"] == ["pbcopy"]
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
