# VMx toolkit adoption — design spec

| Field | Value |
|---|---|
| Status | Drafted, awaiting brainstorm → plan → execution |
| Date | 2026-06-28 |
| Owner | TBD at brainstorm |
| Driver | Architectural review session 2026-06-28 — see §1.2 |
| Related | [[docs/superpowers/specs/2026-06-13-aws-tui-design.md]] (M0 design baseline), [[docs/architecture.md]] (current five-layer model), [VMx 2.6.1 source](https://github.com/thekaveh/VMx) |
| Estimated effort | 4–6 PRs over 2–3 calendar weeks at the project's typical cadence |
| Target VMx version | `>=2.6.0,<3.0.0` (no version bump required) |

---

## 1. Background

### 1.1. The recurring bug pattern that motivated this work

Between PR #98 and PR #103 (a span of six PRs over four days in late June 2026),
the project shipped the same shape of fix repeatedly:

- PR #98 — focus dimming (the menu AND a file pane both rendered focused)
- PR #98 — focus steal when arrow-walking the rail into EMR
- PR #99 — the PR #98 focus-steal fix still leaked under async page mount
- PR #100 — JobRunsPane re-mounted every row on every arrow press
- PR #100 — ApplicationPicker rebuilt its OptionList on every poll tick
- PR #101 — ENTER on a NavMenu row needed to intentionally hand focus to the
  destination pane (the inverse of PR #99's guard)
- PR #103 — four EMR widgets all needed `sender_object` filters on their
  hub-message handlers to stop cross-VM redraws

Every fix landed entirely in the View layer (`src/aws_tui/ui/widgets/...` and
the per-theme `.tcss` files). Surface-read, this looks like a polish phase
ironing out View-side details. **The deeper read, surfaced in the 2026-06-28
review session and recorded in §1.2, is that several of these bugs were
symptoms of a missing VM-layer architectural piece**: the project hand-rolls
the observable-list-with-cursor pattern in every list-shaped ViewModel, then
fights subtle bugs (forgot-to-broadcast, broadcast-too-often, child-VM
dispose-order, cursor-vs-collection-mutation races) that VMx 2.6.1's
`CompositeVM` already solves.

The redraw-flash bug train (PRs #100 and #103 specifically) is the canonical
example. Both bugs arose because `JobRunsPane` / `ApplicationPicker` and the
parent EMR VMs are coordinating over a shared `MessageHub` using hand-emitted
`PropertyChangedMessage` events, instead of using `CompositeVM`'s built-in
`on_collection_changed` observable. If `JobRunsVM` had been
`CompositeVM[JobRunVM]` from the start, the sender-confusion case that
required PR #103 could not have arisen — `CompositeVM` emits granular
`CollectionChangedEvent` per mutation, scoped to the composite instance, with
no cross-instance hub broadcast for the View to filter on.

This spec proposes the case-by-case retrofit.

### 1.2. The conversation that surfaced the design

The work is the output of a structured architectural review held during the
maintenance branch's review window. The conversation walked through:

1. Why have the most recent fixes landed in the View layer rather than the
   ViewModel layer?
2. Did those fixes have to be View-side, or could they have been VM-side?
3. What is the honest LOC / effort saving from MVVM + VMx vs writing
   everything in the View?
4. If a second View (NiceGUI) were added on top of the existing VMs, how much
   of the VM layer is genuinely reusable?
5. Could VMx ship more specialised primitives so that aws-tui has less
   boilerplate? Would they work across UI frameworks?
6. **Couldn't `CompositeVM` cover the list-with-cursor case directly,
   without inventing a new `ListVM` primitive?**

Question 6 is what unblocked the design. The answer turned out to be "yes,
and `CompositeVM` has been sitting unused in 90% of aws-tui's list-shaped
VMs since the project began."

### 1.3. Mistakes made during the review session — record so the next worker
       does not repeat them

The next worker picking up this spec should know that the analysis behind it
went through three rounds of correction. Each correction tightens the
design, and each mistake is worth recording because it is the kind of
mistake a fresh worker can also make.

1. **Mistaken claim that `CompositeVM` is heterogeneous fixed-shape.**
   The first pass through the review asserted that `CompositeVM` was the
   typed N-tuple composition pattern (a struct of named children — left,
   right, top), and that aws-tui therefore needed a *separate*
   homogeneous-collection primitive that VMx did not yet provide. This is
   wrong. Reading
   `.venv/lib/python3.11/site-packages/vmx/composites/composite_vm.py`
   shows `CompositeVM[VM]` is generic over a single `VM` bound to
   `_ComponentVMBase`, exposes `MutableSequence`-style mutation
   (`append`, `add`, `insert(index, item)`, `remove`, `remove_at`,
   `clear`, `__getitem__`, `__len__`, `__iter__`, `__contains__`), publishes
   an `on_collection_changed: Observable[CollectionChangedEvent]`, and has a
   built-in `current: VM | None` selection slot configurable via a
   `current_selector` callback. The N-tuple-of-typed-children pattern lives
   in `vmx.aggregates`, not `vmx.composites`.

   **Next worker should not propose adding a new `ListVM` primitive to
   VMx. The primitive already exists.**

2. **Mistaken claim that VMx is too thin and needs new primitives.**
   The first round suggested VMx should ship five new primitives
   (`ListVM<TItem>`, `MasterDetailVM`, `FormVM`, `AsyncOperationVM`,
   `ChoiceModalVM`). An actual `find .venv/lib/python3.11/site-packages/vmx
   -name '*.py'` revealed all but one already exist:

   | Suggested addition | Actually in VMx 2.6.1 |
   |---|---|
   | `ListVM<TItem>` | `CompositeVM[VM]` (for VM children) + `ObservableList[T]` (for raw items) |
   | `MasterDetailVM` | Compose two `CompositeVM` siblings on a parent + bind `current` |
   | `FormVM<TFields>` | `FormVM<TM>` in `vmx.forms.form_vm` |
   | `AsyncOperationVM` | Domain-specific; can compose on top of a small state-machine VM |
   | `ChoiceModalVM` | `IDialogService` + `DialogService` in `vmx.dialogs` |

   **Next worker should treat the VMx toolkit as the source of truth for
   available patterns**, not the analysis-prose summary above. Open the
   `.venv` and read.

3. **Mistaken classification of view-heavy recent fixes as "view-correct
   by necessity".**
   The first read of the bug train labelled every fix from PR #98 to PR #103
   as View-correct, on the grounds that they touched Textual focus, CSS
   specificity, hub-subscription discipline, or `call_after_refresh` race
   ordering — all of which are View-runtime concerns the VM layer
   genuinely does not own.

   This is partially true but misses the deeper read: PR #103 and PR #100,
   in particular, are downstream of the VM-layer hand-rolling pattern. If
   `JobRunsVM` were `CompositeVM[JobRunVM]`, the cross-VM `state` and
   `selected_id` collisions that motivated PR #103's `sender_object`
   filters would never arise — `CompositeVM` does not broadcast its
   collection mutations through the global hub at all; it emits on its
   own `on_collection_changed` observable, which only the parent View
   subscribes to.

   The recurring focus bugs (PR #98 / #99 / #101) are a separate gap —
   the missing `FocusCoordinatorVM` is a related but distinct piece of
   work, and it is **explicitly out of scope** for this spec (see §8.3).

   **Next worker should not over-correct by attributing every View-side
   fix to the VM gap.** Focus, CSS, layout, and Textual-runtime races are
   genuinely View-side. Only the collection-broadcast and child-lifecycle
   bug families are downstream of the VM-side hand-rolling.

4. **Did not run an inventory of VMx's actual API surface before opining.**
   The first round of "VMx needs more primitives" was an opinion offered
   without reading the framework's source. The fix was to run
   `find .venv/lib/python3.11/site-packages/vmx -name '*.py'` and walk
   the actual directory tree. That single grep dissolved the entire
   first-round design.

   **Next worker should start every architectural critique of VMx by
   walking the framework source.** The published API surface — `vmx/__init__.py`
   re-exports — is small enough to print and read top-to-bottom in five
   minutes.

The next worker should also be told, candidly, that the VMx toolkit
adoption story is a re-evaluation, not a remediation. aws-tui's hand-rolled
VM layer is internally consistent, well-tested, and the test suite catches
regressions cleanly. There is no urgent functional defect this spec
addresses. **The case for the work is asymmetric reduction in future
boilerplate + elimination of two recurring bug categories
(forgot-to-broadcast on mutation; forgot-to-dispose child VM on
collection removal), not "the current code is broken."**

## 2. What VMx 2.6.1 actually provides

A walk through `vmx/__init__.py`'s re-exports as of 2.6.1, with the
contracts each primitive enforces. This section is the reference the next
worker should consult while planning the per-VM migration in §4.

### 2.1. `CompositeVM[VM]` (and `CompositeVMOf[M, VM]`)

**File:** `vmx/composites/composite_vm.py`
**Lifecycle:** `_ComponentVMBase` subclass; participates in
`construct → destruct → dispose` cascading.

Homogeneous collection of `VM` children where `VM` is bound to
`_ComponentVMBase`. The "modeled" variant `CompositeVMOf[M, VM]` binds the
children to a model type `M`.

Public API:

- `add(item)` / `append(item)`
- `insert(index, item)`
- `remove(item) -> bool`
- `remove_at(index)`
- `replace(index, new_item)`
- `clear()`
- `__getitem__(int | slice)` / `__len__` / `__iter__` / `__contains__`
- `current: VM | None` slot, with property setter
- `on_collection_changed: Observable[CollectionChangedEvent]`
- `on_current_changed: Observable[VM | None]` (via the
  `on_current_changed` callback)

Configuration knobs at construction:

- `async_selection: bool` — when `True`, current-changed dispatches via the
  configured `Dispatcher` rather than synchronously.
- `auto_construct_on_add: bool` — when `True`, `add(child)` calls
  `child.construct()` automatically. Use this for collections whose
  children are owned by the composite.
- `current_selector: Callable[[Iterable[VM]], VM | None]` — runs after every
  mutation to compute the new `current` slot. Lets the composite express
  "first non-disabled child", "last-modified child", "child matching a
  predicate", etc. without a separate selection VM.
- `on_current_changed: Callable[[VM | None], None]` — side-effect on
  selection change. Cheaper than subscribing to the observable for the
  common case.

Batch semantics: `_batch_depth` / `_batch_dirty` collapse N mutations
inside a `with batch():` block into a single `on_collection_changed`
emission.

**This is the primitive that subsumes every hand-rolled list-with-cursor
VM in aws-tui.**

### 2.2. `ObservableList[T]`

**File:** `vmx/collections/observable_list.py`

Lower-level than `CompositeVM`: holds raw items `T`, not VMs.
`Generic[T]`. Four granular observables:

- `on_item_added: Observable[tuple[T, int]]` — `(item, index)`
- `on_item_removed: Observable[tuple[T, int]]` — `(item, index_before_removal)`
- `on_item_replaced: Observable[tuple[T, T, int]]` — `(new, old, index)`
- `on_reset: Observable[None]`

`PropertyChanged("Count")` fires after every mutation that changes count.

Use this when the list elements are raw values (filter chip states,
recent-paths breadcrumbs, etc.) and lifting them into per-item VMs would
be overkill.

### 2.3. `PagedComposition`

**File:** `vmx/collections/paged_composition.py`

Paginated wrapper over an `ObservableList[T]`. Built-in `next_page() /
prev_page() / current_page` semantics. Use for boto3-style
`nextToken`-paginated APIs.

**Spike target during Phase 0 of the migration:** the EMR job-run pagination
is forward-only token-driven; verify `PagedComposition` accommodates that
mode (vs index-based pagination). Adapter layer may be required.

### 2.4. `FormVM[TM]` (and `FormVMBuilder`)

**File:** `vmx/forms/form_vm.py`

Typed model `TM` form VM. Built-in:

- `is_dirty` property
- Per-field validators
- Strict mode: `is_dirty` change re-fires `can_execute_changed` on the
  associated submit command
- Form-reverted message
- Field-level change observables

Replaces the hand-rolled `is_dirty` tracking and validator chains in
`s3_connections_vm.py` and `settings_vm.py`.

### 2.5. `IDialogService` / `DialogService` / `NullDialogService`

**File:** `vmx/dialogs/`

Standard contract for "push a modal, await the user's choice, dismiss".
The framework owns push / dismiss / result-future plumbing. Apps inject
either a real implementation (which interfaces with the View framework's
modal stack) or `NullDialogService` (which auto-dismisses with a default
choice — used by tests).

Replaces the bespoke `push_screen_wait` plumbing in `confirm_vm.py`,
`resume_vm.py`, `crash_vm.py`, and `first_run_vm.py`.

### 2.6. `HierarchicalVM` (and `HierarchicalVMBuilder`)

**File:** `vmx/hierarchical/`

Tree-shape VM. Likely **not applicable** to aws-tui today — no tree-shaped
surfaces. Skip unless a future feature (e.g., a hierarchical bucket
browser or a folder-tree navigation pane) adopts the shape.

### 2.7. `GroupVM` (and `GroupVMBuilder`)

**File:** `vmx/groups/`

Grouped composition — N typed children with a discriminator. Could be a
target for the EMR state-filter chip strip (each `JobRunState` is a
"group"), but the current `_KEY_TO_STATE` mapping is fine. Investigate
only if the chip strip grows in complexity.

### 2.8. `ServicedObservableCollection`

**File:** `vmx/collections/serviced_observable_collection.py`

Observable collection that hooks into VMx's service registry: removing
an item from the collection automatically calls the item's `dispose()`.
The "automatic dispose-on-remove" the `transfers_vm.py` and `toast_stack_vm.py`
currently hand-roll.

### 2.9. `ForwardingComponentVM` / `ForwardingCompositeVM`

**File:** `vmx/forwarding/`

VMs that present an unchanged contract while forwarding to an inner
implementation. Useful when a parent VM wants to expose its
construct/destruct/dispose lifecycle but defer the actual behaviour to an
inner. The pattern several aws-tui chrome VMs already manually implement
(`CommandPaletteVM._inner.construct()` etc.). Adopting
`ForwardingComponentVM` would let those chrome VMs drop their lifecycle
trampoline boilerplate.

### 2.10. `Batch` and `ObservableDictionary`

`Batch` is a context manager for batched mutations across observable
collections. `ObservableDictionary` is the key-indexed analog of
`ObservableList`. Investigate during Phase 0 if a per-key access pattern
emerges as common in any aws-tui VM (e.g., per-connection cache lookups).

## 3. Current state in aws-tui

### 3.1. Inventory: what we currently hand-roll

The VM layer at HEAD `5ee16b9` is 7,339 LOC across 35 files. The single
existing `CompositeVM` use is in `vm/nav_menu_vm.py`. The following
table summarises what each list-shaped or form-shaped or modal-shaped VM
currently does manually:

| VM | LOC today | Current pattern | Target |
|---|---|---|---|
| `vm/file_manager/pane_vm.py` | 893 | `_entries: tuple[EntryVM, ...]` + hand-rolled cursor + filter + state machine + manual `PropertyChangedMessage.create(self, "entries")` emissions | `CompositeVM[EntryVM]` with `current_selector` for filter-aware cursor; state machine stays as a property on the parent |
| `vm/emr_serverless/job_runs_vm.py` | ~280 | `_runs_cache: tuple[JobRunSummary, ...]` + `_next_token` + manual broadcasts | `CompositeVM[JobRunVM]` wrapped by `PagedComposition` for forward-only token pagination |
| `vm/emr_serverless/applications_vm.py` | ~200 | `_apps` + `_selected_id` + sorted-applications property + manual broadcasts | `CompositeVM[ApplicationVM]` with sort via `current_selector` |
| `vm/file_manager/transfers_vm.py` | ~250 | hand-rolled child `TransferVM` list + dispose orchestration in `_run_one_transfer` finally | `ServicedObservableCollection[TransferVM]` (automatic dispose-on-remove) or `CompositeVM[TransferVM]` with `auto_construct_on_add=True` |
| `vm/chrome/toast_stack_vm.py` | ~120 | hand-rolled queue + dismiss/expire | `CompositeVM[ToastVM]` with `auto_construct_on_add=True` |
| `vm/settings/s3_connections_vm.py` | ~180 | hand-rolled field list + per-field validators + `is_dirty` | `FormVM<S3Connection>` |
| Forms in `vm/settings/settings_vm.py` | ~80 (form portion) | same | `FormVM<...>` |
| `vm/chrome/confirm_vm.py` | ~130 | push/dismiss/result-future hand-rolled | `IDialogService.show(...)` |
| `vm/chrome/resume_vm.py` | ~110 | same | `IDialogService.show(...)` |
| `vm/chrome/crash_vm.py` | ~100 | same | `IDialogService.show(...)` |
| `vm/chrome/first_run_vm.py` | ~120 | same | `IDialogService.show(...)` |
| `vm/chrome/command_palette_vm.py` | ~240 | hand-rolled list + cursor + filter (palette shape) | `CompositeVM[PaletteEntryVM]` + filter callable. **Verify in Phase 0 spike whether the dynamic action-registration story fits.** |
| `vm/chrome/theme_picker_vm.py` | ~190 | hand-rolled list + cursor + live preview side effect | `CompositeVM[ThemeRowVM]` + `on_current_changed` for preview |
| `vm/nav_menu_vm.py` | ~290 | already uses `CompositeVM[ComponentVMOf[ServiceDescriptor]]` | Verify usage is complete (is the current slot wired?), document as the existing reference implementation |

68 hand-emitted `PropertyChangedMessage.create(self, "...")` calls
across the VM layer; most are list-mutation broadcasts that
`CompositeVM.on_collection_changed` would replace.

364 explicit references to VMx framework primitives (any of:
`MessageHub`, `PropertyChangedMessage`, `RelayCommand`,
`ConstructionStatus`, lifecycle methods) — many of those would either go
away or fall behind the `CompositeVM` abstraction after migration.

### 3.2. LOC analysis

Today, by layer (`find … -name '*.py' | xargs wc -l | tail -1`):

| Layer | LOC | Test LOC | Test-to-code |
|---|---|---|---|
| `src/aws_tui/ui/` | 6,879 | 3,368 | 49% |
| `src/aws_tui/vm/` | 7,339 | 6,489 | 88% |
| `src/aws_tui/domain/` | 2,483 | ~2,400 | ~95% |

Estimated reduction in the VM layer after full adoption:

| Phase | LOC delta (approx) | Cumulative |
|---|---|---|
| Phase 1 (leaf-VM migrations) | −350 | −350 |
| Phase 2 (`job_runs_vm`) | −250 | −600 |
| Phase 3 (`pane_vm`) | −300 | −900 |
| Phase 4 (forms) | −250 | −1,150 |
| Phase 5 (dialog service) | −240 | −1,390 |
| Phase 6 (palette + theme picker) | −150 | −1,540 |
| **Estimated VM layer post-adoption** | | **~5,800 LOC (−21%)** |

These are rough numbers; the Phase 0 spike will validate them. The
genuine value is not LOC delta but **eliminating the
forgot-to-broadcast / forgot-to-dispose-child bug families**.

## 4. Target architecture

### 4.1. Principles

1. **Re-evaluate each existing hand-rolled VM against the toolkit
   before re-writing it.** The mistake §1.3 records is exactly this:
   assume the framework lacks a primitive without checking. Every
   per-VM migration starts by `cat`-ing the candidate VMx primitive's
   source and asking, in writing on the PR, "does this VM's contract
   actually fit?"

2. **Use `CompositeVM[VM]` for homogeneous collections of child VMs.**
   `JobRunsVM`, `PaneVM`, `ApplicationsVM`, `TransfersVM`,
   `ToastStackVM`, `CommandPaletteVM`, `ThemePickerVM`.

3. **Use `ObservableList[T]` for raw item lists.** Where the items don't
   carry their own lifecycle and per-item state. Use sparingly; most
   list-shaped VMs in aws-tui will benefit from `CompositeVM` instead.

4. **Use `FormVM<TM>` for typed input forms.** `S3ConnectionsVM` and the
   form portion of `SettingsVM`.

5. **Use `IDialogService` for modal lifecycle.** The four chrome modal
   VMs.

6. **Reject the abstraction case-by-case if it doesn't fit.** A target
   shape is a hypothesis. The PR must verify the hypothesis with code +
   tests. **If a hand-rolled VM is LARGER after migration, revert and
   document why** — this is the case-by-case re-evaluation the user
   directive calls for.

7. **Use `ServicedObservableCollection` where automatic dispose-on-remove
   matters.** Transfers and toast stack. Skip for collections whose
   children outlive the collection.

### 4.2. Per-VM target shape

#### 4.2.1. `JobRunsVM` → `CompositeVM[JobRunVM]` + `PagedComposition`

**Today:** `_runs_cache: tuple[JobRunSummary, ...]` + `_next_token` +
`_selected_id` + filter state. Six `PropertyChangedMessage` emission
sites (`runs`, `selected_id`, `state`, etc.).

**Target:**
- Lift each `JobRunSummary` into a small `JobRunVM` carrying the summary
  + downstream-bound state (e.g., is-this-run-being-cloned).
- `JobRunsVM` becomes a `CompositeVM[JobRunVM]` wrapped in
  `PagedComposition` for the `nextToken` pagination.
- `current: JobRunVM | None` replaces `_selected_id`.
- `current_selector` re-asserts the user's prior selection after a refresh
  (the "drop stale selection if the run vanished" branch on the current
  code, line ~158).
- State-filter is a `filter: Callable[[JobRunVM], bool]` plumbed into a
  derived view (Phase 0 spike: confirm `CompositeVM` exposes this; if not,
  the filter stays at the View layer where it is today).

**Tests carried over:**
`tests/unit/vm/emr_serverless/test_job_runs_vm.py` (probably ~30 cases).
Add shape tests asserting `on_collection_changed` emits on refresh, on
load_more, on filter change.

**LOC delta estimate:** −250.

#### 4.2.2. `PaneVM` → `CompositeVM[EntryVM]`

**Today:** 893 LOC; central to file-manager. Hand-rolls
`_entries: tuple[EntryVM, ...]` + cursor + filter + the whole PaneState
machine + manual broadcasts.

**Target:**
- `entries` becomes the underlying `CompositeVM` children list.
- `current: EntryVM | None` is the cursor.
- State machine (`PaneState`) stays as a property on the parent — it's not
  list-shaped.
- Filter sits as a `current_selector` augmentation or as a derived
  filtered view, depending on Phase 0 spike findings.

**Risk:** PaneVM is the biggest single VM and the most-tested; consider
splitting the PR into "introduce CompositeVM scaffold with feature flag
gating both code paths" + "remove old paths" if the diff exceeds 600
lines.

**LOC delta estimate:** −300.

#### 4.2.3. `ApplicationsVM` → `CompositeVM[ApplicationVM]`

**Today:** ~200 LOC. Hand-rolls `_apps` + `_selected_id` + sorted view
(STARTED first per PR #90).

**Target:**
- Each `ApplicationSummary` lifted into an `ApplicationVM`.
- `CompositeVM` for the collection.
- Sort via `current_selector` or via a derived sorted view (the
  STARTED-first order is selection-relevant — `current_selector` is the
  better fit).
- `selected_id` retires; views read `current.descriptor.id` instead.

**LOC delta estimate:** −150.

#### 4.2.4. `TransfersVM` → `ServicedObservableCollection[TransferVM]`

**Today:** ~250 LOC. Hand-rolls the child transfer list and explicit
dispose in `_run_one_transfer`'s finally block.

**Target:**
- `ServicedObservableCollection[TransferVM]` auto-disposes a `TransferVM`
  when removed.
- Pre-registration logic (current ~lines 331–383) becomes a `Batch` block
  over the collection so the View redraws once per drop-batch instead of
  per-entry.

**LOC delta estimate:** −100.

#### 4.2.5. `ToastStackVM` → `CompositeVM[ToastVM]`

**Today:** ~120 LOC, hand-rolled.

**Target:** `CompositeVM[ToastVM]` with `auto_construct_on_add=True`.
Dismiss/expire becomes `remove(toast)`.

**LOC delta estimate:** −50.

#### 4.2.6. `S3ConnectionsVM` → `FormVM<S3Connection>`

**Today:** ~180 LOC of hand-rolled fields + validators + dirty tracking.

**Target:** `FormVM<S3Connection>` with a typed model. Validators chain via
the builder. Submit command's predicate becomes the framework's strict
mode.

**LOC delta estimate:** −200.

#### 4.2.7. The four modal VMs → `IDialogService`

**Today:** Each of `ConfirmationVM`, `ResumeVM`, `CrashVM`,
`FirstRunVM` hand-rolls push/dismiss/result-future plumbing.

**Target:** Inject `IDialogService` via composition. Each modal becomes a
thin VM that constructs its model, calls `service.show(...)`, awaits the
result, returns. The "what does dismiss mean" semantics are now the
framework's contract instead of each VM's manual interpretation.

**LOC delta estimate:** −60 × 4 = −240.

#### 4.2.8. `CommandPaletteVM` + `ThemePickerVM`

**Today:** ~240 + ~190 LOC. Each is a list + cursor + filter + side
effects (palette executes actions; theme picker live-previews).

**Target:** `CompositeVM[EntryVM]` + filter callable + `on_current_changed`
side-effect callback.

**Risk:** Highest risk of fighting the abstraction. The palette's dynamic
action-registration story is more bespoke than the rest. If after the
spike + first migration attempt the LOC delta is positive (i.e., the
migration made it bigger), revert and keep the hand-roll. Document the
decision.

**LOC delta estimate:** −150 if it fits; **0 and a documented "fits
poorly" if it doesn't**.

#### 4.2.9. `NavMenuVM` — already uses `CompositeVM`

Verify the existing usage is complete: is `current` wired? Is
`on_collection_changed` consumed by the View? If yes, this VM is the
reference implementation. If partially wired, finish the adoption in
this spec's Phase 1.

#### 4.2.10. Out of scope

- `RootVM` / `ContentHostVM` — orchestrators, not list-shaped.
- `ChromeVM` — orchestrator.
- `EmrServerlessPageVM` (`page_vm.py`) — composite of three child VMs but
  in a fixed-shape (master + detail + logs) pattern; this is an
  `AggregateVM3`-style composition, not `CompositeVM`. The current
  manual orchestration is fine; do not migrate.
- `JobRunDetailVM` / `JobRunLogsVM` — single-target state machines, not
  list-shaped.
- `JobRunCloneVM` — single-form-shaped; could in principle move to
  `FormVM<JobRunCloneInput>` but it's also a one-off modal flow with no
  reusable form state. Defer to a follow-up.
- All settings-section VMs that don't have a form (`SettingsVM` is
  primarily a router/composer once forms are extracted).
- `TransferVM` (the child VM) — the leaf doesn't change shape; only its
  parent's container does.

## 5. Phased migration plan

### 5.1. Phase 0 — bench setup + spike

One PR, no shipped behavior change.

Deliverables:
- `docs/superpowers/specs/2026-06-28-vmx-toolkit-adoption-design.md` (this
  file).
- `docs/superpowers/plans/2026-06-28-vmx-toolkit-adoption.md` (produced
  via `superpowers:writing-plans` after this design is approved).
- A throwaway spike branch demonstrating one VM migrated (recommendation:
  `toast_stack_vm` — smallest blast radius, clearest shape). The spike's
  job is to surface API mismatches (e.g., does
  `auto_construct_on_add=True` actually compose with `ToastVM`'s
  current construct() signature?). Spike is **not** merged.
- A short answer to each Phase 0 open question (§9) recorded as an
  amendment to this spec or in the plan file.

Verification: the spike must run `uv run pytest tests/unit/vm/chrome/`
green after the migration.

### 5.2. Phase 1 — leaf-VM migrations

One PR per VM, in order:

1. `ToastStackVM` → `CompositeVM[ToastVM]`
2. `TransfersVM` → `ServicedObservableCollection[TransferVM]`
3. `ApplicationsVM` → `CompositeVM[ApplicationVM]`

Why this order: each migration is a leaf in the VM tree (no other VM
depends on its internal shape); each has good test coverage; the order
is chosen so that any framework-quirk learned in PR 1 informs PR 2 and 3.

Cumulative LOC delta: −300.

### 5.3. Phase 2 — pagination + the EMR runs train

One PR, `JobRunsVM` → `CompositeVM[JobRunVM]` + `PagedComposition`.

Reserve a dedicated PR because the `PagedComposition` adapter — if needed —
is novel work, and `JobRunsVM`'s test surface includes the redraw-flash
regression tests from PRs #100 and #103. Make those tests the explicit
acceptance gate.

Cumulative LOC delta: −550.

### 5.4. Phase 3 — `PaneVM`

One PR. The biggest single migration. Consider splitting into:
- "Introduce `CompositeVM[EntryVM]` scaffold; old `_entries` tuple co-
  exists behind an internal flag; both broadcast in parallel for one
  release."
- "Remove old path."

Cumulative LOC delta: −850.

### 5.5. Phase 4 — Forms

One PR, `S3ConnectionsVM` + the form portion of `SettingsVM` →
`FormVM<TConnection>`.

Cumulative LOC delta: −1,100.

### 5.6. Phase 5 — Dialog service

One PR with four commits — one per modal. Touches `composition.py` to
inject the service (or wires the existing modal-push helper as the
service implementation), then migrates each of `ConfirmationVM`,
`ResumeVM`, `CrashVM`, `FirstRunVM`.

Cumulative LOC delta: −1,340.

### 5.7. Phase 6 — Command palette + theme picker

One PR. Highest risk of revert (see §4.2.8). Accept the possibility that
one or both stays hand-rolled.

Cumulative LOC delta: −1,490 if both ship; smaller if one reverts.

## 6. Validation strategy

### 6.1. Per-phase gate

Every PR runs the full pytest + snapshot + ruff + mypy + check-layers
suite before merge. Existing project standard; no new tooling required.

### 6.2. Shape tests

Each migrated VM gets a small **shape test** that pins the
`on_collection_changed` (or equivalent) event sequence under standard
mutations:

```python
def test_compositevm_emits_collection_changed_on_add(vm: ToastStackVM) -> None:
    events: list[CollectionChangedEvent] = []
    vm.on_collection_changed.subscribe(events.append)
    vm.add(make_toast())
    assert len(events) == 1
    assert events[0].kind == CollectionChangedEvent.Kind.ADD
```

These are 5–10 lines each, pin the framework contract from the app's
side, and catch silent regressions in either direction (the app misusing
the framework, or the framework changing semantics in a future minor).

### 6.3. LOC accounting

Per-PR commit message records the actual LOC delta vs the estimate in
this spec. If delta is GREATER than zero (the migration grew the VM),
**revert and document** — this is the case-by-case rejection §4.1
principle 6 mandates.

### 6.4. Regression anchors from prior bug train

The bugs that motivated this spec are themselves the strongest
regression anchors. Each phase's PR explicitly references one or more
prior bug-train PRs as the test target:

- Phase 2 (`JobRunsVM`): reference PRs #100, #103. The
  `test_emr_serverless` suite must keep passing without modification.
- Phase 3 (`PaneVM`): the full `tests/unit/vm/file_manager/` suite is the
  acceptance gate.
- Phase 5 (modals): `tests/integration/test_confirm_modal_keyboard.py`
  and `tests/integration/test_first_run_modal.py` are anchors.

## 7. Risks

### 7.1. `PagedComposition` may not fit token-pagination

EMR uses `nextToken`-style forward-only pagination. `PagedComposition` may
have been designed assuming index-based pagination (page N of M). If
that's the case, the migration of `JobRunsVM` would need:

- An adapter layer that maps `nextToken` → page-index; or
- Keeping the pagination logic outside `CompositeVM` and using
  `CompositeVM` only for the per-page item list.

Investigated in Phase 0 spike.

### 7.2. Filter coupling

`PaneVM` and `CommandPaletteVM` do client-side filtering on the
collection. `CompositeVM.current_selector` is per-current-selection,
not a global filter on the children list. Options:

- Two-layer VMs: a `CompositeVM[EntryVM]` for the unfiltered population
  + a derived `CompositeVM[EntryVM]` for the filter result.
- Or: keep the filter at the View layer (where it is today for some
  views) and treat the VM's `current` as the cursor over the unfiltered
  list, with the View mapping cursor positions to filtered visible
  rows. Less idiomatic, but lower-risk.

Decided in Phase 0 spike per the specific VM.

### 7.3. Lifecycle propagation surprises

`CompositeVM` cascades `construct/destruct/dispose` to children. Some
hand-rolled VMs intentionally do not dispose children (e.g., when
ownership is transferred elsewhere, or when the child outlives the
collection). Each migration must audit ownership semantics before
flipping the switch.

In particular, `TransfersVM` and `transfer_journal` interact in the
crash-recovery flow: a transfer journal entry can outlive the
`TransfersVM` that originally tracked it. `ServicedObservableCollection`
must NOT dispose the journal entry when the transfer leaves the live
list. Verify with `test_transfer_journal` suite as the acceptance gate.

### 7.4. Adoption resistance — the "abstraction tax"

Adopting a framework primitive trades hand-rolled clarity for an
abstraction the next reader has to learn. Some readers will find the
hand-roll easier to grok at a glance than the `CompositeVM` wrapping
with its `current_selector` callback + `auto_construct_on_add` flag.

Counter-argument: VMx's primitives are documented and stable; their
contracts are explicit; their bug surface is the framework's, not aws-
tui's. The recurring forgot-to-broadcast / forgot-to-dispose bug class
that the framework eliminates is a higher cost than the learning curve.

Decision lives at the per-VM PR — if the reviewer prefers the hand-
roll for a specific VM, that VM stays hand-rolled and the decision is
documented in the PR description and this spec gets a follow-up
amendment.

### 7.5. Time budget

4–6 PRs over 2–3 weeks is the estimate. This is non-trivial. The
project's typical PR cadence is high (several PRs per active day in the
recent train), but design-heavy PRs land slower. If the work stalls
mid-phase, the **branch must be merge-mergeable** at every PR boundary —
no in-flight half-migrations. The phased plan respects this by making
every phase a complete merge unit.

## 8. Out of scope

### 8.1. View layer changes

The View layer's `subscribe_to_vm` mixin already speaks
`PropertyChangedMessage` and works unchanged with `CompositeVM`'s
`on_collection_changed`. Some views (`JobRunsPane._on_hub_message`) will
need to subscribe to the composite's observable instead of (or in
addition to) the hub; this is a trivial bridge change but should land in
the same PR as the corresponding VM migration to keep the diff
self-contained.

No CSS / layout / theming work is in scope.

### 8.2. Domain layer changes

Pure adoption of an existing framework primitive in the VM layer.
Domain stays as-is.

### 8.3. `FocusCoordinatorVM`

The recurring focus bugs (PR #98 / #99 / #101) are downstream of a
different gap — a missing app-wide focus state machine. This is a real
follow-up worth doing, but bundling it with the toolkit adoption would
double the scope and the risk. See the parallel spec
`docs/superpowers/specs/2026-XX-XX-focus-coordinator-design.md` (to be
written separately).

### 8.4. Performance optimisation

`CompositeVM`'s emission semantics may be slightly different from the
current hand-rolled approach (granular events vs single
PropertyChanged). For most aws-tui views this is a wash. If a specific
view turns out to be re-render-sensitive, address in a follow-up PR
under a different spec.

### 8.5. New service additions

Not a feature-add spec. The seven existing services
(`s3`, `emr-serverless`, `settings`, the three modal flows, the chrome
overlays) keep their current contracts.

## 9. Open questions

To be answered during the Phase 0 spike:

1. Does `PagedComposition` accept forward-only token pagination, or does
   it assume index-based?
2. Can `CompositeVM`'s filter story handle the dynamic-filter case in
   `PaneVM` and `CommandPaletteVM`, or do we need a derived view?
3. Does `auto_construct_on_add=True` compose with `ToastVM`'s current
   construct signature, or does the leaf VM need a small adapter?
4. Does `FormVM<TM>`'s validator API support the cross-field validation
   `S3ConnectionsVM` does (e.g., "endpoint URL must be set IFF
   force_path_style is True")?
5. Does `IDialogService`'s contract include the resume-modal's
   "show again on next boot if the user keeps for later" semantics, or
   does that need a domain-side state outside the dialog service?
6. Is there a simpler form of `ServicedObservableCollection` that does
   NOT take ownership, for the transfer_journal interaction?

Each answered question becomes an amendment to this spec (preferred) or
a note in the corresponding plan task (acceptable if the answer is
narrow and per-task).

## 10. Definition of done

The migration is complete when:

1. All eight per-VM targets in §4.2 have either landed or are explicitly
   documented as "kept hand-rolled" with rationale.
2. Total VM-layer LOC is below 6,000 (down from 7,339).
3. The number of hand-emitted `PropertyChangedMessage.create(self, ...)`
   calls in `src/aws_tui/vm/` is below 30 (down from 68).
4. The `tests/unit/vm/` suite is still green and includes shape tests
   for every migrated VM.
5. `check-layers.sh` still passes.
6. The CHANGELOG `[Unreleased]` records "VM-layer refactor — adopt VMx
   toolkit primitives; user-visible behavior unchanged".
7. This spec is updated with the actual per-VM disposition (migrated /
   kept) and the actual LOC delta vs the estimate.

## 11. References

- VMx 2.6.1 source: `.venv/lib/python3.11/site-packages/vmx/`
- `docs/architecture.md` — current five-layer model
- `docs/superpowers/specs/2026-06-13-aws-tui-design.md` — M0 baseline
- PRs #98, #99, #100, #101, #103 — the bug train that motivated this work
- Maintenance-spec §3.18 — design-pattern opportunities (signals not
  violations); this spec is the per-pattern case for `CompositeVM` /
  `FormVM` / `IDialogService` adoption
- Conversation transcript 2026-06-28 review session — the analytical
  walk-through this spec captures
