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


async def test_trigger_label_shows_name_glyph_when_selected() -> None:
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
        assert "STARTED" in label


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
    dropdown — the user-visible "commit closes" contract. Pass-2
    M-3 (test-review): previous form queried the OptionList by id
    (``#app-options``), coupling the test to a private layout
    choice. The behavior we actually need to pin is the higher-
    level: a committed selection closes the picker.
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
        # We still need a highlight to drive ``action_commit``'s
        # happy path; use the broad ``OptionList`` selector instead
        # of the child id so a layout rename doesn't break the
        # test. The contract under test is the dropdown closing
        # post-commit, not which child id holds the options.
        opts = picker.query_one(OptionList)
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
        # Use the broad ``OptionList`` selector, not the child id.
        opts = picker.query_one(OptionList)
        opts.highlighted = None
        before_selection = vm.selected_id
        picker.action_commit()
        await pilot.pause()
        assert vm.selected_id == before_selection
