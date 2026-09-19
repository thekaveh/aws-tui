"""The copy keystrokes must report what actually happened.

``P`` and ``p`` used to call ``App.copy_to_clipboard`` inside
``contextlib.suppress(Exception)`` and then raise "Copied path"
unconditionally. Textual's ``copy_to_clipboard`` writes OSC 52 and returns
``None`` whether or not the terminal honours it -- macOS Terminal.app never
does, iTerm2 only if the user opted in -- so the suppressed handler was
unreachable and the toast was a guess presented as a fact.

Four outcomes now, each of which a user can act on: the platform helper
took the text; a helper was there and failed; there was no helper, so only
the terminal was written and that is said plainly; or there was no helper
AND the terminal refused, so nothing was copied at all. The decision of
which one happened belongs to ``ClipboardVM`` (pinned in
``tests/unit/vm/test_clipboard_vm.py``); what this file pins is that each
one reaches the user as a distinct, honest toast. The assertion that
matters most: an OSC-52-only delivery is never reported as "copied".
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.clipboard import ClipboardResult, InMemoryClipboard
from aws_tui.ui.widgets.pane import EntryRow
from aws_tui.vm.chrome.toast_vm import ToastLevel
from tests.helpers import drain_workers
from tests.integration.conftest import AppContextBuilder


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _seed() -> InMemoryFS:
    fs = InMemoryFS()
    await fs.write_stream(PathRef(("alpha.txt",)), _stream(b"alpha-content"))
    return fs


def _use_injected_s3_connection(ctx: object) -> None:
    ctx.config_store.path.write_text(  # type: ignore[attr-defined]
        '[defaults]\nconnection = "test"\n\n'
        "[connections.test]\n"
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9000"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
        'region = "us-east-1"\n'
    )


async def _wait_until_entry_rows(app: AwsTuiApp) -> list[EntryRow]:
    for _ in range(100):
        rows = list(app.query(EntryRow))
        if rows:
            return rows
        await asyncio.sleep(0.01)
    return rows


async def _copy_current_path(app: AwsTuiApp) -> None:
    """Press the ``pane.copy_path`` key and let the worker finish.

    The port call is offloaded to a thread inside a Textual worker, so a
    bare ``pilot.pause()`` would assert against the toast stack before the
    write has reported.
    """
    result = app.action_dispatch("pane.copy_path")
    if result is not None:
        await result
    await drain_workers(app)


def _refuse_osc52(_app: AwsTuiApp, _value: str) -> None:
    """Stand in for a terminal write that cannot be encoded.

    ``App.copy_to_clipboard`` base64s ``text.encode("utf-8")``, and a POSIX
    name decoded with ``surrogateescape`` carries lone surrogates, so the
    real call genuinely raises on such a path. Raising here is how the
    fourth outcome -- no helper AND no terminal -- is reachable from a test
    at all.
    """
    raise RuntimeError("OSC 52 unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ok", "mechanism", "error_type", "osc52_raises", "toast_id", "level"),
    [
        pytest.param(
            True,
            "pbcopy",
            None,
            False,
            "clipboard-path",
            ToastLevel.SUCCESS,
            id="native-helper-took-it",
        ),
        pytest.param(
            False,
            "pbcopy",
            "CalledProcessError",
            False,
            "clipboard-failed-path",
            ToastLevel.WARNING,
            id="native-helper-failed",
        ),
        pytest.param(
            False,
            "none",
            None,
            False,
            "clipboard-osc52-path",
            ToastLevel.WARNING,
            id="no-native-helper",
        ),
        pytest.param(
            False,
            "none",
            None,
            True,
            "clipboard-unavailable-path",
            ToastLevel.WARNING,
            id="no-native-helper-and-the-terminal-refused",
        ),
    ],
)
async def test_copy_path_reports_each_outcome_distinctly(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
    ok: bool,
    mechanism: str,
    error_type: str | None,
    osc52_raises: bool,
    toast_id: str,
    level: ToastLevel,
) -> None:
    # Built inside the test, not in the parameter list: a port built at
    # collection time is one shared mutable object for the whole session.
    port = InMemoryClipboard(ok=ok, mechanism=mechanism, error_type=error_type)
    if osc52_raises:
        monkeypatch.setattr(AwsTuiApp, "copy_to_clipboard", _refuse_osc52)
    ctx = app_context_factory(fs=await _seed(), clipboard=port)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()
        assert await _wait_until_entry_rows(app)

        await _copy_current_path(app)
        await pilot.pause()

        dual = ctx.root_vm.content_host.current
        expected = dual.focused_pane.viewmodel.copy_path  # type: ignore[union-attr]
        assert port.writes == [expected], "the port is written on every outcome"

        toast = ctx.root_vm.chrome.toast_stack.toasts[-1].model
        assert toast.id == toast_id
        assert toast.level is level


@pytest.mark.asyncio
async def test_osc52_only_delivery_is_never_reported_as_copied(
    app_context_factory: AppContextBuilder,
) -> None:
    """The regression this whole task exists for.

    With no platform helper the OS clipboard was not written, and OSC 52
    cannot be confirmed, so there is nothing that justifies the word
    "copied". The toast must name the channel it actually used instead.
    """
    port = InMemoryClipboard(ok=False, mechanism="none")
    ctx = app_context_factory(fs=await _seed(), clipboard=port)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()
        assert await _wait_until_entry_rows(app)

        await _copy_current_path(app)
        await pilot.pause()

        text = ctx.root_vm.chrome.toast_stack.toasts[-1].model.text
        assert "copied" not in text.casefold()
        assert "OSC 52" in text


@pytest.mark.asyncio
async def test_native_write_failure_is_logged_without_the_payload(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A helper that failed is a fault worth a durable record.

    The record carries the mechanism and the exception's class name and
    nothing else: a copied path is on occasion a credential or a private
    URI, and this module must never be the thing that writes one to disk.
    """
    port = InMemoryClipboard(ok=False, mechanism="xclip", error_type="TimeoutExpired")
    ctx = app_context_factory(fs=await _seed(), clipboard=port)
    _use_injected_s3_connection(ctx)
    warnings: list[tuple[str, dict[str, object]]] = []
    original = ctx.log_sink.warning

    def _record(event: str, **fields: object) -> None:
        warnings.append((event, dict(fields)))
        original(event, **fields)

    monkeypatch.setattr(ctx.log_sink, "warning", _record)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()
        assert await _wait_until_entry_rows(app)

        await _copy_current_path(app)
        await pilot.pause()

        logged = [entry for entry in warnings if entry[0] == "clipboard.native_write_failed"]
        # Exact equality on the field dict is the payload assertion: these
        # two fields are the whole record, so the copied text cannot be in it.
        assert logged == [
            (
                "clipboard.native_write_failed",
                {"mechanism": "xclip", "error_type": "TimeoutExpired"},
            )
        ]
        assert port.writes, "precondition: the port was actually asked to write"


@pytest.mark.asyncio
async def test_a_refused_terminal_write_is_logged_without_the_payload(
    app_context_factory: AppContextBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OSC 52 leg can fail too, and the app must survive and say so.

    ``copy_to_clipboard`` raising used to be covered in
    ``test_glue_athena_navigation.py``; that axis was replaced by a
    native-helper-failure axis in the same change that ADDED the
    ``try``/``except`` around the terminal write and the fourth toast it
    decides, leaving both uncovered. This is that coverage, in the file
    that owns clipboard reporting.

    The record carries the exception's class name and nothing else, the
    same payload contract ``clipboard.native_write_failed`` keeps: a copied
    path is on occasion a credential.
    """
    port = InMemoryClipboard(ok=False, mechanism="none")
    ctx = app_context_factory(fs=await _seed(), clipboard=port)
    _use_injected_s3_connection(ctx)
    warnings: list[tuple[str, dict[str, object]]] = []
    original = ctx.log_sink.warning

    def _record(event: str, **fields: object) -> None:
        warnings.append((event, dict(fields)))
        original(event, **fields)

    monkeypatch.setattr(ctx.log_sink, "warning", _record)
    monkeypatch.setattr(AwsTuiApp, "copy_to_clipboard", _refuse_osc52)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()
        assert await _wait_until_entry_rows(app)

        await _copy_current_path(app)
        await pilot.pause()

        logged = [entry for entry in warnings if entry[0] == "clipboard.osc52_write_failed"]
        assert logged == [("clipboard.osc52_write_failed", {"error_type": "RuntimeError"})]
        assert app._crash_report is None, (  # type: ignore[attr-defined]
            f"a refused terminal write must not crash the app: {app._crash_report}"  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_nothing_selected_advises_through_the_toast_stack(
    app_context_factory: AppContextBuilder,
) -> None:
    """``self.notify`` wrecks the footer, so the advisory is a toast.

    An empty listing has no cursor entry, so ``p`` has nothing to copy.
    It must say so and it must not write either channel.
    """
    port = InMemoryClipboard()
    ctx = app_context_factory(clipboard=port)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await drain_workers(app)
        await pilot.pause()

        dual = ctx.root_vm.content_host.current
        pane = dual.focused_pane  # type: ignore[union-attr]
        assert pane is not None
        assert pane.viewmodel.copy_selected_path is None, "precondition: nothing under the cursor"

        result = app.action_dispatch("pane.copy_entry_path")
        if result is not None:
            await result
        await drain_workers(app)
        await pilot.pause()

        assert port.writes == []
        assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == "clipboard-nothing-selected"


class _WedgedClipboard:
    """A port whose ``write`` blocks until the test lets it go.

    An ``xclip`` against a stalled X server, a ``pbcopy`` waiting on a hung
    pasteboard server: the call does return, but only after the port's own
    ~2 s timeout. Whatever awaits it for that long is unavailable for that
    long, which is the whole question this fake exists to ask.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.writes: list[str] = []

    def write(self, text: str) -> ClipboardResult:
        self.entered.set()
        # Bounded so a regression cannot hang the suite: the assertions
        # below fail long before this expires.
        self.release.wait(timeout=30.0)
        self.writes.append(text)
        return ClipboardResult(ok=True, mechanism="pbcopy")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pane.copy_path", "pane.copy_entry_path"])
async def test_copy_keystroke_does_not_wait_on_the_helper(
    app_context_factory: AppContextBuilder,
    action: str,
) -> None:
    """``p``/``P`` must hand the write to a worker, not await it inline.

    Offloading the port call to a thread keeps the *event loop* spinning; it
    does not keep the App's *message pump* free, and the pump is where every
    subsequent key event -- ``ctrl+q`` included -- is dequeued. An action
    handler that awaits the write blocks the pump for the helper's full
    timeout, which is exactly the unresponsive window this branch exists to
    remove, and it would do it on the keyboard path only: the border click
    and the Glue ``y`` path already go through the worker seam.

    So the assertion is an ordering one, not a timing one: the handler is
    finished while the helper is still wedged.
    """
    port = _WedgedClipboard()
    ctx = app_context_factory(fs=await _seed(), clipboard=port)
    _use_injected_s3_connection(ctx)
    app = AwsTuiApp(ctx)
    async with app.run_test(size=(120, 40)) as pilot:
        try:
            await pilot.pause()
            await drain_workers(app)
            await pilot.pause()
            assert await _wait_until_entry_rows(app)

            result = app.action_dispatch(action)
            if result is not None:
                # A regression makes this await the wedged helper, so the
                # timeout turns a freeze into a failure instead of a hang.
                await asyncio.wait_for(result, timeout=10.0)

            # The pump is free again: a later message is still serviced.
            await asyncio.wait_for(pilot.pause(), timeout=10.0)

            for _ in range(500):
                if port.entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert port.entered.is_set(), "precondition: the worker reached the helper"
            assert port.writes == [], "the handler returned before the write completed"
        finally:
            port.release.set()
        await drain_workers(app)
        await pilot.pause()

        assert len(port.writes) == 1, "and the deferred write still lands"
