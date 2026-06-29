# VMx toolkit adoption — design spec

| Field | Value |
|---|---|
| Status | Drafted, awaiting brainstorm → plan → execution |
| Date | 2026-06-28 |
| Owner | TBD at brainstorm |
| Driver | Architectural review session 2026-06-28 — see §1.2 |
| Related | [[docs/superpowers/specs/2026-06-13-aws-tui-design.md]] (M0 design baseline), [[docs/architecture.md]] (current five-layer model), [VMx 2.6.1 source](https://github.com/thekaveh/VMx) |
| Estimated effort | 4–6 PRs over 2–3 calendar weeks at the project's typical cadence (Phases 1–6) + 1 PR for Phase 7 (focus coordinator) |
| Target VMx version | `>=2.6.0,<3.0.0` (no version bump required) |

---

## 0. How to use this spec — reading guide for the next worker

> **Read this entire section before doing anything else.** The spec is the
> output of three review rounds; trying to act on it without reading the
> mistakes record (§1.3) and the discipline rules below has a high chance
> of repeating those mistakes.

**Read sections in this order:**

1. **§1.1** — what the work delivers and why (two architectural gaps).
2. **§1.3 — every numbered mistake.** Eight mistakes the prior review went
   through. Each one is a trap the next worker can also fall into. Read
   them as "do not".
3. **§3.2.bis** — the ten parallel sources of focus / selection state.
   Anchors the work to specific files, not vibes.
4. **§4.3** — the FocusCoordinatorVM design.
5. **§4.2.0** — NavMenuVM's incomplete adoption (the only VM in the
   project that already uses `CompositeVM`, and its usage is INCOMPLETE).
6. **§4.2.2** — PaneVM's `CompositeVM` vs `HierarchicalVM` evaluation.
   This is the canonical "explicit per-primitive comparison" the rest of
   §4.2 mirrors.
7. **§5.0** — why toolkit adoption comes before the focus coordinator.
8. **§9** — open questions to resolve in brainstorming.
9. The remaining §4.2 entries, in order — but with §4.2.0 and §4.2.2 as
   the templates for the methodology.

**Discipline rules — non-negotiable.**

These are not aspirational. The methodology mistakes recorded in §1.3
(specifically 1, 4, 6, 8) are all the same shape: someone opined on a
VMx primitive or an aws-tui VM without reading the source first. Every
brainstorming decision, every per-VM PR, every spec amendment must
record:

1. **Which VMx primitive(s) were evaluated** — by name, with file path
   under `.venv/lib/python3.11/site-packages/vmx/`.
2. **Why the chosen primitive fits (or why none does)** — with a quote
   from the primitive's source, not paraphrase.
3. **What was inspected to know** — file paths and line ranges that were
   actually read, not a list of imports.

**Verification commands to run BEFORE making any claim:**

- *Before claiming a VMx primitive exists or doesn't:*
  `find .venv/lib/python3.11/site-packages/vmx -name "*.py" | xargs grep -l "<symbol>"`
- *Before claiming an aws-tui VM does or doesn't have a field:*
  `grep -n "<field_name>" src/aws_tui/vm/<area>/<file>.py`
- *Before claiming a test fails:*
  `uv run pytest tests/path/test_<name>.py::<test_id> -x -v 2>&1 | tail -30`
- *Before claiming a CSS property is invalid in Textual:*
  `rg "<property>" .venv/lib/python3.11/site-packages/textual/`
- *Before claiming a VMx version is missing a symbol:*
  `uv run python -c "from vmx import <Symbol>; print(<Symbol>)"`

If a claim is made without one of those commands behind it, the spec's
§1.3 mistakes 1, 4, 6 are repeating. The reviewer of any PR or
amendment should reject on those grounds.

**Brainstorming-session guardrails.**

The brainstorming session that follows this spec resolves §9's open
questions. It does NOT:

- Propose new VMx primitives (mistake 2: VMx already ships everything we
  need; see §2).
- Re-litigate decisions captured in §4 or §5 (the spec is canonical;
  amendments require an explicit reason recorded in §1.3 as "mistake N").
- Expand scope to include features not listed in §4.2 / §4.3
  (mistake 8 was scope creep in the opposite direction; do not over-
  correct).
- Decide implementation details that belong in `/writing-plans`
  output (e.g., which line of code to delete first — that's for the plan,
  not the brainstorm).
- Produce more than ~150–300 lines of spec amendment. If the
  brainstorming output exceeds that, something has gone wrong — likely
  scope creep or re-litigation.

If brainstorming finds the spec genuinely wrong on a point (a 9th
mistake), record it as such with the same format §1.3 uses, then
proceed.

**Workflow assumptions** — see Appendix A. **Per-VM cross-reference table**
— see Appendix B.

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
symptoms of TWO related architectural gaps**:

1. The project hand-rolls the observable-list-with-cursor pattern in every
   list-shaped ViewModel, then fights subtle bugs (forgot-to-broadcast,
   broadcast-too-often, child-VM dispose-order, cursor-vs-collection-mutation
   races) that VMx 2.6.1's `CompositeVM` already solves.
2. Pieces of selection and focus state that should live in the VM layer on
   plain MVVM discipline currently live in widgets — cursor positions,
   selection IDs, slot tracking. Ten parallel sources of "what is selected /
   focused" across View and VM (inventoried in §3.2.bis). The recurring
   focus-bug train (PRs #98 / #99 / #101) is the surface symptom of this
   fragmentation.

This spec addresses both halves together. The VMx-toolkit-adoption work
(Phases 1–6) re-platforms each list-shaped VM onto `CompositeVM` /
`FormVM` / `IDialogService`; in doing so it consolidates the cursor +
selection state into the framework's `current` slot. Phase 7 then adds a
`FocusCoordinatorVM` (§4.3) that observes the now-uniform `current` slots
and projects the app-wide focused slot, replacing the four-way focus-
signal collision the View currently lives with.

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

> **Update — amendment after a second review round (also 2026-06-28).** The
> first draft of this spec, recording mistakes 1–4 below, was itself wrong on
> three further counts surfaced when the reviewer pushed back with
> "but couldn't the menu just be a `CompositeVM`?" and "shouldn't S3 be
> `HierarchicalVM`?". The draft had:
>
> - **Mistake 5: defaulted to `CompositeVM` for every list-shaped VM
>   without explicitly evaluating `HierarchicalVM` per case.** The S3 file
>   pane is the canonical test of this — a tree-shaped domain whose current
>   View renders one level at a time. Whether the VM should model the flat
>   level (`CompositeVM`) or the lazy tree (`HierarchicalVM`) is a real
>   choice the spec must call out, not silently default. See §4.2.x for the
>   updated per-VM analysis and §9 for the resulting open question.
> - **Mistake 6: did not read `NavMenuVM`'s existing `CompositeVM` usage
>   before claiming it was "the reference implementation".** Reading
>   `vm/nav_menu_vm.py` line 129 shows the VM uses `CompositeVM` for the
>   children list but tracks `_selected_id: str | None` as a parallel
>   hand-rolled state. The composite's `current` slot — which is exactly the
>   primitive that propagates selection — is unused. NavMenuVM is the
>   project's **partial** adoption, not its reference. The user's review
>   question "shouldn't the menu be a `CompositeVM` since it has a selected
>   item that needs propagating?" was diagnosing this gap precisely.
> - **Mistake 7: did not explicitly consider whether the focus / pane-
>   selection refactor (the "FocusCoordinatorVM" follow-up §8.3 punts on)
>   should land BEFORE the toolkit adoption.** §8.3 declared the focus
>   coordinator out of scope without addressing the ordering question. See
>   §5.0 below for the analysis and decision.
>
> **Methodology lesson for the next worker.** Every per-VM target in §4
> must include three explicit answers, in writing in the PR description:
> (1) which VMx primitive(s) was/were evaluated (`CompositeVM`,
> `ObservableList`, `HierarchicalVM`, `PagedComposition`, etc.);
> (2) why the chosen primitive fits (or why none does, and the VM stays
> hand-rolled); (3) what was inspected to know — the file path of the
> primitive's source, the file path of the existing VM, and any test that
> exercises the contract. Without those three answers, the PR is not ready
> for review.

> **Update — amendment after a third review round.** The user pushed back
> on the round-2 draft with one more question that exposed a final mistake:
>
> - **Mistake 8: scoped the spec around the VMx-toolkit-adoption framing
>   and treated the focus / pane-selection refactor as a parallel follow-up
>   (was §8.3 "out of scope").** That framing under-promised what this
>   work needs to deliver. The user's actual diagnosis is that pieces of
>   focus and selection state currently live in the View layer **regardless
>   of VMx** — moving them is a plain MVVM-principle requirement. VMx's
>   `CompositeVM.current` happens to be the cleanest way to accept the
>   migrated state, but the MIGRATION itself stands independent of the
>   framework. Treating it as out-of-scope hides the actual work.
>
>   §3.2.bis (new) inventories the ten parallel sources of focus /
>   selection state — six of which live in the View. §4.3 (was §8.3)
>   promotes the FocusCoordinatorVM into this spec as a real target.
>   Phase 7 (§5.8) executes it after the per-VM toolkit adoption lays
>   uniform `current` slots for the coordinator to bridge.
>
> **Updated methodology lesson.** The scope of this spec is "move VM-layer
> state out of the View, and adopt VMx's toolkit primitives as the
> mechanism". The two halves are not separable. The next worker should not
> treat any per-VM Phase as "just adopt the framework primitive" — every
> Phase has both an MVVM-principle goal (what state moves where) and a
> VMx-toolkit goal (which primitive backs the move). The PR must address
> both.


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

### 2.6. `HierarchicalVM[TModel, TVM]` (and `HierarchicalVMBuilder`)

**File:** `vmx/hierarchical/hierarchical_vm.py`
**Tree-walk utilities:** `vmx/tree/walk.py` — `walk`, `find`, `walk_expanded`
**Expansion capability:** `vmx/capabilities/expansion.py` — `IExpandable`

Generic over a typed `TModel` (the domain model per node) and `TVM` (the VM
type of children, recursively bound to `HierarchicalVM[Any, Any]`).

Key public API:

- `model: TModel` — the domain model at this node.
- `children: Sequence[TVM]` — children of this node.
- `parent: TVM | None` — the parent reference.
- `add_child(child)` / `remove_child(child)` — emit
  `TreeStructureChangedMessage` (`ADDED` / `REMOVED`) on `hub`.
- Reparenting emits `TreeStructureChange.REPARENTED`.
- `__iter__` — yields materialized children (lets `walk` / `walk_expanded`
  / `find` see this node as a traversable).

Construction knobs:

- `children_factory: Callable[[TVM], Iterable[TVM]]` — produces child VMs
  for THIS node. **Called lazily** on first `.children` access by default.
- `eager_children: bool = False` — when `True`, materialises the entire
  subtree at `construct()` time, depth-first.

Capabilities:

- `IExpandable` — opt-in interface a node implements to report
  `is_expanded`; `walk_expanded` traversal then skips collapsed subtrees.

**This is the right primitive when:**

- The DOMAIN is hierarchical (file system, GUI widget tree, DOM, document
  outline).
- The View wants to render multiple levels of the hierarchy at once
  (expand/collapse rows inline).
- Navigation history (parent/child references) is part of the VM's state
  rather than the View's.

**This is NOT the right primitive when:**

- The View shows a single level at a time and "navigation" is a `cd`-style
  re-bind (the Norton-Commander pattern). For that, `CompositeVM` of the
  current level's children is simpler and lower-overhead.
- The collection is naturally flat.

aws-tui's S3 file pane sits exactly on the boundary between these cases —
see §4.2.2 for the explicit evaluation.


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

### 3.2.bis. View-side state that should live in the VM layer
       — regardless of VMx

This section is the MVVM-principle question, independent of which framework
primitive ends up backing the move. The user's review (round 3) explicitly
pushed back on treating focus-coordination as a parallel follow-up: pieces
of selection/focus state currently live in widgets that, on plain MVVM
discipline, belong in the ViewModel layer. They would need to migrate even
if VMx did not ship `CompositeVM` / `current` / `IDialogService` — VMx just
makes the migration cleaner.

**The state-fragmentation inventory.** As of HEAD `5ee16b9` the project has
**ten** parallel sources of "which thing is selected / focused":

| # | Source of state | Layer | Purpose | Drifts with |
|---|---|---|---|---|
| 1 | `ui/widgets/nav_menu.py::NavMenu._cursor_index` | **View** | which row the rail's cursor is on | (2), (10) |
| 2 | `vm/nav_menu_vm.py::NavMenuVM._selected_id` | VM (hand-rolled) | which service is the active service | (1), the composite's unused `current` slot |
| 3 | `ui/widgets/emr_serverless/job_runs_pane.py::JobRunsPane._cursor_index` | **View** | which run row the cursor is on | (4) |
| 4 | `vm/emr_serverless/job_runs_vm.py::JobRunsVM._selected_id` | VM (hand-rolled) | which run is bound to the detail pane | (3), (10) |
| 5 | `ui/widgets/emr_serverless/application_picker.py` (per-Option highlight) | **View** | which app the picker dropdown highlights | (6) |
| 6 | `vm/emr_serverless/applications_vm.py::ApplicationsVM._selected_id` | VM (hand-rolled) | which app is the active app | (5), (10) |
| 7 | `vm/file_manager/dual_pane_vm.py::DualPaneVM._focused` (`FocusedPane.LEFT`/`RIGHT`) | VM | which file pane is the focused pane | (10), Textual's `app.focused` |
| 8 | `ui/widgets/emr_serverless/page.py::EmrServerlessPage._cycle()` (ad-hoc `has_focus` walks) | **View** | which of the four EMR slots the user is on | (10) |
| 9 | Screen-level `.-nav-active` CSS class (added PR #98) | **View** | a 4th focus signal, just so the file pane border can be dimmed | (1), (2), (7), (10) |
| 10 | `app.focused` (Textual runtime) | runtime | which widget the keyboard goes to | every other entry above |

Six of those (entries 1, 3, 5, 8, 9, plus the cursor-vs-selection split
inside the View widgets) genuinely live in the View today. The MVVM-
principle answer is **most of them shouldn't**.

**Why this matters as a separate read.** Even if we threw away VMx and
rewrote against bare Python observables, the principle "selection /
cursor state belongs in the VM, the View only reads it" still applies.
What VMx changes is HOW cleanly the move happens — `CompositeVM.current`
is exactly the slot that absorbs the cursor + selection collapse. But the
re-platforming of the state is the work; the choice of framework primitive
is the implementation detail.

**Per-piece migration targets** (cross-referenced with §4.2):

| Today | Target VM | Belongs to |
|---|---|---|
| (1) NavMenu widget `_cursor_index` | `NavMenuVM._inner.current` | §4.2.0 |
| (2) `NavMenuVM._selected_id` | `NavMenuVM._inner.current` (same slot — collapse the dual-state) | §4.2.0 |
| (3) JobRunsPane widget `_cursor_index` | `JobRunsVM._inner.current` | §4.2.1 |
| (4) `JobRunsVM._selected_id` | `JobRunsVM._inner.current` | §4.2.1 |
| (5) ApplicationPicker per-row highlight | `ApplicationsVM._inner.current` | §4.2.3 |
| (6) `ApplicationsVM._selected_id` | `ApplicationsVM._inner.current` | §4.2.3 |
| (7) `DualPaneVM._focused` | stays in VM but participates in the focus-coordinator below | §4.3 |
| (8) EMR page `has_focus`-walk slot tracking | new `EmrServerlessPageVM` slot field driving from the focus-coordinator | §4.3 |
| (9) Screen-level `.-nav-active` CSS hack | removed — replaced by the focus-coordinator's projection of "which slot has the user" | §4.3 |
| (10) `app.focused` (Textual runtime) | stays as Textual's concern; the focus-coordinator's job is to KEEP it in sync, not replace it | §4.3 |

§4.3 below adds the focus-coordinator design (was §8.3's "follow-up
spec"; promoted into this spec at review round 3).

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

> **Methodology reminder (from §1.3 mistake 5).** Every entry below carries
> three explicit answers: **(1) primitives evaluated**, **(2) chosen primitive
> and reason**, **(3) what was read to know**. The next worker should
> reproduce this discipline for any VM the spec does not pre-evaluate.

#### 4.2.0. `NavMenuVM` — finish the incomplete adoption

**Primitives evaluated:** `CompositeVM[ComponentVMOf[ServiceDescriptor]]`
(currently used for the children list), `HierarchicalVM` (rejected — the
nav rail is flat, not a tree).

**Chosen:** keep `CompositeVM`, but migrate the hand-rolled
`_selected_id: str | None` field to the composite's built-in `current` slot.

**What was read:** `vm/nav_menu_vm.py` lines 128–141 (the existing
`CompositeVM` builder configuration); lines 113–129 (the `_selected_id`
field that lives in parallel to the composite); `.venv/lib/python3.11/
site-packages/vmx/composites/composite_vm.py` lines 110–125 (the `current`
property + its observable + dispatcher-aware `async_selection` flag).

**Current state (gap):**

```python
# vm/nav_menu_vm.py
self._items: list[NavItemVM] = []
self._selected_id: str | None = None   # hand-rolled
self._inner: CompositeVM[…] = CompositeVM[…].builder()…build()
                                          # ↑ children, but NOT selection
```

`_selected_id` and the composite's `current` are two parallel sources of
truth for "which service is selected". Every selection-change site has to
update both. That's exactly the kind of duplicated-state bug surface this
adoption eliminates.

**Target:**

```python
self._inner: CompositeVM[…] = (
    CompositeVM[…]
    .builder()
    .name("nav_menu")
    .services(hub, dispatcher)
    .children(self._initial_children)
    .auto_construct_on_add(True)
    .current_selector(self._pick_default_current)   # ADD
    .on_current_changed(self._broadcast_selected)   # ADD
    .build()
)
```

`selected_id` becomes a derived read-only property:
`@property def selected_id(self) -> str | None: return self._inner.current.id if self._inner.current else None`.
The `switch_service_command` mutates `self._inner.current = …`. The View
subscribes to `on_current_changed` (or `on_collection_changed` for
rebuild). Stop emitting hand-rolled `PropertyChangedMessage(self,
"selected_id")` — the framework does the equivalent automatically.

**LOC delta estimate:** −60 (modest, because the composite is already there;
the win is eliminating the dual-state-tracking bug surface, not lines).

**Tests carried over:** `tests/unit/vm/test_nav_menu_vm.py` —
selected-id-propagation assertions become assertions on `current.id`.

This becomes the **reference implementation** for §4.2.1–§4.2.7 — but only
after this finish-the-adoption work lands. Until then, NavMenuVM is the
**partial** adoption, not the model.

#### 4.2.1. `JobRunsVM` → `CompositeVM[JobRunVM]` + `PagedComposition`

**Primitives evaluated:** `CompositeVM[JobRunVM]`,
`PagedComposition[JobRunVM]`, `HierarchicalVM` (rejected — runs are flat,
not a tree).

**Chosen:** `CompositeVM[JobRunVM]` for the run list, wrapped in
`PagedComposition` for forward-only `nextToken` pagination — pending Phase 0
spike confirming the framework's pagination semantics fit (see §9).

**What to read before starting:** `vm/emr_serverless/job_runs_vm.py` (entire
file, ~280 lines); `vmx/collections/paged_composition.py`;
`tests/unit/vm/emr_serverless/test_job_runs_vm.py` (existing test surface
— don't break these).


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

#### 4.2.2. `PaneVM` — `CompositeVM` vs `HierarchicalVM` evaluation

**Primitives evaluated:** `CompositeVM[EntryVM]`,
`HierarchicalVM[FileSystemNode, FileSystemNodeVM]`, `ObservableList[FileEntry]`
(rejected — entries are per-row VMs, not raw values).

This is the case where the choice is non-obvious and where mistake 5 (§1.3)
was made — the first draft defaulted to `CompositeVM` without explicitly
considering `HierarchicalVM`. The user's review pushback was on exactly this
question. The honest answer is the choice depends on a design call that has
not yet been made.

**Option A: `CompositeVM[EntryVM]` — the "flat per cd-level" interpretation**

The View today is Norton-Commander style: shows ONE level at a time, `cd`
into a folder = re-bind the pane to a new list. Under this model PaneVM
is a flat collection of children that gets replaced wholesale on every
navigation.

- `entries` becomes the composite's children list.
- `current: EntryVM | None` is the cursor.
- `cd` is `self._inner.clear(); self._inner.add_all(new_entries)` (inside a
  `Batch` block so the View redraws once).
- `_path` stays as a property on PaneVM since it's not list-shaped.
- `PaneState` (LOADING / IDLE / EMPTY / AUTH_REQUIRED / FORBIDDEN /
  UNREACHABLE / ERROR) stays as a property on PaneVM — it's the
  observation-of-the-list state machine, not list shape itself.

Pros: minimal change from today's idiom. Stays close to existing tests.
Lowest risk. Maps cleanly to the current View.

Cons: `_path` and the cd-stack are still hand-rolled. Treating
navigation as VM-side state would require a separate path-history VM.

**Option B: `HierarchicalVM[FileSystemNode, FileSystemNodeVM]` — the
"lazy tree of the whole bucket" interpretation**

The DOMAIN is genuinely hierarchical — S3 keys are `/`-delimited; LocalFS
is an actual tree. `HierarchicalVM` with `eager_children=False` (lazy) and
a `children_factory` that calls `provider.list(node.path)` would model the
entire bucket as a lazy tree:

- The root is the bucket (or filesystem root).
- Each folder node's `children_factory` invokes `provider.list(node.path)`
  on first `.children` access, then caches.
- `parent` reference is automatic.
- "cd" becomes "the current pane VM is whichever node is selected"; cd-up
  is `self._current_node.parent`; cd-into is `self._current_node = child`.
- The view still shows one level at a time, just by rendering
  `current_node.children` instead of `pane.entries`.
- Future feature win: an inline-expand tree view is a one-line render change
  (`walk_expanded(root)` instead of `current_node.children`).
- `IExpandable` capability per node lets directories carry their own
  expanded/collapsed state.

Pros: the domain IS hierarchical; the VM finally reflects that. cd-history
moves out of `_path` into actual VM topology. Future-proofs a tree view if
the project ever wants one. Eliminates the `_path` hand-roll.

Cons: significantly larger refactor — PaneVM is 893 LOC and central to
file-manager. The cache invalidation story is non-trivial (when does a
folder node refresh its children? when does it forget them?). The current
hand-rolled refresh semantics ("ask provider on every cd, never cache")
have known and audited timing characteristics; switching to lazy-with-cache
changes the contract. The View bridge must be re-thought: today the pane
subscribes to one VM; under `HierarchicalVM` the subscription target moves
with each `cd`.

**Decision:** **Open question for Phase 0** (see §9 item 7). The user's
review explicitly raised this — the spec must not decide it silently.

The Phase 0 spike should answer:

1. Does the project intend to add a tree-style file view (inline expand)
   in the foreseeable future?
2. If yes → Option B is the right target despite the bigger refactor.
3. If no → Option A is the right target. The `HierarchicalVM` evaluation
   stays in the spec as the "rejected — here's why" record.
4. Either way: the cache invalidation contract must be written down before
   any code change. The current "no cache" contract is a feature, not an
   accident — S3 listings can race a user's external mutation.

**LOC delta estimate:** Option A: −300. Option B: −450 (more deleted from
`_path` history work) but +200–300 of new tree-node VM, so net −150 to
−250. Option A is the lower-LOC outcome.

**Risk note:** PaneVM is the biggest single VM and the most-tested.
Regardless of which option wins, consider splitting the PR into "introduce
scaffold; old `_entries` tuple co-exists behind a flag; both broadcast in
parallel for one release" + "remove old path" if the diff exceeds 600
lines.

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

#### 4.2.9. ~~`NavMenuVM`~~ — see §4.2.0

(Was "NavMenuVM is the reference implementation". Removed at review
round 2 once §4.2.0 documented the incomplete adoption — the existing
`CompositeVM` use lacks the `current` slot wiring. Phase 1 finishes
the adoption per §4.2.0. There is no separate §4.2.9 work.)

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

### 4.3. The FocusCoordinatorVM — promoted into this spec

Was §8.3 "out of scope, parallel follow-up". Promoted into this spec at
review round 3 because §3.2.bis showed it is part of the same MVVM
discipline question, not a separate concern. The toolkit-adoption Phases
1–6 give it the `current`-slot primitive it needs to be small; without
those Phases it would have to inspect five different hand-rolled
selection fields.

**Primitives evaluated:** `CompositeVM` (rejected — the coordinator is not
a homogeneous collection of VMs; it's a discriminated union over a small
fixed set of slot identifiers), `AggregateVM3` (rejected — not a fixed-N-
slots VM either; the slot set depends on which service is active),
`HierarchicalVM` (rejected — not tree-shaped), a small bespoke
`ComponentVM` subclass with a typed slot discriminator (chosen).

**Chosen shape:**

```python
class FocusSlot(StrEnum):
    NAV_MENU = "nav_menu"
    S3_LEFT = "s3.left"
    S3_RIGHT = "s3.right"
    EMR_RUNS = "emr.runs"
    EMR_DETAIL = "emr.detail"
    EMR_LOGS = "emr.logs"
    SETTINGS = "settings"

class FocusCoordinatorVM(ComponentVM):
    """Single source of truth for app-wide focus slot.

    Subscribes to:
    - each candidate VM's ``on_current_changed`` (NavMenuVM, JobRunsVM,
      ApplicationsVM, the PaneVMs)
    - the active ContentHostVM (which page is mounted dictates which
      slots are valid right now)

    Exposes:
    - ``focused_slot: FocusSlot`` — the live discriminator.
    - ``on_focused_slot_changed: Observable[FocusSlot]`` — what views
      subscribe to.

    Drives:
    - the View layer's ``app.set_focus(...)`` calls (replacing the ad-hoc
      ``call_after_refresh(self.focus)`` chains).
    - the per-pane CSS class (replacing the Screen-level
      ``.-nav-active`` hack from PR #98).
    - DualPaneVM's ``focused`` (which becomes a projection of the
      coordinator, not an independent source of truth).
    """
```

**What this replaces:**

- The Screen-level `.-nav-active` CSS class hack from PR #98 — the dim-
  the-file-pane-border decision is a function of `focused_slot ==
  NAV_MENU`, observable by the file-pane widget.
- The `EmrServerlessPage._cycle` `has_focus`-walk slot tracking — the
  coordinator knows which slot is active.
- The `call_after_refresh(self.focus)` race contract NavMenu's commit
  handler has to maintain (the coordinator's `focused_slot` change is
  the authoritative signal; whatever fires last in the Textual queue,
  the View ends up subscribing to the same `focused_slot` event and
  calls `app.set_focus(target)` once).
- Half of `DualPaneVM.switch_focus_command`'s job — flipping
  `DualPaneVM._focused` becomes a side-effect of the coordinator moving
  between `S3_LEFT` and `S3_RIGHT`.

**What it does NOT replace:**

- Textual's `app.focused` (the runtime focus). The coordinator KEEPS that
  in sync via the bridge; it doesn't take over Textual's own focus
  state machine. Textual's focus is still authoritative for "which widget
  gets the keyboard"; the coordinator is authoritative for "which logical
  slot is the user driving" and projects to `app.focused` as a
  consequence.
- Per-VM cursor / selection state. The coordinator observes which
  `CompositeVM`'s `current` slot the user is mutating; it does not own
  that mutation.

**Validation anchors.** The bugs from PRs #98, #99, #101 are the
regression tests. After §4.3 lands, each of those bug scenarios must be
covered by a coordinator-level test:

- "When NavMenu has focus and user arrows down to EMR, the file pane
  border dims; the EMR job-runs slot does NOT auto-grab focus until ENTER
  is pressed; ENTER on a service row moves `focused_slot` to that
  service's default slot."
- "When user is in S3.LEFT and arrow-walks NavMenu, focused_slot
  remains S3.LEFT until ENTER on a different service row."
- "The four EMR-page hub-sender bugs (PR #103) cannot recur because
  cross-VM property echoes can't change `focused_slot` — only
  on_current_changed from the user-driven VM can."

**LOC delta estimate:** +200 (new coordinator + bridge), but
**−250 to −400** across `EmrServerlessPage._cycle`, the Screen-level
`-nav-active` CSS in 10 themes, `NavMenu.on_focus`/`on_blur` ceremony,
several `_maybe_focus_*` defensive checks, and the `call_after_refresh`
race coordination in NavMenu's commit handler. Net **−50 to −200**.

**Risks.**

- Mis-classification of "what's an authoritative source for slot N". The
  bridge code that observes per-VM `current` to compute `focused_slot`
  must encode the priority correctly (Settings overlays EMR overlays
  S3, etc.). Phase 0 spike should sketch this priority table on paper.
- The Textual runtime's focus events fire on a different schedule than
  VMx PropertyChanged. The coordinator must not project to `app.focused`
  on every observable tick or it'll fight Textual's own focus walk on
  first-mount. Use a dispatcher-throttled projection or a flag for
  initial-mount.
- Backward compatibility with the existing manual focus tests. The
  pilot-driven integration tests under `tests/integration/` are
  Textual-focus-aware; they may need re-anchoring.

**Phasing.** §4.3 lands as Phase 7, AFTER Phase 1–3 (NavMenu, JobRuns,
PaneVM all migrated to `CompositeVM`) so the coordinator's bridge
observes uniform `current` slots, not five hand-rolled `_selected_id`
fields. Phases 4–6 (forms, dialogs, palette) can land in any order
relative to Phase 7 — they don't intersect with focus.

## 5. Phased migration plan

### 5.0. Ordering decision — does the focus / selection refactor come BEFORE
       the toolkit adoption, or AFTER?

(Recorded as §5.0 because the user's review explicitly asked. Mistake 7 in
§1.3 was not having addressed it.)

**The two candidates for "first":**

A. **Focus / selection coordinator first.** Build the FocusCoordinatorVM
   (the parallel follow-up §8.3 alludes to), wire pane/menu/EMR widgets
   through it, ship — then start the toolkit adoption against the new
   coordinator.

B. **Toolkit adoption first.** Migrate list VMs to `CompositeVM`,
   migrate forms to `FormVM`, migrate modals to `IDialogService` — then
   build the FocusCoordinatorVM on top of the now-cleaner VM surface.

**Why ordering matters.** The recurring focus bugs (PRs #98 / #99 / #101)
all involve selection state that today is hand-rolled per VM
(`NavMenuVM._selected_id`, `JobRunsVM._selected_id`,
`ApplicationsVM._selected_id`, `DualPaneVM._focused`, plus the Textual
runtime's own focus). A FocusCoordinatorVM built on top of this hand-rolled
mess would need to inspect and mutate each VM's bespoke selection field —
and would need to be rewritten the moment those VMs migrate to
`CompositeVM.current`.

If we do A first, the coordinator's interface is "give me the selection
field of VM X" — five different field names, five getters. If we do B
first, the interface is "give me VM X's `current`" — uniform.

**Decision: B first (toolkit adoption), THEN focus coordinator.**

Why:

1. **Less total work.** A coordinator built on hand-rolled selection then
   re-built on `current` is two coordinator implementations. A coordinator
   built after toolkit adoption is one.
2. **The bug train is already shipped fixed.** PRs #98 / #99 / #101 are
   merged. There is no pending user-visible focus regression to race
   against. We are NOT in "ship visible fixes first" mode.
3. **`CompositeVM.current` IS the selection primitive a coordinator needs.**
   After §4.2.0 finishes NavMenu's adoption and §4.2.1 migrates JobRunsVM
   and §4.2.3 migrates ApplicationsVM, the coordinator's question becomes:
   "of the candidate `CompositeVM`s in the app right now, which one's
   `current` is the user driving?" — and the answer is a single observable
   chain across uniform primitives.
4. **The focus refactor is a smaller spec on top of a finished toolkit
   adoption.** Without the toolkit adoption, the focus refactor has to
   reach into five hand-rolled state fields and risks falling into the
   "fragmented state machine" trap a second time.

**What this means in practice.**

- §8.3 stays as the out-of-scope-for-this-spec marker, with an updated
  cross-reference: the focus coordinator lands as a follow-up SPEC after
  this one ships at least through Phase 5 (modals don't matter for focus;
  Phase 1–3 plus §4.2.0 are the load-bearing pieces).
- The toolkit-adoption PRs (Phase 1–6) MUST NOT introduce new selection
  hand-rolling. Each migration's "Done" criterion includes: the migrated
  VM exposes selection via `current` (or via the framework's idiom for
  that primitive), not via a bespoke `_selected_id` field.
- After Phase 3 completes (PaneVM migrated), open the FocusCoordinatorVM
  spec as a separate `docs/superpowers/specs/YYYY-MM-DD-focus-coordinator-design.md`
  file. Recommended brainstorm question: "given that NavMenuVM,
  JobRunsVM, ApplicationsVM, and PaneVM all now expose `current`, what's
  the smallest VM that observes them and projects an app-wide
  `focused_slot` discriminated union?"

**Counter-argument the next reviewer may raise.** "But the focus bugs are
the actually-visible problem — why are we doing the LOC-shrink work
first?" Answer: the focus bugs are NOT visible — they are shipped fixed.
What this work eliminates is the bug FAMILY (forgot-to-broadcast,
forgot-to-dispose-child), which will keep producing new bugs in the
existing hand-rolled surface for as long as that surface exists. The
ordering puts the structural cure first and the focus-architecture
follow-up at the point where it benefits most.

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

### 5.2. Phase 1 — leaf-VM migrations + finish NavMenu adoption

One PR per VM, in order:

0. **`NavMenuVM` finish-the-adoption** (§4.2.0) — migrate `_selected_id`
   to `CompositeVM.current`. This goes first because it pins the
   selection-via-`current` pattern that every subsequent migration mirrors.
1. `ToastStackVM` → `CompositeVM[ToastVM]`
2. `TransfersVM` → `ServicedObservableCollection[TransferVM]`
3. `ApplicationsVM` → `CompositeVM[ApplicationVM]` (uses `current` for the
   selected application, replacing `_selected_id`)

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

### 5.8. Phase 7 — `FocusCoordinatorVM`

One PR. Lands AFTER at least Phase 1 (NavMenu, Toast, Transfers,
Applications) AND Phase 2 (JobRuns) AND Phase 3 (PaneVM) — the
coordinator's bridge observes their `current` slots.

Touches:

- New `vm/chrome/focus_coordinator_vm.py` (~150 LOC).
- `composition.py` wires the coordinator + per-VM subscriptions.
- `ui/widgets/dual_pane.py` — `_focused` CSS now reads from coordinator.
- `ui/widgets/emr_serverless/page.py` — `_cycle` reads from coordinator
  instead of `has_focus` walks; `_maybe_focus_left` and
  `_maybe_focus_settings` retire.
- `ui/widgets/nav_menu.py` — `on_focus`/`on_blur` retire; the
  `call_after_refresh(self.focus)` race coordination retires.
- `ui/themes/*.tcss` — the Screen-level `.-nav-active` rule deletes
  from all ten themes.
- `tests/integration/test_settings_flow.py` and friends — re-anchored
  to the coordinator's `focused_slot` observable instead of Textual
  `app.focused`.

Cumulative LOC delta: **−1,490 to −1,690 from base**. Phase 7 by itself
is roughly LOC-neutral to slightly negative (new coordinator vs removed
scattered hand-roll), but the bug-class elimination is substantial.

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

### 8.3. ~~`FocusCoordinatorVM`~~ — was here, promoted into the spec at
       review round 3

(Was "out of scope — write a separate follow-up spec".) After review
round 3 surfaced that the View-side focus / selection state is part of
the SAME MVVM-principle refactor as the toolkit adoption (see §1.3
mistake 8 and §3.2.bis), the FocusCoordinatorVM is now §4.3 of this
spec and lands as Phase 7 (§5.8).

What truly stays out of scope: Textual's own focus state machine — the
runtime's `app.focused` remains Textual's concern. The coordinator
keeps Textual in sync with the VM-side `focused_slot`; it does not
replace Textual's focus.

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
7. **(Added at review round 2)** Does the project intend to support an
   inline-expand tree view of S3/local filesystem in the foreseeable
   future? — drives the PaneVM `CompositeVM` vs `HierarchicalVM` choice
   (§4.2.2). If unsure, the Phase 0 spike should prototype both options
   for one provider (probably LocalFS, simplest) and present the diff
   side-by-side so the maintainer can decide on real code.

Each answered question becomes an amendment to this spec (preferred) or
a note in the corresponding plan task (acceptable if the answer is
narrow and per-task).

## 10. Definition of done

The migration is complete when:

1. All nine per-VM targets in §4.2 (including §4.2.0 NavMenu) have either
   landed or are explicitly documented as "kept hand-rolled" with
   rationale.
2. The `FocusCoordinatorVM` (§4.3) has landed and the View-side
   focus / cursor / slot-tracking state inventoried in §3.2.bis entries
   1, 3, 5, 8, 9 has been migrated out of the View.
3. Total VM-layer LOC is below 6,200 (down from 7,339 — slightly higher
   than the round-2 estimate because Phase 7 ADDS a coordinator).
4. The number of hand-emitted `PropertyChangedMessage.create(self, ...)`
   calls in `src/aws_tui/vm/` is below 30 (down from 68).
5. Hand-rolled `_cursor_index` fields across `src/aws_tui/ui/widgets/` are
   reduced to ≤1 (today: at least 3 — NavMenu, JobRunsPane,
   EmrServerlessPage slot tracking).
6. Hand-rolled `_selected_id` fields across `src/aws_tui/vm/` are zero
   (today: 3 — NavMenu, JobRuns, Applications).
7. The Screen-level `.-nav-active` CSS rule has been removed from all 10
   themes.
8. The `tests/unit/vm/` suite is still green and includes shape tests for
   every migrated VM AND focus-coordinator scenario tests anchored to
   the bug train (PR #98 / #99 / #101).
9. `check-layers.sh` still passes.
10. The CHANGELOG `[Unreleased]` records "VM-layer refactor — adopt VMx
    toolkit primitives + consolidate focus/selection state; user-visible
    behavior unchanged".
11. This spec is updated with the actual per-VM disposition (migrated /
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

---

## Appendix A — Workflow & project conventions

The next worker should know these without having to discover them in a
PR review.

### A.1. Python environment

- Project floor: Python 3.11 (`pyproject.toml::requires-python`).
- CI matrix: `[macos-14, ubuntu-24.04, windows-latest] × [3.11, 3.12, 3.13]`
  for unit + in-process integration tests.
- Local `.venv` is created by `uv sync`. May resolve to pyenv's 3.11.0
  if installed — that release has a `typing.py:1253` regression fixed
  in 3.11.1+. One test (`test_aggregate_vm3_lazy_factories`) fails under
  3.11.0 due to this. **It's an env issue, not a repo issue.** Bump to
  3.11.1 or use uv's installed 3.12.

### A.2. Test invocation

```bash
# Full suite — the two deselects are known env/flake issues, not failures:
uv run pytest tests/ -q \
  --deselect tests/unit/vm/file_manager/test_dual_pane_vm.py::test_dual_copy_across_cancel_event_interrupts_in_flight_copy \
  --deselect tests/unit/vm/test_vmx_smoke.py::test_aggregate_vm3_lazy_factories

# Quick tier-targeted runs during development:
uv run pytest tests/unit/vm/                # VM unit tests (~6,500 LOC)
uv run pytest tests/unit/ui/                # UI widget unit tests
uv run pytest tests/snapshot/               # Textual SVG goldens (10 themes)
uv run pytest tests/integration/            # MinIO testcontainer + pilot-driven flows
```

Expect `1439 passed, 11 deselected, 274 snapshots` on a green run.

### A.3. Lint, type, layers

- **Lint:** `uv run ruff check src/ tests/` — strict; all of src/ + tests/.
- **Format:** `uv run ruff format src/ tests/`.
- **Type check:** `uv run mypy src/` — STRICT on src/ (106 files); tests
  are not gated. `Success: no issues found in 106 source files` is the
  green signal.
- **Layer rules:** `scripts/check-layers.sh` — enforces the import
  boundaries between domain/, vm/, ui/, infra/, services/, demo/. After
  the round-3 maintenance loop, this also runs as a pre-commit hook
  (`.pre-commit-config.yaml`). `layer rules clean` is the green signal.
- **Pre-commit:** `uv run pre-commit run --all-files` runs every hook
  CI runs, in one command.

### A.4. PR conventions

- **Branch naming:** `<kind>/<topic>` — e.g., `fix/runs-picker-flicker`,
  `feat/nav-enter-shifts-focus`, `docs/vmx-toolkit-adoption-spec`,
  `chore/overnight-maintenance-20260628`. Snake_case is fine within the
  topic.
- **PR creation:** `gh pr create --title "<conventional commit>" --body "<body>"`.
- **Merge strategy:** `gh pr merge <num> --squash --delete-branch --admin`
  for every PR. The repo has 0 required reviewers + no required status
  checks set yet, so `--admin` is what flushes through; the maintainer
  manually reads the diff first.
- **Commit messages:** conventional commits (`fix(area): ...`,
  `feat(area): ...`, `docs(area): ...`, `chore: ...`,
  `refactor(area): ...`, `test(area): ...`, `ci(area): ...`).
- **Heredoc commits:** every multi-line commit message uses
  `git commit -m "$(cat <<'EOF' ... EOF)"` for safe formatting.

### A.5. Repo state at spec time

- Latest tag: `v0.7.0`.
- `version.py` + `pyproject.toml`: `0.8.0` (PyPI publish blocked on
  `pypi/support#11264` — PEP 541 name-similarity appeal).
- `main` HEAD when this spec was written: `5ee16b9`.
- Open Dependabot PRs: #46 (checkout v7), #64 (pre-commit-hooks v6),
  #65 (ruff-pre-commit v0.15.18) — independent of this spec.

---

## Appendix B — Per-VM cross-reference table

The "I'm migrating X" → "open these files" index. Each row maps to
exactly one §4 entry.

| VM / target | Source file (current) | LOC | Tests | Spec § | Prior PRs that touched it | Target primitive |
|---|---|---|---|---|---|---|
| **NavMenuVM** | `vm/nav_menu_vm.py` | ~290 | `tests/unit/vm/test_nav_menu_vm.py` | §4.2.0 | #94 (rewrite), #101 (ENTER focus), #102 (ribbon), #105 (selected bg) | finish `CompositeVM.current` adoption |
| **JobRunsVM** | `vm/emr_serverless/job_runs_vm.py` | ~280 | `tests/unit/vm/emr_serverless/test_job_runs_vm.py` | §4.2.1 | #91 (sorted-apps), #100 (no re-mount), #103 (sender filter) | `CompositeVM[JobRunVM]` + `PagedComposition` |
| **PaneVM** | `vm/file_manager/pane_vm.py` | 893 | `tests/unit/vm/file_manager/test_pane_vm.py` | §4.2.2 | the entire M-series | **OPEN — Option A or B** (see §9 question 7) |
| **ApplicationsVM** | `vm/emr_serverless/applications_vm.py` | ~200 | `tests/unit/vm/emr_serverless/test_applications_vm.py` | §4.2.3 | #88, #90, #91 | `CompositeVM[ApplicationVM]` |
| **TransfersVM** | `vm/file_manager/transfers_vm.py` | ~250 | `tests/unit/vm/file_manager/test_transfers_vm.py` | §4.2.4 | (the cancel-race flake lives here) | `ServicedObservableCollection[TransferVM]` |
| **ToastStackVM** | `vm/chrome/toast_stack_vm.py` | ~120 | `tests/unit/vm/chrome/test_toast.py` (timing) | §4.2.5 | none recent | `CompositeVM[ToastVM]` |
| **S3ConnectionsVM** | `vm/settings/s3_connections_vm.py` | ~180 | `tests/integration/test_settings_flow.py` | §4.2.6 | the Settings-nav-page train | `FormVM<S3Connection>` |
| **ConfirmationVM** | `vm/chrome/confirm_vm.py` | ~130 | `tests/integration/test_confirm_modal_keyboard.py` | §4.2.7 | #47 (modal+toast polish) | `IDialogService.show(...)` |
| **ResumeVM** | `vm/chrome/resume_vm.py` | ~110 | `tests/unit/vm/chrome/test_resume_modal.py` | §4.2.7 | #47 | `IDialogService.show(...)` |
| **CrashVM** | `vm/chrome/crash_vm.py` | ~100 | `tests/unit/vm/chrome/test_crash_vm.py` | §4.2.7 | none recent | `IDialogService.show(...)` |
| **FirstRunVM** | `vm/chrome/first_run_vm.py` | ~120 | `tests/integration/test_first_run_modal.py` | §4.2.7 | #54 / #55 / #56 (Settings rework) | `IDialogService.show(...)` |
| **CommandPaletteVM** | `vm/chrome/command_palette_vm.py` | ~240 | `tests/unit/vm/chrome/test_command_palette.py` | §4.2.8 | none recent | `CompositeVM[PaletteEntryVM]` (revert if it doesn't fit) |
| **ThemePickerVM** | `vm/chrome/theme_picker_vm.py` | ~190 | `tests/unit/vm/chrome/test_theme_picker.py` + `tests/snapshot/test_theme_picker.py` | §4.2.8 | none recent | `CompositeVM[ThemeRowVM]` (revert if it doesn't fit) |
| **FocusCoordinatorVM** | **NEW** `vm/chrome/focus_coordinator_vm.py` | (target ~150) | NEW shape tests + re-anchored integration tests | §4.3, §5.8 | indirectly: #98, #99, #101, #103 | bespoke `ComponentVM` with `FocusSlot` discriminator |

**VMs explicitly kept hand-rolled** (do not migrate; see §4.2.10):
`RootVM`, `ContentHostVM`, `ChromeVM`, `EmrServerlessPageVM` (page_vm.py
— composite of three children in a fixed-shape `AggregateVM3`-style
pattern), `JobRunDetailVM`, `JobRunLogsVM`, `JobRunCloneVM`.

---

## Appendix C — VMx primitive cheat sheet (for quick lookup during brainstorm)

| Primitive | File | When to reach for it |
|---|---|---|
| `ComponentVM` / `ComponentVMOf[M]` | `vmx/components/` | Single VM; the leaf you build composites from. |
| `CompositeVM[VM]` / `CompositeVMOf[M, VM]` | `vmx/composites/composite_vm.py` | **Homogeneous collection of VMs** with cursor (`current`), `on_collection_changed`, lifecycle cascade. Default choice for list-shaped VMs. |
| `ObservableList[T]` | `vmx/collections/observable_list.py` | Raw item list (no per-item VM). Granular `on_item_added`/`removed`/`replaced`/`reset`. |
| `ObservableDictionary[K, V]` | `vmx/collections/observable_dictionary.py` | Key-indexed observable. |
| `PagedComposition` | `vmx/collections/paged_composition.py` | Pagination wrapper over `ObservableList`. |
| `Batch` / `BatchUpdateHandle` | `vmx/collections/batch.py` | Collapse N mutations into one collection_changed emission. |
| `FormVM<TM>` / `FormVMBuilder` | `vmx/forms/form_vm.py` | Typed model form. `is_dirty`, per-field validators, strict mode. |
| `IDialogService` / `DialogService` / `NullDialogService` | `vmx/dialogs/` | Modal push/dismiss/result lifecycle. Inject `NullDialogService` in tests. |
| `HierarchicalVM[TModel, TVM]` | `vmx/hierarchical/hierarchical_vm.py` | Recursive tree. Lazy children by default; eager opt-in. `IExpandable` for collapse. |
| `GroupVM` | `vmx/groups/` | Grouped composition. Likely not applicable here. |
| `ServicedObservableCollection` | `vmx/collections/serviced_observable_collection.py` | Observable collection that auto-disposes items on removal via the service registry. |
| `ForwardingComponentVM` / `ForwardingCompositeVM` | `vmx/forwarding/` | Delegate-to-inner with intact lifecycle. Replaces hand-rolled `_inner.construct()` trampolines. |
| `AggregateVM1`–`AggregateVM6` / `AggregateVMBuilderN` | `vmx/aggregates/` | Typed N-tuple struct of NAMED children. Fixed shape. Page composites use this — not `CompositeVM`. |
| `RelayCommand` / `RelayCommandOf[T]` | `vmx/commands/` | Command pattern with predicates + `can_execute_changed`. Already used universally. |
| `MessageHub` / `Message` / `PropertyChangedMessage` | `vmx/messages/` and `vmx/services/message_hub.py` | Pub/sub plumbing. Already used universally. |
| `RxDispatcher` / `Dispatcher` | `vmx/services/dispatcher.py` | Async/dispatch scheduling. `RxDispatcher.immediate()` is the project default; `NULL_DISPATCHER` for tests. |
| `ConstructionStatus` | `vmx/lifecycle/status.py` | The `construct → destruct → dispose` state machine. The contract every `_ComponentVMBase` participates in. |
| `walk` / `walk_expanded` / `find` | `vmx/tree/walk.py` | Tree-walk utilities. `walk_expanded` respects `IExpandable.is_expanded`. |

**Distinction trap (mistake 1):** `CompositeVM` (homogeneous, dynamic
cardinality, has `current` and `on_collection_changed`) is NOT the same
as `AggregateVMN` (heterogeneous, fixed-N typed slots). The first draft
of this spec confused them. If a primitive is "N typed children with
named accessors" → `AggregateVMN`. If it's "many children of one type" →
`CompositeVM` or `ObservableList`.
