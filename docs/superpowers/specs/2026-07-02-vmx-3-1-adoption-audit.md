# 1. VMx 3.1.0 adoption audit for aws-tui

| Field | Value |
|---|---|
| Status | Drafted on `codex/vmx-3-1-adoption-audit` |
| Date | 2026-07-02 |
| Target dependency | `vmx>=3.1.0,<4.0.0` |
| Prior dependency | `vmx>=2.6.0,<3.0.0` resolving to `vmx==2.6.1` |
| Primary prior spec | `docs/superpowers/specs/2026-06-28-vmx-upstream-vnext-asks.md` |
| Goal | Identify VMx 3.1.0 primitives that can replace aws-tui VM/view bespoke code with better-fitting upstream abstractions. |

---

## 1.1. Executive summary

VMx 3.1.0 ships several components that directly answer the prior aws-tui
vNext asks:

- `TokenPagedComposition`
- `FilteredCompositeVM`
- `ScoredFilteredCompositeVM`
- `FormVM` validators and `errors`
- `ModalVM` plus `DialogService.present`
- `DiscriminatorVM`
- disposed-command inertness for `RelayCommand`
- `AsyncRelayCommand`
- `when_property_changed`
- `HierarchicalVM.invalidate_children()` / `invalidate_subtree()`

This branch bumps the dependency, applies import/test hygiene, and implements
the first contained VMx 3.1.0 replacements: S3 settings forms now delegate form
validation and approve gating directly to VMx `FormVM`, and the command palette
now delegates score-ranked projection to VMx `ScoredFilteredCompositeVM`.
`PaneVM` also now delegates visible-entry projection to VMx
`FilteredCompositeVM`, and `FocusCoordinatorVM` now delegates active-slot and
modal restore state to VMx `DiscriminatorVM`. `JobRunsVM` now delegates its
forward-only AWS token pagination accumulator and current token to VMx
`TokenPagedComposition`. Result-bearing chrome modal facades now delegate
result completion and cancellation defaults to VMx `ModalVM`.

The highest-value remaining follow-up refactors are:

1. Add an optional Textual `DialogService.present` host.
2. Replace safe shared-hub property filters with `when_property_changed`.
3. Evaluate `AsyncRelayCommand` for command-palette action scheduling.

---

## 1.2. VMx 3.1.0 public API additions relevant to aws-tui

Compared with VMx 2.6.1, the top-level VMx 3.1.0 export set adds:

| Added export | Best aws-tui fit |
|---|---|
| `TokenPagedComposition` | `JobRunsVM` forward-only AWS `nextToken` pagination. |
| `FilteredCompositeVM`, `FilteredCursorPolicy` | `PaneVM` visible-entry projection; possibly simple filtered lists elsewhere. |
| `ScoredFilteredCompositeVM` | `CommandPaletteVM` fuzzy score/rank projection. |
| `AsyncRelayCommand`, `AsyncRelayCommandBuilder` | Async load/refresh commands and palette action execution where cancellation/error channels matter. |
| `ModalVM` | `ConfirmationVM`, `ResumeVM`, `FirstRunVM`, `CrashVM`, and clone/log-filter modals. |
| `DiscriminatorVM` | `FocusCoordinatorVM` active focus slot and modal precedence. |
| `when_property_changed` | Shared-hub property subscriptions in view widgets and `_subscriber.py`. |

It removes old aliases:

| Removed export | Replacement |
|---|---|
| `AggregateVMBuilder1..6` | `AggregateVM1Builder..6Builder` |
| `RelayCommandOfT`, `RelayCommandOfTBuilder` | `RelayCommandOf`, `RelayCommandOfBuilder` |
| `null_message_hub_of` | Use `NULL_MESSAGE_HUB` or the protocol-typed null helper exposed under `vmx.services` if needed. |

aws-tui only used the removed aggregate alias in the VMx smoke test and an
old cheatsheet. Those have been mechanically updated.

---

## 1.3. Compatibility changes already made on this branch

| Area | Change |
|---|---|
| Dependency | `pyproject.toml` now requires `vmx>=3.1.0,<4.0.0`; `uv.lock` resolves `vmx==3.1.0`. |
| Import alias | `tests/unit/vm/test_vmx_smoke.py` now imports `AggregateVM3Builder`, not removed `AggregateVMBuilder3`. |
| Command disposal test | `tests/unit/vm/chrome/test_command_palette.py` now asserts disposed commands are inert (`can_execute() is False`). |
| VMx notes | `docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md` now documents the VMx 3.1.0 builder name. |
| Contract ledger | `docs/contract-ledger.md` now records `vmx==3.1.0` and the broader contracts in use. |

These are intentionally low-risk compatibility edits. They do not perform the
larger VM/view refactors below.

---

## 1.4. Replacement matrix

### 1.4.1. `JobRunsVM` pagination -> `TokenPagedComposition`

Prior code:

- `src/aws_tui/vm/emr_serverless/job_runs_vm.py`
- Holds `_items: list[JobRunItemVM]`, `_next_token: str | None`,
  `refresh()`, `load_more()`, and dedup-on-set logic.
- Uses `CompositeVM[ComponentVMOf[JobRunSummary]]` for row lifecycle and
  selection, but keeps pagination outside VMx.

VMx 3.1.0 candidate:

- `vmx.TokenPagedComposition[TVM, TToken]`
- Provides `items`, `current_token`, `has_more`, `load_more_command`,
  `refresh_command`, `on_collection_changed`, `on_property_changed`,
  `pages_equal`, and optional `auto_construct_on_add`.

Implemented refactor:

- Replaced `_items` and `_next_token` storage with
  `TokenPagedComposition[JobRunItemVM, str]`.
- Keep `CompositeVM.current` as the selection source of truth.
- Kept the target-identity guards from `refresh()` / `load_more()`; the pager
  fetch closure drops stale results when application or token lineage changes.
- Used `pages_equal` to preserve first-page dedup-on-set behavior, including
  the aws-tui rule that a refresh after loading extra pages collapses back to
  the first page rather than retaining stale appended pages.
- Surface `has_more` from the pager.

Notes:

- This is a high-value refactor because it directly retires the largest custom
  pagination ask from the prior spec.
- Do not blindly expose `TokenPagedComposition.load_more_command` to the view
  until the existing stale-target and pane-state transitions are preserved.
- The implementation is intentionally not a LOC reduction: preserving aws-tui's
  stale app/token guards and CompositeVM selection bridge around VMx's simpler
  pager costs 40 net VM LOC.

### 1.4.2. Local `FilteredCompositeVM` -> VMx `FilteredCompositeVM`

Prior code:

- `src/aws_tui/vm/_composition/filtered_composite_vm.py`
- Used by `PaneVM`.
- Local navigation wraps at ends for `move_to_next_visible()` /
  `move_to_previous_visible()`.
- Local `set_predicate()` identity-checks and treats the same predicate object
  as a no-op.

VMx 3.1.0 candidate:

- `vmx.FilteredCompositeVM`
- `FilteredCursorPolicy.SNAP_TO_FIRST`, `CLEAR`, `PRESERVE_IF_VISIBLE`
- Provides `visible`, `visible_count`, `current`, `set_predicate`,
  `move_to_next_visible`, `move_to_previous_visible`, `on_changed`, `dispose`.

Implemented refactor:

- Replace the aws-tui helper import with `from vmx import FilteredCompositeVM,
  FilteredCursorPolicy`.
- Update `PaneVM` to pass `FilteredCursorPolicy.SNAP_TO_FIRST`.
- Preserved `PaneVM._move_cursor()` as the public cursor movement source, so
  the replacement does not change pane navigation semantics.
- Keep `PaneVM`'s filtered-position index bridge if the view public contract
  remains `cursor_index: int`.

Notes:

- Deleted the local helper package and implementation-specific helper tests.
- Added a consumer shape test that pins `PaneVM` to VMx
  `FilteredCompositeVM`.
- Focused pane and pane-contract tests preserve filter/cursor behavior.

### 1.4.3. `CommandPaletteVM` scoring -> `ScoredFilteredCompositeVM`

Current code:

- `src/aws_tui/vm/chrome/command_palette_vm.py`
- Keeps a `CompositeVM[ComponentVMOf[PaletteEntry]]` registry.
- Recomputes `_filtered: tuple[PaletteEntry, ...]` manually with `_score()`.
- Tracks `_selected_index` manually and clamps movement.
- Manages async palette actions with `_pending_tasks` and done callbacks.

VMx 3.1.0 candidates:

- `vmx.ScoredFilteredCompositeVM`
- `vmx.AsyncRelayCommand`

Implemented refactor:

- Keep `PaletteEntryVM` and the registry composite.
- Added a `ScoredFilteredCompositeVM` over the registry:
  - scorer closure reads `self._filter_text`
  - score returns `None` when hidden
  - call `refresh_scores()` when `filter_text`, registration, or
    unregistration changes
- Derived `filtered_entries` from `scored.visible`.
- Kept `selected_index` as the public view contract.
- Preserved stable tie-breaking by returning `-_score(...)`, because VMx
  `ScoredFilteredCompositeVM` ranks higher scores first while the existing
  aws-tui scorer ranks lower scores first.
- Left `_pending_tasks` and async action error routing unchanged; `AsyncRelayCommand`
  remains a separate optional refactor because palette action errors currently
  emit `PropertyChangedMessage("action_failed")`.

Notes:

- This was one of the cleanest direct wins: it removes bespoke score/rank list
  maintenance while preserving the existing score function.
- Shape tests now assert `CommandPaletteVM` composes VMx
  `ScoredFilteredCompositeVM` internally.

### 1.4.4. Local `ValidatingFormVM` -> VMx `FormVM` validators

Prior code:

- `src/aws_tui/vm/_composition/validating_form_vm.py`
- `src/aws_tui/vm/settings/s3_connection_form_vm.py`
- `src/aws_tui/ui/widgets/settings/connection_form.py`
- Local helper supports multiple field validators per field, multiple model
  validators, an `errors` map, `has_errors`, `is_valid`, and approve gating.

VMx 3.1.0 candidate:

- `vmx.FormVM`
- `FormVM.builder().validator(field, fn).model_validator(fn)`
- `errors`, `is_valid`, `field_error(field)`, `errors_changed`
- approve gating: `is_valid and (not strict or is_dirty)`
- `approve_errors` and `approve_async()`

Implemented refactor:

- Deleted local `ValidatingFormVM` after replacing it with direct VMx `FormVM`.
- Kept `S3ConnectionFormVM` as the aws-tui domain facade because it owns
  S3-specific field names, `set_field`, and extra widget-level validators.
- Implemented an aggregation layer inside `S3ConnectionFormVM` because multiple
  validators per field remain required:
  - VMx `FormVM` accepts one validator per field at construction time.
  - aws-tui still allows `add_field_validator()` after construction.
  - The facade owns closure-backed validator lists and calls `set_model()` to
    revalidate after registration.
- Preserved the endpoint-IFF-force-path-style model validator.

Notes:

- This branch completed the top refactor candidate because VMx now covers the
  prior exact ask.
- The old implementation-specific primitive tests were deleted; facade behavior
  remains covered by S3 form tests, round-3 composition tests, UI inline-form
  tests, and the unit/integration coverage run recorded in §1.6.3.

### 1.4.5. `FocusCoordinatorVM` -> `DiscriminatorVM`

Prior code:

- `src/aws_tui/vm/chrome/focus_coordinator_vm.py`
- Tracks `focused_slot`, `is_modal`, `on_focused_slot_changed`,
  `set_focused_slot`, focus-ring cycling, modal open/close, lifecycle status,
  and shared-hub `PropertyChangedMessage`.

VMx 3.1.0 candidate:

- `vmx.DiscriminatorVM[TKey]`
- Provides `active_key`, `active_changed`, `is_active`, `set_active_key`,
  `modal_open`, `modal_close`, `dispose`.

Implemented refactor:

- Keep `FocusCoordinatorVM` as the aws-tui facade because it owns:
  - the `FocusSlot` enum,
  - S3/settings focus-cycle rings,
  - Textual bridge naming,
  - shared hub `PropertyChangedMessage`,
  - lifecycle `status` proxy expected by tests.
- Replaced `_focused_slot`, `_saved_slot`, and the local changed subject with
  an inner `DiscriminatorVM[FocusSlot]`.
- Subscribed to `DiscriminatorVM.active_changed` and republished
  `PropertyChangedMessage("focused_slot")`.
- Preserved the implicit modal-close override contract:
  `set_focused_slot(non_modal)` while modal is active switches directly to the
  requested slot and leaves no stale modal restore.

Notes:

- This preserved the public facade while deleting bespoke active-key and modal
  restore state.

### 1.4.6. Modal VMs -> `ModalVM` and `DialogService.present`

Prior code:

- `ConfirmationVM`, `ResumeVM`, `FirstRunVM`, `CrashVM`
- Each owns `asyncio.Future[...]`, `is_open`, result commands, and disposal
  fallback result.
- Textual widgets host the visual modals directly.

VMx 3.1.0 candidates:

- `vmx.ModalVM[T]`
- `DialogService.present(modal_vm)`

Implemented refactor:

- Composed a one-shot `ModalVM[T]` inside each `ask()` call to replace repeated
  future/result/dispose machinery.
- Kept existing `is_open` properties, request/report/entry data, command
  predicates, and hub `PropertyChangedMessage("is_open")` notifications.
- Medium-term follow-up: add a Textual dialog host implementing
  `DialogService.present` and route modal presentation through it.
- Keep existing modal-specific data and commands:
  - `ConfirmationVM` still owns `ConfirmRequest`.
  - `ResumeVM` still owns transfer-journal entries.
  - `FirstRunVM` still owns first-run action options and S3 form handoff.
  - `CrashVM` still owns `CrashReport` and safe-continue policy.

Notes:

- Shape tests now assert each result-bearing chrome modal uses VMx `ModalVM`
  while an ask is active.
- `DialogService.present` remains separate because it affects Textual screen
  hosting rather than VM result state.

### 1.4.7. Shared-hub property filtering -> `when_property_changed`

Current code:

- `src/aws_tui/ui/widgets/_subscriber.py`
- Several widgets still filter `PropertyChangedMessage` by sender identity.
- EMR VMs added per-instance `Subject[str]` streams to avoid shared-hub
  collisions.

VMx 3.1.0 candidate:

- `vmx.when_property_changed(hub, sender, property_name)`
- `_ComponentVMBase.property_changed`

Recommended refactor:

- Replace manual sender/property filters in `_subscriber.py` with
  `when_property_changed` where the widget knows the property names.
- Do not remove EMR per-VM `on_property_changed` streams in the same task; they
  are still useful because those VMs are facades, not VMx components.
- Longer term, consider a small aws-tui facade base that exposes
  `on_property_changed` uniformly and uses VMx helpers internally.

Notes:

- This is a view-layer cleanup candidate, not just VM-layer cleanup.
- It should be kept separate from data-structure refactors to avoid mixing
  event-source changes with behavior changes.

### 1.4.8. Async operations -> `AsyncRelayCommand`

Current code:

- `CommandPaletteVM` manually tracks async action tasks and drains exceptions.
- `JobRunsVM.refresh/load_more`, `ApplicationsVM.refresh`,
  `JobRunDetailVM.refresh`, and `JobRunLogsVM.load` are async methods driven by
  Textual workers rather than VMx commands.

VMx 3.1.0 candidate:

- `AsyncRelayCommand`
- `is_executing`, `cancel()`, `errors`, `execute_async()`, fire-and-forget
  `execute()`

Recommended refactor:

- Use `AsyncRelayCommand` only where the view should bind to a command rather
  than call an async VM method.
- Best first target: command-palette async action execution, because the current
  `_pending_tasks` set and done-callback are localized.
- Do not move EMR pollers to `AsyncRelayCommand` until the Textual worker
  cancellation model is reviewed; those paths have important stale-target
  guards.

### 1.4.9. Hierarchical and collection fixes

Prior asks:

- `ServicedObservableCollection` docs/ownership clarity.
- `HierarchicalVM` cache invalidation.

VMx 3.1.0 status:

- `ServicedObservableCollection` now explicitly documents caller ownership.
- `HierarchicalVM` now exposes `invalidate_children()` and
  `invalidate_subtree()`.

aws-tui impact:

- No direct current refactor. aws-tui does not currently use these primitives in
  a way that benefits from immediate changes.
- Keep them noted for future tree/navigation features.

---

## 1.5. Prioritized implementation backlog

### Phase A — compatibility and report, already in this branch

- Bump VMx to `>=3.1.0,<4.0.0`.
- Fix removed alias usage.
- Tighten disposed-command test expectation.
- Record this audit.
- Replace `S3ConnectionFormVM`'s local `ValidatingFormVM` dependency with VMx
  `FormVM` validators and record the replacement metric ledger.
- Replace `CommandPaletteVM`'s bespoke score/rank projection with VMx
  `ScoredFilteredCompositeVM` and record the replacement metric ledger.

### Phase B — contained VM-layer swaps, implemented

1. `PaneVM` backed by VMx `FilteredCompositeVM`.
2. `FocusCoordinatorVM` backed by VMx `DiscriminatorVM`.

These should each be separate commits with focused tests.

### Phase C — higher-coupling flow refactors, partially implemented

Implemented:

1. `JobRunsVM` pagination on `TokenPagedComposition`.
2. Modal result primitives on `ModalVM`.

Remaining:

1. Optional Textual `DialogService.present` host.
2. Optional shared-hub subscription cleanup with `when_property_changed`.
3. Optional localized `AsyncRelayCommand` adoption.

These touch more view/event boundaries and should be planned carefully.

---

## 1.6. Replacement savings and coverage tracking

Each follow-up refactor should leave an audit trail that shows what VMx 3.1.0
replaced and what the project gained from the replacement. The goal is not only
to say "we adopted a better primitive"; it is to quantify how much bespoke
aws-tui code disappeared from the VM and view layers while preserving behavior.

### 1.6.1. Per-replacement ledger

For each Phase B/C replacement, add a short ledger entry to the implementing
commit or follow-up report:

| Field | Required value |
|---|---|
| Replacement ID | Stable name, for example `vmx31-formvm-s3-settings`. |
| VMx 2.x-era implementation | Local class/functions/files being replaced. |
| VMx 3.1.0 primitive | Upstream primitive and public API used instead. |
| VM files touched | `src/aws_tui/vm/...` files that changed or were deleted. |
| View files touched | `src/aws_tui/ui/...` files that changed or were deleted, if any. |
| Tests changed | Tests deleted, rewritten, or added. |
| Behavior preserved | User-visible and VM-facing contracts that stayed unchanged. |
| Behavior intentionally changed | Any semantic change, with a linked test. |
| Coverage command | Exact pytest/coverage command run for this replacement. |
| LOC metric | VM LOC saved, view LOC saved, test LOC delta, and net implementation LOC saved. |

### 1.6.2. LOC accounting method

Use line counts as a directional maintainability metric, not as the only
measure of quality.

- Count implementation LOC separately for:
  - Viewmodel layer: `src/aws_tui/vm/**`
  - View layer: `src/aws_tui/ui/**`
- Exclude docs, lockfiles, generated snapshots, and purely formatting-only
  churn from savings totals.
- Track tests separately. Test LOC can increase while implementation LOC falls;
  that is healthy when the new tests pin behavior delegated to VMx.
- Report these numbers for each replacement:
  - `vm_deleted`
  - `vm_added`
  - `vm_loc_saved = vm_deleted - vm_added`
  - `view_deleted`
  - `view_added`
  - `view_loc_saved = view_deleted - view_added`
  - `implementation_loc_saved = vm_loc_saved + view_loc_saved`
  - `test_deleted`
  - `test_added`
  - `test_loc_delta = test_added - test_deleted`
- Treat moved code as neutral when it is mechanically relocated without being
  replaced by VMx. The savings number should represent bespoke code that no
  longer exists because VMx owns the abstraction.

A simple per-commit starting point is:

```bash
git diff --numstat <before-refactor>...HEAD -- src/aws_tui/vm src/aws_tui/ui tests
```

When a replacement spans multiple commits, calculate the metric over the merge
base of the replacement branch and the final replacement commit. Record the
commit range beside the ledger entry.

### 1.6.3. Aggregate VMx 3.1.0 benefit metric

At the end of the adoption series, produce one roll-up table:

| Replacement | VM LOC saved | View LOC saved | Implementation LOC saved | Test LOC delta | Coverage delta |
|---|---:|---:|---:|---:|---:|
| `FormVM` validators | 184 | 0 | 184 | -171 | -0.08 pp |
| `ScoredFilteredCompositeVM` | 9 | 0 | 9 | +22 | +0.01 pp |
| `FilteredCompositeVM` | 283 | 0 | 283 | -322 | -0.07 pp |
| `DiscriminatorVM` | 7 | 0 | 7 | +5 | -0.01 pp |
| `TokenPagedComposition` | -40 | 0 | -40 | +11 | +0.02 pp |
| `ModalVM` result primitives | 13 | 0 | 13 | +12 | -0.03 pp |
| `DialogService.present` | record after implementation | record after implementation | record after implementation | record after implementation | record after implementation |
| `when_property_changed` | record after implementation | record after implementation | record after implementation | record after implementation | record after implementation |
| `AsyncRelayCommand` | record after implementation | record after implementation | record after implementation | record after implementation | record after implementation |
| **Total implemented so far** | 456 | 0 | 456 | -443 | -0.16 pp |

Current headline metric:

```text
VMx 3.1.0 adoption has saved 456 implementation LOC so far:
  456 in viewmodels
  0 in views
with -443 net test LOC and -0.16 coverage-point change.
```

Positive implementation LOC saved means the newer VMx version reduced bespoke
aws-tui code compared with the older VMx 2.6.1-compatible implementation.

The implemented replacement ledger:

| Field | Value |
|---|---|
| Replacement ID | `vmx31-formvm-s3-settings` |
| VMx 2.x-era implementation | `ValidatingFormVM` in `src/aws_tui/vm/_composition/validating_form_vm.py`, plus S3 settings facade composition. |
| VMx 3.1.0 primitive | `vmx.FormVM[S3CompatForm]` with `validators`, `model_validator`, `errors`, `errors_changed`, and strict `approve_command` gating. |
| VM files touched | `src/aws_tui/vm/settings/s3_connection_form_vm.py`, `src/aws_tui/vm/_composition/__init__.py`, deleted `src/aws_tui/vm/_composition/validating_form_vm.py`. |
| View files touched | `src/aws_tui/ui/widgets/settings/connection_form.py` comments/docstrings only; no view behavior changed. |
| Tests changed | Deleted `tests/unit/vm/_composition/test_validating_form_vm.py`; added post-construction validator coverage in `tests/unit/vm/settings/test_s3_connection_form_vm.py`; updated round-3 composition assertion in `tests/unit/vm/test_round3_compliance.py`. |
| Behavior preserved | Public `S3ConnectionFormVM` facade, field/model validators, cross-field endpoint invariant, `errors`, `has_errors`, `is_valid`, `can_submit`, `set_field`, `submit_command`, `revert_command`, and `on_errors_changed`. |
| Behavior intentionally changed | Internal composition now uses VMx `FormVM` directly; no user-visible behavior change. |
| Coverage command | Baseline: `/Users/kaveh/repos/aws-tui/.venv/bin/python -m pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml` in detached worktree at `f9a8b68`. After: `uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml` in this worktree. |
| LOC metric | `vm_deleted=252`, `vm_added=68`, `vm_loc_saved=184`; `view_deleted=4`, `view_added=4`, `view_loc_saved=0`; `implementation_loc_saved=184`; `test_deleted=204`, `test_added=33`, `test_loc_delta=-171`. |
| Coverage metric | Baseline `83.25%` over `1275 passed, 9 deselected`; after `83.17%` over `1262 passed, 9 deselected`; `coverage_delta=-0.08` percentage points. |

| Field | Value |
|---|---|
| Replacement ID | `vmx31-scored-filter-command-palette` |
| VMx 2.x-era implementation | Manual `_recompute_filtered()` score/rank list in `src/aws_tui/vm/chrome/command_palette_vm.py`. |
| VMx 3.1.0 primitive | `vmx.ScoredFilteredCompositeVM[ComponentVMOf[PaletteEntry]]`. |
| VM files touched | `src/aws_tui/vm/chrome/command_palette_vm.py`. |
| View files touched | None. |
| Tests changed | Added VMx composition shape tests in `tests/unit/vm/chrome/test_command_palette.py` and `tests/unit/vm/test_round3_compliance.py`. |
| Behavior preserved | Existing `_score()` matching semantics, stable insertion-order tie-breaking, public `filtered_entries`, `selected_index`, register/unregister, command execution, and async action scheduling behavior. |
| Behavior intentionally changed | Internal projection now uses VMx `ScoredFilteredCompositeVM`; no user-visible behavior change. |
| Coverage command | `uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml`. |
| LOC metric | `vm_deleted=26`, `vm_added=17`, `vm_loc_saved=9`; `view_deleted=0`, `view_added=0`, `view_loc_saved=0`; `implementation_loc_saved=9`; `test_deleted=0`, `test_added=22`, `test_loc_delta=+22`. |
| Coverage metric | Before `83.17%`; after `83.18%` over `1264 passed, 9 deselected`; `coverage_delta=+0.01` percentage points. |

| Field | Value |
|---|---|
| Replacement ID | `vmx31-filtered-composite-pane` |
| VMx 2.x-era implementation | Local `FilteredCompositeVM` in `src/aws_tui/vm/_composition/filtered_composite_vm.py`, used only by `PaneVM`. |
| VMx 3.1.0 primitive | `vmx.FilteredCompositeVM[ComponentVMOf[EntryState]]` with `FilteredCursorPolicy.SNAP_TO_FIRST`. |
| VM files touched | `src/aws_tui/vm/file_manager/pane_vm.py`; deleted `src/aws_tui/vm/_composition/filtered_composite_vm.py` and `src/aws_tui/vm/_composition/__init__.py`. |
| View files touched | None. |
| Tests changed | Deleted implementation-only `tests/unit/vm/_composition/test_filtered_composite_vm.py`; added VMx composition shape coverage in `tests/unit/vm/file_manager/test_pane_vm.py`. |
| Behavior preserved | Public pane `filtered_entries`, `cursor_index`, filter text behavior, selection bridge, current-entry projection, clamp-style cursor movement, and pane viewmodel projection. |
| Behavior intentionally changed | Internal visible-entry projection now uses VMx `FilteredCompositeVM`; no user-visible behavior change. |
| Coverage command | `uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml`. |
| LOC metric | `vm_deleted=293`, `vm_added=10`, `vm_loc_saved=283`; `view_deleted=0`, `view_added=0`, `view_loc_saved=0`; `implementation_loc_saved=283`; `test_deleted=334`, `test_added=12`, `test_loc_delta=-322`. |
| Coverage metric | Before `83.18%`; after `83.11%` over `1242 passed, 9 deselected`; `coverage_delta=-0.07` percentage points. |

| Field | Value |
|---|---|
| Replacement ID | `vmx31-discriminator-focus-coordinator` |
| VMx 2.x-era implementation | Hand-rolled `FocusCoordinatorVM` active slot, saved modal slot, and local changed subject. |
| VMx 3.1.0 primitive | `vmx.DiscriminatorVM[FocusSlot]` with `active_key`, `active_changed`, `is_active`, `set_active_key`, `modal_open`, and `modal_close`. |
| VM files touched | `src/aws_tui/vm/chrome/focus_coordinator_vm.py`. |
| View files touched | None. |
| Tests changed | Added VMx composition shape coverage and rewrote modal-restore assertions in `tests/unit/vm/chrome/test_focus_coordinator_vm.py` to avoid removed private state. |
| Behavior preserved | Public `focused_slot`, `is_modal`, `on_focused_slot_changed`, hub `PropertyChangedMessage("focused_slot")`, focus-cycle rings, modal open/close restoration, explicit non-modal override while modal is active, and lifecycle proxying. |
| Behavior intentionally changed | Internal active-key and modal restore state now live in VMx `DiscriminatorVM`; no user-visible behavior change. |
| Coverage command | `uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml`. |
| LOC metric | `vm_deleted=28`, `vm_added=21`, `vm_loc_saved=7`; `view_deleted=0`, `view_added=0`, `view_loc_saved=0`; `implementation_loc_saved=7`; `test_deleted=8`, `test_added=13`, `test_loc_delta=+5`. |
| Coverage metric | Before `83.11%`; after `83.10%` over `1243 passed, 9 deselected`; `coverage_delta=-0.01` percentage points. |

| Field | Value |
|---|---|
| Replacement ID | `vmx31-token-paged-job-runs` |
| VMx 2.x-era implementation | Manual `JobRunsVM` `_items` accumulator and `_next_token` field with custom `refresh()` / `load_more()` token mutation. |
| VMx 3.1.0 primitive | `vmx.TokenPagedComposition[JobRunItemVM, str]` with `items`, `current_token`, `refresh_command`, `load_more_command`, and `pages_equal`. |
| VM files touched | `src/aws_tui/vm/emr_serverless/job_runs_vm.py`. |
| View files touched | None. |
| Tests changed | Added VMx composition shape coverage in `tests/unit/vm/emr_serverless/test_job_runs_vm.py`; existing pagination, dedup, stale-target, selection, UI, and acceptance tests were preserved. |
| Behavior preserved | Public `runs`, `has_more`, `selected_id`, state-filtering, refresh dedup-on-set, selection restoration/clear, load-more append behavior, load-more error handling without destructive reset, and stale application/token response dropping. |
| Behavior intentionally changed | Internal pagination accumulator and current token now live in VMx `TokenPagedComposition`; no user-visible behavior change. |
| Coverage command | `uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml`. |
| LOC metric | `vm_deleted=93`, `vm_added=133`, `vm_loc_saved=-40`; `view_deleted=0`, `view_added=0`, `view_loc_saved=0`; `implementation_loc_saved=-40`; `test_deleted=0`, `test_added=11`, `test_loc_delta=+11`. |
| Coverage metric | Before `83.10%`; after `83.12%` over `1244 passed, 9 deselected`; `coverage_delta=+0.02` percentage points. |

| Field | Value |
|---|---|
| Replacement ID | `vmx31-modalvm-chrome-results` |
| VMx 2.x-era implementation | Per-modal `asyncio.Future[...]` result fields in `ConfirmationVM`, `ResumeVM`, `FirstRunVM`, and `CrashVM`. |
| VMx 3.1.0 primitive | One-shot `vmx.ModalVM[T]` instances with `dismiss()`, `dispose()`, and `wait_result()`. |
| VM files touched | `src/aws_tui/vm/chrome/confirm_vm.py`, `src/aws_tui/vm/chrome/resume_vm.py`, `src/aws_tui/vm/chrome/first_run_vm.py`, `src/aws_tui/vm/chrome/crash_vm.py`. |
| View files touched | None. |
| Tests changed | Added VMx `ModalVM` shape assertions to the existing chrome modal VM tests. |
| Behavior preserved | Public `ask()` APIs, single-open guard, `is_open`, modal-specific data, command predicates, disposal fallback results, and hub `PropertyChangedMessage("is_open")` notifications. |
| Behavior intentionally changed | Internal result completion and disposal cancellation now use VMx `ModalVM`; no user-visible behavior change. |
| Coverage command | `uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml`. |
| LOC metric | `vm_deleted=58`, `vm_added=45`, `vm_loc_saved=13`; `view_deleted=0`, `view_added=0`, `view_loc_saved=0`; `implementation_loc_saved=13`; `test_deleted=0`, `test_added=12`, `test_loc_delta=+12`. |
| Coverage metric | Before `83.12%`; after `83.09%` over `1244 passed, 9 deselected`; `coverage_delta=-0.03` percentage points. |

### 1.6.4. Test coverage accounting

Every replacement must preserve or improve confidence in the delegated
behavior. Because VMx now owns more of the primitive behavior, aws-tui tests
should move away from retesting VMx internals and toward facade contracts,
integration boundaries, and view behavior.

For each replacement:

- Run the targeted tests listed in §1.7.
- Run `uv run pytest tests/unit/vm tests/unit/ui -q`.
- For larger view/event refactors, run the relevant snapshot tests.
- Run coverage for unit and in-process integration tests:

```bash
uv run pytest tests/unit tests/integration \
  --cov=aws_tui --cov-report=term-missing --cov-report=xml
```

Record:

- overall coverage before/after,
- files whose coverage materially dropped,
- tests deleted because they only asserted local implementation details now
  delegated to VMx,
- tests added or rewritten to assert aws-tui facade behavior against the new
  VMx primitive.

Coverage drops are acceptable only when they come from deleting bespoke code and
the remaining facade/view behavior is still covered by focused tests.

---

## 1.7. Tests to preserve or add during follow-up refactors

For each replacement, keep these contracts green:

- Dependency/import canary: `tests/unit/vm/test_vmx_smoke.py`
- Round-3 facade shape: `tests/unit/vm/test_round3_compliance.py`
- Pane filter/cursor: `tests/unit/vm/file_manager/test_pane_vm.py`,
  `tests/unit/vm/file_manager/test_pane_vm_contracts.py`
- Command palette ranking and command disposal:
  `tests/unit/vm/chrome/test_command_palette.py`
- Settings validation:
  `tests/unit/vm/settings/test_s3_connection_form_vm.py`,
  `tests/unit/ui/test_connection_form_inline.py`
- EMR pagination and stale-target guards:
  `tests/unit/vm/emr_serverless/test_job_runs_vm.py`
- Focus coordination:
  `tests/unit/vm/chrome/test_focus_coordinator_vm.py`
- Modal choices:
  `tests/unit/vm/chrome/test_confirm.py`,
  `tests/unit/vm/chrome/test_resume.py`,
  `tests/unit/vm/chrome/test_first_run.py`,
  `tests/unit/vm/chrome/test_crash.py`

After each phase, run:

```bash
uv run pytest tests/unit/vm/test_vmx_smoke.py -q
uv run pytest tests/unit/vm tests/unit/ui -q
uv run mypy
uv run ruff check
```

Run snapshot tests for view-affecting phases.

---

## 1.8. Non-goals for this branch

- Do not replace all local VM facades wholesale.
- Do not expose raw VMx primitives in public aws-tui VM surfaces where the
  round-3 directive intentionally hides them.
- Do not change user-visible view behavior while doing the dependency bump.
- Do not remove historical specs/plans that accurately describe earlier work;
  create new docs that supersede them.

---

## 1.9. Acceptance criteria for this branch

- `pyproject.toml` and `uv.lock` resolve VMx 3.1.0.
- Import-level VMx smoke tests pass.
- Low-risk compatibility fixes are committed with the bump.
- This report exists and maps the old vNext asks to concrete VMx 3.1.0
  replacement candidates.
- This report defines how follow-up work will track replaced code, VM/view LOC
  savings, test LOC deltas, and coverage changes.
- The first contained refactor, S3 settings `FormVM` adoption, is implemented
  with LOC and coverage metrics recorded.
- Remaining larger refactors are deferred into explicit follow-up phases.
