"""Keystone wiring: the App installs BindingResolver-materialized bindings.

Guards that routing bindings through KeymapStore + BindingResolver +
ActionRegistry preserves dispatch targets while ``[keybindings]`` overlays
take effect. Bare printable keys yield to editors; modified and named keys
retain priority routing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.app import AwsTuiApp
from aws_tui.domain.data_catalog import TableFormat
from aws_tui.vm.glue.iceberg_vm import GlueIcebergVM
from aws_tui.vm.messages import OpenAthenaTableRequest
from tests.unit.vm.glue.test_iceberg_vm import ICEBERG_REF, RecordingInspector

# The full set the App must install under the default keymap: our
# resolver-materialized bindings (dispatch form, Textual key names) plus
# Textual's built-in ctrl+q (alt-quit) and ctrl+p (command palette) that
# survive super().__init__(). (key, action, show, priority).
_EXPECTED: set[tuple[str, str, bool, bool]] = {
    ("q", "dispatch('app.quit')", True, False),
    ("ctrl+c", "dispatch('app.quit')", False, False),
    ("tab", "dispatch('pane.switch_focus')", True, True),
    ("shift+tab", "dispatch('pane.switch_focus_back')", False, True),
    ("up", "dispatch('pane.move_up')", True, True),
    ("k", "dispatch('pane.move_up')", False, False),
    ("down", "dispatch('pane.move_down')", True, True),
    ("j", "dispatch('pane.move_down')", False, False),
    ("enter", "dispatch('pane.descend')", True, True),
    ("backspace", "dispatch('pane.ascend')", True, True),
    ("left", "dispatch('pane.modal_left')", False, True),
    ("right", "dispatch('pane.modal_right')", False, True),
    ("r", "dispatch('pane.refresh')", True, False),
    ("question_mark", "dispatch('app.help')", True, False),
    ("colon", "dispatch('app.command_palette')", True, False),
    ("ctrl+k", "dispatch('app.command_palette')", False, True),
    ("t", "dispatch('app.themes')", True, False),
    ("T", "dispatch('app.cycle_theme')", True, False),
    ("comma", "dispatch('app.open_settings')", True, False),
    ("c", "dispatch('pane.copy')", True, False),
    ("d", "dispatch('pane.delete')", True, False),
    ("S", "dispatch('app.swap_source')", True, False),
    ("A", "dispatch('emr.next_application')", True, False),
    ("1", "dispatch('glue.catalog')", False, False),
    ("2", "dispatch('glue.jobs')", False, False),
    ("3", "dispatch('glue.crawlers')", False, False),
    ("V", "dispatch('glue.time_travel_in_athena')", False, False),
    ("1", "dispatch('athena.query')", False, False),
    ("2", "dispatch('athena.history')", False, False),
    ("3", "dispatch('athena.results')", False, False),
    ("4", "dispatch('athena.saved')", False, False),
    ("ctrl+enter", "dispatch('athena.execute')", False, True),
    ("escape", "dispatch('athena.cancel')", False, False),
    ("l", "dispatch('athena.load_more')", False, False),
    ("shift+up", "dispatch('pane.mark_up')", False, True),
    ("shift+down", "dispatch('pane.mark_down')", False, True),
    ("space", "dispatch('pane.quick_look')", False, False),
    ("ctrl+q", "quit", False, True),
    ("ctrl+p", "command_palette", False, True),
}


def _installed(app: AwsTuiApp) -> set[tuple[str, str, bool, bool]]:
    out: set[tuple[str, str, bool, bool]] = set()
    for key, binds in app._bindings.key_to_bindings.items():
        for b in binds:
            out.add((key, b.action, b.show, b.priority))
    return out


def test_default_bindings_match_runtime_contract(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    assert _installed(app) == _EXPECTED


def test_no_handlerless_keys_bound(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    keys = set(app._bindings.key_to_bindings)
    # Deferred (handlerless) actions' keys must NOT be bound: filter (slash),
    # enter_multiselect (v), select_all/authenticate (a), move (m), new (n).
    # (`space`->quick_look and `:`/`ctrl+k`->command_palette are now wired.)
    for k in ("slash", "v", "a", "m", "n"):
        assert k not in keys, f"{k} should be unbound (handlerless)"


def test_dispatch_invokes_registered_handler(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    app = AwsTuiApp(app_context_factory())
    calls: list[str] = []
    app._actions.register("pane.copy", lambda: calls.append("copy"))
    app.action_dispatch("pane.copy")
    assert calls == ["copy"]


@pytest.mark.asyncio
async def test_global_v_dispatch_uses_active_snapshot_action_guard(
    app_context_factory,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = app_context_factory()
    hub: MessageHub[Message] = MessageHub()
    inspector = RecordingInspector()
    iceberg = GlueIcebergVM(
        inspector=inspector,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    iceberg.construct()
    await iceberg.bind_table(ICEBERG_REF, table_format=TableFormat.ICEBERG)
    await iceberg.select_view("snapshots")
    assert iceberg.select_snapshot(43)
    page = SimpleNamespace(vm=SimpleNamespace(time_travel_in_athena=iceberg.time_travel_in_athena))
    received: list[OpenAthenaTableRequest] = []
    subscription = hub.messages.subscribe(
        on_next=lambda message: (
            received.append(message) if isinstance(message, OpenAthenaTableRequest) else None
        )
    )
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(100, 30)) as pilot:
        monkeypatch.setattr(app, "_glue_page", lambda: page)
        assert ("V", "dispatch('glue.time_travel_in_athena')", False, False) in _installed(app)

        await pilot.press("V")
        await pilot.pause()
        assert received == [OpenAthenaTableRequest(table_ref=ICEBERG_REF, snapshot_id=43)]

        await iceberg.select_view("history")
        await pilot.press("V")
        await pilot.pause()

        assert received == [OpenAthenaTableRequest(table_ref=ICEBERG_REF, snapshot_id=43)]
        assert ctx.root_vm.chrome.toast_stack.toasts[-1].model.id == (
            "glue-athena-snapshot-unavailable"
        )

    subscription.dispose()
    iceberg.dispose()


def test_overlay_remaps_a_handled_action() -> None:
    from aws_tui.infra.keymap_store import KeymapStore
    from aws_tui.ui.actions import ActionRegistry
    from aws_tui.ui.bindings import BindingResolver

    keymap = KeymapStore(overlay={"pane.copy": "y"})
    actions = ActionRegistry()
    actions.register("pane.copy", lambda: None)
    resolver = BindingResolver(keymap=keymap, actions=actions)
    keys = {b.key for b in resolver.to_textual_bindings()}
    assert "y" in keys
    assert "c" not in keys


@pytest.mark.asyncio
async def test_priority_tab_binding_fires_at_runtime(app_context_factory) -> None:  # type: ignore[no-untyped-def]
    # The "Tab does nothing" regression: without priority, Textual's Screen
    # consumes tab for focus traversal before the App binding fires. Pressing
    # tab must reach our dispatch -> switch_focus handler, proving the priority
    # binding is installed AND honored at runtime.
    app = AwsTuiApp(app_context_factory())
    calls: list[str] = []
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._actions.register("pane.switch_focus", lambda: calls.append("tab"))
        await pilot.press("tab")
        await pilot.pause()
    assert calls == ["tab"]
