"""The copy keystrokes must report what actually happened.

``P`` and ``p`` used to call ``App.copy_to_clipboard`` inside
``contextlib.suppress(Exception)`` and then raise "Copied path"
unconditionally. Textual's ``copy_to_clipboard`` writes OSC 52 and returns
``None`` whether or not the terminal honours it -- macOS Terminal.app never
does, iTerm2 only if the user opted in -- so the suppressed handler was
unreachable and the toast was a guess presented as a fact.

Three outcomes now, each of which a user can act on: the platform helper
took the text; a helper was there and failed; there was no helper, so only
the terminal was written and that is said plainly. The fourth assertion
this file makes is the one that matters most: an OSC-52-only delivery is
never reported as "copied".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.clipboard import InMemoryClipboard
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ok", "mechanism", "error_type", "toast_id", "level"),
    [
        pytest.param(
            True, "pbcopy", None, "clipboard-path", ToastLevel.SUCCESS, id="native-helper-took-it"
        ),
        pytest.param(
            False,
            "pbcopy",
            "CalledProcessError",
            "clipboard-failed-path",
            ToastLevel.WARNING,
            id="native-helper-failed",
        ),
        pytest.param(
            False, "none", None, "clipboard-osc52-path", ToastLevel.WARNING, id="no-native-helper"
        ),
    ],
)
async def test_copy_path_reports_each_outcome_distinctly(
    app_context_factory: AppContextBuilder,
    ok: bool,
    mechanism: str,
    error_type: str | None,
    toast_id: str,
    level: ToastLevel,
) -> None:
    # Built inside the test, not in the parameter list: a port built at
    # collection time is one shared mutable object for the whole session.
    port = InMemoryClipboard(ok=ok, mechanism=mechanism, error_type=error_type)
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
