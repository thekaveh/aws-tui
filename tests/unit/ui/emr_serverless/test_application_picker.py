"""ApplicationPicker widget tests.

Covers H4 of the Pass-1 test-review gaps. The picker has three
public hooks (``toggle_open`` / ``action_commit`` / ``_trigger_label``)
plus a CSS-driven ``-open`` class flip that no other test pins. A
user-reported PR #76 comment flagged "There's no dropdown!" — these
tests lock the open/closed contract in place.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import OptionList, Static
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr
from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from tests.helpers import focus_and_settle, wait_until
from tests.snapshot.apps.emr import EmrPageApp, _build_page_vm


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
        self.open_states: list[bool] = []

    def compose(self) -> ComposeResult:
        yield ApplicationPicker(self._vm, id="picker")
        yield _FocusableStatic(id="after-picker")

    def on_application_picker_open_changed(self, event: ApplicationPicker.OpenChanged) -> None:
        self.open_states.append(event.is_open)


class _FocusableStatic(Static, can_focus=True):
    pass


# ── _trigger_label (pure) ─────────────────────────────────────────────────────


async def test_trigger_label_no_application_when_list_is_empty() -> None:
    vm, hub = _make_vm()
    # Refresh into the empty IDLE/EMPTY state so the trigger reads
    # the "no application" string. Without ``refresh()`` the VM's
    # initial state is LOADING, which now correctly renders as
    # "loading…" instead.
    await vm.refresh()
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


async def test_created_and_stopped_use_distinct_glyphs_and_textual_tooltips() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="created", name="ready", state=ApplicationState.CREATED)
    fake.add_application(app_id="stopped", name="quiet", state=ApplicationState.STOPPED)
    vm, hub = _make_vm(fake)
    await vm.refresh()
    async with _PickerApp(vm, hub).run_test() as pilot:
        picker = pilot.app.query_one(ApplicationPicker)
        await pilot.pause()

        vm.select("created")
        await pilot.pause()
        created_marker, _ = picker._trigger_fragments()  # type: ignore[attr-defined]
        assert picker.tooltip == "ready · CREATED"

        vm.select("stopped")
        await pilot.pause()
        stopped_marker, _ = picker._trigger_fragments()  # type: ignore[attr-defined]
        assert picker.tooltip == "quiet · STOPPED"
        assert created_marker != stopped_marker


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
        await wait_until(
            lambda: "-open" in picker.classes,
            what="application dropdown opened",
        )
        assert "-open" in picker.classes
        picker.toggle_open()
        await wait_until(
            lambda: "-open" not in picker.classes,
            what="application dropdown closed",
        )
        assert "-open" not in picker.classes


async def test_focused_picker_opens_with_enter() -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        picker = pilot.app.query_one(ApplicationPicker)
        picker.focus()
        await pilot.pause()

        await pilot.press("enter")
        await wait_until(
            lambda: picker.has_class("-open") and picker.has_focus_within,
            what="keyboard-opened application picker took focus",
        )

        assert picker.has_class("-open")
        assert picker.has_focus_within


async def test_action_close_removes_open_class() -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        picker.toggle_open()
        await wait_until(
            lambda: "-open" in picker.classes,
            what="application dropdown opened",
        )
        assert "-open" in picker.classes
        picker.action_close()
        await wait_until(
            lambda: "-open" not in picker.classes,
            what="application close removed open class",
        )
        assert "-open" not in picker.classes


async def test_closed_picker_cannot_run_stale_deferred_dropdown_focus() -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        picker = pilot.app.query_one(ApplicationPicker)
        outside = pilot.app.query_one("#after-picker", Static)

        picker.toggle_open()
        picker.close(refocus=False)
        outside.focus()
        # Drain stale dropdown-focus callbacks before checking they did not steal outside focus.
        await pilot.pause()

        assert not picker.is_open
        assert pilot.app.focused is outside


async def test_deferred_application_focus_yields_to_newer_outside_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        outside = pilot.app.query_one("#after-picker", Static)
        await focus_and_settle(picker)
        await wait_until(
            lambda: pilot.app.focused is picker,
            what="application picker took focus",
        )
        assert pilot.app.focused is picker
        callbacks: list[Callable[[], None]] = []
        monkeypatch.setattr(picker, "call_after_refresh", callbacks.append)

        picker.open()
        assert len(callbacks) == 1
        outside.focus()
        callbacks[0]()
        # Drain the stale callback effects before checking outside focus remains intact.
        await pilot.pause()

        assert not picker.is_open
        assert pilot.app.focused is outside


async def test_close_reopen_invalidates_stale_application_refocus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        picker = pilot.app.query_one(ApplicationPicker)
        callbacks: list[Callable[[], None]] = []
        monkeypatch.setattr(picker, "call_after_refresh", callbacks.append)

        picker.toggle_open()
        picker.close()
        picker.toggle_open()
        # Drain open/close events before inspecting the intercepted callback queue.
        await pilot.pause()

        assert len(callbacks) == 3
        callbacks[2]()
        await wait_until(
            lambda: pilot.app.focused is picker.query_one("#app-options", OptionList),
            what="reopened application options took focus",
        )
        assert pilot.app.focused is picker.query_one("#app-options", OptionList)

        callbacks[1]()
        callbacks[0]()
        # Drain stale callbacks before checking they did not close or refocus the reopened picker.
        await pilot.pause()
        assert picker.is_open
        assert pilot.app.focused is picker.query_one("#app-options", OptionList)


def test_application_picker_close_uses_no_private_textual_lifecycle_state() -> None:
    assert "_pruning" not in inspect.getsource(ApplicationPicker.close)


async def test_application_picker_normal_close_emits_once() -> None:
    vm, hub = _make_vm()
    app = _PickerApp(vm, hub)
    async with app.run_test() as pilot:
        picker = app.query_one(ApplicationPicker)

        picker.toggle_open()
        await pilot.pause()
        picker.close()
        picker.close()
        await wait_until(
            lambda: app.open_states == [True, False],
            what="normal close emitted one open and one close event",
        )

        assert app.open_states == [True, False]


async def test_application_picker_unmount_relays_one_close_to_parent() -> None:
    vm, hub = _make_vm()
    app = _PickerApp(vm, hub)
    async with app.run_test() as pilot:
        picker = app.query_one(ApplicationPicker)

        picker.toggle_open()
        await pilot.pause()
        await picker.remove()
        await wait_until(
            lambda: app.open_states == [True, False],
            what="removed picker relayed its close event",
        )

        assert app.open_states == [True, False]


async def test_application_picker_live_refresh_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        value = picker.query_one(".app-value", Static)

        def fail_update(_value: object) -> None:
            raise RuntimeError("live application trigger defect")

        monkeypatch.setattr(value, "update", fail_update)
        with pytest.raises(RuntimeError, match="live application trigger defect"):
            picker._refresh_trigger()  # type: ignore[attr-defined]


async def test_application_picker_refresh_is_safe_after_child_teardown() -> None:
    """A detached option list must be left alone, not rebuilt into.

    This test previously had no assertions at all: it called
    ``_refresh_options`` and relied on "did not raise", which stays true with
    the ``is_attached`` guard deleted, because ``set_options`` on a detached
    widget does not raise either. Assert the guard's observable effect instead.
    """
    vm, hub = _make_vm()
    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        options = picker.query_one("#app-options", OptionList)
        await options.remove()

        # Precondition: it is the ``is_attached`` arm of the guard under test,
        # not the ``is None`` arm -- the reference is assigned in compose and
        # is never cleared.
        assert picker._option_list is options  # type: ignore[attr-defined]
        assert not options.is_attached

        options.clear_options()
        picker._refresh_options()  # type: ignore[attr-defined]

        assert options.option_count == 0, (
            "refresh repopulated a detached option list; the is_attached guard "
            "is not doing anything"
        )


# ── action_commit (highlighted option → vm.select) ────────────────────────────


async def test_action_commit_with_highlighted_option_closes_dropdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a row is highlighted, ``action_commit`` closes the
    dropdown — the user-visible "commit closes" contract.

    The option list remains the picker's direct child for message
    bubbling, while CSS overlays it on the screen so opening it does
    not reflow the page.
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
        await wait_until(
            lambda: (
                picker.has_class("-open")
                and pilot.app.focused is picker.query_one("#app-options", OptionList)
            ),
            what="application dropdown prepared its options and took focus for commit",
        )
        assert picker.has_class("-open")
        # OptionList is the picker's direct child again.
        opts = picker.query_one("#app-options", OptionList)
        opts.highlighted = 0
        await pilot.pause()

        original_select = vm.select

        def select_after_close(app_id: str) -> None:
            assert not picker.is_open
            original_select(app_id)

        monkeypatch.setattr(vm, "select", select_after_close)
        picker.action_commit()
        await wait_until(
            lambda: not picker.has_class("-open") and vm.selected_id is not None,
            what="application selection committed and closed its dropdown",
        )
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
        # Drain the no-highlight commit event path before asserting selection remains unchanged.
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


async def test_application_options_expose_literal_state_with_state_styling() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="created", name="ready", state=ApplicationState.CREATED)
    fake.add_application(app_id="stopped", name="quiet", state=ApplicationState.STOPPED)
    vm, hub = _make_vm(fake)
    await vm.refresh()

    async with _PickerApp(vm, hub).run_test() as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        options = picker._build_options()  # type: ignore[attr-defined]
        prompts = [option.prompt for option in options]

        assert all(isinstance(prompt, Text) for prompt in prompts)
        assert [prompt.plain for prompt in prompts if isinstance(prompt, Text)] == [
            "ready · ◇ CREATED",
            "quiet · ○ STOPPED",
        ]
        assert [prompt.spans[0].style for prompt in prompts if isinstance(prompt, Text)] == [
            "white",
            "dim",
        ]


@pytest.mark.parametrize("state", list(ApplicationState))
async def test_application_option_starts_with_literal_name_before_state(
    state: ApplicationState,
) -> None:
    fake = _InMemoryEmr()
    name = "production-etl [blue] [/red]"
    fake.add_application(app_id="stable-app-id", name=name, state=state)
    vm, _ = _make_vm(fake)
    await vm.refresh()
    picker = ApplicationPicker(vm)

    option = picker._build_options()[0]
    assert isinstance(option.prompt, Text)
    assert option.prompt.plain.startswith(f"{name} · ")
    assert option.prompt.plain.endswith(f" {state.value}")
    assert option.id == "stable-app-id"
    # AWS-controlled brackets remain text; only the trailing state is styled.
    assert option.prompt.spans[0].start >= len(name)


@pytest.mark.parametrize("terminal_size", [(120, 40), (80, 24), (40, 16)])
async def test_application_overlay_fits_names_and_screen_and_commits_stable_id(
    terminal_size: tuple[int, int],
) -> None:
    fake = _InMemoryEmr()
    name = "production-analytics-nightly-batch [blue]"
    fake.add_application(app_id="first-id", name=name, state=ApplicationState.STARTED)
    fake.add_application(app_id="second-id", name=name, state=ApplicationState.STARTED)
    app = EmrPageApp(theme="carbon")
    app._page_vm = _build_page_vm(fake)

    async with app.run_test(size=terminal_size) as pilot:
        await pilot.pause()
        picker = app.query_one(ApplicationPicker)
        options = picker.query_one(OptionList)
        runs = app.query_one("#emr-runs-pane")
        before = (picker.region, runs.region)
        picker.open()
        await wait_until(
            lambda: picker.is_open and app.focused is options,
            what="application overlay opened and focused before measuring",
        )
        await pilot.pause()

        prompt = options.get_option_at_index(0).prompt
        assert isinstance(prompt, Text)
        required_width = prompt.cell_len + options.styles.gutter.width
        assert options.region.width >= min(required_width, terminal_size[0])
        assert options.region.width > picker.region.width
        assert app.screen.region.contains_region(options.region)
        assert (picker.region, runs.region) == before
        # Assert actual mounted rendering, including state at sizes where it fits.
        rendered = options.render_line(0).text
        assert name[:20] in rendered
        if terminal_size[0] >= required_width:
            assert name in rendered
            assert "● STARTED" in rendered

        await pilot.press("escape")
        await wait_until(
            lambda: not picker.is_open and app.focused is picker,
            what="Escape closed the widened application overlay and returned focus",
        )
        assert app._page_vm.applications.selected_id == "first-id"

        picker.open()
        await wait_until(
            lambda: picker.is_open and app.focused is options,
            what="application overlay reopened for stable-id selection",
        )
        # Rebuilding options on open leaves no highlight; the first Down
        # highlights row zero and the second reaches the duplicate name.
        assert options.highlighted is None
        await pilot.press("down", "down", "enter")
        await wait_until(
            lambda: (
                not picker.is_open
                and app._page_vm.applications.selected_id == "second-id"
                and app.focused is picker
            ),
            what="keyboard selection committed the second duplicate name by stable id",
        )
        assert (picker.region, runs.region) == before


class _RightEdgePickerApp(_PickerApp):
    CSS = """
    #picker-row { height: 3; }
    #leading-space { width: 1fr; }
    #picker { width: 14; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="picker-row"):
            yield Static(id="leading-space")
            yield ApplicationPicker(self._vm, id="picker")
        yield _FocusableStatic(id="after-picker")


async def test_application_overlay_at_right_edge_resizes_and_clicks_by_id() -> None:
    fake = _InMemoryEmr()
    name = "production-etl [blue] [/red]"
    fake.add_application(app_id="first-id", name=name, state=ApplicationState.CREATED)
    fake.add_application(app_id="second-id", name=name, state=ApplicationState.CREATED)
    for index in range(18):
        fake.add_application(app_id=f"z-extra-{index}", name=name, state=ApplicationState.CREATED)
    vm, hub = _make_vm(fake)
    await vm.refresh()

    async with _RightEdgePickerApp(vm, hub).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        picker = pilot.app.query_one(ApplicationPicker)
        options = picker.query_one(OptionList)
        picker.open()
        await wait_until(
            lambda: picker.is_open and pilot.app.focused is options,
            what="right-edge application overlay opened and focused",
        )
        await pilot.pause()
        assert picker.region.right == 80
        assert options.region.x < picker.region.x
        assert pilot.app.screen.region.contains_region(options.region)
        assert name in options.render_line(0).text

        await pilot.resize_terminal(30, 12)
        await pilot.pause()
        assert picker.is_open
        assert pilot.app.focused is options
        assert options.region.width == 30
        assert options.region.height <= 12
        assert pilot.app.screen.region.contains_region(options.region)
        assert name[:20] in options.render_line(0).text

        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert pilot.app.screen.region.contains_region(options.region)
        assert name in options.render_line(0).text
        assert "◇ CREATED" in options.render_line(0).text
        await pilot.click(options, offset=(2, options.styles.gutter.top + 1))
        await wait_until(
            lambda: (
                not picker.is_open and vm.selected_id == "second-id" and pilot.app.focused is picker
            ),
            what="click in widened overlay committed the second duplicate name by id",
        )
