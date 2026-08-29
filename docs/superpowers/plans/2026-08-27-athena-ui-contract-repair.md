# 1. Athena UI Contract Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the shared command alignment, Athena context controls, persistent service selection, Glue-to-Athena starter query, and Athena query toolbar as one verified interaction update.

**Architecture:** Retain the existing Textual widgets and VMx MVVM ownership boundaries. Apply presentation changes in shared widgets and TCSS, express query availability through the existing `AsyncRelayCommand` predicates, and strengthen the serialized Glue-to-Athena transaction with visible-editor acceptance coverage instead of adding parallel state.

**Tech Stack:** Python 3.11-3.13, Textual 8.2.8, VMx 3.23.0, sqlglot 30.x, Rich, pytest, pytest-asyncio, pytest-textual-snapshot, TCSS, MkDocs Material.

## 1.1. Global Constraints

- Work only on `fix/athena-ui-contracts`, created from synchronized `develop` commit `49ac96dc`.
- Preserve Athena's source -> workgroup -> catalog -> database resolver order.
- Preserve the existing keyboard focus sequence: editor -> Run -> Stop -> execution detail.
- Keep selectors as `ServiceSourceHeader` and `ContextPicker`; do not add a replacement selector abstraction.
- Keep the existing overlay geometry and one-open-picker coordination.
- Keep `NavMenuVM.selected_id` as the active-service source of truth and `FocusCoordinatorVM` as focus source of truth.
- Keep query policy and availability in VMx commands; do not add view-local lifecycle booleans.
- Generated starter SQL must quote catalog, database, and table identifiers and end with `LIMIT 5`.
- Glue-to-Athena handoff must never execute the generated query automatically.
- Use test-driven development for every behavior change.
- Intermediate task commits are allowed; the PR must be squash-merged so `develop` receives one comprehensive fix commit.
- Do not modify dependency versions or public action ids.

---

## 1.2. File Map

- `src/aws_tui/ui/widgets/hint_legend.py`: center the already fitted one-line command sequence.
- `tests/unit/ui/test_chrome_widgets.py`: command-row centering and constrained-width geometry.
- `src/aws_tui/ui/widgets/nav_menu.py`: paint active service selection independently from keyboard focus.
- `tests/unit/ui/test_nav_menu.py`: persistent service and Settings selection tests.
- `src/aws_tui/ui/widgets/athena/page.py`: unframed context row and synchronized selector availability.
- `src/aws_tui/ui/themes/operational-panes.tcss`: remove Athena's outer context border/focus styling.
- `tests/unit/ui/athena/test_page.py`: Athena context interaction, query geometry, focus, and button-state tests.
- `tests/unit/ui/test_themes.py`: shared operational-pane selector ownership tests.
- `src/aws_tui/vm/athena/query_vm.py`: align Stop command availability with true interruptibility.
- `tests/unit/vm/athena/test_query_vm.py`: VMx Run/Stop lifecycle gating tests.
- `src/aws_tui/ui/widgets/athena/query_view.py`: compact controls above editor and mounted-editor synchronization.
- `src/aws_tui/domain/sql_policy.py`: generate bounded five-row starter SQL.
- `tests/unit/domain/test_sql_policy.py`: exact ordinary and Iceberg starter SQL.
- `tests/integration/test_glue_athena_navigation.py`: visible-editor handoff acceptance and no-execution assertions.
- `docs/services/athena.md`, `docs/keybindings.md`, `docs/architecture.md`, `docs/contract-ledger.md`: canonical documentation updates.
- `generated/site/**`, `generated/wiki/**`, `mkdocs.yml`: regenerated three-surface outputs.
- `tests/snapshot/test_athena.py`, `tests/snapshot/test_main_screen.py`, and affected `.raw` files: all-theme visual contracts.

---

## 1.3. Task Plan

### 1.3.1. Center the Shared One-Line Command Legend

**Files:**
- Modify: `tests/unit/ui/test_chrome_widgets.py`
- Modify: `src/aws_tui/ui/widgets/hint_legend.py`

**Interfaces:**
- Consumes: `_fit_actions(actions, available_width)` and the current `_HintChip` content-sized geometry.
- Produces: one centered chip sequence in `#hint-strip` without changing fitting, action order, tooltips, or overflow.

- [ ] **Step 1: Add failing wide and narrow centering assertions**

Extend `test_hint_legend_is_one_compact_row_at_wide_athena_width` and `test_hint_legend_uses_more_instead_of_wrapping_when_narrow` with a shared assertion:

```python
def _assert_hint_row_centered(legend: HintLegend) -> None:
    chips = list(legend.query(".hint-chip"))
    assert chips
    left_space = chips[0].region.x - legend.content_region.x
    right_space = legend.content_region.right - chips[-1].region.right
    assert abs(left_space - right_space) <= 1
    assert {chip.region.y for chip in chips} == {legend.content_region.y}


# In both tests, after collecting `legend`:
_assert_hint_row_centered(legend)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py::test_hint_legend_is_one_compact_row_at_wide_athena_width tests/unit/ui/test_chrome_widgets.py::test_hint_legend_uses_more_instead_of_wrapping_when_narrow -q
```

Expected: the wide assertion fails because the fitted row begins at the left content edge.

- [ ] **Step 3: Center the fitted strip without changing its children**

In `HintLegend.DEFAULT_CSS`, add alignment to `#hint-strip`:

```tcss
HintLegend > #hint-strip {
    width: 1fr;
    height: 1;
    layout: horizontal;
    align: center middle;
    overflow: hidden hidden;
}
```

Do not change `_fit_actions`, chip widths, margins, or rebuild scheduling.

- [ ] **Step 4: Run the complete legend test module**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py tests/integration/test_chrome_and_hint_legend.py -q
```

Expected: PASS; wide and narrow rows remain one line and overflow selection is unchanged.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/aws_tui/ui/widgets/hint_legend.py tests/unit/ui/test_chrome_widgets.py
git commit -m "fix(ui): center fitted command legends"
```

---

### 1.3.2. Keep the Active Service Selected Outside Rail Focus

**Files:**
- Modify: `tests/unit/ui/test_nav_menu.py`
- Modify: `src/aws_tui/ui/widgets/nav_menu.py`

**Interfaces:**
- Consumes: `NavMenuVM.selected_id`, `NavRow.set_selected(bool)`, and `FocusCoordinatorVM.focused_slot`.
- Produces: persistent `NavRow.-selected` for the active service; focus coordination remains separate.

- [ ] **Step 1: Write a failing focus-independent selection test**

Add imports for `FocusCoordinatorVM` and `FocusSlot`, then add:

```python
@pytest.mark.asyncio
async def test_active_service_stays_selected_when_focus_moves_into_content() -> None:
    vm, hub = _vm_with_services()
    coordinator = FocusCoordinatorVM(
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        initial=FocusSlot.NAV_MENU,
    )
    coordinator.construct()
    nav = NavMenu(vm=vm, hub=hub, focus_coordinator=coordinator)
    app = _Host(nav)
    try:
        async with app.run_test() as pilot:
            vm.switch_service_command.execute("athena")
            await pilot.pause()
            athena = next(row for row in nav.query(NavRow) if row.descriptor_id == "athena")
            assert athena.has_class("-selected")

            coordinator.project_focused_slot(FocusSlot.ATHENA_PRIMARY)
            await pilot.pause()

            assert athena.has_class("-selected")
            assert sum(row.has_class("-selected") for row in nav.query(NavRow)) == 1
    finally:
        coordinator.dispose()
        vm.dispose()
```

Add the same assertion for `settings` with `FocusSlot.SETTINGS` followed by a non-navigation focus projection.

- [ ] **Step 2: Run the new tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_nav_menu.py -q
```

Expected: selection disappears after `focused_slot` leaves `NAV_MENU`.

- [ ] **Step 3: Remove focus from selection eligibility**

In `_rebuild_rows`, construct each row with:

```python
is_selected=(item.descriptor.id == self._vm.selected_id),
```

In `_repaint_rows`, use:

```python
selected_id = self._vm.selected_id
for row in self.query(NavRow):
    row.set_selected(row.descriptor_id == selected_id)
```

Remove `_nav_slot_is_visual_focus` if it has no remaining callers. Keep `_apply_focus_slot_class` so `Screen.-rail-active` still represents keyboard focus.

- [ ] **Step 4: Run navigation and theme-selection regressions**

Run:

```bash
uv run pytest tests/unit/ui/test_nav_menu.py tests/unit/ui/test_themes.py::test_settings_navrow_has_no_specificity_clobber_on_selected_bg tests/unit/ui/test_themes.py::test_selected_state_blocks_use_readable_text_token -q
```

Expected: PASS; active service and Settings retain one selected row across focus changes.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/aws_tui/ui/widgets/nav_menu.py tests/unit/ui/test_nav_menu.py
git commit -m "fix(ui): persist active service selection"
```

---

### 1.3.3. Unframe and Restore Athena Context Selectors

**Files:**
- Modify: `tests/unit/ui/athena/test_page.py`
- Modify: `tests/unit/ui/test_themes.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
- Modify: `src/aws_tui/ui/themes/operational-panes.tcss`

**Interfaces:**
- Consumes: `ContextPicker.set_options`, `ContextPicker.set_state`, `PickerOpenIntent`, and `AthenaPageVM` list/state properties.
- Produces: unframed `#athena-context-row` with individually bordered, genuinely interactive selectors.

- [ ] **Step 1: Replace the grouped-frame test with an unframed-row test**

Replace `test_athena_retains_its_grouped_context_header` with:

```python
@pytest.mark.asyncio
async def test_athena_context_uses_an_unframed_row_of_bordered_selectors() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        row = app.query_one("#athena-context-row", Horizontal)

        assert row.border_title is None
        assert row.styles.border_top[0] in {"", "none"}
        assert app.query_one("#athena-source-header") in row.children
        for selector in ("#athena-workgroup", "#athena-catalog", "#athena-database"):
            picker = app.query_one(selector, ContextPicker)
            assert picker in row.children
            assert picker.styles.border_top[0] in {"solid", "heavy"}
```

Update every test query from `#athena-context-header` to `#athena-context-row`.

- [ ] **Step 2: Add real mouse and keyboard picker tests**

Add one parameterized test at the representative wide viewport that proves each
populated selector participates in the focus ring and opens through the routed
UI:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("picker_id", ["athena-workgroup", "athena-catalog", "athena-database"])
async def test_populated_athena_context_picker_opens_by_mouse_and_keyboard(
    picker_id: str,
) -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(245, 62)) as pilot:
        picker = app.query_one(f"#{picker_id}", ContextPicker)
        assert picker.disabled is False

        await pilot.click(f"#{picker_id}")
        await pilot.pause()
        assert picker.is_open

        await pilot.press("escape")
        picker.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert picker.is_open
```

Keep the existing named-command tests as the keyboard path and the existing
overlay-region tests as the no-reflow path.

- [ ] **Step 3: Update theme-ownership assertions and confirm RED**

Replace grouped-header expectations in
`test_operational_pane_structure_is_shared_theme_owned` and
`test_glue_context_layout_has_no_theme_owned_frame` with assertions that neither
`AthenaPage > #athena-context-row` nor its `:focus-within` variant owns a
theme-owned border.

Run:

```bash
uv run pytest tests/unit/ui/athena/test_page.py::test_athena_context_uses_an_unframed_row_of_bordered_selectors tests/unit/ui/athena/test_page.py::test_populated_athena_context_picker_opens_by_mouse_and_keyboard tests/unit/ui/test_themes.py -q
```

Expected: failures show the old id/title/border and expose any click or enabled-state mismatch.

- [ ] **Step 4: Implement the unframed row and consistent selector state**

In `AthenaPage.compose`, rename the container:

```python
with Horizontal(id="athena-context-row"):
    # Existing source, picker, and load-more children remain in the same order.
```

Remove the `border_title = "AWS context"` assignment from `on_mount`. Replace the corresponding CSS selector with:

```tcss
AthenaPage > #athena-context-row {
    width: 1fr;
    height: auto;
    min-height: 3;
    layout: horizontal;
    border: none;
}
```

Update all child selectors from `#athena-context-header` to `#athena-context-row`. Remove the grouped Athena block from `operational-panes.tcss`.

Keep `_sync_picker` as the single adapter. Its existing availability rule remains:

```python
picker.set_state(
    loading=state is PaneState.LOADING,
    disabled=state is not PaneState.IDLE or not values,
    warning=state is PaneState.FORBIDDEN,
    error=state is PaneState.ERROR,
    tooltip=error_text,
)
```

- [ ] **Step 5: Run Athena context and overlay regressions**

Run:

```bash
uv run pytest tests/unit/ui/athena/test_page.py tests/unit/ui/test_context_picker.py tests/unit/ui/test_themes.py -q
```

Expected: PASS; open overlays do not move the row, tabs, or view host, and context focus order remains unchanged.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/aws_tui/ui/widgets/athena/page.py src/aws_tui/ui/themes/operational-panes.tcss tests/unit/ui/athena/test_page.py tests/unit/ui/test_themes.py
git commit -m "fix(athena): restore context picker interactions"
```

---

### 1.3.4. Align VMx Stop Availability with Interruptibility

**Files:**
- Modify: `tests/unit/vm/athena/test_query_vm.py`
- Modify: `src/aws_tui/vm/athena/query_vm.py`

**Interfaces:**
- Consumes: existing `_can_execute()`, `_can_interrupt()`, `_cancel_active()`, and `AsyncRelayCommand`.
- Produces: `cancel_command.can_execute()` is true during submitting or an owned active execution, matching `cancel()`.

- [ ] **Step 1: Replace the narrow cancel predicate test**

Replace `test_cancel_command_requires_an_owned_active_query` with:

```python
def test_cancel_command_tracks_every_interruptible_query_phase() -> None:
    vm = make_query_vm(InMemoryAthena())
    ref = QueryExecutionRef(
        "q-owned",
        _CONTEXT.connection_name,
        _CONTEXT.region,
        _CONTEXT.workgroup,
    )

    assert not vm.cancel_command.can_execute()

    vm._busy = True  # type: ignore[attr-defined]
    vm._is_submitting = True  # type: ignore[attr-defined]
    assert vm.cancel_command.can_execute()

    vm._is_submitting = False  # type: ignore[attr-defined]
    vm._execution_ref = ref  # type: ignore[attr-defined]
    assert not vm.cancel_command.can_execute()

    vm._owns_active_query = True  # type: ignore[attr-defined]
    assert vm.cancel_command.can_execute()

    vm._busy = False  # type: ignore[attr-defined]
    assert not vm.cancel_command.can_execute()
```

Also extend `test_commands_follow_vmx_gating_and_disposal` so disposal disables both commands from a simulated submitting state.

- [ ] **Step 2: Run the VM test and confirm RED**

Run:

```bash
uv run pytest tests/unit/vm/athena/test_query_vm.py::test_cancel_command_tracks_every_interruptible_query_phase tests/unit/vm/athena/test_query_vm.py::test_commands_follow_vmx_gating_and_disposal -q
```

Expected: submitting-phase cancellation is unavailable through the command.

- [ ] **Step 3: Use the existing interrupt predicate for the VMx command**

Construct the command with the lifecycle-complete predicate using the existing
VMx builder:

```python
self._cancel_command = (
    AsyncRelayCommand.builder()
    .predicate(self._can_interrupt)
    .triggers(self._on_property_changed)
    .task(self._cancel_active)
    .build()
)
```

Simplify `cancel()` to execute that command only when it can execute, and remove
the now-unused `_can_cancel` method:

```python
async def cancel(self) -> None:
    if self._cancel_command.can_execute():
        await self._cancel_command.execute_async()
```

Preserve cancellation ownership, detached-submission cleanup, generation guards,
and shutdown behavior.

- [ ] **Step 4: Run the entire Athena query VM suite**

Run:

```bash
uv run pytest tests/unit/vm/athena/test_query_vm.py -q
```

Expected: PASS; submitting cancellation, active-query cancellation, context replacement, disposal, and shutdown remain safe.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/aws_tui/vm/athena/query_vm.py tests/unit/vm/athena/test_query_vm.py
git commit -m "fix(athena): align stop command with query lifecycle"
```

---

### 1.3.5. Put Compact Query Controls Above the Editor

**Files:**
- Modify: `tests/unit/ui/athena/test_page.py`
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`

**Interfaces:**
- Consumes: `AthenaQueryVM.execute_command`, `cancel_command`, `on_property_changed`, and existing focus slots.
- Produces: stable `Query controls -> Query editor -> Execution detail` geometry with VM-driven button state.

- [ ] **Step 1: Write failing geometry and state tests**

Add:

```python
@pytest.mark.asyncio
async def test_query_controls_are_compact_above_editor_and_inside_their_frame() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        controls = app.query_one("#athena-query-controls")
        editor = app.query_one("#athena-editor", TextArea)
        execute = app.query_one("#athena-execute", Button)
        cancel = app.query_one("#athena-cancel", Button)

        assert controls.border_title == "query controls"
        assert controls.region.bottom <= editor.region.y
        assert controls.region.x <= execute.region.x < execute.region.right <= controls.region.right
        assert controls.region.x <= cancel.region.x < cancel.region.right <= controls.region.right
        assert controls.region.y <= execute.region.y < execute.region.bottom <= controls.region.bottom
        assert controls.region.y <= cancel.region.y < cancel.region.bottom <= controls.region.bottom
        assert execute.region.y == cancel.region.y
```

Add a state test:

```python
@pytest.mark.asyncio
async def test_query_buttons_follow_vmx_command_state() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        execute = app.query_one("#athena-execute", Button)
        cancel = app.query_one("#athena-cancel", Button)
        assert execute.disabled
        assert cancel.disabled

        vm.query.set_sql("SELECT 1")
        await pilot.pause()
        assert not execute.disabled
        assert cancel.disabled

        vm.query._busy = True  # type: ignore[attr-defined]
        vm.query._is_submitting = True  # type: ignore[attr-defined]
        vm.query._notify("is_submitting")  # type: ignore[attr-defined]
        await pilot.pause()
        assert execute.disabled
        assert not cancel.disabled
```

Keep `test_default_focus_and_tab_cycle_are_stable` unchanged as the keyboard-order guard.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/athena/test_page.py::test_query_controls_are_compact_above_editor_and_inside_their_frame tests/unit/ui/athena/test_page.py::test_query_buttons_follow_vmx_command_state tests/unit/ui/athena/test_page.py::test_default_focus_and_tab_cycle_are_stable -q
```

Expected: controls are below the editor, buttons exceed the intended compact frame, title is `query status`, and submitting Stop is disabled before Task 4.

- [ ] **Step 3: Recompose and resize the query view**

Yield the controls before the editor:

```python
with Horizontal(id="athena-query-controls"):
    yield Button("▶", id="athena-execute", classes="-primary", compact=True, flat=True, tooltip="Run the valid read-only query")
    yield Button("■", id="athena-cancel", classes="-danger", compact=True, flat=True, tooltip="Stop query submission or the active query")
    yield Static("", id="athena-query-status", markup=False)
yield TextArea(
    self._vm.sql,
    language="sql",
    soft_wrap=True,
    tab_behavior="focus",
    show_line_numbers=True,
    placeholder="Enter a read-only query",
    id="athena-editor",
)
```

Use stable compact rows:

```tcss
AthenaQueryView {
    height: 1fr;
    layout: grid;
    grid-size: 1 3;
    grid-rows: 3 1fr 7;
    grid-columns: 1fr;
}
AthenaQueryView > #athena-query-controls {
    width: 1fr;
    height: 3;
    layout: horizontal;
    padding: 0 1;
    border-title-align: left;
}
AthenaQueryView #athena-execute,
AthenaQueryView #athena-cancel {
    width: 3;
    min-width: 3;
    height: 1;
    margin: 0 1 0 0;
}
AthenaQueryView #athena-query-status {
    width: 1fr;
    height: 1;
    content-align: left middle;
    text-overflow: ellipsis;
}
```

Set `border_title = "query controls"` in `on_mount`. Keep `_refresh` as the only button-state adapter and preserve its VMx predicates.

- [ ] **Step 4: Run all Athena page widget tests**

Run:

```bash
uv run pytest tests/unit/ui/athena/test_page.py -q
```

Expected: PASS; physical controls move above the editor while Tab still moves editor -> Run -> Stop -> detail.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/aws_tui/ui/widgets/athena/query_view.py tests/unit/ui/athena/test_page.py
git commit -m "fix(athena): place compact query controls above editor"
```

---

### 1.3.6. Prefill Five-Row SQL in the Visible Athena Editor

**Files:**
- Modify: `tests/unit/domain/test_sql_policy.py`
- Modify: `src/aws_tui/domain/sql_policy.py`
- Modify: `tests/integration/test_glue_athena_navigation.py`

**Interfaces:**
- Consumes: `select_starter_sql(TableRef, snapshot_id)`, `AthenaPageVM.open_table`, `AthenaQueryVM.set_sql`, and `AthenaQueryView._refresh`.
- Produces: exact quoted `LIMIT 5` starter SQL in both VM and mounted `TextArea`, without `start_query`.

- [ ] **Step 1: Change starter-query expectations to five rows**

Update the parameterized ordinary and snapshot expected strings in
`tests/unit/domain/test_sql_policy.py`:

```python
(
    None,
    'SELECT * FROM "Catalog""Name"."db name"."table""name" LIMIT 5',
),
(
    42,
    'SELECT * FROM "Catalog""Name"."db name"."table""name" '
    "FOR VERSION AS OF 42 LIMIT 5",
),
```

Update `test_select_starter_sql_delegates_qualified_identifier_quoting` to
expect:

```python
'SELECT * FROM "canonical"."table"."reference" LIMIT 5'
```

Update every generated-starter expectation in `tests/integration/test_glue_athena_navigation.py` from `LIMIT 100` to `LIMIT 5`.

- [ ] **Step 2: Strengthen the real handoff test with visible-editor assertions**

In `test_glue_handoff_surfaces_preserve_source_and_prefill_without_execution`, after waiting for setup, assert:

```python
editor = app.query_one("#athena-editor", TextArea)
assert athena.query.sql == expected_sql
assert editor.text == expected_sql
assert athena.active_view == "query"
assert athena.query.execute_command.can_execute()
assert athena.query.execution_ref is None
assert not any(call.method == "start_query" for call in client.calls)
```

In `test_glue_to_athena_preserves_identity_and_prefills_without_running`, add the same visible-editor equality for the ordinary `Q` app action.

- [ ] **Step 3: Run tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/domain/test_sql_policy.py::test_select_starter_sql_quotes_every_identifier_exactly tests/unit/domain/test_sql_policy.py::test_select_starter_sql_delegates_qualified_identifier_quoting tests/integration/test_glue_athena_navigation.py::test_glue_handoff_surfaces_preserve_source_and_prefill_without_execution tests/integration/test_glue_athena_navigation.py::test_glue_to_athena_preserves_identity_and_prefills_without_running -q
```

Expected: old `LIMIT 100` generation fails. The visible-editor assertion passes
only after the handoff has projected the generated SQL into the mounted Query
view, permanently guarding the reported workflow.

- [ ] **Step 4: Generate the approved five-row starter**

Change only the bounded starter helper:

```python
def select_starter_sql(ref: TableRef, snapshot_id: int | None = None) -> str:
    if snapshot_id is not None and (
        isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int) or snapshot_id < 0
    ):
        raise ValueError("snapshot ID must be a non-negative integer")
    qualified = quote_athena_table_ref(ref)
    travel = f" FOR VERSION AS OF {snapshot_id}" if snapshot_id is not None else ""
    return f"SELECT * FROM {qualified}{travel} LIMIT 5"
```

Do not bypass `AthenaQueryVM.set_sql` or mutate `TextArea` from `AthenaPageVM`.
The acceptance test pins the existing `AthenaQueryView._refresh` projection.

- [ ] **Step 5: Run domain and cross-service navigation suites**

Run:

```bash
uv run pytest tests/unit/domain/test_sql_policy.py tests/integration/test_glue_athena_navigation.py tests/integration/test_athena_s3_handoff.py -q
```

Expected: PASS; ordinary and Iceberg handoffs display exact five-row SQL and make no implicit AWS query call.

- [ ] **Step 6: Commit Task 6**

```bash
git add src/aws_tui/domain/sql_policy.py tests/unit/domain/test_sql_policy.py tests/integration/test_glue_athena_navigation.py
git commit -m "fix(athena): prefill visible five-row starter queries"
```

---

### 1.3.7. Synchronize Documentation and Visual Contracts

**Files:**
- Modify: `docs/services/athena.md`
- Modify: `docs/keybindings.md`
- Modify: `docs/architecture.md`
- Modify: `docs/contract-ledger.md`
- Modify: `tests/snapshot/test_athena.py`
- Modify: `tests/snapshot/test_main_screen.py` only when the persistent selection or command centering changes its golden.
- Regenerate: `generated/site/**`, `generated/wiki/**`, `mkdocs.yml`
- Regenerate: affected files under `tests/snapshot/__snapshots__/test_athena/` and `tests/snapshot/__snapshots__/test_main_screen/`

**Interfaces:**
- Consumes: the implemented UI and the repository's canonical three-surface docs pipeline.
- Produces: synchronized Markdown/site/wiki wording and reviewed all-theme goldens.

- [ ] **Step 1: Update canonical documentation wording**

Make these exact contract changes:

```text
Athena context: four individually framed selectors in one unframed row.
Tab order: source, workgroup, catalog, database, tabs, active view.
Query layout: Query controls, Query editor, Execution detail.
Starter query: quoted SELECT * with LIMIT 5; never auto-executed.
Navigation: active service remains selected while focus is inside its content.
Commands: fitted one-line row is centered; overflow and shortcuts are unchanged.
```

Remove statements that describe a grouped or framed `AWS context` pane and examples ending in `LIMIT 100`.

- [ ] **Step 2: Regenerate and verify all three documentation surfaces**

Run:

```bash
uv run python -m scripts.docs.build_docs --site --wiki
make docs-check
uv run pytest tests/docs -q
```

Expected: generated site/wiki content matches canonical docs, MkDocs strict build passes, and no stale contract wording remains.

- [ ] **Step 3: Update Athena and main-screen goldens**

Run:

```bash
uv run pytest tests/snapshot/test_athena.py tests/snapshot/test_main_screen.py --snapshot-update -q
```

Expected: goldens show an unframed Athena context row, centered one-line Commands pane, persistent active service, and compact Query controls above the editor.

- [ ] **Step 4: Add or update snapshot content guards**

In `tests/snapshot/test_athena.py`, update the existing content guards using the
module's `_snapshot` and `_named_snapshot` helpers:

```python
raw = _named_snapshot("test_athena_query_narrow_snapshot")
assert "AWS&#160;context" not in raw
assert "query&#160;controls" in raw
assert "query&#160;editor" in raw
assert raw.index("query&#160;controls") < raw.index("query&#160;editor")
```

- [ ] **Step 5: Re-run snapshots without update mode**

Run:

```bash
uv run pytest tests/snapshot/test_athena.py tests/snapshot/test_main_screen.py -q
```

Expected: PASS against committed goldens.

- [ ] **Step 6: Commit Task 7**

```bash
git add docs/services/athena.md docs/keybindings.md docs/architecture.md docs/contract-ledger.md generated/site generated/wiki mkdocs.yml tests/snapshot/test_athena.py tests/snapshot/test_main_screen.py tests/snapshot/__snapshots__
git commit -m "docs: synchronize Athena interaction contracts"
```

---

### 1.3.8. Full Verification, Review, and Gitflow Delivery

**Files:**
- Verify: all changed source, tests, docs, generated outputs, and snapshots.
- Update: `docs/superpowers/specs/2026-08-27-athena-ui-contract-repair-design.md` status only after successful integration.

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: one reviewed PR, one squash commit on `develop`, and no dangling feature branch.

- [ ] **Step 1: Run static and architecture gates**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pre-commit run --all-files
```

Expected: all commands exit 0.

- [ ] **Step 2: Run focused acceptance suites**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py tests/unit/ui/test_nav_menu.py tests/unit/ui/athena/test_page.py tests/unit/ui/test_context_picker.py tests/unit/ui/test_themes.py tests/unit/vm/athena/test_query_vm.py tests/unit/domain/test_sql_policy.py tests/integration/test_chrome_and_hint_legend.py tests/integration/test_glue_athena_navigation.py tests/integration/test_athena_page.py tests/snapshot/test_athena.py tests/snapshot/test_main_screen.py -q
```

Expected: PASS with no xfail added for this work.

- [ ] **Step 3: Run the full repository suite and docs gates**

Run:

```bash
uv run pytest -q
make docs-check
uv run pytest tests/docs -q
```

Expected: all tests and strict docs checks pass.

- [ ] **Step 4: Inspect the complete diff and working tree**

Run:

```bash
git diff --check develop...HEAD
git status --short
git log --oneline --decorate develop..HEAD
```

Expected: no whitespace errors, only intended files changed, and no untracked artifacts.

- [ ] **Step 5: Request code review and fix every confirmed finding**

Review specifically for:

```text
VMx command ownership and lifecycle races
Textual focus order and overlay behavior
VM-to-TextArea synchronization races
Theme selector specificity
Snapshot clipping or overlap at narrow and wide sizes
Three-surface documentation drift
```

After each correction, rerun the smallest failing test and then Steps 1-3.

- [ ] **Step 6: Push and create the PR targeting develop**

Run:

```bash
git push -u origin fix/athena-ui-contracts
gh pr create --base develop --head fix/athena-ui-contracts --title "fix: repair Athena interaction contracts" --body-file /tmp/aws-tui-athena-ui-pr.md
gh pr checks --watch
```

The PR body must summarize the five repaired contracts, list exact verification commands, and state that no query is auto-executed.

- [ ] **Step 7: Squash-merge and synchronize local develop**

Run:

```bash
gh pr merge --squash --delete-branch
git switch develop
git pull --ff-only origin develop
```

Expected: `develop` contains one comprehensive fix commit from the PR.

- [ ] **Step 8: Remove local branch and verify cleanup**

Run:

```bash
git branch -d fix/athena-ui-contracts
git fetch --prune origin
git status --short --branch
git branch --all --list "*athena-ui-contracts*"
```

Expected: clean synchronized `develop` and no local or remote feature branch.

---

## 1.4. Acceptance Checklist

- [ ] Commands are centered on every service screen and remain one line.
- [ ] Athena has no outer `AWS context` border.
- [ ] Source, Workgroup, Catalog, and Database retain individual borders.
- [ ] All healthy populated Athena dropdowns open by mouse, Enter/Space, and named command.
- [ ] Athena context and active-view Tab order is unchanged.
- [ ] The active sidebar service remains visibly selected outside rail focus.
- [ ] Glue `Q` opens Athena Query with exact quoted `LIMIT 5` SQL visible.
- [ ] Iceberg snapshot handoff retains `FOR VERSION AS OF` and uses `LIMIT 5`.
- [ ] Handoffs never auto-execute.
- [ ] Query controls render above the editor inside a compact frame.
- [ ] Run is enabled only for valid, idle, read-only SQL.
- [ ] Stop is enabled during submission and owned active execution only.
- [ ] Existing keyboard focus sequence remains editor -> Run -> Stop -> detail.
- [ ] Canonical, site, and wiki docs agree.
- [ ] All affected theme snapshots are nonblank, unclipped, and approved.
- [ ] Full static, test, docs, and snapshot gates pass.
- [ ] PR is squash-merged into `develop` and feature branches are removed.
