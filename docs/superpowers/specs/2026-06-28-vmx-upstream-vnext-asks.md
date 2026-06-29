# VMx vNext — upstream feedback from the aws-tui adoption review

| Field | Value |
|---|---|
| Status | Drafted 2026-06-28; re-framed 2026-06-29 under the "compose, don't reject" directive (see §0 addendum) |
| Date | 2026-06-28 (initial) / 2026-06-29 (round-2 re-framing) |
| Source project | [aws-tui](https://github.com/thekaveh/aws-tui) — Textual-based TUI for AWS, currently consuming `vmx>=2.6.0,<3.0.0` |
| Target audience | VMx maintainer(s) planning the vNext (post-2.6.x) release |
| Related | [[2026-06-28-vmx-toolkit-adoption-design]] §4 (per-VM adoption targets), §9 (open questions), §9.bis (brainstorm resolutions incl. round 3 directive at §9.bis.11), §1.3 (mistakes record) |
| Tone (post round-2) | "We composed VMx primitive X inside a custom aws-tui VM that adds behaviour Y on top; vNext shipping X-natively-with-Y would let consumers skip the wrapper." |

---

## 0. Why this report exists

> **Round-2 addendum (2026-06-29).** The body of this report below
> was drafted during the round-1 brainstorm with the framing: "we
> considered VMx primitive X, found it almost fit, here's the
> workaround we landed on". That framing reflects the round-1
> implicit dichotomy — VMx fits → adopt; doesn't fit → hand-roll —
> that the aws-tui maintainer's 2026-06-29 directive supersedes
> (see [[2026-06-28-vmx-toolkit-adoption-design]] §9.bis.11). Under
> the directive: ALL view logic moves out of the View into the VM;
> when no VMx primitive fits directly, aws-tui builds a custom VM
> that COMPOSES the closest VMx primitive(s) internally + adds the
> missing behaviour on top, without exposing the primitive in the
> custom VM's public surface.
>
> **What this means for the items below.** The "What aws-tui did
> instead" lines should now be read as **the composition shape
> aws-tui ended up with**, not as a workaround that avoids VMx.
> The "Proposed vNext API" lines describe the primitive that would
> let consumers skip the composition wrapper — *exactly* the
> abstraction the aws-tui custom VM ends up exposing on top of the
> composed primitive. Each item is therefore a candidate for VMx
> vNext to ship natively so consumers don't have to compose.
>
> The underlying findings — which primitive's contract gap drove
> the composition — are unchanged from round 1. Only the framing
> flips: from "blocker" to "here's what we ended up building; bake
> it in".

aws-tui is migrating ~14 list-shaped / form-shaped / modal VMs from
hand-rolled MVVM patterns onto the VMx toolkit (the full plan is in
`2026-06-28-vmx-toolkit-adoption-design.md`). During that design review,
every per-VM target was forced through the discipline of:

1. Naming the VMx primitive(s) evaluated.
2. Reading the primitive's source — not its docstring — to verify fit.
3. Recording why the primitive was chosen, adapted, or rejected.

That process surfaced a set of cases where the primitive **almost** fit
out of the box but didn't quite, AND where a small upstream change would
let aws-tui (and presumably other consumers) adopt the primitive without
a custom composition wrapper. This document is that list.

**For each item:**

- **Primitive evaluated** — name and file path under
  `.venv/lib/python3.11/site-packages/vmx/`.
- **aws-tui use case** — the concrete shape we wanted to fit it to.
- **What blocked clean out-of-the-box adoption** — the specific
  contract / API / shape mismatch.
- **What aws-tui did instead** — the workaround that ships in the
  adoption PRs.
- **Proposed vNext API or behavior** — concrete, scoped to the gap.
- **Estimated upstream effort** — relative sizing, not hours.
- **Reference** — the aws-tui spec section.

Items are ordered by impact on the aws-tui adoption (high → low), not by
upstream effort.

---

## Item 1 — `PagedComposition` cannot model nextToken (forward-only, unknown total) pagination

**Primitive evaluated:** `PagedComposition[TVM]` —
`vmx/collections/paged_composition.py`.

**aws-tui use case:** `JobRunsVM` (EMR Serverless) loads job runs via
`list_job_runs_page(start_token=…)` which returns `(page, next_token)`.
The total count is unknown until the last page; the user advances with
"Load more" (PgDn or a button). The aws-tui spec §4.2.1 originally
targeted `CompositeVM[JobRunVM]` wrapped in `PagedComposition` for the
pagination.

**What blocked out-of-the-box adoption:**

1. `PagedComposition` is **strictly index-based**:
   - `_source_count()` calls `len(src)` (paged_composition.py:204–210)
     with a `sum(1 for _ in src)` fallback that materialises the whole
     iterable.
   - `page_count = ceil(n / page_size)` (line 115) requires knowing `n`.
   - `items` slices `source[start : start + page_size]` (line 171) —
     random access required.
   - There is no opaque-token cursor concept anywhere in the API.

2. The aws-tui UX is **infinite-scroll "Load more"**, not "page N of M"
   (see `ui/widgets/emr_serverless/page.py:451` — `LoadMoreRequested`
   event). `move_to_next_page` would be a misuse: it would mean "view a
   different in-memory slice" but the UX wants "fetch more from AWS".

3. Even if we accumulated pages into an `ObservableList`, see Item 2
   below: `PagedComposition` cannot subscribe to a `CompositeVM`'s
   mutation stream because the observable shapes differ.

**What aws-tui did instead:** Dropped `PagedComposition` from §4.2.1
entirely. JobRunsVM uses `CompositeVM[JobRunVM]` as the accumulated
list; `next_token: str | None` stays as a VM field; `load_more:
RelayCommand` calls the API and appends to the composite. `refresh:
RelayCommand` clears the composite and re-fetches the first page.

**Proposed vNext API:** Ship a `TokenPagedComposition[VM]` (or
`CursorPagedComposition[VM]`) alongside the existing index-based one:

```python
class TokenPagedComposition(Generic[VM]):
    def __init__(
        self,
        fetch_next: Callable[[Token | None], Awaitable[tuple[list[VM], Token | None]]],
        *,
        auto_construct_on_add: bool = False,
    ) -> None: ...

    @property
    def items(self) -> Sequence[VM]: ...          # accumulated VMs
    @property
    def current_token(self) -> Token | None: ...  # None when fully drained
    @property
    def has_more(self) -> bool: ...               # current_token is not None
    @property
    def load_more_command(self) -> RelayCommand: ...  # can_execute = has_more
    @property
    def refresh_command(self) -> RelayCommand: ...    # clears + re-fetches with token=None
    @property
    def on_collection_changed(self) -> Observable[CollectionChangedEvent]: ...
```

This is what every AWS-API consumer wants (`list_*` endpoints all use
`nextToken`). The existing index-based `PagedComposition` stays for
finite-known-size sources. Documenting which one to reach for is part
of the deliverable.

**Estimated upstream effort:** **Small–medium.** ~150 LOC + tests +
docs. Reuses existing `CollectionChangedEvent` and `RelayCommand`.

**Reference:** aws-tui spec §4.2.1, §7.1, §9 q1.

---

## Item 2 — `PagedComposition` does not compose with `CompositeVM` as its source

**Primitive evaluated:** `PagedComposition[TVM]` (source-subscription
path) — `vmx/collections/paged_composition.py:232–240`.

**aws-tui use case:** The natural composition for paged-view-over-typed-
children-with-lifecycle is "CompositeVM holds the children; PagedComposition
slices them". The adoption spec §4.2.1 originally framed it exactly that way.

**What blocked out-of-the-box adoption:**

`PagedComposition._try_subscribe_source` only subscribes to objects
exposing the four named observables that `ObservableList` ships:

```
("on_item_added", "on_item_removed", "on_item_replaced", "on_reset")
```

`CompositeVM` exposes `on_collection_changed:
Observable[CollectionChangedEvent]` (composite_vm.py:103) — a single
typed event stream — and does NOT expose the four split ObservableList
hooks. So `PagedComposition(composite_vm, page_size=N)` runs but
silently fails to re-emit `page_count` / `items` changes on mutation:
the View sees stale slices until something else forces a redraw.

This is a particularly nasty mismatch because the constructor accepts
anything iterable, so the failure is at runtime not at construction.

**What aws-tui did instead:** Combined with Item 1 — dropped
`PagedComposition` entirely for the JobRunsVM case. We did NOT write
the adapter. Had we needed `PagedComposition`'s slicing semantics for a
different VM, we'd have hand-rolled a ~20-LOC adapter that subscribes
to `composite.on_collection_changed` and re-emits split
`on_item_added`/`on_item_removed`/`on_item_replaced`/`on_reset` per
event action.

**Proposed vNext API:** Two options, pick one:

- **(A) Make `PagedComposition` accept `on_collection_changed` shape natively.**
  Extend `_try_subscribe_source` to also try `on_collection_changed`
  and map the typed event into internal page-state updates. ~20 LOC of
  switch on `CollectionChangedEvent.action`.
- **(B) Ship a `CompositeView` capability mixin** that the
  `PagedComposition` constructor sniffs for: any class implementing it
  exposes `as_observable_list_view() -> ObservableListLike[T]`. This is
  more invasive but lets future composites (e.g. an eventually-
  shipped `FilteredCompositeVM`) plug in too.

Recommendation: ship (A) now, leave (B) for vNext+1.

**Estimated upstream effort:** **Small.** ~20–40 LOC + test.

**Reference:** aws-tui spec §4.2.1, §7.1, §9 q1.

---

## Item 3 — No "filtered / derived view over `CompositeVM`" primitive

**Primitive evaluated:** `CompositeVM[VM]` —
`vmx/composites/composite_vm.py`. Specifically the absence of a derived-
view companion.

**aws-tui use case:** Two VMs need a filter over an underlying composite:

- `PaneVM` (`vm/file_manager/pane_vm.py`) — file listing with
  hide/show-dotfiles and substring filter. Filter is settings-driven
  and ephemeral.
- `CommandPaletteVM` (`vm/chrome/command_palette_vm.py`) — fuzzy
  filter input as the user types; cursor moves over the **filtered**
  rows (per-keystroke recomputation).

In both, the cursor index means "row N in the filter-visible list", not
"row N in the underlying list" (pane_vm.py:549).

**What blocked out-of-the-box adoption:**

VMx ships `CompositeVM` (homogeneous collection + cursor + lifecycle
cascade) but no derived/filtered companion. To get
"`CompositeVM[A]` + a filter predicate → filter-visible
`CompositeVM[A]` with its own cursor", aws-tui would have to hand-roll
~50–80 LOC: subscribe to outer's `on_collection_changed`, recompute
filtered indices, re-emit translated `CollectionChangedEvent`, manage
its own `current` slot, resolve outer-vs-inner cursor mapping.

We considered that and rejected it as over-abstraction for two VMs.

**What aws-tui did instead:** Kept the derived-filter view as a
**`@property` on the wrapping VM** (Option C in §9 q2):

- `_entries: CompositeVM[EntryVM]` holds the unfiltered list +
  lifecycle + the canonical `current` cursor.
- `filter_text: str` + `filtered_entries: tuple[EntryVM, ...]` stay as
  VM fields/property — recomputed on filter or collection change.
- On filter change, the VM snaps `_entries.current` to the first
  filter-visible entry (matching today's "cursor snaps to top of
  filtered" behaviour).
- The View binds to `filtered_entries` for rendering and `current` for
  the selected row.

This works, keeps the VM layer fully Vie-portable (NiceGUI-friendly),
but the filter logic and the cursor-mapping logic both live on the
wrapping VM instead of in a reusable primitive.

**Proposed vNext API:** A `FilteredCompositeVM[VM]` / `DerivedCompositeVM[VM]`
decorator:

```python
class FilteredCompositeVM(Generic[VM]):
    def __init__(
        self,
        source: CompositeVM[VM],
        predicate: Callable[[VM], bool] | rx.Observable[Callable[[VM], bool]],
        *,
        cursor_policy: CursorPolicy = CursorPolicy.SNAP_TO_FIRST_VISIBLE,
    ) -> None: ...

    @property
    def children(self) -> Sequence[VM]: ...      # filter-visible only
    @property
    def current(self) -> VM | None: ...          # own cursor, kept visible
    @property
    def on_collection_changed(self) -> Observable[CollectionChangedEvent]: ...

    def set_predicate(self, predicate) -> None: ...
```

`CursorPolicy` options at minimum:
- `SNAP_TO_FIRST_VISIBLE` — what aws-tui's two VMs both want.
- `CLEAR_IF_FILTERED_OUT` — set `current = None` if the source's
  current is no longer visible.
- `KEEP_INVISIBLE` — for edge cases where the cursor outlives visibility.

A second variant — `ScoredFilteredCompositeVM` — taking a
`scorer: Callable[[VM, Query], int | None]` would directly model
`CommandPaletteVM`'s fuzzy-match-with-span-scoring case.

**Estimated upstream effort:** **Medium.** ~200 LOC + tests +
docs. The cursor-mapping is the subtle part.

**Reference:** aws-tui spec §4.2.2, §4.2.8, §7.2, §9 q2.

---

## Item 4 — `FormVM` has no declarative validator API; cross-field rules need predicate scaffolding

**Primitive evaluated:** `FormVM[TM]` — `vmx/forms/form_vm.py`.

**aws-tui use case:** `S3ConnectionsVM` edits an `S3Connection` model
that has a cross-field invariant: **`endpoint_url` must be set IFF
`force_path_style` is True**. Other settings VMs are likely to add
similar rules (e.g., "region must be a known AWS region if not using
endpoint_url override").

**What blocked out-of-the-box adoption:**

Reading `form_vm.py:48–86`:
- The only validation hook is `approve_command.predicate` (a plain
  callable that gates `can_execute`) plus `strict` (gates on
  `is_dirty`).
- The `persister: Callable[[TM], Awaitable[None]]` may raise — the
  docstring says "Raise on failure".
- There is no `field_validator(...)` decorator, no
  `model_validator(...)` hook, no `errors: dict[str, str]` reactive
  property, no per-field invalid state to bind View error markers to.

So cross-field validation works, but the VM-author has to do all the
plumbing: write `_is_valid_invariants() -> bool`, wire it into a custom
`approve_command.predicate`, raise the same condition from the
persister as belt-and-suspenders, and emit `PropertyChangedMessage`
themselves so the View knows the validity changed.

**What aws-tui did instead:** Accepted the manual pattern for §4.2.6.
~5 LOC of predicate wiring per cross-field rule. Works, but is the
exact "framework primitive almost fits, here's the boilerplate that
keeps coming back" shape this whole adoption was supposed to eliminate.

**Proposed vNext API:** Declarative validators on `FormVMBuilder`:

```python
form = (
    FormVM.builder()
    .initial(s3_conn)
    .persister(persist_fn)
    .strict()
    # Field validators: (model) -> error message or None
    .validator("endpoint_url", lambda m: (
        "endpoint URL is required when force_path_style is True"
        if m.force_path_style and not m.endpoint_url else None
    ))
    # Model validators: cross-field, returns dict[field, message] or {} when valid
    .model_validator(lambda m: (
        {} if (bool(m.endpoint_url) == m.force_path_style) else
        {"endpoint_url": "required when force_path_style is True",
         "force_path_style": "implies endpoint_url"}
    ))
    .build()
)

# Reactive surface:
form.errors          # dict[str, str], live
form.field_errors("endpoint_url")  # str | None, live
form.is_valid        # bool, live
form.approve_command.can_execute  # auto-gated: is_dirty AND is_valid
```

The View binds error strings next to each field; the approve button
auto-disables; the persister keeps its "raise on failure" semantics as
the last-line check.

**Estimated upstream effort:** **Medium.** ~250 LOC + tests + docs.
Mostly state machinery; the API surface is short.

**Reference:** aws-tui spec §4.2.6, §9 q4.

---

## Item 5 — `IDialogService` is a closed contract; no escape hatch for VM-backed modals

**Primitive evaluated:** `DialogService` (abstract) —
`vmx/dialogs/dialog_service.py`. Concrete: `NullDialogService` (tests),
`DialogService` host implementations.

**aws-tui use case:** Four hand-rolled modal VMs we wanted to migrate
in §4.2.7: `ConfirmationVM`, `CrashVM`, `ResumeVM`, `FirstRunVM`.

**What blocked out-of-the-box adoption:**

`DialogService`'s abstract surface is **closed-set** (dialog_service.py
lines 38–96):

```python
async def pick_file_to_open(filter, title) -> str | None
async def pick_file_to_save(filter, title, suggested_name) -> str | None
async def confirm(message, title) -> bool
async def notify(message, title, severity) -> None
```

There is NO generic `present(modal_vm: VM) -> Awaitable[VM.result]` or
similar. Consequence for the four aws-tui modals:

- **ConfirmationVM** → maps to `confirm(...) -> bool`. **Clean fit.**
- **CrashVM** → maps to `notify(severity=ERROR)`. **Clean fit.**
- **ResumeVM** → three-way decision (Resume / Discard / KeepForLater).
  Boolean `confirm` is two-way; "KeepForLater" needs persistent state.
  **No direct fit.**
- **FirstRunVM** → multi-step welcome flow with form fields.
  **No direct fit.**

Round-1 framing put the §4.2.7 LOC delta at `−60 × 2 = −120`
(only the two clean fits migrated). Round-2 framing under the
"compose, don't reject" directive (see report addendum) re-estimates
toward `−180 to −210`: all four migrate, with the two no-direct-fit
cases composing the closest VMx primitive(s) inside a custom aws-tui
VM rather than staying hand-rolled.

**What aws-tui did instead (round 2 framing):**

- ConfirmationVM → custom aws-tui VM composing
  `IDialogService.confirm` directly. Minimal wrapper.
- CrashVM → custom aws-tui VM composing
  `IDialogService.notify(severity=ERROR)` directly. Minimal wrapper.
- ResumeVM → custom aws-tui VM composing a bespoke `ComponentVM`
  subclass + adding a three-way result type
  (`Resume / Discard / KeepForLater`) + a domain-side
  "persist-and-show-on-next-boot" hook on top. The composed
  primitive is NOT `IDialogService.confirm` (bool return won't fit);
  it's `ComponentVM` with a result-projection field.
- FirstRunVM → custom aws-tui VM composing N `FormVM[TM]` instances
  (one per wizard step) + a small "current step" state machine on
  top. Multi-step shape is owned by the custom abstraction;
  `FormVM` is internal.

None stay hand-rolled in the View. The composition wrappers for
Resume and FirstRun are small (~30 LOC each of added behaviour)
because the underlying primitives (`ComponentVM`, `FormVM`) cover
most of the shape already.

**Proposed vNext API:** Extend `DialogService` with a generic VM-backed
modal escape hatch:

```python
class DialogService(ABC):
    # ... existing typed methods unchanged ...

    @abstractmethod
    async def present(self, modal_vm: ModalVM[T]) -> T:
        """Push a VM-backed modal; resolve when the host dismisses it.

        ``modal_vm`` exposes a `result: T` accessor that the host reads
        on dismissal. The host-side glue picks the appropriate widget
        from a registry keyed on the VM's runtime type, or via a
        protocol the modal VM advertises.
        """
        ...
```

Plus a small companion protocol/base:

```python
@runtime_checkable
class ModalVM(Protocol[T]):
    @property
    def result(self) -> T | None: ...
    @property
    def is_dismissed(self) -> bool: ...
    def dismiss(self, result: T) -> None: ...
```

ResumeVM's `result: ResumeDecision` (`Resume | Discard | KeepForLater`)
then fits naturally. FirstRunVM's `result: FirstRunSelections | None`
fits naturally. Persistence of "show next boot" stays on the consumer
side (it's domain state, not dialog state), but the modal itself
adopts a primitive.

**Sub-asks (each independently small):**

- `MultiStepFormVM[TM]` — a `FormVM` flavor with named steps and
  `next_step` / `previous_step` commands. Covers FirstRunVM and any
  future wizard. ~150 LOC.
- `ChoiceVM[ChoiceEnum]` — generalises `ConfirmationVM` to N-way
  (instead of just two-way `confirm`). Covers ResumeVM's three-way
  decision out of the box. ~80 LOC.

**Estimated upstream effort:** **Medium.** `present()` + protocol ~80
LOC + test + docs. The sub-asks are each independently small.

**Reference:** aws-tui spec §4.2.7, §9 q5.

---

## Item 6 — `ServicedObservableCollection`: name and docstring suggest ownership semantics it does not implement

**Primitive evaluated:** `ServicedObservableCollection[T]` —
`vmx/collections/serviced_observable_collection.py:1–137`.

**aws-tui use case:** `TransfersVM` (`vm/file_manager/transfers_vm.py`)
holds a list of in-flight `TransferVM`s. Today's hand-roll uses
explicit `vm.dispose()` in `_run_one_transfer`'s `finally:` block.
Spec §4.2.4 originally targeted this primitive expecting auto-dispose
semantics.

**What blocked out-of-the-box adoption — and a doc bug recorded as
"mistake 9" in the aws-tui spec:**

Source review (serviced_observable_collection.py):

- Class docstring: *"An observable list that **optionally publishes**
  CollectionChangedMessage events to an `MessageHub`-compatible hub."*
- `__delitem__` (line 86–93): `del self._items[index]` then emit
  `CollectionChangedMessage.for_remove(...)`. No `dispose()` call.
- `remove` (line 121–125): same shape. No `dispose()` call.
- Constructor: `def __init__(self, hub: object = None)` — the *only*
  service it touches is the message hub. There is no service registry
  involved.

**The "Serviced" in the name refers to message-hub publication**, not
to service-managed lifecycle. The class is, in effect, a
`HubPublishingObservableList`. It does NOT take ownership of items.

The aws-tui adoption spec's Appendix C cheat sheet had recorded
"Observable collection that auto-disposes items on removal via the
service registry" — paraphrased from somewhere outside the source, and
**wrong against the source**. This was recorded as **Mistake 9** in
§1.3 of the spec, per the same shape as Mistakes 1, 4, 6 (paraphrase
substituted for source review).

**What aws-tui did instead:** Corrected the spec. Keep the existing
`finally: vm.dispose()` block in TransfersVM since the primitive
doesn't dispose on its behalf. Reduced LOC delta from `−100` to `−60`
(the savings come from a `Batch` block on the pre-registration path,
not from auto-dispose).

**Proposed vNext changes — pick one or both:**

- **(A) Docs/rename fix (smallest):** Either rename the class to
  `HubPublishingObservableList[T]` (or `MessagingObservableList[T]`),
  OR add an explicit "Ownership" section to the docstring:
  *"This class does NOT call `dispose()` on removed items. Ownership
  stays with the caller. If you need lifecycle-cascading removal, use
  `CompositeVM[VM]`."*

- **(B) Ship an actual lifecycle-aware variant (larger):** A new
  `OwnedObservableCollection[T]` that DOES call `dispose()` (or a
  configurable `release_fn: Callable[[T], None]`) on removal. This
  pairs naturally with `CompositeVM`'s lifecycle cascade for VMs but
  serves the "owned plain-value collection" case too.

Recommendation: ship (A) immediately as a non-breaking docs/deprecation
pass; consider (B) for vNext+1 once a second use case shows up.

**Estimated upstream effort:** **Trivial for (A)** (1-line rename or
30-line docstring), **small for (B)** (~50 LOC + tests).

**Reference:** aws-tui spec §4.2.4, §9 q6, §1.3 mistake 9.

---

## Item 7 — `HierarchicalVM` has no documented cache-invalidation contract

**Primitive evaluated:** `HierarchicalVM[TModel, TVM]` —
`vmx/hierarchical/hierarchical_vm.py` (per Appendix C of the aws-tui
adoption spec — not deep-read in this session because aws-tui chose
Option A and deferred B).

**aws-tui use case:** `PaneVM` (`vm/file_manager/pane_vm.py`, 893 LOC,
the project's largest) models a file pane. The DOMAIN is hierarchical
(LocalFS is a tree; S3 keys are `/`-delimited). The View today is
Norton-Commander style — one level at a time — but the user **does**
plan an "Exploded path" feature where the breadcrumb across the top
shows each path segment as a clickable jump-to-parent.

**What blocked out-of-the-box adoption:**

aws-tui §4.2.2 evaluated `CompositeVM` vs `HierarchicalVM` explicitly.
Option B (`HierarchicalVM`) was rejected this round, but the reason
matters for vNext:

1. **Cache-invalidation contract is unwritten.** `HierarchicalVM` with
   `eager_children=False` caches children on first `.children` access.
   When does a folder forget its cached children? When the user
   navigates away? When a TTL expires? When an explicit `invalidate()`
   is called? The aws-tui project's current "no cache, ask provider on
   every cd" contract is an audited feature: S3 listings race external
   mutation, so cache-fresh listings are the safer default. Switching
   to lazy-cached-with-no-invalidation-contract is a behaviour change
   that needs design.

2. **No documented "refresh-this-subtree" pattern.** Even when caching
   is desired, the primitive doesn't surface how to flush it.

These weren't the only reasons aws-tui chose Option A — the breadcrumb
feature alone doesn't justify the refactor cost — but they were
significant. If the cache-invalidation gaps were closed, the
HierarchicalVM option would be more attractive next time it comes up.

**What aws-tui did instead:** Chose Option A
(`CompositeVM[EntryVM]`). Kept the §4.2.2 HierarchicalVM analysis in
the spec as a "rejected — here's why" record so a future inline-expand
tree-view feature can re-open the decision.

**Proposed vNext API:**

```python
class HierarchicalVM(Generic[TModel, TVM]):
    # ... existing surface ...

    def invalidate(self, node: TVM) -> None:
        """Drop *node*'s cached children. Next .children access re-fetches."""

    def invalidate_recursive(self, node: TVM) -> None:
        """Drop *node* and all descendants' cached children."""

    children_ttl: timedelta | None  # constructor option; None = no TTL (current behaviour)

    # Optional: refresh-on-focus hook for the host UI to wire.
    on_refresh_requested: Observable[TVM]
```

Plus a docs section explicitly stating the cache-invalidation contract
in vNext.

**Estimated upstream effort:** **Small.** ~100 LOC (the TTL bookkeeping
is the only non-trivial part) + tests + docs.

**Reference:** aws-tui spec §4.2.2, §9 q7.

---

## Summary table

| # | Primitive | Issue | aws-tui workaround | vNext ask effort |
|---|---|---|---|---|
| 1 | `PagedComposition` | index-based; can't model nextToken | dropped; `CompositeVM` + manual `next_token` + `load_more` | Small–medium (`TokenPagedComposition`) |
| 2 | `PagedComposition` × `CompositeVM` | observable shapes don't compose | dropped via Item 1 (no adapter built) | Small (extend `_try_subscribe_source`) |
| 3 | `CompositeVM` derived/filtered view | no primitive ships | filter as VM @property; cursor kept visible by VM | Medium (`FilteredCompositeVM`) |
| 4 | `FormVM` validators | no declarative API | manual predicate + persister raise | Medium (declarative validators + `errors` map) |
| 5 | `IDialogService` | closed contract; no VM-backed modal | all 4 modals migrate as custom VMs composing VMx; 2 compose `confirm`/`notify` directly, 2 compose `ComponentVM` + `FormVM` with result projection on top | Medium (`present()` + `ModalVM` protocol; optional `MultiStepFormVM`, `ChoiceVM`) |
| 6 | `ServicedObservableCollection` | docs say ownership; source does not | corrected spec; kept manual `finally: dispose` | Trivial (rename / docstring); small (owned variant) |
| 7 | `HierarchicalVM` | no cache-invalidation contract | chose `CompositeVM` instead | Small (`invalidate*` + TTL + docs) |

---

## Suggested prioritisation for VMx vNext

If only a subset can ship:

1. **Item 6 docs fix** — trivial; closes a real bug (paraphrase-vs-source confusion). Ship in next patch.
2. **Item 1 `TokenPagedComposition`** — unblocks every AWS-API consumer; aws-tui would adopt immediately.
3. **Item 4 declarative validators** — high recurring boilerplate cost across consumers; the API shape is uncontroversial.
4. **Item 5 `present()` + `ModalVM`** — opens the closed-set contract without breaking it; lets bespoke modals re-platform.
5. **Item 3 `FilteredCompositeVM`** — useful but workable around (the VM @property pattern is fine).
6. **Item 7 `HierarchicalVM` invalidation** — only matters if a tree-view consumer materialises.
7. **Item 2 PagedComposition-Composite bridge** — automatically obsolete if Item 1 ships, since aws-tui doesn't pair the two any more.

---

## Acknowledgements / disclaimers

- This is a **single-consumer** report (aws-tui). Other VMx consumers
  may have different priorities. Don't change a contract on the
  strength of this report alone.
- The adoption-spec discipline rules (`§0` of
  `2026-06-28-vmx-toolkit-adoption-design.md`) required every claim
  here to cite a primitive's source by file + line range and quote
  from the source rather than paraphrase. Items above follow that
  rule; if a paraphrase has slipped in, treat it as Mistake-9-shape
  and verify before acting.
- Every "aws-tui's workaround" line above describes the **design**
  intent; the actual migration PRs haven't shipped yet. If a workaround
  description turns out to be wrong against the eventual PR, update
  this document.
