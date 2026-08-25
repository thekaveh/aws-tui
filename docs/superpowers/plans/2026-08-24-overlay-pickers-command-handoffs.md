# Overlay Pickers, One-Line Commands, and Athena Handoffs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every scoped inline picker overlay without reflow, keep the Commands pane to one compact tooltip-rich row, and expose unified Glue table and Iceberg snapshot handoffs that prefill Athena without executing.

**Architecture:** Retain `ContextPicker` and `ApplicationPicker` as specialized Textual wrappers while sharing a small overlay option-list primitive modeled on Textual 0.89.1's `SelectOverlay`. Keep command metadata and availability in the existing VMx-backed `HintLegendVM`, terminal-width fitting in `HintLegend`, Glue selection in Glue VMs, and all button/key/palette dispatch through `ActionRegistry` and `OpenAthenaTableRequest`.

**Tech Stack:** Python 3.12, Textual 0.89.1, VMx 3.1, Rich, pytest, pytest-textual-snapshot, TCSS, MkDocs Material.

## Global Constraints

- Work only on `codex/overlay-pickers-command-handoffs`, based on current `develop`; merge only through a PR back to `develop`.
- `ContextPicker` and EMR `ApplicationPicker` are the only inline-dropdown classes in scope; resource `OptionList` panes, modal pickers, and the command palette are excluded.
- A picker trigger keeps its three-row collapsed footprint while open; opening and closing may not change parent, sibling, tab-strip, or service-content regions.
- Dropdown overlays use Textual's screen overlay and viewport constraint facilities; do not manually calculate absolute screen coordinates.
- Only one dropdown may be open; commit, Escape, outside focus/click, focus cycling, service switch, and unmount close it without stale refocus.
- Idle selector borders use `$rule-dim`; accent applies only to focus/open state, with error, warning, loading, and disabled state taking precedence.
- `HintLegend` always has exactly one content row; it may hide hints by deterministic priority but may never wrap, scroll, overlap, or grow vertically.
- Every visible hint has terse copy plus an accurate tooltip containing canonical shortcut notation, complete effect, execution/mutation behavior, and any disabled prerequisite.
- Hidden hints remain bound and available through the service-scoped command palette; `[:] more` and `[q] quit` survive the narrowest supported width.
- `glue.query_in_athena` uses `Q` (`Shift + Q`); `glue.time_travel_in_athena` retains `V` (`Shift + V`). Both prefill bounded read-only SQL and never execute it.
- Button, keyboard, and palette activation converge on the registered app action, Glue VM capability checks, and one typed `OpenAthenaTableRequest` path.
- Preserve Glue/Athena view order, typed focus order, source resolver order, serialized handoff supersession, public AWS SDK use, and the read-only service contract.
- Keep transient overlay geometry out of VMx; reuse `FocusCoordinatorVM`, `HintLegendVM`, Glue VMs, `ActionRegistry`, and typed messages as the most specialized existing owners.
- Use TDD for every behavior change, focused commits per task, all-theme wide/narrow visual coverage, and synchronized repository/site/wiki documentation.

---

## File Map

- `src/aws_tui/ui/widgets/overlay_option_list.py`: shared screen-overlaid `OptionList` with explicit dismiss semantics.
- `src/aws_tui/ui/widgets/context_picker.py`: stable three-row picker geometry, overlay lifecycle, semantic border precedence.
- `src/aws_tui/ui/widgets/emr_serverless/application_picker.py`: EMR-rich option rendering on the same overlay lifecycle.
- `src/aws_tui/ui/widgets/service_source_header.py`: source-wrapper focus styling without an extra Tab stop.
- `src/aws_tui/vm/chrome/hint_legend_vm.py`: compact labels, canonical shortcut text, tooltip copy, priorities, and Glue action membership.
- `src/aws_tui/ui/widgets/hint_legend.py`: content-width chips and deterministic one-row fitting.
- `src/aws_tui/infra/keymap_store.py`: default `Q` binding for `glue.query_in_athena`.
- `src/aws_tui/ui/bindings.py`: user-facing description for the direct Glue query action.
- `src/aws_tui/ui/actions.py`: structural protocol for views that dispatch registered app actions.
- `src/aws_tui/ui/widgets/glue/iceberg_view.py`: button dispatch through `ActionRegistry` rather than direct VM invocation.
- `src/aws_tui/vm/glue/page_vm.py`: page-scoped table/snapshot handoff capability properties.
- `src/aws_tui/app.py`: complete Glue disabled-action projection for copy, table query, and snapshot query.
- `tests/unit/ui/test_context_picker.py`: overlay geometry and lifecycle tests.
- `tests/unit/ui/emr_serverless/test_application_picker.py`: EMR overlay parity tests.
- `tests/unit/ui/emr_serverless/test_page_focus.py`: EMR focus-cycle and stale-refocus coverage.
- `tests/unit/vm/chrome/test_hint_legend.py`: command metadata, tooltips, priorities, and remapped-key coverage.
- `tests/unit/ui/test_chrome_widgets.py`: strict one-row wide/narrow legend geometry.
- `tests/unit/infra/test_keymap_store.py`, `tests/unit/ui/test_bindings.py`, `tests/integration/test_keybinding_wiring.py`: `Q` binding projection.
- `tests/unit/vm/glue/test_page_vm.py`, `tests/unit/ui/glue/test_iceberg_view.py`: capability and button-dispatch behavior.
- `tests/integration/test_glue_athena_navigation.py`, `tests/e2e/test_journeys.py`: full app button/key/palette handoffs and no-auto-execute proof.
- `tests/snapshot/test_glue.py`, `tests/snapshot/test_athena.py`, `tests/snapshot/test_emr.py` and generated raw snapshots: visual contracts.
- `docs/keybindings.md`, `docs/architecture.md`, `docs/adding-a-service.md`, `README.md`, `CHANGELOG.md`: canonical documentation.
- `generated/site/**`, `generated/wiki/**`, `site/**`, `assets/screenshots/aws-tui-running.png`: regenerated documentation surfaces and representative hero.

---

### Task 1: Give ContextPicker a Stable Screen Overlay

**Files:**
- Create: `src/aws_tui/ui/widgets/overlay_option_list.py`
- Modify: `src/aws_tui/ui/widgets/context_picker.py`
- Modify: `src/aws_tui/ui/widgets/service_source_header.py`
- Test: `tests/unit/ui/test_context_picker.py`
- Test: `tests/unit/ui/test_service_source_header.py`
- Test: `tests/unit/ui/glue/test_page.py`
- Test: `tests/unit/ui/athena/test_page.py`

**Interfaces:**
- Consumes: `ContextPicker.open()`, `close(restore=True, refocus=True)`, `is_open`, `ContextPicker.Changed`, and existing page focus rings.
- Produces: `OverlayOptionList.Dismissed(lost_focus: bool)`, an overlaid direct child with unchanged `ContextPicker` public API, and rule-dim idle/accent focus styling.

- [ ] **Step 1: Write failing overlay geometry and dismissal tests**

Add a sibling to `PickerHost` and add these focused tests:

```python
@pytest.mark.asyncio
async def test_context_picker_overlay_never_reflows_its_host_or_sibling() -> None:
    picker = _picker()
    async with PickerHost(picker).run_test(size=(60, 16)) as pilot:
        await pilot.pause()
        sibling = pilot.app.query_one("#after-picker", Static)
        before = (picker.region, sibling.region)

        picker.open()
        await pilot.pause()

        options = picker.query_one(OverlayOptionList)
        assert options.display
        assert options.styles.overlay == "screen"
        assert options.region.width == picker.content_region.width
        assert (picker.region, sibling.region) == before

        picker.close()
        await pilot.pause()
        assert (picker.region, sibling.region) == before


@pytest.mark.asyncio
async def test_context_picker_loses_open_state_without_refocusing_on_blur() -> None:
    picker = _picker()
    async with PickerHost(picker).run_test() as pilot:
        picker.open()
        await pilot.pause()
        pilot.app.query_one("#after-picker", Static).focus()
        await pilot.pause()

        assert not picker.is_open
        assert pilot.app.focused.id == "after-picker"
```

Extend Glue and Athena picker tests to capture the context row/header, tab strip,
and content-host regions before opening and assert exact equality while open and
after Escape. Add a style assertion that an unfocused source picker uses
`$rule-dim`-resolved border color while the focused Iceberg/content target owns
the accent.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_context_picker.py tests/unit/ui/test_service_source_header.py tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py -q
```

Expected: failures show `ContextPicker.-open` grows to `height: auto`, moves the
sibling/tab/content region, and has no `OverlayOptionList` or rule-dim idle state.

- [ ] **Step 3: Add the shared overlaid option-list primitive**

Create `overlay_option_list.py` with this public surface:

```python
from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.events import Blur
from textual.message import Message
from textual.widgets import OptionList


class OverlayOptionList(OptionList):
    """Option list that asks its owner to dismiss on Escape or focus loss."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close selector", show=False, priority=True),
    ]

    class Dismissed(Message):
        def __init__(self, *, lost_focus: bool) -> None:
            super().__init__()
            self.lost_focus = lost_focus

    def action_dismiss(self) -> None:
        self.post_message(self.Dismissed(lost_focus=False))

    def on_blur(self, _event: Blur) -> None:
        if self.display:
            self.post_message(self.Dismissed(lost_focus=True))


__all__ = ["OverlayOptionList"]
```

- [ ] **Step 4: Convert ContextPicker to fixed overlay geometry**

Replace its `OptionList` with `OverlayOptionList`, handle
`OverlayOptionList.Dismissed` by calling
`close(refocus=not event.lost_focus)`, and replace the expanding CSS with:

```tcss
ContextPicker {
    width: 1fr;
    height: 3;
    min-height: 3;
    layout: vertical;
    border: solid $rule-dim;
}
ContextPicker:focus,
ContextPicker:focus-within,
ContextPicker.-open {
    border: heavy $accent;
}
ContextPicker > OverlayOptionList {
    width: 1fr;
    height: auto;
    max-height: 12;
    display: none;
    overlay: screen;
    constrain: none inside;
}
ContextPicker.-open > OverlayOptionList {
    display: block;
}
ContextPicker.-warning,
ContextPicker.-warning:focus-within { border: solid $warning; }
ContextPicker.-error,
ContextPicker.-error:focus-within { border: solid $error; }
```

Keep `ServiceSourceHeader` as the focus target and style its child with
`ServiceSourceHeader:focus > ContextPicker`; do not make the child an additional
page-ring entry. Guard deferred refocus with `self.is_mounted`.

- [ ] **Step 5: Run ContextPicker and page focus tests**

Run:

```bash
uv run pytest tests/unit/ui/test_context_picker.py tests/unit/ui/test_service_source_header.py tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py -q
```

Expected: PASS; all existing selection, indicator, direct-command, and forward/
reverse focus tests remain green with invariant geometry.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/aws_tui/ui/widgets/overlay_option_list.py src/aws_tui/ui/widgets/context_picker.py src/aws_tui/ui/widgets/service_source_header.py tests/unit/ui/test_context_picker.py tests/unit/ui/test_service_source_header.py tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py
git commit -m "fix(ui): overlay service context pickers"
```

---

### Task 2: Give EMR ApplicationPicker Overlay Parity

**Files:**
- Modify: `src/aws_tui/ui/widgets/emr_serverless/application_picker.py`
- Modify: `src/aws_tui/ui/widgets/emr_serverless/page.py`
- Test: `tests/unit/ui/emr_serverless/test_application_picker.py`
- Test: `tests/unit/ui/emr_serverless/test_page_focus.py`
- Test: `tests/integration/test_emr_page.py`

**Interfaces:**
- Consumes: `OverlayOptionList`, `ApplicationPicker.toggle_open()`,
  `action_commit()`, `action_close()`, `is_open`, and `ApplicationCommitted`.
- Produces: fixed three-row EMR trigger geometry with the existing Rich state
  prompts, VM commit flow, and page `-application-picker-open` lifecycle signal.

- [ ] **Step 1: Write failing EMR geometry and lifecycle tests**

Add tests that capture `ApplicationPicker`, `.emr-app-box`, `JobRunsPane`, and
`JobRunDetailPane` regions before open and require exact equality while open and
after Escape. Add a focus-loss test:

```python
@pytest.mark.asyncio
async def test_application_picker_overlay_closes_when_focus_leaves() -> None:
    app = EmrPageApp(theme="carbon")
    async with app.run_test() as pilot:
        picker = app.query_one(ApplicationPicker)
        picker.toggle_open()
        await pilot.pause()

        app.query_one(JobRunsPane).focus()
        await pilot.pause()

        assert not picker.is_open
        assert not app.query_one(EmrServerlessPage).has_class("-application-picker-open")
```

Retain assertions for sorted Rich prompts, loading/error placeholder rows, commit
messages, keyboard activation, and selection restoration.

- [ ] **Step 2: Run EMR picker tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/emr_serverless/test_application_picker.py tests/unit/ui/emr_serverless/test_page_focus.py tests/integration/test_emr_page.py -q
```

Expected: geometry assertions fail because `.emr-app-box` grows and the runs pane
shrinks when the current in-flow `OptionList` is displayed.

- [ ] **Step 3: Reuse OverlayOptionList in ApplicationPicker**

Compose `OverlayOptionList(*self._build_options(), id="app-options")`, handle its
`Dismissed` message through the existing close path, and replace the expansion
CSS with:

```tcss
ApplicationPicker {
    width: 1fr;
    height: 3;
    min-height: 3;
    layout: vertical;
}
ApplicationPicker > OverlayOptionList {
    width: 1fr;
    height: auto;
    max-height: 16;
    display: none;
    overlay: screen;
    constrain: none inside;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
ApplicationPicker.-open > OverlayOptionList {
    display: block;
}
```

Keep `_build_options`, `_trigger_fragments`, placeholder-state handling, and
`ApplicationCommitted` unchanged. Continue notifying `EmrServerlessPage` when
open state changes, but remove any page CSS that depends on growing the app box.

- [ ] **Step 4: Run EMR picker and page tests**

Run:

```bash
uv run pytest tests/unit/ui/emr_serverless/test_application_picker.py tests/unit/ui/emr_serverless/test_page_focus.py tests/integration/test_emr_page.py -q
```

Expected: PASS with stable regions, one open picker, preserved state prompts, and
no stale focus after page/service transitions.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/aws_tui/ui/widgets/emr_serverless/application_picker.py src/aws_tui/ui/widgets/emr_serverless/page.py tests/unit/ui/emr_serverless/test_application_picker.py tests/unit/ui/emr_serverless/test_page_focus.py tests/integration/test_emr_page.py
git commit -m "fix(emr): overlay the application picker"
```

---

### Task 3: Enrich Command Metadata and Bind Glue Table Queries

**Files:**
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Modify: `src/aws_tui/infra/keymap_store.py`
- Modify: `src/aws_tui/ui/bindings.py`
- Test: `tests/unit/vm/chrome/test_hint_legend.py`
- Test: `tests/unit/infra/test_keymap_store.py`
- Test: `tests/unit/ui/test_bindings.py`
- Test: `tests/integration/test_keybinding_wiring.py`

**Interfaces:**
- Consumes: current service action sets, `KeymapStore.resolve(action_id)`, and
  `HintLegendVM.set_disabled_actions(frozenset[str])`.
- Produces: `HintAction(action_id, key_label, action_label, tooltip, priority,
  overflow_only, enabled)` and the default `Q` binding.

- [ ] **Step 1: Write failing metadata and binding tests**

Add assertions that Glue actions contain both handoffs and that every resolved
service/global action has complete help:

```python
def test_glue_handoffs_have_direct_keys_and_detailed_help() -> None:
    vm, hub = _constructed_vm(service="glue")
    try:
        chips = {chip.action_id: chip for chip in (*vm.actions, *vm.global_actions)}
        assert chips["glue.query_in_athena"].key_label == "Q"
        assert chips["glue.query_in_athena"].action_label == "Athena"
        assert "Shortcut: Shift + Q" in chips["glue.query_in_athena"].tooltip
        assert "does not execute" in chips["glue.query_in_athena"].tooltip
        assert chips["glue.time_travel_in_athena"].key_label == "V"
        assert "visible selected snapshot row" in chips[
            "glue.time_travel_in_athena"
        ].tooltip
        assert all(chip.tooltip.startswith("Shortcut: ") for chip in chips.values())
    finally:
        vm.dispose()
        hub.dispose()
```

Extend keymap and binding expectations with:

```python
assert store.resolve("glue.query_in_athena") == ("Q",)
assert ("Q", "dispatch('glue.query_in_athena')", False, False) in _installed(app)
```

- [ ] **Step 2: Run metadata/binding tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_hint_legend.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_bindings.py tests/integration/test_keybinding_wiring.py -q
```

Expected: `glue.query_in_athena` is absent from default bindings and both Glue
handoffs are absent from the legend's service set/tooltip model.

- [ ] **Step 3: Extend HintAction and define complete presentation metadata**

Use this immutable shape:

```python
@dataclass(frozen=True, slots=True)
class HintAction:
    action_id: str
    key_label: str
    action_label: str
    tooltip: str
    priority: int = 50
    overflow_only: bool = False
    enabled: bool = True
```

Add `app.command_palette` to global metadata as label `more`, priority `0`, and
`overflow_only=True`. Add `glue.query_in_athena` and
`glue.time_travel_in_athena` after `glue.copy_table_ref` in the Glue action set.

Define exact compact labels for Athena (`group`, `table`, `run`, `stop`, `more`,
`source`, `next theme`) and Glue (`Athena`, `snapshot`, `copy`). Use the
following complete effect and prerequisite metadata; enforce completeness with a
test over the union of `_SERVICE_ACTIONS` and `_GLOBAL_ACTIONS`:

```python
_ACTION_EFFECTS: dict[str, str] = {
    "app.command_palette": (
        "Open commands available for the active service. This does not perform "
        "an AWS operation."
    ),
    "app.themes": "Open the theme picker. This changes presentation only.",
    "app.cycle_theme": "Switch to the next theme. This changes presentation only.",
    "app.help": "Open keyboard and workflow help. This does not perform an AWS operation.",
    "app.quit": "Exit aws-tui after the application's normal shutdown sequence.",
    "app.swap_source": (
        "Switch to the next configured source and rebuild the active service context. "
        "This does not write AWS resources."
    ),
    "pane.switch_focus": "Move keyboard focus to the next operational pane.",
    "pane.descend": "Open the selected item or descend into the selected location.",
    "pane.copy": "Copy the selected item through the existing transfer workflow.",
    "pane.delete": "Delete the selected item through the existing confirmation workflow.",
    "pane.refresh": "Reload the active operational surface from its current source.",
    "emr.next_application": (
        "Select the next EMR Serverless application and load its runs. "
        "This does not start a job."
    ),
    "emr.clone": "Open the clone workflow for the selected EMR Serverless job run.",
    "glue.catalog": "Show the Glue Catalog view. This performs read-only discovery.",
    "glue.jobs": "Show Glue jobs and their read-only run history.",
    "glue.crawlers": "Show Glue crawlers and their read-only state.",
    "glue.choose_run_state": "Open the Glue job-run state filter.",
    "glue.choose_crawler_state": "Open the Glue crawler-state filter.",
    "glue.copy_table_ref": "Copy the selected Glue table's fully qualified SQL identifier.",
    "glue.query_in_athena": (
        "Open the selected Glue table in Athena and prefill a bounded read-only SELECT. "
        "This does not execute the query."
    ),
    "glue.time_travel_in_athena": (
        "Open the selected Iceberg snapshot in Athena and prefill FOR VERSION AS OF SQL. "
        "This does not execute the query."
    ),
    "athena.query": "Show the Athena query editor.",
    "athena.history": "Show read-only Athena query history.",
    "athena.results": "Show rows for the current Athena execution.",
    "athena.saved": "Show saved Athena queries.",
    "athena.choose_workgroup": "Open the Athena workgroup selector.",
    "athena.choose_catalog": "Open the Athena catalog selector.",
    "athena.choose_database": "Open the Athena database selector.",
    "athena.insert_table_ref": "Insert the same-source copied Glue table at the editor cursor.",
    "athena.execute": "Execute the validated read-only SQL in the active Athena context.",
    "athena.cancel": "Stop the active app-owned Athena query execution.",
    "athena.load_more": "Load the next available page for the active Athena view.",
}

_ACTION_REQUIREMENTS: dict[str, str] = {
    "pane.copy": "Requires a copyable selected item.",
    "pane.delete": "Requires a deletable selected item.",
    "emr.clone": "Requires a selected cloneable job run.",
    "glue.copy_table_ref": "Requires a visible selected Glue table.",
    "glue.query_in_athena": "Requires a visible selected Glue table.",
    "glue.time_travel_in_athena": "Requires a visible selected snapshot row.",
    "athena.insert_table_ref": "Requires a copied table from the active Athena source.",
    "athena.execute": "Requires valid non-empty read-only SQL and an idle query runner.",
    "athena.cancel": "Requires an active app-owned Athena query.",
    "athena.load_more": "Requires another result page in the active Athena view.",
}

_ACTION_PRIORITIES: dict[str, int] = {
    "app.command_palette": 0,
    "app.quit": 0,
    "athena.execute": 10,
    "athena.cancel": 10,
    "glue.query_in_athena": 10,
    "glue.time_travel_in_athena": 10,
    "app.swap_source": 20,
    "pane.refresh": 20,
    "glue.catalog": 80,
    "glue.jobs": 80,
    "glue.crawlers": 80,
    "athena.query": 80,
    "athena.history": 80,
    "athena.results": 80,
    "athena.saved": 80,
    "app.themes": 90,
    "app.cycle_theme": 90,
    "app.help": 90,
}
```

Unlisted actions use priority `50`. Build each tooltip as:

```python
def _tooltip_for(action_id: str, key: str, *, enabled: bool) -> str:
    lines = [f"Shortcut: {_canonical_shortcut(key)}", "", _ACTION_EFFECTS[action_id]]
    if not enabled and action_id in _ACTION_REQUIREMENTS:
        lines.extend(("", _ACTION_REQUIREMENTS[action_id]))
    return "\n".join(lines)
```

`_canonical_shortcut` expands `ctrl` to `Control`, `shift` to `Shift`,
`enter` to `Enter`, `escape` to `Escape`, and uppercase one-letter bindings to
`Shift + <letter>`. Preserve remapped keys by deriving both display and tooltip
from `KeymapStore.resolve()` on every rebuild.

- [ ] **Step 4: Add the Q binding and binding description**

Add:

```python
"glue.query_in_athena": ("Q",),
```

to `KeymapStore.DEFAULT_BINDINGS`, and add
`"glue.query_in_athena": "Open selected Glue table in Athena"` to
`_ACTION_DESCRIPTIONS`. Do not add an approved alias: `Q` is unique.

- [ ] **Step 5: Run metadata and binding tests**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_hint_legend.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_bindings.py tests/integration/test_keybinding_wiring.py -q
```

Expected: PASS; keymap overlays, immutability, deduplication, disabled-state, and
remapped-key tests remain green.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/aws_tui/vm/chrome/hint_legend_vm.py src/aws_tui/infra/keymap_store.py src/aws_tui/ui/bindings.py tests/unit/vm/chrome/test_hint_legend.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_bindings.py tests/integration/test_keybinding_wiring.py
git commit -m "feat(commands): expose Glue Athena handoffs"
```

---

### Task 4: Render Commands as One Deterministic Row

**Files:**
- Modify: `src/aws_tui/ui/widgets/hint_legend.py`
- Test: `tests/unit/ui/test_chrome_widgets.py`

**Interfaces:**
- Consumes: ordered `HintAction` tuples and their `priority`, `overflow_only`, and
  `enabled` fields.
- Produces: `_fit_actions(actions: tuple[HintAction, ...], width: int) ->
  tuple[HintAction, ...]` and content-width non-focusable hint chips.

- [ ] **Step 1: Replace wrapping expectations with failing one-row tests**

Change the wide and narrow tests to require one row and overflow behavior:

```python
@pytest.mark.asyncio
async def test_hint_legend_is_one_compact_row_at_wide_athena_width() -> None:
    vm, hub = _athena_hint_vm()
    try:
        async with _HintApp(vm, hub).run_test(size=(245, 62)) as pilot:
            await pilot.pause()
            legend = pilot.app.query_one(HintLegend)
            chips = list(legend.query(".hint-chip"))
            assert {chip.region.y for chip in chips} == {legend.content_region.y}
            assert legend.region.height == 3
            assert not any(chip.action.overflow_only for chip in chips)
            assert all(chip.tooltip == chip.action.tooltip for chip in chips)
    finally:
        vm.dispose()
        hub.dispose()


@pytest.mark.asyncio
async def test_hint_legend_uses_more_instead_of_wrapping_when_narrow() -> None:
    vm, hub = _athena_hint_vm()
    try:
        async with _HintApp(vm, hub).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            chips = list(pilot.app.query(".hint-chip"))
            assert len({chip.region.y for chip in chips}) == 1
            assert {chip.action.action_id for chip in chips} >= {
                "app.command_palette",
                "app.quit",
            }
            assert all(chip.region.right <= pilot.app.query_one(HintLegend).content_region.right for chip in chips)
    finally:
        vm.dispose()
        hub.dispose()
```

Add pure fitting tests proving full-width preservation, duplicate tab hints are
removed first, disabled same-priority actions lose before enabled actions, and
`more`/`quit` remain at the minimum supported width.

- [ ] **Step 2: Run HintLegend tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py -k hint_legend -q
```

Expected: the current 22-column `ItemGrid` produces two or more rows and has no
tooltip, priority fitter, or overflow-only chip.

- [ ] **Step 3: Implement content-width chips and fitting**

Replace `ItemGrid` with a one-row `Horizontal`, introduce a private `_HintChip`
that stores `action: HintAction`, and compute cell width with Rich's
`cell_len(f"[{key}] {label}")` plus one separator cell. Use this CSS:

```tcss
HintLegend {
    height: 3;
    min-height: 3;
    margin: 0 1 1 1;
    border-title-align: left;
}
HintLegend > #hint-strip {
    width: 1fr;
    height: 1;
    layout: horizontal;
    overflow: hidden hidden;
}
HintLegend .hint-chip {
    width: auto;
    min-width: 0;
    height: 1;
    margin-right: 1;
}
```

Implement `_fit_actions` by excluding `overflow_only` actions initially, returning
all actions when they fit, and otherwise repeatedly removing the highest numeric
priority (disabled before enabled, later source-order item before earlier) until
the remaining actions plus the overflow action fit. Never remove
`app.command_palette` or `app.quit` once overflow mode is active. Refit after
mount, resize, and VM action changes. Assign `chip.tooltip = action.tooltip`.

- [ ] **Step 4: Run focused VM/widget and theme tests**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py tests/unit/vm/chrome/test_hint_legend.py tests/unit/ui/test_themes.py -q
```

Expected: PASS with one row at 245 and 80 columns, bounded chips, accurate
tooltips, no new focus stops, and unchanged theme token ownership.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/aws_tui/ui/widgets/hint_legend.py tests/unit/ui/test_chrome_widgets.py
git commit -m "fix(commands): keep hints on one compact row"
```

---

### Task 5: Converge Glue Button, Key, and Palette Actions

**Files:**
- Modify: `src/aws_tui/ui/actions.py`
- Modify: `src/aws_tui/ui/widgets/glue/iceberg_view.py`
- Modify: `src/aws_tui/vm/glue/page_vm.py`
- Modify: `src/aws_tui/app.py`
- Test: `tests/unit/vm/glue/test_page_vm.py`
- Test: `tests/unit/ui/glue/test_iceberg_view.py`
- Test: `tests/integration/test_command_palette_wiring.py`

**Interfaces:**
- Consumes: `AwsTuiApp.action_dispatch(action_id)`, `GluePageVM.query_in_athena()`,
  `GluePageVM.time_travel_in_athena()`, and `GlueIcebergVM.can_time_travel_in_athena`.
- Produces: `ActionDispatcher` protocol, `GluePageVM.can_query_in_athena`,
  `GluePageVM.can_time_travel_in_athena`, and registry-routed button activation.

- [ ] **Step 1: Write failing capability and button-dispatch tests**

Add page-VM assertions:

```python
assert vm.can_query_in_athena is (vm.active_view == "catalog" and vm.catalog.can_copy_table_reference)
assert vm.can_time_travel_in_athena is (
    vm.active_view == "catalog" and vm.catalog.iceberg.can_time_travel_in_athena
)
```

In the Iceberg widget test host, provide `action_dispatch`, record action IDs, and
assert clicking the arrow records exactly `glue.time_travel_in_athena` without
directly sending an `OpenAthenaTableRequest` from the widget fixture.

Add an app disabled-state test proving copy and table handoff disable together
when no table is selected, while snapshot handoff additionally requires the
Snapshots view and a visible selected row.

- [ ] **Step 2: Run capability/action tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/vm/glue/test_page_vm.py tests/unit/ui/glue/test_iceberg_view.py tests/integration/test_command_palette_wiring.py -q
```

Expected: page-level capability properties and structural dispatcher protocol are
missing; the button invokes `GlueIcebergVM.time_travel_in_athena()` directly.

- [ ] **Step 3: Add page-scoped capability properties**

Add:

```python
@property
def can_query_in_athena(self) -> bool:
    return (
        self._is_alive()
        and self._active_view == "catalog"
        and self.catalog.can_copy_table_reference
    )

@property
def can_time_travel_in_athena(self) -> bool:
    return (
        self._is_alive()
        and self._active_view == "catalog"
        and self.catalog.iceberg.can_time_travel_in_athena
    )
```

Guard `query_in_athena()` and `time_travel_in_athena()` with these properties.

- [ ] **Step 4: Route the arrow button through ActionRegistry**

Add to `ui/actions.py`:

```python
class ActionDispatcher(Protocol):
    def action_dispatch(self, action_id: str) -> Awaitable[None] | None: ...
```

In `GlueIcebergView._time_travel_selected`, cast `self.app` to
`ActionDispatcher`, call `action_dispatch("glue.time_travel_in_athena")`, and if
the result is awaitable run it in the widget's existing worker lifecycle. The
widget must not call `self._vm.time_travel_in_athena()`.

Extend `AwsTuiApp._recompute_hint_disables()`:

```python
disabled: set[str] = set()
if not glue_page.vm.can_copy_table_reference:
    disabled.add("glue.copy_table_ref")
if not glue_page.vm.can_query_in_athena:
    disabled.add("glue.query_in_athena")
if not glue_page.vm.can_time_travel_in_athena:
    disabled.add("glue.time_travel_in_athena")
hint_legend.set_disabled_actions(frozenset(disabled))
```

Recompute after child Iceberg property changes as well as table selection and
view changes so the button and legend agree immediately.

- [ ] **Step 5: Run Glue capability, widget, palette, and command tests**

Run:

```bash
uv run pytest tests/unit/vm/glue/test_page_vm.py tests/unit/ui/glue/test_iceberg_view.py tests/integration/test_command_palette_wiring.py tests/unit/vm/chrome/test_hint_legend.py -q
```

Expected: PASS; invalid actions retain one advisory toast, shutdown remains
silent, and direct VM message tests continue to cover the typed handoff boundary.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/aws_tui/ui/actions.py src/aws_tui/ui/widgets/glue/iceberg_view.py src/aws_tui/vm/glue/page_vm.py src/aws_tui/app.py tests/unit/vm/glue/test_page_vm.py tests/unit/ui/glue/test_iceberg_view.py tests/integration/test_command_palette_wiring.py
git commit -m "fix(glue): unify Athena handoff actions"
```

---

### Task 6: Prove Full-App Handoffs and Visual Contracts

**Files:**
- Modify: `tests/integration/test_glue_athena_navigation.py`
- Modify: `tests/e2e/test_journeys.py`
- Modify: `tests/snapshot/apps/glue.py`
- Modify: `tests/snapshot/apps/athena.py`
- Modify: `tests/snapshot/apps/emr.py`
- Modify: `tests/snapshot/test_glue.py`
- Modify: `tests/snapshot/test_athena.py`
- Modify: `tests/snapshot/test_emr.py`
- Regenerate: affected `tests/snapshot/__snapshots__/*.raw`

**Interfaces:**
- Consumes: seeded demo snapshot `4201`, real `#glue-iceberg-time-travel`
  button, installed `Q`/`V` bindings, and serialized service mount helpers.
- Produces: end-to-end evidence that button/key/palette routes prefill exact SQL,
  preserve source identity, and do not execute.

- [ ] **Step 1: Add the failing real-button journey**

Replace the direct dispatcher call in Journey 9's prefill phase with the real UI
button:

```python
await glue.open_table(
    TableRef(
        "AwsDataCatalog",
        "dev_analytics",
        "dev_events_iceberg",
        "demo-dev",
        "us-east-1",
    )
)
await glue.catalog.iceberg.select_view("snapshots")
assert glue.catalog.iceberg.select_snapshot(4201)
await pilot.pause()

button = app.query_one("#glue-iceberg-time-travel", Button)
assert not button.disabled
await pilot.click("#glue-iceberg-time-travel")
await _await_service_mount(pilot, app)

athena = ctx.root_vm.content_host.current
assert isinstance(athena, AthenaPageVM)
assert athena.context.connection_name == "demo-dev"
assert athena.query.sql.endswith(
    '"AwsDataCatalog"."dev_analytics"."dev_events_iceberg" '
    "FOR VERSION AS OF 4201 LIMIT 100"
)
assert athena.query.execution_ref is None
assert athena.results.rows == ()
```

Add parameterized integration cases for `Q`, `V`, and the corresponding palette
entry; all must reach the same source and exact SQL and leave execution unset.

- [ ] **Step 2: Run the newly expanded handoff tests**

Run:

```bash
uv run pytest tests/integration/test_glue_athena_navigation.py tests/e2e/test_journeys.py -k 'handoff or iceberg_snapshot or query_in_athena' -q
```

Expected: PASS after Task 5. If the real button, `Q`, or palette route differs,
preserve the failing test and return to Task 5's single dispatch path before
updating snapshots.

- [ ] **Step 3: Add snapshot scenarios for overlay and one-row states**

Add representative states for:

- Glue source idle while Iceberg table owns focus;
- Glue run-state picker open with unchanged tab/content geometry;
- Athena catalog picker open with one-line command legend;
- EMR application picker open without shrinking runs/detail panes; and
- narrow Athena/Glue legends showing `[:] more` on one row.

Assertions in the snapshot app fixtures must compare captured regions before and
after opening; raw snapshots remain visual supplements.

- [ ] **Step 4: Run focused snapshot tests with update, then without update**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py tests/snapshot/test_emr.py --snapshot-update -q
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py tests/snapshot/test_emr.py -q
```

Expected: both commands PASS; review every changed raw snapshot for one active
focus frame, overlay rather than reflow, one-row commands, no clipping, and no
unexpected unrelated churn.

- [ ] **Step 5: Run full handoff and focus regressions**

Run:

```bash
uv run pytest tests/integration/test_glue_athena_navigation.py tests/integration/test_keybinding_wiring.py tests/integration/test_command_palette_wiring.py tests/unit/ui/glue tests/unit/ui/athena tests/unit/ui/emr_serverless -q
```

Expected: PASS with exact source identity, rollback/supersession behavior, forward
and reverse focus traversal, and no automatic query execution.

- [ ] **Step 6: Commit Task 6**

```bash
git add tests/integration/test_glue_athena_navigation.py tests/e2e/test_journeys.py tests/snapshot/apps/glue.py tests/snapshot/apps/athena.py tests/snapshot/apps/emr.py tests/snapshot/test_glue.py tests/snapshot/test_athena.py tests/snapshot/test_emr.py tests/snapshot/__snapshots__
git commit -m "test(ui): cover overlay and Athena handoff journeys"
```

---

### Task 7: Synchronize Documentation and Run the Release Gate

**Files:**
- Modify: `docs/keybindings.md`
- Modify: `docs/architecture.md`
- Modify: `docs/adding-a-service.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/plans/README.md`
- Regenerate: `generated/site/**`
- Regenerate: `generated/wiki/**`
- Regenerate: `site/**`
- Regenerate: `assets/screenshots/aws-tui-running.png`

**Interfaces:**
- Consumes: final command labels/tooltips, `Q`/`V` semantics, overlay behavior,
  canonical docs build scripts, and approved spec.
- Produces: synchronized repository, site, and wiki documentation plus full
  verification evidence for PR review.

- [ ] **Step 1: Update canonical documentation**

Document these exact user contracts:

```text
Shift+Q opens the selected Glue table in Athena and prefills a bounded SELECT;
Shift+V opens the selected visible Iceberg snapshot and adds FOR VERSION AS OF.
Neither action executes the query.

Inline service-context and EMR application pickers overlay the current layout;
opening or closing a picker does not resize adjacent panes.

The Commands pane is one compact row. Hovering a visible command shows its full
shortcut, effect, execution behavior, and prerequisite. [:] more opens the
service-scoped command palette when lower-priority hints do not fit.
```

Update the plan index with this plan. Update architecture ownership prose without
changing architecture diagrams unless a diagram still depicts inline picker
expansion or a different command/handoff owner. Add a concise Unreleased
changelog entry.

- [ ] **Step 2: Regenerate all documentation surfaces and the hero**

Run:

```bash
make docs-build
make docs-wiki
make docs-hero
```

Expected: MkDocs strict build passes; generated site/wiki content matches the
canonical files; the hero reflects the approved final UI.

- [ ] **Step 3: Run formatting, lint, types, architecture, and full tests**

Run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
```

Expected: all commands exit 0. Record the final test count in the PR body; do not
claim success from a subset run.

- [ ] **Step 4: Run documentation and repository hygiene checks**

Run:

```bash
make docs-check
git diff --check
git status --short
```

Expected: docs checks and diff checks pass; status contains only intended tracked
implementation, test, snapshot, generated-doc, screenshot, spec-status, and plan
changes.

- [ ] **Step 5: Perform final manual visual review**

Launch demo mode at representative wide and narrow terminals and inspect Carbon,
one light theme, and every remaining supported theme through snapshots. Verify
source-vs-Iceberg focus, each dropdown overlay, one-row Athena/Glue commands,
tooltips, `Q`, `V`, and the real arrow button. Record any discovered defect as a
new failing test before changing code.

- [ ] **Step 6: Commit Task 7**

```bash
git add docs/keybindings.md docs/architecture.md docs/adding-a-service.md README.md CHANGELOG.md docs/superpowers/specs/2026-08-24-overlay-pickers-command-handoffs-design.md docs/superpowers/plans/2026-08-24-overlay-pickers-command-handoffs.md docs/superpowers/plans/README.md generated site assets/screenshots/aws-tui-running.png
git commit -m "docs: publish overlay picker and handoff behavior"
```

- [ ] **Step 7: Push, open the PR to develop, verify checks, and merge**

```bash
git push
gh pr create --base develop --head codex/overlay-pickers-command-handoffs --title "Polish pickers, commands, and Athena handoffs" --body-file /tmp/aws-tui-overlay-pr.md
gh pr checks --watch <PR_NUMBER>
gh pr merge <PR_NUMBER> --merge --delete-branch
```

Expected: required checks pass before merge; the PR targets `develop`, not
`main`. After merge, update local `develop`, remove the local feature branch, and
confirm the remote feature branch and any task worktrees are gone.

---

## Final Acceptance Gate

- [ ] Every scoped dropdown overlays with invariant surrounding regions.
- [ ] Only one picker is open, and no stale refocus survives unmount or service switch.
- [ ] Idle source borders no longer resemble the focused Iceberg frame.
- [ ] Commands use exactly one tightly spaced row at wide and narrow supported widths.
- [ ] Every visible command tooltip accurately describes shortcut, effect, execution, and prerequisite.
- [ ] Narrow overflow preserves `[:] more` and `[q] quit`; hidden actions remain bound and palette-visible.
- [ ] `Shift+Q`, `Shift+V`, palette entries, and the actual arrow button use the same registered action paths.
- [ ] Table and snapshot handoffs preserve exact source identity, prefill exact bounded SQL, and never auto-execute.
- [ ] Full tests, lint, format, types, architecture checks, docs checks, and all-theme snapshots pass.
- [ ] Repository, generated site, and generated wiki documentation are synchronized.
- [ ] PR is merged into `develop` and local/remote feature branches and worktrees are cleaned up.
