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

> **Update — amendment after the brainstorm session (2026-06-28).** A
> fourth round of correction surfaced during the §9 brainstorm:
>
> - **Mistake 9: spec Appendix C entry for `ServicedObservableCollection`
>   was paraphrased from outside the source without verifying against
>   the primitive.** The Appendix C cell read "Observable collection
>   that auto-disposes items on removal via the service registry."
>   Reading `vmx/collections/serviced_observable_collection.py:1–137`
>   shows: the class is a `MutableSequence[T]` with optional hub
>   publication; `__delitem__` (lines 86–93) does
>   `del self._items[index]` then emits `for_remove(...)`; `remove`
>   (lines 121–125) is the same shape; there is no `dispose()` call
>   on removed items anywhere; the constructor takes only a hub.
>   The "Serviced" in the name refers to **message-hub publication**,
>   not service-managed lifecycle. Same shape as mistakes 1, 4, 6:
>   paraphrase substituted for source review.
>
> **Methodology lesson reinforced.** Appendix C is a navigation aid, not
> a contract document. Any per-VM PR that depends on a primitive's
> behaviour must re-verify against the source per §0's discipline
> rules, regardless of what Appendix C says. The Appendix C entry has
> been corrected; §4.2.4 target description and LOC estimate have been
> revised; the resolution is recorded in §9.bis.7.

> **Update — second amendment after the brainstorm session (2026-06-28).**
> A reviewer push-back surfaced a fifth round mistake:
>
> - **Mistake 10: §9's open-questions set was scoped only to VMx-
>   primitive-fit; the MVVM-half open questions implicit in §3.2.bis /
>   §4.3 / §5.8 were not surfaced.** Specifically: the slot-priority
>   table §4.3 risk note defers to "Phase 0 should sketch on paper";
>   the projection-timing strategy choice (throttled vs flagged); the
>   integration-test re-anchoring scope; and the cursor-vs-selection
>   split inside View widgets §3.2.bis line 622 names but does not
>   enumerate. None of these appeared in §9. The brainstorm initially
>   resolved only the seven VMx-fit questions §9 posed and treated the
>   MVVM half as "designed, Phase-0-deferred".
>
> **Methodology lesson.** Specs with two halves (here: VMx-toolkit
> adoption + MVVM-discipline migration) need open-questions sets that
> explicitly span both halves. A single §9 question list reads as
> "the only open items"; if half the spec's scope is missing from §9,
> the brainstorm will silently scope down to the question set's
> implicit framing. The fix this time was a post-hoc walk of today's
> 2026-06-28 bug train (PRs #98–#105) against the §3.2.bis 10-state
> inventory, producing per-Phase regression anchors with concrete PR
> references. See §9.bis.9 for the resulting mapping table.

> **Update — third amendment after the brainstorm session (2026-06-29).**
> A sixth round of correction landed one day after the round-1 and
> round-2 commits, when the maintainer delivered an explicit
> directive that supersedes the framing the prior rounds operated on:
>
> - **Mistake 11: brainstorm rounds 1 and 2 operated on an implicit
>   "VMx fits → adopt; doesn't fit → hand-roll" dichotomy.** When a
>   VMx primitive didn't fit cleanly (`IDialogService` for
>   ResumeVM/FirstRunVM in §9.bis.6; `PagedComposition` for
>   JobRunsVM's load-more UX in §9.bis.2; no `FilteredCompositeVM`
>   for PaneVM/CommandPaletteVM in §9.bis.3; no declarative
>   validators on `FormVM` in §9.bis.5), the resolutions defaulted
>   to "stay hand-rolled" or "the derived view stays as a `@property`
>   on the wrapping VM" — both implicitly tolerating logic-in-View
>   that the directive explicitly forbids. The directive: ALL view
>   logic out, no exceptions; VMx primitives are COMPOSED inside
>   custom aws-tui VMs when no direct fit exists; the upstream
>   report becomes a post-verification "here's what to ship
>   natively next" list, not a parallel justification for
>   hand-rolling.
>
> **Methodology lesson.** Treat VMx primitives as building blocks
> for composition, not as choose-or-skip artifacts. When the closest
> fit needs added behaviour, the work is composition + facade — a
> custom aws-tui VM wrapping the VMx primitive without exposing it.
> See §9.bis.11 for the re-framing of §9.bis.2 / .3 / .5 / .6 and
> §9.bis.12 for the canonical Mistake 11 record.


The next worker picking up this spec should know that the analysis behind it
went through three rounds of correction, then a fourth, fifth, and sixth
round during the §9 brainstorm. Each correction tightens the
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

#### 4.2.1. `JobRunsVM` → `CompositeVM[JobRunVM]` (PagedComposition dropped)

> **RESOLVED — see §9.bis.2.** Brainstorm verified that `PagedComposition`
> is index-based, cannot observe a `CompositeVM` source, and does not
> match the "Load more" UX. The target shape below is preserved EXCEPT
> the `PagedComposition` wrapping is removed; pagination state stays as
> a VM-level `next_token` field + `load_more` command. Read §9.bis.2
> before acting on the text below.

**Primitives evaluated:** `CompositeVM[JobRunVM]`,
`PagedComposition[JobRunVM]` (**rejected — see §9.bis.2**),
`HierarchicalVM` (rejected — runs are flat, not a tree).

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

> **RESOLVED — see §9.bis.1: Option A (`CompositeVM[EntryVM]`).** The
> "Open question for Phase 0" verdict in §4.2.2 below is now closed.
> The `HierarchicalVM` analysis is retained in this section as the
> rejected-option record so a future inline-expand tree-view feature
> can re-open the decision on its own merits.

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

> **CORRECTED — see §9.bis.7 and §9.bis.8 (Mistake 9).** The "auto-
> disposes" claim below was paraphrased from outside the source and is
> wrong. `ServicedObservableCollection` does NOT call `dispose()` on
> removed items; it publishes `CollectionChangedMessage` on mutation
> and nothing else. The TransfersVM `finally: vm.dispose()` block in
> `_run_one_transfer` stays.

**Today:** ~250 LOC. Hand-rolls the child transfer list and explicit
dispose in `_run_one_transfer`'s finally block.

**Target:**
- `ServicedObservableCollection[TransferVM]` publishes
  `CollectionChangedMessage` on mutation. **Caller still owns disposal**
  (the existing `finally: vm.dispose()` block stays).
- Pre-registration logic (current ~lines 331–383) becomes a `Batch` block
  over the collection so the View redraws once per drop-batch instead of
  per-entry.

**LOC delta estimate:** **−60** (revised down from −100 once auto-
dispose savings dissolved; remaining savings come from the `Batch`
pattern).

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

#### 4.2.7. Modal VMs → custom aws-tui VMs composing VMx (all four)

> **SUPERSEDED — see §9.bis.11 (round 3, 2026-06-29).** Under the
> maintainer's "compose, don't reject" directive, no VM stays
> hand-rolled in the View. All four modal VMs become custom aws-tui
> VMs composing the closest VMx primitive(s): `ConfirmationVM` /
> `CrashVM` compose `IDialogService.confirm` / `notify` respectively
> (minimal wrappers); `ResumeVM` composes a bespoke `ComponentVM`
> subclass + three-way result projection + domain-side persisted-bit
> hook; `FirstRunVM` composes N `FormVM`s + a "current step" state
> machine on top. Net LOC delta re-estimates toward `−180 to −210`.
> The "RESOLVED — see §9.bis.6" pointer below is preserved as the
> round-1 audit trail.

> **RESOLVED — see §9.bis.6 (round 1, 2026-06-28).** `IDialogService` is a closed-set contract
> (`confirm` / `notify` / `pick_file_*`). Only `ConfirmationVM`
> (→ `confirm`) and `CrashVM` (→ `notify(severity=ERROR)`) have a clean
> fit. `ResumeVM` (three-way decision + persisted "show next boot"
> state) and `FirstRunVM` (multi-step welcome flow) stay hand-rolled
> with documented "no fit" rationale.

**Today:** Each of `ConfirmationVM`, `ResumeVM`, `CrashVM`,
`FirstRunVM` hand-rolls push/dismiss/result-future plumbing.

**Target (revised):**
- `ConfirmationVM` → `IDialogService.confirm(message, title) -> bool`. Adopt.
- `CrashVM` → `IDialogService.notify(message, title, severity=ERROR)`. Adopt.
- `ResumeVM` → stays hand-rolled. `confirm`'s `bool` return cannot
  express three-way `Resume / Discard / KeepForLater`; the
  "KeepForLater → show next boot" persistence is domain state, not
  dialog state. See §9.bis.6.
- `FirstRunVM` → stays hand-rolled. Multi-step form-shaped flow
  doesn't fit `confirm`/`notify`. See §9.bis.6.

**LOC delta estimate:** **−60 × 2 = −120** (revised down from −240).

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

**Per-PR breakdown — see §9.bis.9.** The brainstorm round 2 walked the
2026-06-28 bug train (PRs #98 / #99 / #100 / #101 / #103 / #105) and
mapped each MVVM-violation-shaped fix to (a) the §3.2.bis row whose
state-fragmentation drove it, (b) the §4.2 or §4.3 work item whose
landing eliminates the bug class, and (c) a concrete "the bug cannot
recur because mechanism X no longer exists in the code" acceptance
check. Use §9.bis.9 as the per-Phase acceptance-criterion source when
writing the Phase PRs.

## 7. Risks

### 7.1. `PagedComposition` may not fit token-pagination — CLOSED

> **CLOSED — see §9.bis.2.** Brainstorm verified from
> `vmx/collections/paged_composition.py` source that `PagedComposition`
> IS strictly index-based AND cannot observe a `CompositeVM` source.
> Adopted resolution: drop `PagedComposition` from §4.2.1 entirely;
> `CompositeVM[JobRunVM]` + VM-level `next_token` + `load_more` command.
> The text below is preserved as the audit trail of how this risk was
> originally framed.

EMR uses `nextToken`-style forward-only pagination. `PagedComposition` may
have been designed assuming index-based pagination (page N of M). If
that's the case, the migration of `JobRunsVM` would need:

- An adapter layer that maps `nextToken` → page-index; or
- Keeping the pagination logic outside `CompositeVM` and using
  `CompositeVM` only for the per-page item list.

Investigated in Phase 0 spike.

### 7.2. Filter coupling — CLOSED

> **CLOSED — see §9.bis.3.** Adopted resolution: Option C (derived
> view stays at the VM as a @property; CompositeVM holds unfiltered
> list + cursor; cursor snaps to first filter-visible entry on filter
> change). Both PaneVM and CommandPaletteVM follow Option C. The text
> below is preserved as the audit trail of the two options that were
> considered first.

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

## 9.bis. Resolutions from the brainstorm session — 2026-06-28

This section closes §9's seven open questions, records the spec
amendments each resolution triggers, surfaces the MVVM-half open
questions §9 itself failed to pose (round 2), and points to the
upstream feedback report that captures the gaps where a VMx primitive
almost fit but didn't quite. Numbered `.bis` to keep §10/§11
downstream section numbers stable (same convention as §3.2.bis).

**Structure:**
- §9.bis.1–7 — round 1: one per §9 question (Q7, Q1, Q2, Q3, Q4, Q5, Q6).
- §9.bis.8 — Mistake 9 (the `ServicedObservableCollection` paraphrase
  bug surfaced while resolving Q6).
- §9.bis.9 — round 2: MVVM-half re-classification of today's bug
  train against the §3.2.bis 10-state inventory, producing per-Phase
  regression anchors with concrete PR refs.
- §9.bis.10 — Mistake 10 (the round-2 amendment trigger: §9's
  question set was too narrowly scoped to VMx-fit).
- §9.bis.11 — round 3: maintainer's "compose, don't reject"
  directive applied — re-frames §9.bis.2 / .3 / .5 / .6 around the
  composition pattern (custom aws-tui VMs wrapping VMx primitives).
- §9.bis.12 — Mistake 11 (the round-3 amendment trigger: rounds 1
  and 2 implicitly tolerated "stay hand-rolled" outcomes the
  directive forbids).
- §9.bis.13 — pointer to the upstream feedback artifact.

Each resolution lists, per §0's discipline rules: which VMx
primitive(s) were evaluated, what was inspected (file + line ranges),
and why the resolution fits.

### 9.bis.1. Q7 / §4.2.2 — PaneVM: choose Option A (`CompositeVM[EntryVM]`)

**Decision:** Option A.

**Inspected:** `vmx/composites/composite_vm.py` (mutation surface,
`current` slot, `on_collection_changed`), `vmx/hierarchical/hierarchical_vm.py`
(via Appendix C cheat sheet entry only — not deep-read because B was
not chosen), `vm/file_manager/pane_vm.py:210–561` (today's shape:
unfiltered entries, filter projection, cursor over filtered list),
`CHANGELOG.md` "Deferred / v0.9 roadmap" (lines 1533–1565 — no
inline-expand tree view planned).

**Rationale:** The product's foreseeable tree-shaped UI feature is a
clickable **breadcrumb / "exploded path"** at the top of the pane.
That feature needs `pathlib.PurePosixPath.parts` + a `cd_to(path)`
command (~20 LOC of derived property on PaneVM) — it does NOT need to
render siblings/descendants at multiple levels, which is what
`HierarchicalVM` was built for. The breadcrumb is satisfied under A.

`HierarchicalVM` would impose its refactor cost (893 LOC pane + new
`FileSystemNode`/`FileSystemNodeVM` shapes + a moving View-subscription
target + a redesign of the today-audited "no cache, ask provider on
every cd" contract) for a benefit only collected if a multi-level
inline-expand tree view ships — which is not on the v0.9 roadmap.

**Spec amendments:** §4.2.2 decision recorded; the §4.2.2
`HierarchicalVM` analysis is retained as the explicit "rejected — here's
why" record so a future inline-expand tree-view feature has a paved
path to re-open it on its own merits. No other sections affected.

**Upstream ask:** Item 7 in the vNext feedback report — the gaps that
made `HierarchicalVM` unattractive (cache-invalidation contract not
documented; no `invalidate(node)` / TTL / refresh-on-focus surface) are
recorded so they can be closed before a tree-view consumer arrives.

---

### 9.bis.2. Q1 / §4.2.1 — JobRunsVM: drop `PagedComposition`

**Decision:** Replace §4.2.1's `CompositeVM[JobRunVM]` + `PagedComposition`
target with **`CompositeVM[JobRunVM]` alone**, plus a VM-level
`next_token: str | None` field and a `load_more: RelayCommand` (shape
unchanged from today).

**Inspected:** `vmx/collections/paged_composition.py:107–115` (page
math), `:165–171` (slice access), `:204–210` (`_source_count` calls
`len(src)`), `:232–240` (subscribes only to `on_item_added`/`on_item_removed`/
`on_item_replaced`/`on_reset`); `vmx/composites/composite_vm.py:103`
(`on_collection_changed` — composite does not expose the four
ObservableList hooks PagedComposition wants); `vm/emr_serverless/job_runs_vm.py:51,96,105,
137–190` (today's nextToken accumulating load); `ui/widgets/emr_serverless/page.py:451`
(`LoadMoreRequested` event — UX is **infinite-scroll "Load more"**, not
"page N of M").

**Rationale:** Three independent misfits, any one of them sufficient:

1. `PagedComposition` is **strictly index-based** — requires `len(source)`
   and random access. Forward-only nextToken pagination has neither.
2. `PagedComposition` cannot auto-observe a `CompositeVM`'s mutation
   stream because the two primitives speak different observable shapes;
   composition silently emits stale slices.
3. The aws-tui UX is "Load more", not "page-navigation". `move_to_next_page`
   would conflate "view a different in-memory slice" with "make a
   network call" — semantically wrong.

`CompositeVM[JobRunVM]` alone provides the cursor (`current` retires
`_selected_id`), lifecycle cascade, and `on_collection_changed`. The
~250 LOC saving estimate for §4.2.1 holds because pagination math
was not where the savings came from — `_selected_id` retirement +
PropertyChangedMessage collapse + lifecycle cascade are.

**Spec amendments:** §4.2.1 target ("wrapped in `PagedComposition`") →
"unwrapped; pagination state stays as a VM field with a `load_more`
command". §7.1 risk closed as "misfit confirmed; not used".

**Upstream asks:** Items 1 and 2 in the vNext feedback report —
`TokenPagedComposition[VM]` would let aws-tui (and any AWS-API
consumer) adopt a framework primitive for nextToken pagination instead
of hand-rolling.

---

### 9.bis.3. Q2 / §7.2 — Filter coupling: Option C (derived view as a VM @property)

**Decision:** For both `PaneVM` and `CommandPaletteVM`:
`CompositeVM[EntryVM]` holds the unfiltered list with cursor +
lifecycle; `filter_text` and `filtered_entries` stay as VM
fields/@property; on filter change, the VM snaps `_entries.current`
to the first filter-visible entry. The View binds to
`filtered_entries` for rendering and to `current` for the selected row.

**Inspected:** `vm/file_manager/pane_vm.py:210–212` (unfiltered
`_entries` + filtered-index tuple + filter text), `:347–348`
(`filtered_entries` property), `:549–561` (cursor maps onto
`filtered_entries` index); `vm/chrome/command_palette_vm.py:128–129`
(same shape — `_filter_text` + `_filtered` tuple), `:170–176`
(filter-text setter recomputes filtered), scoring at `:57–93`.

**Rationale:** Both VMs already implement Option C's shape today. The
CompositeVM adoption slots into the `_entries` backbone; the
derived-filter view stays exactly where it is today, but the cursor
moves from "View-tracked index over filtered" → "CompositeVM's `current`
slot, kept filter-visible by the VM". Preserves NiceGUI-portability of
the VM layer. Avoids inventing a derived-CompositeVM primitive VMx
doesn't ship.

Option A (two-layer derived composite) was rejected: VMx doesn't ship
the primitive, ~50–80 LOC hand-roll wrapper would have been required,
and cursor-mapping between outer and inner adds duplicated state.
Option B (filter at the View) was rejected: pushes filter logic into
widget code, breaks the "VM layer reusable by a second View" property
the spec's §1.2 q4 asked about.

**Spec amendments:** §7.2 risk closed with "Option C adopted for both
VMs". §4.2.2 (already updated by 12.1) reads consistently. §4.2.8
target gains a note: "filter+score stays as a VM @property; cursor
snaps to first filter-visible entry on filter-text change".

**Upstream ask:** Item 3 in the vNext feedback report —
`FilteredCompositeVM[VM]` (plus a `ScoredFilteredCompositeVM` variant
for the palette's fuzzy-match scoring) would let aws-tui delete the
@property and the cursor-mapping logic in both VMs.

---

### 9.bis.4. Q3 — `auto_construct_on_add=True` composes with `ToastVM`

**Decision:** `auto_construct_on_add=True` fits as-is. No adapter.

**Inspected:** `vmx/composites/composite_vm.py:298–305`
(`_maybe_auto_construct` calls zero-arg `child.construct()`);
`vm/chrome/toast_vm.py:136` (`def construct(self) -> None` — zero-arg,
matches).

**Spec amendments:** §4.2.5 target is final. Phase 0 confirms with a
one-line shape test asserting `toast_stack.append(toast)` leaves
`toast.is_constructed is True`.

---

### 9.bis.5. Q4 — `FormVM` cross-field validators: fits via custom predicate

**Decision:** Wrap S3ConnectionsVM around `FormVM<S3Connection>`. The
cross-field invariant ("`endpoint_url` set IFF `force_path_style`") is
expressed as: (a) `_is_valid_invariants() -> bool` method on the
wrapping VM, called from a custom `approve_command.predicate` so the
button auto-disables when violated; (b) the same condition raised from
the `persister` callback as belt-and-suspenders.

**Inspected:** `vmx/forms/form_vm.py:48–86` — `FormVM` exposes
`strict` (gates approve on `is_dirty`), `approve_command.predicate`
(custom callable), and `persister: (model) -> Awaitable[None]` that
may raise. **No declarative validator API** (`field_validator`,
`model_validator`, reactive `errors` map).

**Rationale:** Works, but is the recurring boilerplate-vs-primitive
case. ~5 LOC of predicate wiring per cross-field rule per form VM
that consumes `FormVM`.

**Spec amendments:** §4.2.6 target gains a note about the predicate
pattern; LOC delta `−200` stays approximately correct.

**Upstream ask:** Item 4 in the vNext feedback report — declarative
`field_validator(...)` / `model_validator(...)` + a reactive
`errors: dict[str, str]` map + auto-gated `approve_command` would let
the wrapping VM delete the `_is_valid_invariants` boilerplate.

---

### 9.bis.6. Q5 / §4.2.7 — `IDialogService` for the four modal VMs: 2 adopt, 2 stay

**Decision:**

- `ConfirmationVM` → `IDialogService.confirm(message, title) -> bool`. **Adopt.**
- `CrashVM` → `IDialogService.notify(message, title, severity=ERROR)`. **Adopt.**
- `ResumeVM` → **stays hand-rolled.** No fit: three-way decision
  (`Resume / Discard / KeepForLater`) cannot be expressed by `confirm`'s
  `bool` return, and "KeepForLater → show again next boot" is domain
  state, not dialog state.
- `FirstRunVM` → **stays hand-rolled.** No fit: multi-step welcome flow
  with form fields cannot be expressed via `confirm`/`notify`.

**Inspected:** `vmx/dialogs/dialog_service.py:38–96` — the abstract
surface is closed-set: `pick_file_to_open`, `pick_file_to_save`,
`confirm(...) -> bool`, `notify(severity)`. There is **no generic
`present(modal_vm) -> result` method**. `vm/chrome/resume_vm.py:34`
confirms ResumeVM's three-way `KEEP_FOR_LATER` decision exists today.

**Spec amendments:** §4.2.7's "all four modal VMs → `IDialogService`"
framing is too aspirational. §4.2.7 target now reads "two of four
modals → `IDialogService` (Confirmation, Crash); two stay hand-rolled
with a documented 'no fit' rationale (Resume's three-way decision +
persisted state; FirstRun's multi-step shape)". §4.2.7 LOC delta
estimate revised from `−60 × 4 = −240` to **`−60 × 2 = −120`**. §10
definition-of-done item 1 ("all nine per-VM targets ... or documented
as kept hand-rolled with rationale") now explicitly covers ResumeVM /
FirstRunVM as "kept hand-rolled, no IDialogService fit".

**Upstream ask:** Item 5 in the vNext feedback report — a generic
`present(modal_vm: ModalVM[T]) -> Awaitable[T]` + `ModalVM[T]` protocol
on `IDialogService`, plus optional `ChoiceVM[Enum]` and
`MultiStepFormVM[TM]` companions, would let ResumeVM and FirstRunVM
re-platform too.

---

### 9.bis.7. Q6 — `ServicedObservableCollection`: premise was wrong; mistake 9 recorded

**Decision:** No new primitive needed. `ServicedObservableCollection`
is **already** the non-owning observable — its name suggested
ownership semantics it doesn't implement. The TransfersVM `finally:
vm.dispose()` block stays.

**Inspected:** `vmx/collections/serviced_observable_collection.py:1–137`
— class docstring says "observable list that **optionally publishes**
CollectionChangedMessage events to an MessageHub-compatible hub". The
constructor takes only `hub: object = None`. `__delitem__` (lines
86–93) does `del self._items[index]` then emits a `for_remove(...)`
message — **no `dispose()` call.** `remove` (lines 121–125) is the
same shape. The "Serviced" in the name refers to **message-hub
publication**, not service-managed lifecycle.

**Spec amendments:** §4.2.4 target description changes from "auto-
disposes a `TransferVM` when removed" to "publishes
`CollectionChangedMessage` on mutation; caller still owns disposal —
TransfersVM's `finally: vm.dispose()` block stays". LOC delta revised
from `−100` to **`−60`** (the remaining savings come from a `Batch`
block on pre-registration, not from auto-dispose). Appendix C cheat
sheet entry for `ServicedObservableCollection` is corrected
accordingly. **Mistake 9 (below) is added to §1.3** as the audit-
trail entry.

**Upstream ask:** Item 6 in the vNext feedback report — rename to
`HubPublishingObservableList` or add an explicit "Ownership" section
to the docstring. Optional follow-on: ship a true lifecycle-aware
`OwnedObservableCollection[T]` for the case where consumers want
auto-dispose-on-remove.

---

### 9.bis.8. Mistake 9 (recorded against §1.3)

**Mistake 9: spec Appendix C entry for `ServicedObservableCollection`
was paraphrased from an unspecified source — likely an upstream doc —
without verifying against the primitive's source.**

The Appendix C entry read "Observable collection that auto-disposes
items on removal via the service registry." Reading the source
(`vmx/collections/serviced_observable_collection.py`) shows: the
class is a `MutableSequence[T]` with optional hub publication; no
`dispose()` call on removed items; no service registry interaction
beyond the message hub. Same shape as mistakes 1, 4, 6: paraphrase
substituted for source.

**Methodology lesson reinforced:** Appendix C is a **navigation
aid**, not a contract document. Any per-VM PR that depends on a
primitive's behaviour must re-verify against the primitive's source
per §0's discipline rules, regardless of what Appendix C says.

---

### 9.bis.9. MVVM-half re-classification of today's bug train

§9 only posed VMx-primitive-fit questions; the MVVM-discipline half
(§3.2.bis / §4.3 / §5.0 / §5.8) was treated as "designed, Phase-0-
deferred". A reviewer's pushback during the brainstorm flagged this as
too narrow — the brainstorm should have surfaced concrete regression
anchors for the MVVM half too, not left them to the Phase 0 spike to
discover. This subsection is the resulting amendment.

**Method.** Walked every PR landed on `main` on 2026-06-28 (`git log
--since "2026-06-27 00:00"`), classified each fix as either
"MVVM-violation-shaped" (landed in the View as a workaround for state
that belongs in the VM) or "genuinely View-side" (CSS specificity,
Textual runtime quirk, pure polish). Cross-referenced each MVVM-
violation against the §3.2.bis 10-state inventory and the §4.2 / §4.3
work items.

**Findings.** Of 11 fixes shipped today (PRs #98–#105), **nine** were
MVVM-violation-shaped — fixes that landed in the View as workarounds
for state that the §3.2.bis inventory says belongs in the VM. The
other two (PR #102 CSS padding, PR #104 banner subtitle) were
genuine View-layer concerns.

**Concrete bug-train → work-item mapping** (regression anchors for
the per-Phase tests; column "Eliminated by" = the Phase whose
acceptance criterion must keep this bug fixed):

| PR | Fix description (today) | Landed in | §3.2.bis row(s) | Eliminated by | Why the migration eliminates the bug class |
|---|---|---|---|---|---|
| #98(2) | NavMenu queues `call_after_refresh(self.focus)` to win focus race on arrow-walk into a new page | View (NavMenu + page mount handlers) | 1, 10 | **Phase 7 (FocusCoordinatorVM)** | `focused_slot` is the authoritative signal; page mount reads it once, no race against Textual's queue ordering |
| #98(3) | New `Screen.-nav-active Pane.-focused` CSS rule across 10 themes; `NavMenu.on_focus/on_blur` drive the class | View (CSS in 10 themes + NavMenu handlers) | 9, 1, 10 | **Phase 7** | the dim-the-file-pane decision becomes `focused_slot == NAV_MENU`; the Screen-level CSS hack deletes; per-pane CSS reads from the coordinator |
| #99(a) | `EmrServerlessPage._maybe_focus_left` and `SettingsView._maybe_focus` skip auto-focus when NavMenu owns `app.focused` | View (per-page mount handlers inspect `app.focused`) | 8, 10 | **Phase 7** | page mount reads `focus_coordinator.focused_slot` instead of inspecting `app.focused`; no inspection logic on either side |
| #99(b) | `JobRunsPane._on_hub_message` drops `selected_id` from watch set; selection visuals come from `_cursor_index` | View (JobRunsPane watch-set + hand-rolled cursor) | 3, 4 | **Phase 2 (JobRunsVM → CompositeVM)** | `CompositeVM.on_current_changed` is granular — there is no `selected_id` PropertyChanged for the View to mis-watch; selection visual painted on `current` not on a separate index |
| #100(a) | `JobRunsPane._repaint_selection` (flips `-selected` class) replaces `_refresh_rows` on arrow | View (new hand-rolled paint helper) | 3, 4 | **Phase 2** | the helper stays in spirit, but its trigger becomes a single `on_current_changed` subscription on the VM-owned cursor instead of a watch on a hand-rolled property |
| #100(b) | `ApplicationPicker` hub-watch split + fingerprint diff guard | View (per-property watch routing + manual fingerprint) | 5, 6 | **Phase 1 (ApplicationsVM → CompositeVM[ApplicationVM])** | `on_collection_changed` only fires on actual structural change; the 30s no-change poll tick produces no event; fingerprint guard becomes redundant |
| #101 | `App.focus_active_service_pane` + `DualPane.focus_left_pane` + `SettingsView.focus_default` cross-View focus dispatch on ENTER | View (App-level dispatcher + per-page focus methods) | 7, 8, 10 | **Phase 7** | ENTER on NavMenu mutates `nav_menu_vm.current` (a service descriptor); the coordinator projects `focused_slot` to that service's default pane; per-page `focus_default` methods retire |
| #103 | `sender_object is self._vm` guards on all four EMR panes' `_on_hub_message` | View (per-pane sender filtering on a shared hub) | 3, 4, 5, 6 | **Phase 1 + Phase 2** | `CompositeVM.on_collection_changed` is emitted on the composite instance's own Observable, NOT broadcast via the global hub; cross-VM property echoes can't reach the wrong pane because they don't traverse the same channel |
| #105 | Removed redundant `NavMenu > #menu-settings-rows > NavRow { background: transparent; }` from 10 themes | View (10 theme files) | 2 | **Phase 1 (NavMenu §4.2.0 — finish the incomplete adoption)** | once `NavMenuVM._inner.current` is the uniform "this row is selected" slot driving a single `NavRow.-selected` CSS rule, per-section theme overrides become impossible to drift; the bug class (CSS-specificity clobbering selection visuals) is structurally eliminated |
| #102 | `NavRow` padding `0 1` → `0` to align ribbon with `EntryRow` | View (CSS in NavRow widget) | — (genuine View concern) | — | not MVVM-shaped; pure visual polish |
| #104 | `BrandBanner` prepends DEMO chip instead of replacing pedigree | View (BrandBanner ctor) | — (genuine View concern) | — | not MVVM-shaped; pure copy/UX polish |

**Why this mapping matters for the migration plan:**

The §6.4 "Regression anchors from prior bug train" section already
identifies PRs #100 / #103 as regression targets for Phase 2 and
#98 / #99 / #101 as targets for the focus coordinator. The mapping
above SHARPENS those references: each row above pins a specific
mechanism (the `.-nav-active` CSS hack, the `selected_id` watch, the
`call_after_refresh` queue ordering, the `sender_object` guards) to a
specific Phase whose acceptance criterion is "the bug from this PR
cannot recur because the mechanism that caused it no longer exists in
the code".

**Per-Phase acceptance criteria additions** (derived from the table —
these strengthen §10 / Definition of done):

- **Phase 1 (NavMenu finish + Applications + Toast + Transfers)**
  - Acceptance: PR #105 cannot recur — assertable by a test that
    renders the menu with each row selected in turn and asserts the
    `-selected` CSS class drives the visual (not a per-section
    background override).
  - Acceptance: PR #100(b) cannot recur — assertable by a test that
    invokes `applications.refresh()` 30 times with the same upstream
    response and asserts `on_collection_changed` fires zero times
    (composite-internal equality check, no cross-VM echo).
- **Phase 2 (JobRunsVM)**
  - Acceptance: PR #99(b) cannot recur — assertable by a test that
    triggers a `current` change and asserts NO `PropertyChangedMessage`
    crosses the hub for an old-style `selected_id` property.
  - Acceptance: PR #100(a) cannot recur — assertable by a test that
    moves `current` and asserts a single `on_current_changed` fires,
    no `on_collection_changed` reset.
  - Acceptance: PR #103 cannot recur — assertable by a test that
    constructs both JobRunsVM and JobRunDetailVM on the same hub,
    triggers `state` PropertyChanged on JobRunDetailVM, and asserts
    JobRunsPane (or its subscription proxy) does NOT see the event
    via the composite's `on_collection_changed`.
- **Phase 7 (FocusCoordinatorVM)**
  - Acceptance: PR #98(2) cannot recur — assertable by a test that
    arrow-walks NavMenu past a non-active service and asserts
    `focused_slot == NAV_MENU` throughout (no race with the new
    page's auto-focus).
  - Acceptance: PR #98(3) cannot recur — assertable by a test that
    sets `focused_slot = NAV_MENU` and asserts the file-pane border
    CSS class is `-dim`; no Screen-level `.-nav-active` class
    survives the migration.
  - Acceptance: PR #99(a) cannot recur — assertable by the same
    test as PR #98(2) but with an async-mounted page; the
    coordinator's `focused_slot` is checked at mount-time, no
    `app.focused` inspection.
  - Acceptance: PR #101 still works — assertable by a test that
    fires `nav_menu_vm.current = <service>` then asserts
    `focused_slot == <service's default slot>`; no
    `App.focus_active_service_pane` indirection.

**Open Phase-0 questions surfaced by the table** (these are the
items §4.3 deferred to Phase 0 with no starter; the mapping above
makes them concrete enough to answer in the spike):

- **Q-MVVM-A:** Slot priority table. The PR #101 mapping needs an
  explicit "ENTER → default slot for service X" lookup. Settings
  has no `JobRunsPane` analog — what's its default slot? Probably
  the first `CollapsibleTitle`, but the coordinator's `FocusSlot`
  enum has `SETTINGS` (line 1077) and not `SETTINGS_FIRST_SECTION`
  — clarification needed: do per-service slots have sub-slots, or
  does the coordinator coarsely project to `SETTINGS` and the
  Settings page handles its own internal focus?
- **Q-MVVM-B:** Modal precedence. None of the PRs above involve a
  modal-while-rail-focused interaction, but Phase 7 must answer:
  when a Confirmation modal is up, does `focused_slot` freeze on
  the prior slot, or take a new `MODAL` value? The `FocusSlot` enum
  at §4.3 line 1070–1077 has no `MODAL` entry; the spike must add
  it or document the alternative.
- **Q-MVVM-C:** PR #99(a)'s mount-time race. The fix today
  inspects `app.focused`. The coordinator's bridge to `app.focused`
  must NOT echo back via the same path (or it will defeat its own
  authority). Spike must sketch the dispatcher-throttled or initial-
  mount-flag option named in §4.3 risk lines 1157–1161.

These are NOT being resolved in this brainstorm — they require
running real Phase 7 code to verify the right answer. But they are
now CONCRETE (named PR/file/line) rather than abstract.

**Mistake 10 recorded.** §9's question set was VMx-fit-scoped; the
MVVM-half open questions implicit in §3.2.bis / §4.3 / §5.8 were not
called out. See §1.3 amendment for the canonical record.

**Spec amendments triggered by this subsection:**

- §6.4 "Regression anchors from prior bug train" gains a pointer to
  the bug→work-item table above as the per-PR breakdown.
- §10 "Definition of done" gains per-Phase acceptance criteria from
  the bulleted list above (Phase 1, Phase 2, Phase 7 each get
  explicit "PR #X cannot recur" checks).
- §4.3 risk note (slot priority table; projection timing) gains
  cross-references to Q-MVVM-A, Q-MVVM-B, Q-MVVM-C.
- §1.3 gains a Mistake 10 amendment block (next subsection ties it
  together with the existing record).

### 9.bis.10. Mistake 10 (recorded against §1.3)

**Mistake 10: §9's open-questions set was scoped only to VMx-
primitive-fit; the MVVM-half open questions implicit in §3.2.bis /
§4.3 / §5.8 were not surfaced.**

§9 contained seven questions, all of the shape "does VMx primitive X
fit aws-tui use case Y?". None asked about: the slot-priority table
§4.3 risk note defers to "Phase 0 should sketch on paper"; the
projection-timing strategy choice (throttled vs flagged); the
integration-test re-anchoring scope; the cursor-vs-selection split
inside View widgets §3.2.bis line 622 names but does not enumerate.

A reviewer's pushback during the brainstorm — "the spec also has
the MVVM half about state belonging in the VM, did you cover that?"
— flagged the gap. The brainstorm response was §9.bis.9: a
classification of today's bug train against §3.2.bis rows + §4.2 /
§4.3 work items, producing per-Phase regression anchors with
concrete PR references.

**Methodology lesson.** Specs with two halves (here: VMx-toolkit
adoption + MVVM-discipline migration) need open-questions sets that
explicitly span both halves. A single §9 question list reads as
"the only open items"; if half the spec's scope is missing from §9,
the brainstorm will silently scope down to the question set's
implicit framing.

### 9.bis.11. Round 3 — directive "compose, don't reject" applied (2026-06-29)

The brainstorm continued one day after the round-1/round-2 commits.
The maintainer's directive, delivered verbatim:

> Move ALL view logic out of the view layer and into the view model
> layer no matter what. In doing so, use VMx as much as possible.
> Pick the closest view model abstraction that most closely matches
> your needs in each case and then either directly use it or
> customize it either by inheritance or by instantiating a VMx
> artifact inside your custom abstraction and then adding more on
> top of it without directly exposing it. Once everything works and
> is verified, compile a list of suggestions to the upstream VMx
> library to add the new capabilities and abstractions in their next
> releases so we can refactor again.

This supersedes the implicit "VMx fits → adopt; doesn't fit → leave
hand-rolled" dichotomy rounds 1 and 2 operated on (recorded as
Mistake 11 in §9.bis.12 and as a §1.3 amendment block).

**The VMx-use ladder applied to each VM need:**

1. **Direct adoption** — use a VMx primitive out of the box.
2. **Inheritance** — subclass a VMx primitive and override the
   specific method(s) that need different behaviour.
3. **Composition (facade)** — instantiate a VMx primitive INSIDE a
   custom aws-tui VM that adds the missing behaviour on top. The
   VMx primitive is NOT exposed in the custom VM's public surface;
   consumers (Views, other VMs) bind only to the custom abstraction.
   This is the same pattern aws-tui already uses for leaf VMs
   (`ToastVM`, `EntryVM`, `TransferVM` all wrap `ComponentVMOf`).
   Round 3 extends the pattern to composite-shaped VMs too.

The underlying *decision* about which VMx primitive backs each
migration is unchanged in every case. The directive changes
**where the boundary sits** between aws-tui-side code and VMx
primitive surface.

#### §9.bis.2 (Q1 JobRunsVM) — re-framed

Was: "drop `PagedComposition`; `CompositeVM[JobRunVM]` + VM-level
`next_token` field + `load_more` command."

Now: build a custom aws-tui shape that COMPOSES
`CompositeVM[JobRunVM]` internally + adds `next_token`, `load_more`,
`has_more`, `refresh` on top. Two equivalent forms acceptable:
(a) inline the composition inside `JobRunsVM` directly (one
consumer — JobRunsVM is the only AWS list with nextToken pagination
in scope today); (b) lift to a small reusable aws-tui-side
`TokenPagedCompositeVM[T]` mini-primitive if a second consumer
materialises later. Either way, `CompositeVM` is NOT exposed in the
custom VM's public surface. The View binds only to the custom
abstraction's surface.

#### §9.bis.3 (Q2 filter coupling) — re-framed

Was: "Option C — derived filter view stays as a VM `@property`;
CompositeVM holds unfiltered + cursor; VM snaps cursor to first
filter-visible entry on filter change."

Now: build a custom aws-tui `FilteredCompositeVM[VM]` mini-primitive
that COMPOSES `CompositeVM[VM]` internally + adds `filter_text`,
`filtered_entries`, visible-cursor projection, `set_predicate` on
top. `CompositeVM` is NOT exposed in the custom VM's public surface.
Both `PaneVM` and `CommandPaletteVM` consume the custom abstraction;
neither touches the inner `CompositeVM` directly. `CommandPaletteVM`
additionally composes a `ScoredFilteredCompositeVM` variant if the
fuzzy-match score logic doesn't fit comfortably in the same
abstraction (Phase 0 spike decides shape, not whether).

#### §9.bis.5 (Q4 `FormVM` cross-field validators) — re-framed

Was: "cross-field invariant via custom `approve_command.predicate` +
persister raise; ~5 LOC per cross-field rule on each consuming VM."

Now: build a custom aws-tui `ValidatingFormVM[TM]` that COMPOSES
`FormVM[TM]` internally + adds declarative `field_validator(field,
fn)` / `model_validator(fn)` registration + reactive
`errors: dict[str, str]` map + auto-gated `approve_command` (where
`can_execute = is_dirty AND not has_errors`) on top. `FormVM` is
NOT exposed in the custom VM's public surface. `S3ConnectionsVM`
and every future form-shaped VM consume `ValidatingFormVM`.

#### §9.bis.6 (Q5 modal VMs) — re-framed (significant: no VM stays hand-rolled)

Was: "`ConfirmationVM` + `CrashVM` adopt `IDialogService` directly;
`ResumeVM` + `FirstRunVM` stay hand-rolled with documented 'no fit'
rationale."

Now: all four modal VMs become custom aws-tui VMs composing the
closest VMx primitive(s):

- `ConfirmationVM` → custom VM composing `IDialogService.confirm`
  directly. (Minimal composition wrapper — close to direct adoption,
  but still a wrapper so cross-cutting concerns like analytics /
  test hooks have one place to live.)
- `CrashVM` → custom VM composing
  `IDialogService.notify(severity=ERROR)` directly. (Same — minimal
  wrapper.)
- `ResumeVM` → custom VM composing the closest VMx primitive (a
  bespoke `ComponentVM` subclass with a result-projection field;
  `IDialogService.confirm` can't be the composed primitive because
  its `bool` return can't carry `Resume / Discard / KeepForLater`).
  The custom VM adds: a three-way result type, a domain-side
  "persist KeepForLater bit; show-on-next-boot" hook (the
  persistence stays in the domain because it's domain state, not
  dialog state; the VM owns the orchestration).
- `FirstRunVM` → custom VM composing N `FormVM[TM]` instances (one
  per wizard step) + a small "current step" state machine on top.
  Multi-step shape becomes a property of the custom abstraction;
  each step's form is a `FormVM` internally.

None stay hand-rolled in the View. The round-1 finding "no clean
fit in `IDialogService` for the three-way / multi-step shapes"
still holds (and feeds upstream Item 5); under the directive that
translates into **compose around it**, not skip the migration.

§4.2.7 LOC delta revises again: round 1 said `−60 × 4 = −240`;
round 1 brainstorm dropped it to `−60 × 2 = −120` once the
two-stay-hand-rolled framing landed; round 3 re-estimates toward
`−180 to −210` net (the composition wrappers for Resume and
FirstRun are small — ~30 LOC each of added behaviour — so the net
recovers most of the round-1 estimate). Phase 0 spike confirms
once the wrapper shapes are sketched.

#### Other resolutions — unchanged under the directive

- **§9.bis.1 (Q7 PaneVM = `CompositeVM`, not `HierarchicalVM`)** —
  primitive choice; the directive doesn't change which primitive
  backs the migration.
- **§9.bis.4 (Q3 ToastVM + `auto_construct_on_add`)** — direct
  adoption; no wrapper needed. `ToastStackVM` already wraps
  `CompositeVM[ToastVM]` per the spec.
- **§9.bis.7 (Q6 `ServicedObservableCollection`)** — direct
  adoption with explicit "caller still disposes" semantics. The
  `finally: vm.dispose()` block lives in `TransfersVM`'s own
  transfer-worker code path (VM-side already, not View-side); no
  re-framing needed.
- **§9.bis.9 (round-2 MVVM-half bug→work mapping)** — the
  per-Phase acceptance criteria all hold under the directive.
  Each criterion is a structural assertion about the post-
  migration code (mechanism X no longer exists); the directive
  reinforces the eliminations by ensuring no logic survives in the
  View as an escape hatch.

**Spec amendments triggered elsewhere by the directive:**

- §4.2.7 inline pointer gains a "SUPERSEDED — see §9.bis.11"
  notice above the round-1 "two of four stay hand-rolled"
  framing.
- §10 Definition of done item 1 softens from "kept hand-rolled
  with rationale" to "landed (either via direct VMx adoption, via
  inheritance, or via a custom aws-tui VM composing VMx
  primitives)".
- Upstream feedback report
  (`2026-06-28-vmx-upstream-vnext-asks.md`) gains a
  round-2 addendum at the top: the body's "what aws-tui did
  instead" lines should now read as **the composition shape aws-
  tui ended up with**, and the report's purpose becomes "vNext
  could ship these natively so consumers skip the wrapper"
  rather than "we couldn't use these primitives".

### 9.bis.12. Mistake 11 (recorded against §1.3)

**Mistake 11: brainstorm rounds 1 and 2 operated on an implicit
"VMx fits → adopt; doesn't fit → hand-roll" dichotomy.**

When a VMx primitive didn't fit cleanly (`IDialogService` for
ResumeVM/FirstRunVM, `PagedComposition` for JobRunsVM's load-more
UX, no `FilteredCompositeVM` for PaneVM/CommandPaletteVM, no
declarative validators on `FormVM`), the resolutions defaulted to
"stay hand-rolled" or "the derived view stays as a `@property` on
the wrapping VM" — both implicitly leaving logic in places the
maintainer's directive (2026-06-29) explicitly forbids: ALL view
logic out, no exceptions; VMx primitives are COMPOSED inside custom
aws-tui VMs when no direct fit exists.

The dichotomy fell out of treating VMx primitives as "the thing you
either use or don't" rather than "the thing you compose into the
abstraction you actually need". §9.bis.11 records the round-3
re-framing; this Mistake 11 entry records the methodology gap.

**Methodology lesson.** Treat VMx primitives as building blocks for
composition, not as choose-or-skip artifacts. When no primitive
fits cleanly, the work is: build a custom aws-tui VM that composes
the closest primitive(s) + adds the missing behaviour on top, WITHOUT
exposing the primitive in its public surface. The upstream feedback
report then captures the recurring composition shapes as candidate
primitives for vNext.

### 9.bis.13. Upstream feedback artifact

The seven resolutions above identified five places (Items 1–5 in the
vNext report) where a VMx primitive almost fit but didn't quite, plus
two doc / contract gaps (Items 6–7). All seven are documented as
upstream asks in:

> `docs/superpowers/specs/2026-06-28-vmx-upstream-vnext-asks.md`

Each item carries: primitive evaluated, aws-tui use case, what blocked
out-of-the-box adoption, what aws-tui did instead, the proposed vNext
API or behaviour change, and an effort estimate. The report is
addressed to the VMx maintainer.

---

## 10. Definition of done

The migration is complete when:

1. All nine per-VM targets in §4.2 (including §4.2.0 NavMenu) have
   landed — either via direct VMx adoption, via inheritance from a
   VMx primitive, or via a custom aws-tui VM composing the closest
   VMx primitive(s) per the §9.bis.11 ladder. **No VM stays
   hand-rolled in the View** (the round-1 "kept hand-rolled with
   rationale" framing is superseded by the §9.bis.11 directive).
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
- `docs/superpowers/specs/2026-06-28-vmx-upstream-vnext-asks.md` —
  upstream feedback report for the VMx maintainer; captures the seven
  primitives that almost fit but didn't quite, with proposed vNext APIs.
  Output of §9.bis.
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
# Full default suite. The pyproject marker filter excludes MinIO
# testcontainer tests (`-m 'not integration'`) unless explicitly requested.
uv run pytest -q

# Quick tier-targeted runs during development:
uv run pytest tests/unit/vm/                # VM unit tests (~6,500 LOC)
uv run pytest tests/unit/ui/                # UI widget unit tests
uv run pytest tests/snapshot/               # Textual SVG goldens (10 themes)
uv run pytest tests/integration/            # MinIO testcontainer + pilot-driven flows
```

Expect every default-tier test to pass. Recount the current inventory
with `uv run pytest --collect-only -q | tail -1`; recount snapshot
goldens with `find tests/snapshot/__snapshots__ -name '*.raw' | wc -l`.

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
| `ServicedObservableCollection` | `vmx/collections/serviced_observable_collection.py` | **CORRECTED — see §9.bis.7 / Mistake 9.** Observable list (`MutableSequence[T]`) that **optionally publishes** `CollectionChangedMessage` to a `MessageHub`. **Does NOT call `dispose()` on removed items** — caller still owns disposal. The "Serviced" refers to message-hub publication, not service-managed lifecycle. |
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
