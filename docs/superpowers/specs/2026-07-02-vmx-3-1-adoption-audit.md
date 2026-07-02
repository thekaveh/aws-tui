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

This branch already bumps the dependency and applies only import/test hygiene
needed to stay clean. The larger refactors should happen in a follow-up plan,
because several replacements affect public VM contracts and view bindings.

The highest-value follow-up refactors are:

1. Replace aws-tui's local `ValidatingFormVM` with VMx 3.1.0 `FormVM` validators.
2. Replace `CommandPaletteVM`'s inlined score/rank machinery with
   `ScoredFilteredCompositeVM`.
3. Replace `PaneVM`'s local `FilteredCompositeVM` with VMx 3.1.0
   `FilteredCompositeVM`, after pinning cursor movement semantics.
4. Move `JobRunsVM` pagination state onto `TokenPagedComposition`.
5. Rebase `FocusCoordinatorVM` on `DiscriminatorVM`.
6. Rebase result-bearing modal VMs on `ModalVM`, then optionally add a Textual
   `DialogService.present` host.

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

Current code:

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

Recommended refactor:

- Replace `_items` and `_next_token` with a token pager over
  `JobRunItemVM` or `ComponentVMOf[JobRunSummary]`.
- Keep `CompositeVM.current` as the selection source of truth.
- Keep the target-identity guards from current `refresh()` / `load_more()`;
  the pager fetch closure must capture `(application_id, token)` and drop stale
  results when the application changes.
- Use `pages_equal` to preserve the existing first-page dedup-on-set behavior.
- Surface `has_more` from the pager.

Notes:

- This is a high-value refactor because it directly retires the largest custom
  pagination ask from the prior spec.
- Do not blindly expose `TokenPagedComposition.load_more_command` to the view
  until the existing stale-target and pane-state transitions are preserved.

Estimated cleanup:

- Moderate source reduction in `JobRunsVM`.
- Tests should move from asserting `_next_token` side effects toward asserting
  pager-backed `has_more`, stale-target guards, and selection preservation.

### 1.4.2. Local `FilteredCompositeVM` -> VMx `FilteredCompositeVM`

Current code:

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

Recommended refactor:

- Replace the aws-tui helper import with `from vmx import FilteredCompositeVM,
  FilteredCursorPolicy`.
- Update `PaneVM` to pass `FilteredCursorPolicy.SNAP_TO_FIRST`.
- Decide whether Pane cursor movement must keep clamping semantics, wrapping
  semantics, or delegate to VMx movement:
  - `PaneVM._move_cursor()` currently clamps by index.
  - aws-tui local `FilteredCompositeVM` movement wraps, but `PaneVM` does not
    currently call those movement methods.
  - VMx 3.1.0 movement clamps.
- Keep `PaneVM`'s filtered-position index bridge if the view public contract
  remains `cursor_index: int`.

Notes:

- This can delete the local helper and most of
  `tests/unit/vm/_composition/test_filtered_composite_vm.py` after equivalent
  consumer tests are redirected to VMx behavior.
- The upstream primitive is close enough that this should be a contained
  follow-up task.

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

Recommended refactor:

- Keep `PaletteEntryVM` and the registry composite.
- Add a `ScoredFilteredCompositeVM` over the registry:
  - scorer closure reads `self._filter_text`
  - score returns `None` when hidden
  - call `refresh_scores()` when `filter_text`, registration, or
    unregistration changes
- Derive `filtered_entries` from `scored.visible`.
- Keep `selected_index` as public view contract for now, but map it to
  `scored.current` internally or introduce `selected_entry`.
- Consider replacing `_pending_tasks` with `AsyncRelayCommand` only after
  deciding how command-palette action errors should surface to the user.

Notes:

- This is one of the cleanest direct wins: it removes bespoke score/rank list
  maintenance while preserving the existing score function.
- Keep stable tie-breaking: VMx `ScoredFilteredCompositeVM` sorts by descending
  score, but aws-tui `_score()` treats lower scores as better. Either invert the
  score or adjust the scorer to produce higher-is-better values.

### 1.4.4. Local `ValidatingFormVM` -> VMx `FormVM` validators

Current code:

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

Recommended refactor:

- Delete local `ValidatingFormVM` after replacing it with direct VMx `FormVM`.
- Keep `S3ConnectionFormVM` as the aws-tui domain facade because it owns
  S3-specific field names, `set_field`, and extra widget-level validators.
- Implement an aggregation layer inside `S3ConnectionFormVM` if multiple
  validators per field remain required:
  - VMx builder accepts one validator per field at build time.
  - aws-tui currently allows `add_field_validator()` after construction.
  - The facade can retain the additive API and rebuild/aggregate validators
    into one VMx validator per field.
- Preserve the endpoint-IFF-force-path-style model validator.

Notes:

- This is the top refactor candidate because VMx now covers the prior exact ask.
- It will delete a local mini-primitive and simplify the settings form tests.

### 1.4.5. `FocusCoordinatorVM` -> `DiscriminatorVM`

Current code:

- `src/aws_tui/vm/chrome/focus_coordinator_vm.py`
- Tracks `focused_slot`, `is_modal`, `on_focused_slot_changed`,
  `set_focused_slot`, focus-ring cycling, modal open/close, lifecycle status,
  and shared-hub `PropertyChangedMessage`.

VMx 3.1.0 candidate:

- `vmx.DiscriminatorVM[TKey]`
- Provides `active_key`, `active_changed`, `is_active`, `set_active_key`,
  `modal_open`, `modal_close`, `dispose`.

Recommended refactor:

- Keep `FocusCoordinatorVM` as the aws-tui facade because it owns:
  - the `FocusSlot` enum,
  - S3/settings focus-cycle rings,
  - Textual bridge naming,
  - shared hub `PropertyChangedMessage`,
  - lifecycle `status` proxy expected by tests.
- Replace `_focused_slot`, `_saved_slot`, and `_on_changed` with an inner
  `DiscriminatorVM[FocusSlot]`.
- Subscribe to `DiscriminatorVM.active_changed` and republish
  `PropertyChangedMessage("focused_slot")`.

Notes:

- This is a low-risk medium-value refactor. It preserves the public facade while
  deleting bespoke modal-stack and active-key state.

### 1.4.6. Modal VMs -> `ModalVM` and `DialogService.present`

Current code:

- `ConfirmationVM`, `ResumeVM`, `FirstRunVM`, `CrashVM`
- Each owns `asyncio.Future[...]`, `is_open`, result commands, and disposal
  fallback result.
- Textual widgets host the visual modals directly.

VMx 3.1.0 candidates:

- `vmx.ModalVM[T]`
- `DialogService.present(modal_vm)`

Recommended refactor:

- Short term: compose or subclass `ModalVM[T]` inside each modal VM to replace
  the repeated future/result/dispose machinery.
- Medium term: add a Textual dialog host implementing `DialogService.present`
  and route modal presentation through it.
- Keep existing modal-specific data and commands:
  - `ConfirmationVM` still owns `ConfirmRequest`.
  - `ResumeVM` still owns transfer-journal entries.
  - `FirstRunVM` still owns first-run action options and S3 form handoff.
  - `CrashVM` still owns `CrashReport` and safe-continue policy.

Notes:

- This is not a one-line replacement. `ModalVM` gives the result primitive; the
  Textual host integration decides how much view-layer code disappears.
- A good first task is to refactor only `ConfirmationVM`, then apply the pattern
  to the other three.

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

### Phase B — contained VM-layer swaps

1. `S3ConnectionFormVM` backed by VMx `FormVM` validators.
2. `CommandPaletteVM` backed by VMx `ScoredFilteredCompositeVM`.
3. `PaneVM` backed by VMx `FilteredCompositeVM`.
4. `FocusCoordinatorVM` backed by VMx `DiscriminatorVM`.

These should each be separate commits with focused tests.

### Phase C — higher-coupling flow refactors

1. `JobRunsVM` pagination on `TokenPagedComposition`.
2. Modal result primitives on `ModalVM`.
3. Optional Textual `DialogService.present` host.
4. Optional shared-hub subscription cleanup with `when_property_changed`.
5. Optional localized `AsyncRelayCommand` adoption.

These touch more view/event boundaries and should be planned carefully.

---

## 1.6. Tests to preserve or add during follow-up refactors

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

## 1.7. Non-goals for this branch

- Do not replace all local VM facades wholesale.
- Do not expose raw VMx primitives in public aws-tui VM surfaces where the
  round-3 directive intentionally hides them.
- Do not change user-visible view behavior while doing the dependency bump.
- Do not remove historical specs/plans that accurately describe earlier work;
  create new docs that supersede them.

---

## 1.8. Acceptance criteria for this branch

- `pyproject.toml` and `uv.lock` resolve VMx 3.1.0.
- Import-level VMx smoke tests pass.
- Low-risk compatibility fixes are committed with the bump.
- This report exists and maps the old vNext asks to concrete VMx 3.1.0
  replacement candidates.
- Larger refactors are deferred into explicit follow-up phases.
