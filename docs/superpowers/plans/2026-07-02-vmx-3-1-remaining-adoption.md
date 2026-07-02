# VMx 3.1 Remaining Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remaining aws-tui viewmodel mini-primitives and bespoke control-flow code with the best-fitting VMx 3.1.0 primitives where doing so preserves aws-tui's public VM/view contracts.

**Architecture:** Keep aws-tui facades as the public API for the view layer. Compose VMx 3.1 primitives internally: `ScoredFilteredCompositeVM` for palette filtering, VMx `FilteredCompositeVM` for pane projection, `DiscriminatorVM` for focus state, `TokenPagedComposition` for EMR job-run pagination, `ModalVM` for result-bearing modal facades, `when_property_changed` for shared hub subscriptions, and `AsyncRelayCommand` where the view needs a command-shaped async action. Each replacement gets its own tests, verification, LOC metric, and commit.

**Tech Stack:** Python 3.11+, VMx 3.1.0, reactivex, Textual, pytest, pytest-cov, ruff, mypy.

## Global Constraints

- Runtime dependency remains `vmx>=3.1.0,<4.0.0`.
- Public aws-tui VM/view APIs remain stable unless a test explicitly records an intentional behavior change.
- Do not expose raw VMx internals in public aws-tui VM APIs; private `_inner` or `_filtered_composite` shape assertions are allowed only in tests.
- Preserve view-facing events: existing `PropertyChangedMessage` names and per-VM observables must continue to fire.
- Preserve async stale-target guards in EMR polling and pagination.
- Record VM/view/test LOC deltas and coverage deltas in `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md` after each committed replacement.

---

## File Structure

- Modify `src/aws_tui/vm/chrome/command_palette_vm.py`: replace manual `_filtered` recompute machinery with VMx `ScoredFilteredCompositeVM`; evaluate localized `AsyncRelayCommand` for async palette actions.
- Modify `src/aws_tui/vm/file_manager/pane_vm.py`: replace local aws-tui `FilteredCompositeVM` with VMx `FilteredCompositeVM` and `FilteredCursorPolicy`.
- Delete `src/aws_tui/vm/_composition/filtered_composite_vm.py` and `tests/unit/vm/_composition/test_filtered_composite_vm.py` after `PaneVM` uses VMx directly.
- Modify `src/aws_tui/vm/_composition/__init__.py`: remove the deleted local filter export.
- Modify `src/aws_tui/vm/chrome/focus_coordinator_vm.py`: replace `_focused_slot`, `_saved_slot`, and `_on_changed` storage with VMx `DiscriminatorVM[FocusSlot]`.
- Modify `src/aws_tui/vm/emr_serverless/job_runs_vm.py`: move accumulated page storage and token state to VMx `TokenPagedComposition` while preserving `CompositeVM.current` selection and stale identity guards.
- Modify modal VMs in `src/aws_tui/vm/chrome/{confirm_vm,resume_vm,first_run_vm,crash_vm}.py`: compose VMx `ModalVM[T]` for result completion.
- Modify `src/aws_tui/ui/widgets/_subscriber.py` and direct widget subscribers where safe: use VMx `when_property_changed` for shared hub property filtering.
- Modify tests under `tests/unit/vm`, `tests/unit/ui`, and `tests/integration` as listed in each task.
- Modify `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`: update replacement matrix, roll-up table, and ledger entries after each task.

---

### Task 1: CommandPaletteVM Uses ScoredFilteredCompositeVM

**Files:**
- Modify: `tests/unit/vm/chrome/test_command_palette.py`
- Modify: `tests/unit/vm/test_round3_compliance.py`
- Modify: `src/aws_tui/vm/chrome/command_palette_vm.py`
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: VMx `ScoredFilteredCompositeVM[ComponentVMOf[PaletteEntry]]`.
- Produces: unchanged public `filter_text`, `filtered_entries`, `selected_index`, register/unregister, and command surface.

- [ ] **Step 1: Write failing shape tests**

Add to `tests/unit/vm/chrome/test_command_palette.py`:

```python
def test_palette_uses_vmx_scored_filtered_composite() -> None:
    from vmx import ScoredFilteredCompositeVM

    vm = _build()
    try:
        assert isinstance(vm._scored_filter, ScoredFilteredCompositeVM)
    finally:
        vm.dispose()
```

Add to `tests/unit/vm/test_round3_compliance.py`:

```python
def test_command_palette_vm_composes_scored_filter_internally() -> None:
    from vmx import ScoredFilteredCompositeVM

    from aws_tui.vm.chrome.command_palette_vm import CommandPaletteVM

    vm = CommandPaletteVM(hub=_hub(), dispatcher=NULL_DISPATCHER)
    assert hasattr(vm, "_scored_filter")
    assert isinstance(vm._scored_filter, ScoredFilteredCompositeVM)
    assert not any("scored_filter" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_command_palette.py::test_palette_uses_vmx_scored_filtered_composite tests/unit/vm/test_round3_compliance.py::test_command_palette_vm_composes_scored_filter_internally -q
```

Expected: both fail because `_scored_filter` does not exist.

- [ ] **Step 3: Implement the scored filter**

In `src/aws_tui/vm/chrome/command_palette_vm.py`, import:

```python
from vmx import ScoredFilteredCompositeVM
```

Add a private scorer that preserves existing lower-is-better ranking by returning a higher-is-better VMx score:

```python
def _score_for_vmx(self, item_inner: ComponentVMOf[PaletteEntry]) -> int | None:
    score = _score(item_inner.model, self._filter_text)
    if score is None:
        return None
    return -score
```

Construct:

```python
self._scored_filter: ScoredFilteredCompositeVM[ComponentVMOf[PaletteEntry]] = (
    ScoredFilteredCompositeVM(self._inner_registry, scorer=self._score_for_vmx)
)
```

Replace `_recompute_filtered()` so it calls `self._scored_filter.refresh_scores()`, derives `new_filtered` from `self._scored_filter.visible`, and keeps the existing public `filtered_entries` / `selected_index` event behavior.

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_command_palette.py tests/unit/vm/test_round3_compliance.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Record metrics and commit**

Run:

```bash
git diff --numstat HEAD -- src/aws_tui/vm src/aws_tui/ui tests
uv run pytest tests/unit/vm/chrome/test_command_palette.py tests/unit/vm/test_round3_compliance.py -q
uv run ruff check
uv run mypy
git add src/aws_tui/vm/chrome/command_palette_vm.py tests/unit/vm/chrome/test_command_palette.py tests/unit/vm/test_round3_compliance.py docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md docs/superpowers/plans/2026-07-02-vmx-3-1-remaining-adoption.md
git commit -m "refactor: use VMx scored filter for command palette"
```

---

### Task 2: PaneVM Uses VMx FilteredCompositeVM

**Files:**
- Modify: `tests/unit/vm/file_manager/test_pane_vm.py`
- Modify: `tests/unit/vm/test_round3_compliance.py`
- Modify: `src/aws_tui/vm/file_manager/pane_vm.py`
- Modify: `src/aws_tui/vm/_composition/__init__.py`
- Delete: `src/aws_tui/vm/_composition/filtered_composite_vm.py`
- Delete: `tests/unit/vm/_composition/test_filtered_composite_vm.py`
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: VMx `FilteredCompositeVM[ComponentVMOf[EntryState]]` and `FilteredCursorPolicy.SNAP_TO_FIRST`.
- Produces: unchanged `PaneVM.filtered_entries`, `cursor_index`, `selected_entry`, and filter commands.

- [ ] **Step 1: Write failing shape test**

Add to `tests/unit/vm/file_manager/test_pane_vm.py`:

```python
@pytest.mark.asyncio
async def test_pane_uses_vmx_filtered_composite() -> None:
    from vmx import FilteredCompositeVM

    fs = await _seed_fs()
    pane = await _make_pane(fs)
    try:
        assert isinstance(pane._filtered_composite, FilteredCompositeVM)
    finally:
        pane.dispose()
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/unit/vm/file_manager/test_pane_vm.py::test_pane_uses_vmx_filtered_composite -q
```

Expected: fails because `_filtered_composite` is the aws-tui helper class.

- [ ] **Step 3: Swap imports and constructor**

In `src/aws_tui/vm/file_manager/pane_vm.py`, replace:

```python
from aws_tui.vm._composition import FilteredCompositeVM
```

with:

```python
from vmx import FilteredCompositeVM, FilteredCursorPolicy
```

Construct with:

```python
self._filtered_composite = FilteredCompositeVM(
    self._inner,
    predicate=self._filter_predicate,
    cursor_policy=FilteredCursorPolicy.SNAP_TO_FIRST,
)
```

Keep `PaneVM._move_cursor()` as the source of clamped cursor behavior; do not call VMx `move_to_next_visible()` / `move_to_previous_visible()` from PaneVM.

- [ ] **Step 4: Delete local helper and stale tests**

Remove local files:

```bash
git rm src/aws_tui/vm/_composition/filtered_composite_vm.py tests/unit/vm/_composition/test_filtered_composite_vm.py
```

Update `src/aws_tui/vm/_composition/__init__.py` to:

```python
"""aws-tui-side mini-primitives composing VMx artifacts."""

__all__: list[str] = []
```

- [ ] **Step 5: Verify green**

Run:

```bash
uv run pytest tests/unit/vm/file_manager/test_pane_vm.py tests/unit/vm/file_manager/test_pane_vm_contracts.py tests/unit/vm/test_round3_compliance.py -q
rg -n "aws_tui\\.vm\\._composition|FilteredCompositeVM — aws-tui|test_filtered_composite_vm" src tests
```

Expected: tests pass and `rg` finds no stale local-helper references.

- [ ] **Step 6: Record metrics and commit**

Run focused verification, update the audit, then:

```bash
git commit -m "refactor: use VMx filtered composite for panes"
```

---

### Task 3: FocusCoordinatorVM Uses DiscriminatorVM

**Files:**
- Modify: `tests/unit/vm/chrome/test_focus_coordinator_vm.py`
- Modify: `tests/unit/vm/test_round3_compliance.py`
- Modify: `src/aws_tui/vm/chrome/focus_coordinator_vm.py`
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: VMx `DiscriminatorVM[FocusSlot]`.
- Produces: unchanged `focused_slot`, `is_modal`, `on_focused_slot_changed`, `set_focused_slot`, `modal_open`, `modal_close`, and focus-ring cycling.

- [ ] **Step 1: Write failing shape test**

Add to `tests/unit/vm/chrome/test_focus_coordinator_vm.py`:

```python
def test_focus_coordinator_uses_vmx_discriminator() -> None:
    from vmx import DiscriminatorVM

    vm = _make()
    try:
        assert isinstance(vm._discriminator, DiscriminatorVM)
    finally:
        vm.dispose()
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_focus_coordinator_vm.py::test_focus_coordinator_uses_vmx_discriminator -q
```

Expected: fails because `_discriminator` does not exist.

- [ ] **Step 3: Implement discriminator-backed facade**

In `src/aws_tui/vm/chrome/focus_coordinator_vm.py`, import `DiscriminatorVM`. Replace `_focused_slot` with `_discriminator = DiscriminatorVM[FocusSlot](initial)`. Subscribe to `active_changed` and route changes through a helper that emits `on_focused_slot_changed` and the hub `PropertyChangedMessage`. Keep a private `_saved_slot: FocusSlot | None` if needed to preserve the existing defensive test that manually clears `_saved_slot`.

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_focus_coordinator_vm.py tests/unit/vm/test_round3_compliance.py -q
```

- [ ] **Step 5: Record metrics and commit**

Update the audit and commit:

```bash
git commit -m "refactor: use VMx discriminator for focus coordination"
```

---

### Task 4: JobRunsVM Uses TokenPagedComposition

**Files:**
- Modify: `tests/unit/vm/emr_serverless/test_job_runs_vm.py`
- Modify: `src/aws_tui/vm/emr_serverless/job_runs_vm.py`
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: VMx `TokenPagedComposition[JobRunItemVM, str]`.
- Produces: unchanged `runs`, `selected_id`, `has_more`, `refresh()`, `load_more()`, stale application/token guards, and client-side state filters.

- [ ] **Step 1: Write failing shape test**

Add:

```python
def test_job_runs_vm_uses_token_paged_composition() -> None:
    from vmx import TokenPagedComposition

    vm, _fake = _make()
    try:
        assert isinstance(vm._pager, TokenPagedComposition)
    finally:
        vm.dispose()
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/unit/vm/emr_serverless/test_job_runs_vm.py::test_job_runs_vm_uses_token_paged_composition -q
```

Expected: fails because `_pager` does not exist.

- [ ] **Step 3: Implement pager-backed storage**

Replace `_items` and `_next_token` as the storage source with `_pager`. Keep `_items` as a read-only compatibility property only if needed by existing private tests; otherwise migrate private tests. Add `async def _fetch_page(token: str | None) -> tuple[Sequence[JobRunItemVM], str | None]` that captures current application/token identity in `refresh()` and `load_more()` wrappers. Use `pages_equal` comparing `JobRunItemVM.summary` values. Sync pager collection changes into the inner `CompositeVM` children.

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/unit/vm/emr_serverless/test_job_runs_vm.py tests/unit/ui/emr_serverless/test_job_runs_pane.py -q
```

- [ ] **Step 5: Record metrics and commit**

Update the audit and commit:

```bash
git commit -m "refactor: use VMx token pager for job runs"
```

---

### Task 5: Modal Facades Use ModalVM

**Files:**
- Modify: `tests/unit/vm/chrome/test_confirm.py`
- Modify: `tests/unit/vm/chrome/test_resume.py`
- Modify: `tests/unit/vm/chrome/test_first_run.py`
- Modify: `tests/unit/vm/chrome/test_crash.py`
- Modify: `tests/unit/vm/test_round3_compliance.py`
- Modify: `src/aws_tui/vm/chrome/confirm_vm.py`
- Modify: `src/aws_tui/vm/chrome/resume_vm.py`
- Modify: `src/aws_tui/vm/chrome/first_run_vm.py`
- Modify: `src/aws_tui/vm/chrome/crash_vm.py`
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: VMx `ModalVM[T]`.
- Produces: unchanged modal `ask()` methods, result commands, `is_open`, lifecycle, and cancellation results.

- [ ] **Step 1: Write failing shape tests**

Add per-modal assertions that each facade owns `_modal: ModalVM[...]`.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_confirm.py tests/unit/vm/chrome/test_resume.py tests/unit/vm/chrome/test_first_run.py tests/unit/vm/chrome/test_crash.py -q
```

Expected: new shape assertions fail.

- [ ] **Step 3: Replace Future fields with ModalVM**

For each modal facade, create `_modal: ModalVM[T] | None` at ask time, use `_modal.wait_result()` to await, use `_modal.dismiss(value)` in command handlers, and call `_modal.dispose()` with the same cancellation result in facade `dispose()`.

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_confirm.py tests/unit/vm/chrome/test_resume.py tests/unit/vm/chrome/test_first_run.py tests/unit/vm/chrome/test_crash.py tests/unit/ui/test_first_run_modal.py tests/unit/ui/test_crash_modal.py tests/unit/ui/test_resume_modal.py -q
```

- [ ] **Step 5: Record metrics and commit**

Update the audit and commit:

```bash
git commit -m "refactor: use VMx ModalVM for chrome modals"
```

---

### Task 6: Shared Hub Subscribers Use when_property_changed

**Files:**
- Modify: `tests/unit/ui/test_overlay_widgets.py`
- Modify: `src/aws_tui/ui/widgets/_subscriber.py`
- Modify: direct widget subscribers that still manually filter `PropertyChangedMessage` by `sender_object` and can use the mixin/helper safely.
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: VMx `when_property_changed(hub, sender, property_name)`.
- Produces: same widget refresh behavior and subscription cleanup.

- [ ] **Step 1: Write failing helper behavior test**

Add a test that monkeypatches or inspects `HubSubscriberMixin.subscribe_to_vm()` to ensure it no longer manually processes non-matching senders and invokes callbacks only through VMx property filtering.

- [ ] **Step 2: Implement helper-based subscription**

Import `when_property_changed` and subscribe to the relevant properties. If a widget wants all properties for a sender, keep the manual filter because VMx 3.1 helper is property-specific; record that as a non-replacement in the audit.

- [ ] **Step 3: Verify green**

Run:

```bash
uv run pytest tests/unit/ui/test_overlay_widgets.py tests/unit/ui/test_pane_widgets.py tests/unit/ui/test_nav_menu.py -q
```

- [ ] **Step 4: Record metrics and commit**

Update the audit and commit:

```bash
git commit -m "refactor: use VMx property-change subscriptions"
```

---

### Task 7: Final Verification And Roll-Up

**Files:**
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: all replacement commits.
- Produces: final total LOC, test LOC, and coverage metrics for VMx 3.1 adoption.

- [ ] **Step 1: Run full verification**

Run:

```bash
uv run pytest -q
uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml
uv run ruff check
uv run mypy
```

- [ ] **Step 2: Update final roll-up table**

Update the aggregate benefit table and headline metric in the audit with all implemented replacements.

- [ ] **Step 3: Commit final docs if needed**

```bash
git add docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md
git commit -m "docs: finalize VMx adoption metrics"
```
