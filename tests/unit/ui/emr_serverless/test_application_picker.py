"""ApplicationPicker widget tests.

Covers H4 of the Pass-1 test-review gaps. The picker has three
public hooks (``toggle_open`` / ``action_commit`` / ``_trigger_label``)
plus a CSS-driven ``-open`` class flip that no other test pins. A
user-reported PR #76 comment flagged "There's no dropdown!" — these
tests lock the open/closed contract in place.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import OptionList
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make_vm(fake: _InMemoryEmr | None = None) -> tuple[ApplicationsVM, MessageHub[Message]]:
    fake = fake or _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = ApplicationsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, hub


class _PickerApp(App[None]):
    def __init__(self, vm: ApplicationsVM, hub: MessageHub[Message]) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub

    def compose(self) -> ComposeResult:
        yield ApplicationPicker(self._vm, hub=self._hub, id="picker")


# ── _trigger_label (pure) ─────────────────────────────────────────────────────


async def test_trigger_label_no_application_when_list_is_empty() -> None:
    vm, hub = _make_vm()
    # No apps refreshed in. _trigger_label is callable directly via the widget.
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        assert picker._trigger_label() == "(no application)"  # type: ignore[attr-defined]


async def test_trigger_label_select_application_when_no_selection() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    vm, hub = _make_vm(fake)
    await vm.refresh()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        assert picker._trigger_label() == "(select application)"  # type: ignore[attr-defined]


async def test_trigger_label_select_application_when_selection_stale() -> None:
    """If the VM still holds a ``selected_id`` that no longer
    corresponds to any app (cleared between refreshes), the picker
    must NOT crash with KeyError — it falls back to the
    ``(select application)`` placeholder."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    vm, hub = _make_vm(fake)
    await vm.refresh()
    vm.select("ghost")  # not in the application list
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        assert picker._trigger_label() == "(select application)"  # type: ignore[attr-defined]


async def test_trigger_label_shows_name_and_colored_glyph_when_selected() -> None:
    """Post-PR-state-glyphs the trigger label drops the textual
    state name (``STARTED``) in favour of a colored Rich-markup
    glyph (green ●). User feedback: "If we do this then we don't
    need to show the STARTED OR STOPPED ETC statuses next to them"."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl", state=ApplicationState.STARTED)
    vm, hub = _make_vm(fake)
    await vm.refresh()
    vm.select("a1")
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        label = picker._trigger_label()  # type: ignore[attr-defined]
        assert "etl" in label
        # STARTED ⇒ green ● glyph, no textual state name.
        assert "●" in label
        assert "[green]" in label
        assert "STARTED" not in label


# ── toggle_open (CSS class flip) ──────────────────────────────────────────────


async def test_toggle_open_flips_open_class_on_and_off() -> None:
    """The CSS contract is: ``ApplicationPicker.-open > OptionList`` is
    ``display: block`` and the bare selector keeps it ``display: none``.
    Pinning the ``-open`` class toggle pins the user-visible
    dropdown-shown state."""
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        assert "-open" not in picker.classes
        picker.toggle_open()
        await pilot.pause()
        assert "-open" in picker.classes
        picker.toggle_open()
        await pilot.pause()
        assert "-open" not in picker.classes


async def test_action_close_removes_open_class() -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause()
        assert "-open" in picker.classes
        picker.action_close()
        await pilot.pause()
        assert "-open" not in picker.classes


# ── action_commit (highlighted option → vm.select) ────────────────────────────


async def test_action_commit_with_highlighted_option_closes_dropdown() -> None:
    """When a row is highlighted, ``action_commit`` closes the
    dropdown — the user-visible "commit closes" contract.

    Post-batch-4: the dropdown is back inline (OptionList is a
    direct child of the picker, rendered inside the apps-box
    which grows via ``height: auto`` to accommodate it). The
    prior screen-mount approach broke positioning + message
    bubbling — see the picker module docstring for the history.
    """
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    vm, hub = _make_vm(fake)
    await vm.refresh()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause()
        assert picker.has_class("-open")
        # OptionList is the picker's direct child again.
        opts = picker.query_one("#app-options", OptionList)
        opts.highlighted = 0
        await pilot.pause()
        picker.action_commit()
        await pilot.pause()
        # Commit closes the dropdown AND lands a selection on the VM.
        assert not picker.has_class("-open")
        assert vm.selected_id is not None


async def test_action_commit_no_highlight_is_noop() -> None:
    """Defensive: no row highlighted → ``action_commit`` does not
    crash and does not change the selection."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    vm, hub = _make_vm(fake)
    await vm.refresh()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause()
        opts = picker.query_one("#app-options", OptionList)
        opts.highlighted = None
        before_selection = vm.selected_id
        picker.action_commit()
        await pilot.pause()
        assert vm.selected_id == before_selection


# ── Dropdown sort order ──────────────────────────────────────────────────────


async def test_build_options_sorts_started_first_then_other_states() -> None:
    """User feedback: "list the started ones first … then list the
    remaining". The dropdown lists STARTED applications first; the
    remaining states group as transitional (STARTING / STOPPING),
    idle (CREATING / CREATED / STOPPED), terminated. Within a group
    the tie-break is the application name (alphabetical).
    """
    fake = _InMemoryEmr()
    # Add in deliberately-shuffled order to confirm the sort, not the
    # insertion order, drives the dropdown.
    fake.add_application(app_id="a-terminated", name="killed", state=ApplicationState.TERMINATED)
    fake.add_application(app_id="a-stopped", name="zzz-quiet", state=ApplicationState.STOPPED)
    fake.add_application(app_id="a-started-b", name="bravo", state=ApplicationState.STARTED)
    fake.add_application(app_id="a-starting", name="warming-up", state=ApplicationState.STARTING)
    fake.add_application(app_id="a-started-a", name="alpha", state=ApplicationState.STARTED)
    fake.add_application(app_id="a-created", name="ready", state=ApplicationState.CREATED)
    vm, hub = _make_vm(fake)
    await vm.refresh()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        options = picker._build_options()  # type: ignore[attr-defined]
        ids_in_order = [opt.id for opt in options]
        # STARTED comes first (alphabetical within group: alpha, bravo),
        # then STARTING (transitional), then CREATED (idle),
        # then STOPPED (idle), then TERMINATED.
        assert ids_in_order == [
            "a-started-a",
            "a-started-b",
            "a-starting",
            "a-created",
            "a-stopped",
            "a-terminated",
        ]
