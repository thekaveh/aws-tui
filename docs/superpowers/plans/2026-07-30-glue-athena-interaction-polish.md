# Glue and Athena Interaction Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Glue and Athena selectors, focus traversal, profile switching,
commands, cross-service table transfer, borders, and navigation consistent,
discoverable, and keyboard-complete.

**Architecture:** Reuse EMR's inline picker mechanics in a shared Textual
`ContextPicker`, while extending the existing VMx
`DiscriminatorVM[FocusSlot]` facade for deterministic service focus. Keep AWS
source reconstruction in the existing app transaction. Add a VMx-backed typed
table clipboard alongside the existing message-based direct Glue-to-Athena
handoff.

**Tech Stack:** Python 3.13, Textual, VMx 3.1.0, reactivex, pytest,
pytest-textual-snapshot, mypy, Ruff.

## Global Constraints

- Branch from `develop`; merge back to `develop`; do not merge to `main`.
- Preserve read-only Glue and Athena behavior.
- Use `vmx>=3.1.0,<4.0.0` and only VMx public APIs.
- For each changed VM responsibility, compare the complete relevant VMx
  hierarchy and use the most specialized semantically correct primitive.
- Do not add a parallel focus authority beside
  `FocusCoordinatorVM`/`DiscriminatorVM[FocusSlot]`.
- Do not make Textual views call boto, service plugins, or AWS domain clients.
- Keep `Shift+S` as `app.swap_source`.
- Do not bind new printable selector keys at priority over the Athena editor.
- Source-mismatched table-reference insertion must leave the Athena editor and
  typed clipboard unchanged.
- System clipboard integration is best effort; the typed in-app clipboard is
  authoritative.
- Follow red-green-refactor for every production behavior change.
- Record VM/view/test LOC and coverage deltas against the branch point.

---

### Task 1: Shared bordered picker and service tab strip

**Files:**
- Create: `src/aws_tui/ui/widgets/context_picker.py`
- Create: `src/aws_tui/ui/widgets/service_tab_strip.py`
- Create: `tests/unit/ui/test_context_picker.py`
- Create: `tests/unit/ui/test_service_tab_strip.py`
- Reference: `src/aws_tui/ui/widgets/emr_serverless/application_picker.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True, slots=True)
class ContextOption:
    label: str
    value: str

class ContextPicker(Widget):
    class Changed(Message):
        value: str

    def set_options(
        self,
        options: tuple[ContextOption, ...],
        *,
        selected: str | None,
    ) -> None: ...
    def set_state(
        self,
        *,
        loading: bool = False,
        disabled: bool = False,
        warning: bool = False,
        error: bool = False,
        tooltip: str | None = None,
    ) -> None: ...
    def open(self) -> None: ...
    def close(self, *, restore: bool = True) -> None: ...
    @property
    def is_open(self) -> bool: ...

class ServiceTabStrip(Widget):
    class Changed(Message):
        value: str

    def __init__(
        self,
        tabs: tuple[tuple[str, str], ...],
        *,
        active: str,
        id: str | None = None,
    ) -> None: ...
    def set_active(self, value: str) -> None: ...
```

- `ContextPicker` expands its `OptionList` inline and never mounts a detached
  `Select` overlay.
- `ServiceTabStrip` is one Textual focus target; left/right changes the active
  tab and emits `Changed`; Enter/Space selects the highlighted tab.

- [ ] **Step 1: Write failing picker behavior tests**

Cover initial label/value, open/close, up/down, Enter commit, Escape restore,
mouse selection, empty/disabled/loading/warning/error states, and stable widget
height while closed.

```python
async def test_context_picker_commits_keyboard_selection() -> None:
    picker = ContextPicker(
        "Workgroup",
        (
            ContextOption("primary", "primary"),
            ContextOption("analytics", "analytics"),
        ),
        selected="primary",
    )
    async with PickerHost(picker).run_test() as pilot:
        picker.focus()
        await pilot.press("enter", "down", "enter")
        assert picker.value == "analytics"
        assert picker.is_open is False
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest tests/unit/ui/test_context_picker.py \
  tests/unit/ui/test_service_tab_strip.py -q
```

Expected: collection fails because the new modules do not exist.

- [ ] **Step 3: Implement the minimal shared widgets**

Port the proven inline trigger/list interaction from EMR without copying
service-specific application state. Use Textual messages for committed values
and keep labels, open state, and styles local to the widgets.

- [ ] **Step 4: Verify GREEN and refactor**

Run the focused command from Step 2 plus:

```bash
uv run pytest tests/unit/ui/emr_serverless/test_application_picker.py -q
uv run mypy
uv run ruff check
```

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/ui/widgets/context_picker.py \
  src/aws_tui/ui/widgets/service_tab_strip.py \
  tests/unit/ui/test_context_picker.py \
  tests/unit/ui/test_service_tab_strip.py
git commit -m "feat(ui): add bordered context pickers"
```

---

### Task 2: VMx-backed deterministic Glue and Athena focus rings

**Files:**
- Modify: `src/aws_tui/vm/chrome/focus_coordinator_vm.py`
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/ui/widgets/glue/page.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
- Modify: `src/aws_tui/ui/widgets/glue/jobs_view.py`
- Modify: `src/aws_tui/ui/widgets/glue/crawlers_view.py`
- Modify: `tests/unit/vm/chrome/test_focus_coordinator_vm.py`
- Modify: `tests/unit/ui/glue/test_page.py`
- Modify: `tests/unit/ui/athena/test_page.py`

**Interfaces:**
- Extend `FocusSlot` with:

```python
GLUE_SOURCE = "glue.source"
GLUE_FILTER = "glue.filter"
GLUE_TABS = "glue.tabs"
GLUE_PRIMARY = "glue.primary"
GLUE_SECONDARY = "glue.secondary"
GLUE_DETAIL = "glue.detail"
ATHENA_SOURCE = "athena.source"
ATHENA_WORKGROUP = "athena.workgroup"
ATHENA_CATALOG = "athena.catalog"
ATHENA_DATABASE = "athena.database"
ATHENA_TABS = "athena.tabs"
ATHENA_PRIMARY = "athena.primary"
ATHENA_SECONDARY = "athena.secondary"
ATHENA_DETAIL = "athena.detail"
```

- Add:

```python
def cycle_focus_ring(
    self,
    slots: tuple[FocusSlot, ...],
    *,
    reverse: bool = False,
) -> FocusSlot: ...
```

- `cycle_focus_ring` delegates active identity to the existing VMx
  `DiscriminatorVM`; it validates a non-empty ring and chooses the first slot
  when the current slot is absent.
- Glue/Athena pages expose `_focus_targets() -> tuple[(FocusSlot, Widget), ...]`
  and project the active logical slot to the current view's concrete widget.

- [ ] **Step 1: Write failing VM focus tests**

Test ring entry, wraparound, reverse inversion, hidden-slot omission performed by
the caller, modal restore, and composition shape:

```python
def test_service_ring_uses_vmx_discriminator() -> None:
    vm = make_focus_coordinator()
    assert isinstance(vm._focus_discriminator, DiscriminatorVM)
    vm.cycle_focus_ring((FocusSlot.GLUE_SOURCE, FocusSlot.GLUE_TABS))
    assert vm.focused_slot is FocusSlot.GLUE_SOURCE
```

- [ ] **Step 2: Write failing complete page-ring tests**

For every Glue view and Athena view, collect visited widget IDs by repeatedly
calling `cycle_focus(reverse=False)` until the first ID repeats. Assert the
expected ordered IDs, then repeat with reverse traversal and assert the exact
inverse. Verify disabled load-more controls and hidden panes are omitted.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
uv run pytest tests/unit/vm/chrome/test_focus_coordinator_vm.py \
  tests/unit/ui/glue/test_page.py \
  tests/unit/ui/athena/test_page.py -q
```

Expected: new slots/methods are missing and current raw DOM traversal produces
the wrong order.

- [ ] **Step 4: Implement VMx focus-ring ownership and view projection**

Replace both page implementations of `app.action_focus_next/previous()` with
logical ring construction plus `FocusCoordinatorVM.cycle_focus_ring()`. Use the
shared one-stop `ServiceTabStrip`. Retain runtime Textual focus as the final
widget projection.

- [ ] **Step 5: Verify GREEN**

Run Step 3 plus:

```bash
uv run pytest tests/unit/ui/glue/test_iceberg_view.py \
  tests/unit/ui/emr_serverless/test_application_picker.py \
  tests/unit/vm/chrome/test_focus_coordinator_vm.py -q
uv run mypy
uv run ruff check
```

- [ ] **Step 6: Record VMx decision and commit**

Append the `vmx31-glue-athena-focus-rings` entry to the design's evidence ledger
with `DiscriminatorVM` selected and `FilteredCompositeVM`, `GroupVM`, and
`HierarchicalVM` rejected for the semantic reasons in section 6.

```bash
git add src/aws_tui/vm/chrome/focus_coordinator_vm.py \
  src/aws_tui/app.py \
  src/aws_tui/ui/widgets/glue \
  src/aws_tui/ui/widgets/athena \
  tests/unit/vm/chrome/test_focus_coordinator_vm.py \
  tests/unit/ui/glue/test_page.py \
  tests/unit/ui/athena/test_page.py \
  docs/superpowers/specs/2026-07-30-glue-athena-interaction-polish-design.md
git commit -m "feat(ui): make service focus rings deterministic"
```

---

### Task 3: Focusable source selection and named selector commands

**Files:**
- Modify: `src/aws_tui/ui/widgets/service_source_header.py`
- Modify: `src/aws_tui/ui/widgets/service_view_factory.py`
- Modify: `src/aws_tui/ui/widgets/glue/page.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/infra/keymap_store.py`
- Modify: `src/aws_tui/ui/bindings.py`
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Modify: `tests/unit/ui/test_service_source_header.py`
- Modify: `tests/unit/ui/test_service_view_factory.py`
- Modify: `tests/integration/test_keybinding_wiring.py`
- Modify: `tests/integration/test_command_palette_wiring.py`
- Modify: `tests/integration/test_service_source_swap.py`
- Modify: `tests/integration/test_chrome_and_hint_legend.py`

**Interfaces:**
- `ServiceSourceHeader` wraps `ContextPicker` and accepts:

```python
def __init__(
    self,
    source: ServiceSourceContext,
    *,
    candidates: tuple[ServiceSourceContext, ...] = (),
    id: str | None = None,
) -> None: ...
```

- The source picker emits the selected `(connection_name, region)` identity.
- Add app helper:

```python
async def _switch_single_context_source_to(
    self,
    service_id: str,
    connection_name: str,
    region: str,
) -> None: ...
```

- `_swap_single_context_source()` resolves the next candidate and delegates to
  `_switch_single_context_source_to()`.
- Add action IDs from design section 3.3. Selector actions focus and open the
  named picker; they do not cycle values.

- [ ] **Step 1: Write failing source-picker and action-wiring tests**

Assert that source candidates are filtered by `service.supports`, the active
source is selected, choosing a target probes and mounts exactly that connection,
`Shift+S` still cycles, selector actions appear in the palette/hints, and no new
printable selector binding steals input from `TextArea`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/unit/ui/test_service_source_header.py \
  tests/unit/ui/test_service_view_factory.py \
  tests/integration/test_keybinding_wiring.py \
  tests/integration/test_command_palette_wiring.py \
  tests/integration/test_service_source_swap.py \
  tests/integration/test_chrome_and_hint_legend.py -q
```

- [ ] **Step 3: Implement source projection and named commands**

Pass supported source contexts from `AwsTuiApp` through
`build_service_view()` into Glue/Athena pages. Route picker changes through the
existing cancellation/rebuild transaction. Register all selector actions in
`ActionRegistry`, `_PALETTE_ACTIONS`, binding descriptions, hint labels, and
service action sets.

- [ ] **Step 4: Verify GREEN and regress source handoffs**

Run Step 2 plus:

```bash
uv run pytest tests/integration/test_glue_athena_navigation.py \
  tests/integration/test_glue_iceberg_lifecycle.py -q
uv run mypy
uv run ruff check
```

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/app.py src/aws_tui/infra/keymap_store.py \
  src/aws_tui/ui/bindings.py \
  src/aws_tui/ui/widgets/service_source_header.py \
  src/aws_tui/ui/widgets/service_view_factory.py \
  src/aws_tui/ui/widgets/glue/page.py \
  src/aws_tui/ui/widgets/athena/page.py \
  src/aws_tui/vm/chrome/hint_legend_vm.py \
  tests/unit/ui/test_service_source_header.py \
  tests/unit/ui/test_service_view_factory.py \
  tests/integration/test_keybinding_wiring.py \
  tests/integration/test_command_palette_wiring.py \
  tests/integration/test_service_source_swap.py \
  tests/integration/test_chrome_and_hint_legend.py
git commit -m "feat(ui): expose service context commands"
```

---

### Task 4: Bordered Glue/Athena layout and centered navigation

**Files:**
- Modify: `src/aws_tui/ui/widgets/glue/page.py`
- Modify: `src/aws_tui/ui/widgets/glue/catalog_view.py`
- Modify: `src/aws_tui/ui/widgets/glue/jobs_view.py`
- Modify: `src/aws_tui/ui/widgets/glue/crawlers_view.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`
- Modify: `src/aws_tui/ui/widgets/athena/history_view.py`
- Modify: `src/aws_tui/ui/widgets/athena/results_view.py`
- Modify: `src/aws_tui/ui/widgets/athena/saved_view.py`
- Modify: `src/aws_tui/ui/widgets/nav_menu.py`
- Modify: `src/aws_tui/ui/widgets/nav_row.py`
- Modify: `src/aws_tui/ui/themes/*.tcss`
- Modify: `tests/unit/ui/test_nav_menu.py`
- Modify: `tests/unit/ui/test_themes.py`
- Modify: `tests/snapshot/apps/glue.py`
- Modify: `tests/snapshot/apps/athena.py`
- Modify: `tests/snapshot/apps/nav_menu.py`
- Modify: `tests/snapshot/test_glue.py`
- Modify: `tests/snapshot/test_athena.py`
- Modify: `tests/snapshot/test_nav_menu.py`

**Interfaces:**
- Navigation width is one shared constant used by widget CSS assumptions and
  themes; it provides at least one cell of padding around `Athena`.
- Resource and detail borders use existing theme variables and
  `border-title-color: $accent` under `:focus-within`.

- [ ] **Step 1: Add failing layout and theme assertions**

Assert centered service labels, stable rail width, matching theme selectors,
context border titles, query-editor/result borders, and focus accents.

- [ ] **Step 2: Add failing snapshot cases**

Add standard and narrow fixtures showing Glue Catalog/Jobs/Crawlers, Athena
Query with all context controls, an open picker, and the full service rail.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
uv run pytest tests/unit/ui/test_nav_menu.py tests/unit/ui/test_themes.py \
  tests/snapshot/test_glue.py tests/snapshot/test_athena.py \
  tests/snapshot/test_nav_menu.py -q
```

- [ ] **Step 4: Implement borders, sizing, and centering**

Apply the approved pane hierarchy without nesting decorative cards. Keep stable
row heights in closed state and enough open-picker height for meaningful
iteration.

- [ ] **Step 5: Update snapshots and inspect rendered SVGs**

Use the repository snapshot update command for only the affected files. Render
or open every changed SVG and check text fit, border continuity, focus
visibility, overlap, and narrow-layout clipping.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/unit/ui/test_nav_menu.py tests/unit/ui/test_themes.py \
  tests/snapshot/test_glue.py tests/snapshot/test_athena.py \
  tests/snapshot/test_nav_menu.py -q
uv run mypy
uv run ruff check
git add src/aws_tui/ui tests/unit/ui tests/snapshot
git commit -m "fix(ui): align service pane presentation"
```

---

### Task 5: Canonical SQL identifier and VMx typed table clipboard

**Files:**
- Modify: `src/aws_tui/domain/sql_policy.py`
- Create: `src/aws_tui/vm/table_clipboard_vm.py`
- Modify: `src/aws_tui/composition.py`
- Modify: `tests/unit/domain/test_sql_policy.py`
- Create: `tests/unit/vm/test_table_clipboard_vm.py`
- Modify: `tests/unit/vm/test_vmx_smoke.py`
- Modify: `tests/unit/vm/test_round3_compliance.py`

**Interfaces:**

```python
def quote_athena_table_ref(ref: TableRef) -> str: ...

@dataclass(frozen=True, slots=True)
class CopiedTableReference:
    table_ref: TableRef
    sql_identifier: str

class TableClipboardVM:
    @property
    def copied_table(self) -> CopiedTableReference | None: ...
    @property
    def copy_command(self) -> RelayCommandOf[TableRef]: ...
    @property
    def on_property_changed(self) -> Observable[str]: ...
    def construct(self) -> None: ...
    def dispose(self) -> None: ...
```

- `TableClipboardVM` composes
  `ComponentVMOf[CopiedTableReference | None]` and VMx
  `RelayCommandOf[TableRef]`.
- `select_starter_sql()` delegates table quoting to
  `quote_athena_table_ref()`.
- `AppContext.table_clipboard_vm` is constructed and disposed with the app.

- [ ] **Step 1: Write failing quote and clipboard tests**

Cover embedded double quotes in all three identifier segments, exact
`TableRef` preservation, equal-copy no-op notification, replacement copy,
command disposal, and VMx composition shape.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest tests/unit/domain/test_sql_policy.py \
  tests/unit/vm/test_table_clipboard_vm.py \
  tests/unit/vm/test_vmx_smoke.py \
  tests/unit/vm/test_round3_compliance.py -q
```

- [ ] **Step 3: Implement the minimal domain helper and VM**

Use only public `ComponentVMOf.model`, `on_property_changed`, and
`RelayCommandOf` APIs. Do not add a custom subject when the modeled component's
observable already provides the required signal.

- [ ] **Step 4: Verify GREEN and lifecycle integration**

Run Step 2 plus:

```bash
uv run pytest tests/unit/test_composition_emr_registered.py \
  tests/unit/test_app_context_unreachable.py \
  tests/unit/test_app_sanity.py -q
uv run mypy
uv run ruff check
```

- [ ] **Step 5: Record VMx decision and commit**

Record `vmx31-table-clipboard` with `ComponentVMOf` and `RelayCommandOf`
selected; record that `DiscriminatorVM`, `CompositeVM`, `DerivedProperty`, and
messages alone do not model one replaceable persistent payload.

```bash
git add src/aws_tui/domain/sql_policy.py \
  src/aws_tui/vm/table_clipboard_vm.py src/aws_tui/composition.py \
  tests/unit/domain/test_sql_policy.py \
  tests/unit/vm/test_table_clipboard_vm.py \
  tests/unit/vm/test_vmx_smoke.py \
  tests/unit/vm/test_round3_compliance.py \
  docs/superpowers/specs/2026-07-30-glue-athena-interaction-polish-design.md
git commit -m "feat(vmx): add typed table clipboard"
```

---

### Task 6: Glue copy and Athena cursor insertion

**Files:**
- Modify: `src/aws_tui/vm/messages.py`
- Modify: `src/aws_tui/vm/glue/catalog_vm.py`
- Modify: `src/aws_tui/vm/glue/page_vm.py`
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/infra/keymap_store.py`
- Modify: `src/aws_tui/ui/bindings.py`
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Modify: `tests/unit/vm/test_messages.py`
- Modify: `tests/unit/vm/glue/test_catalog_vm.py`
- Modify: `tests/unit/vm/glue/test_page_vm.py`
- Create: `tests/unit/ui/athena/test_query_view.py`
- Create: `tests/integration/test_glue_athena_clipboard.py`
- Modify: `tests/integration/test_command_palette_wiring.py`
- Modify: `tests/integration/test_service_source_swap.py`
- Modify: `tests/integration/test_glue_athena_navigation.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class CopyTableReferenceRequest:
    table_ref: TableRef

def copy_table_reference(self) -> bool: ...

def insert_table_reference(self, identifier: str) -> bool: ...
```

- `GlueCatalogVM.copy_table_reference()` sends the immutable request only for a
  currently valid selected table.
- App handling executes `TableClipboardVM.copy_command`, calls
  `App.copy_to_clipboard(sql_identifier)`, and raises a success toast.
- Athena insertion uses the public `TextArea.replace()` range API, replaces the
  active selection or inserts at the cursor, then synchronizes
  `AthenaQueryVM.sql`.
- Source mismatch refuses insertion without changing clipboard or editor.
- Insertion does not execute SQL.

- [ ] **Step 1: Write failing VM/message tests**

Assert hostile `TableRef` validation, selected-table request identity, no
selection no-op, and page delegation.

- [ ] **Step 2: Write failing editor and integration tests**

Cover cursor insertion, selection replacement, VM synchronization, palette
actions, best-effort system copy call, empty clipboard, source mismatch,
matching-source insertion, query-view activation, and no execution.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
uv run pytest tests/unit/vm/test_messages.py \
  tests/unit/vm/glue/test_catalog_vm.py \
  tests/unit/vm/glue/test_page_vm.py \
  tests/unit/ui/athena/test_query_view.py \
  tests/integration/test_glue_athena_clipboard.py \
  tests/integration/test_command_palette_wiring.py \
  tests/integration/test_service_source_swap.py \
  tests/integration/test_glue_athena_navigation.py -q
```

- [ ] **Step 4: Implement copy and insert paths**

Keep the existing `OpenAthenaTableRequest` transaction unchanged. Add the copy
request as a parallel non-navigating path and use existing toast infrastructure.

- [ ] **Step 5: Verify GREEN and regression behavior**

Run Step 3 plus:

```bash
uv run pytest tests/integration/test_glue_iceberg_lifecycle.py \
  tests/unit/vm/athena/test_query_vm.py \
  tests/unit/ui/glue/test_iceberg_view.py -q
uv run mypy
uv run ruff check
```

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/app.py src/aws_tui/infra/keymap_store.py \
  src/aws_tui/ui/bindings.py src/aws_tui/ui/widgets/athena \
  src/aws_tui/vm/messages.py src/aws_tui/vm/glue \
  src/aws_tui/vm/chrome/hint_legend_vm.py \
  tests/unit/vm tests/unit/ui/athena \
  tests/integration/test_glue_athena_clipboard.py \
  tests/integration/test_command_palette_wiring.py \
  tests/integration/test_service_source_swap.py \
  tests/integration/test_glue_athena_navigation.py
git commit -m "feat(glue): copy table references into Athena"
```

---

### Task 7: Documentation, metrics, and full branch verification

**Files:**
- Modify: `docs/keybindings.md`
- Modify: `docs/superpowers/specs/2026-07-30-glue-athena-interaction-polish-design.md`
- Modify: `docs/superpowers/plans/2026-07-30-glue-athena-interaction-polish.md`
- Modify: `CHANGELOG.md` if the repository's unreleased section requires it

**Interfaces:**
- The final evidence ledger records each VMx decision, exact public APIs,
  rejected alternatives, bespoke code retained, tests, LOC, and coverage.
- Metrics use the branch point:

```bash
BASE=$(git merge-base develop HEAD)
git diff --numstat "$BASE"...HEAD -- src/aws_tui/vm src/aws_tui/ui tests
```

- [ ] **Step 1: Update user-facing documentation**

Document full Glue/Athena focus order, selector commands, `Shift+S`, copy,
insert, direct query handoff, source-mismatch behavior, and editor-safe
keybinding priority.

- [ ] **Step 2: Calculate LOC and coverage metrics**

Record VM deleted/added/saved, view deleted/added/saved, test delta, net
implementation savings, suite count, and before/after coverage. Treat moved
code as neutral.

- [ ] **Step 3: Run focused and architecture verification**

```bash
uv run pytest tests/unit/vm/test_vmx_smoke.py -q
uv run pytest tests/unit/vm/glue tests/unit/vm/athena \
  tests/unit/vm/chrome -q
uv run pytest tests/unit/ui/glue tests/unit/ui/athena \
  tests/unit/ui/test_nav_menu.py -q
uv run pytest tests/integration/test_glue_iceberg_lifecycle.py \
  tests/integration/test_glue_athena_navigation.py \
  tests/integration/test_glue_athena_clipboard.py \
  tests/integration/test_service_source_swap.py -q
uv run mypy
uv run ruff check
```

- [ ] **Step 4: Run full suite and snapshots**

```bash
uv run pytest tests/unit tests/integration \
  --cov=aws_tui --cov-report=term-missing --cov-report=xml
uv run pytest tests/snapshot -q
```

- [ ] **Step 5: Inspect final diff and repository state**

```bash
git diff --check
git status --short
git diff "$(git merge-base develop HEAD)"...HEAD --stat
```

- [ ] **Step 6: Commit final documentation**

```bash
git add docs/keybindings.md \
  docs/superpowers/specs/2026-07-30-glue-athena-interaction-polish-design.md \
  docs/superpowers/plans/2026-07-30-glue-athena-interaction-polish.md \
  CHANGELOG.md
git commit -m "docs: record Glue and Athena interaction polish"
```
