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

**Round-3 implementation evidence:** Landed on the refactor branch at
`refactor/vmx-toolkit-adoption` (PR #109):
- `src/aws_tui/vm/emr_serverless/job_runs_vm.py:127-200` —
  `_inner: CompositeVM[ComponentVMOf[JobRunSummary]]` (composite
  internal), `_next_token: str | None` (VM field), `refresh()` /
  `load_more()` methods.
- Plus dedup-on-set in `refresh()` (commit `c114b36`) — if the new
  page matches the current accumulator head, the composite is NOT
  mutated. Same pattern as ApplicationsVM
  (`applications_vm.py:226-290`).
- The "what we wish VMx shipped" shape would directly absorb this
  pattern — `TokenPagedComposition[VM]` would carry both the
  next_token threading AND the dedup-on-set guard.

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

This works, keeps the VM layer fully View-portable (NiceGUI-friendly),
but the filter logic and the cursor-mapping logic both live on the
wrapping VM instead of in a reusable primitive.

**Round-3 implementation evidence:**

PaneVM (commit `c641d1a`) now treats `composite.current` as the
canonical cursor source-of-truth. Its `_cursor_index` is a
property bridge:
- **Getter:** derives position from
  `_inner.current`'s entry index within the visible filter
  projection. Returns 0 when current is None.
- **Setter:** clamps a filtered-position write; promotes the
  corresponding entry's inner to `_inner.current`.

This **property-bridge pattern** is a useful generalisation
worth noting for vNext: any VM that wants to expose an `int`
cursor index while owning the canonical state in
`composite.current` (or a filtered projection thereof) can use
this pattern. The pattern composes cleanly with
`FilteredCompositeVM`'s visible projection — the index domain is
"position within visible", not "position within source".

Built as `aws_tui.vm._composition.FilteredCompositeVM[VM]` at
`src/aws_tui/vm/_composition/filtered_composite_vm.py`. The public
surface ended up being:

```python
class FilteredCompositeVM(Generic[VM]):  # VM bound to _ComponentVMBase
    def __init__(
        self,
        source: CompositeVM[VM],
        *,
        predicate: Callable[[VM], bool] | None = None,
        cursor_policy: str = "snap_to_first",  # or "clear"
    ) -> None: ...

    @property
    def visible(self) -> tuple[VM, ...]: ...
    @property
    def visible_count(self) -> int: ...
    @property
    def current(self) -> VM | None: ...
    @property
    def on_changed(self) -> rx.Observable[None]: ...  # fires on predicate or source change

    def set_predicate(self, predicate) -> None: ...
    def set_current(self, item: VM | None) -> None: ...
    def move_to_next_visible(self) -> None: ...
    def move_to_previous_visible(self) -> None: ...
    def dispose(self) -> None: ...
```

23 tests at `tests/unit/vm/_composition/test_filtered_composite_vm.py`
cover predicate filtering, both cursor policies, navigation wrapping,
on_changed event timing, source-mutation reconciliation, and dispose
discipline.

`PaneVM` consumes it at `src/aws_tui/vm/file_manager/pane_vm.py:234`
with the `on_changed` Observable wired in `__init__` to re-derive
`_filtered`. `CommandPaletteVM` does NOT consume it — it inlines a
score-rank variant directly because the boolean predicate model
doesn't capture rank-by-score (see new Item 8 below).

**Differences from the round-1 proposal worth folding into vNext:**
- `on_changed` payload is bare `None` (not the typed
  `CollectionChangedEvent` — subscribers re-read `visible`/`current`).
  Simpler. Worth pinning the contract before vNext lifts it.
- `set_predicate` is identity-checked — passing the same predicate
  object is a no-op. PaneVM's consumer had to wrap its bound method
  in a fresh closure each call to avoid silent no-ops on
  `filter_text` change (pane_vm.py recompute path). vNext should
  consider value-based equality OR document the identity contract.
- VM bound is `_ComponentVMBase` (matches `CompositeVM`'s
  constraint) — not exposed publicly via vmx's `__init__` but
  importable from `vmx.components.base`.

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

**Round-3 implementation evidence:** Lifted the manual pattern into a
reusable aws-tui-side `ValidatingFormVM[TM]` mini-primitive at
`src/aws_tui/vm/_composition/validating_form_vm.py` that composes
`vmx.forms.FormVM` internally. Public surface:

```python
class ValidatingFormVM(Generic[TM]):
    def __init__(self, initial: TM, persister, *, strict: bool = True): ...

    @property
    def model(self) -> TM: ...
    @property
    def snapshot(self) -> TM: ...
    @property
    def is_dirty(self) -> bool: ...
    @property
    def errors(self) -> dict[str, str]: ...
    @property
    def has_errors(self) -> bool: ...
    @property
    def is_valid(self) -> bool: ...
    @property
    def approve_command(self) -> RelayCommand: ...  # auto-gated
    @property
    def deny_command(self) -> RelayCommand: ...     # revert
    @property
    def on_errors_changed(self) -> rx.Observable[dict[str, str]]: ...

    def add_field_validator(self, field: str, fn: FieldValidator) -> None: ...
    def add_model_validator(self, fn: ModelValidator) -> None: ...
    def set_model(self, model: TM) -> None: ...
    def dispose(self) -> None: ...
```

14 tests at `tests/unit/vm/_composition/test_validating_form_vm.py`
cover field validators, the §9.bis.5 canonical
`endpoint_url IFF force_path_style` cross-field example, approve
gating (strict + non-strict), on_errors_changed event timing.

**Real consumer evidence:**
`src/aws_tui/vm/settings/s3_connection_form_vm.py` — composes
`ValidatingFormVM[S3CompatForm]` + registers the field-presence
validators for the five required string fields + the
endpoint-IFF-force-path-style cross-field invariant. 11 tests at
`tests/unit/vm/settings/test_s3_connection_form_vm.py`.

**Differences from the round-1 proposal worth folding into vNext:**
- `add_field_validator` / `add_model_validator` are imperative
  registration methods, NOT builder-time callables. The original
  proposal showed a builder DSL; aws-tui's consumer ended up
  preferring runtime registration because the form's validators
  could vary by mode (add vs edit). vNext might support BOTH.
- `errors` is a `dict[str, str]` — one error per field. Multi-error
  scenarios (e.g., a field that fails three different validators)
  collapse to the first non-None message per the
  registration-order rule. Document that.
- `approve_command.can_execute = is_valid AND (not strict OR
  is_dirty)`. The same gating policy aws-tui's S3ConnectionFormVM
  needed.
- `model_validator` returns `dict[field, error]` so a cross-field
  invariant can flag MULTIPLE fields. The original "field flag +
  optional cross-field" framing wasn't expressive enough.

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

## Item 8 — Per-VM Observable surface for shared-hub VMs (NEW from round-3 implementation)

**Primitive evaluated:** `ComponentVM` / `CompositeVM` — specifically,
their event-emission contract through a shared `MessageHub`.

**aws-tui use case:** PR #103's flicker bug — four sibling EMR VMs
(`JobRunsVM`, `JobRunDetailVM`, `JobRunLogsVM`, `ApplicationsVM`)
share a single `MessageHub` AND all emit `state` / `selected_id`
PropertyChangedMessages on it. View widgets had to
`if msg.sender_object is not self._vm: return` to ignore cross-VM
events.

**What blocked out-of-the-box adoption:**

VMx VMs emit through the shared hub by convention. There is no
per-instance Observable on the VM that View subscribers can bind to
exclusively. The View ends up coupling to the hub + a sender filter,
which is both extra coupling (the View knows the hub exists) AND
extra defensive code (the sender check).

**What aws-tui did instead:** Added a `Subject[str]` field
`_on_property_changed` to each migrated VM. Every emission goes
through a per-VM `_notify(prop)` helper that fires BOTH the hub
PropertyChangedMessage AND the per-VM Subject — the hub side
keeps back-compat with other listeners; the per-VM side gives
View widgets a sender_object-free subscription. **All four EMR VMs
(ApplicationsVM, JobRunsVM, JobRunDetailVM, JobRunLogsVM) now
have the surface, with consistent `_notify(prop)` helpers.**

**Round-3 implementation evidence:**

VM-side (all four EMR VMs expose the surface with a uniform
`_notify(prop)` helper as the sole hub.send + Subject.on_next
emission site):
- `src/aws_tui/vm/emr_serverless/applications_vm.py` —
  `_on_property_changed: Subject[str]` + `_notify` helper; four
  emission sites (state, applications, selected_id × 2).
- `src/aws_tui/vm/emr_serverless/job_runs_vm.py` —
  same shape; `_notify` consolidates 10 emission sites into one.
- `src/aws_tui/vm/emr_serverless/job_run_detail_vm.py` —
  `_notify` helper consolidates 3 emission sites (detail × 2,
  state).
- `src/aws_tui/vm/emr_serverless/job_run_logs_vm.py` —
  added in commit `ac3ad81`; `_notify` helper consolidates 9
  emission sites including the `_notify_all` loop.

View consumers (all four EMR view widgets — every `sender_object`
guard retired):
- `ui/widgets/emr_serverless/application_picker.py:138-146` —
  `_on_vm_property_changed(prop: str)` replaces `_on_hub_message`.
- `ui/widgets/emr_serverless/job_runs_pane.py:on_mount` — same
  migration.
- `ui/widgets/emr_serverless/job_run_detail_pane.py:71-95` —
  added in commit `ac3ad81`.
- `ui/widgets/emr_serverless/job_run_logs_pane.py:155-167,219-230`
  — added in commit `ac3ad81`.

Integration tests:
- `tests/integration/test_bug_train_acceptance.py:101-159` —
  pin the cross-VM isolation contract structurally; assert that
  events on `vm1.on_property_changed` do NOT echo on
  `vm2.on_property_changed` when both share a hub.

**PR #103 acceptance is fully closed — zero sender_object guards
remain in the EMR view layer** (commit `ac3ad81`).

**Per-VM Observable surface is now consumed in every aws-tui VM
that has sibling-VM-on-shared-hub collisions.** Item 8 is no
longer a "consider for vNext" — it's a contract aws-tui depends
on. Promoting `on_property_changed: Observable[str]` to
`_ComponentVMBase` would let aws-tui delete ~15 LOC of duplicated
Subject-creation + dispose boilerplate across the four migrated
VMs (`applications_vm.py`, `job_runs_vm.py`,
`job_run_detail_vm.py`, `job_run_logs_vm.py`).

**Proposed vNext API:** Promote `on_property_changed: Observable[str]`
into the `_ComponentVMBase` (or `ComponentVMBase`) contract directly.
Every VMx VM gains it automatically; the hub-broadcast path can
either stay (back-compat) or be deprecated in favour of per-VM
subscriptions.

```python
class _ComponentVMBase:
    @property
    def on_property_changed(self) -> rx.Observable[str]:
        """Hot observable of property names that just changed,
        scoped to THIS VM instance. Subscribers see events for
        THIS VM only — no cross-VM cross-talk."""
        ...
```

This eliminates a recurring boilerplate pattern AND makes the
"per-VM Observable" the natural subscription target for Views.

**Estimated upstream effort:** **Small.** ~50 LOC + migration of
existing PropertyChangedMessage emission sites + tests. The hub
path stays for cross-VM coordination use cases.

**Reference:** §9.bis.9 PR #103 acceptance criterion.

---

## Item 9 — `ScoredFilteredCompositeVM` for rank-by-score filtering (NEW from round-3 implementation)

**Primitive evaluated:** Hypothetical extension of Item 3's
`FilteredCompositeVM`.

**aws-tui use case:** `CommandPaletteVM` filters palette entries by
a fuzzy-match score (substring + leading-char + tight-subsequence,
in priority order). The filter is NOT a boolean predicate — it's a
rank function `(entry, query) -> int | None` where `None` means
"excluded" and lower scores rank higher in the result.

**What blocked out-of-the-box adoption:**

The Item 3 `FilteredCompositeVM` takes `Callable[[VM], bool]` — a
boolean predicate. It returns visible items in source order. For
the palette this doesn't fit: the ordering matters (best-match
first), and the score computation would have to happen twice
(once in predicate, once for sort).

**What aws-tui did instead:** Inlined the score+rank+sort logic
into `CommandPaletteVM._recompute_filtered` at
`src/aws_tui/vm/chrome/command_palette_vm.py:391-408`. The
composite still provides the underlying entry registry +
on_collection_changed; the rank logic is a VM-side @property layer
on top.

**Proposed vNext API:** A sibling primitive
`ScoredFilteredCompositeVM[VM, Q]`:

```python
class ScoredFilteredCompositeVM(Generic[VM, Q]):
    def __init__(
        self,
        source: CompositeVM[VM],
        scorer: Callable[[VM, Q], int | None],
        initial_query: Q,
        *,
        cursor_policy: str = "snap_to_first",
        order: str = "ascending",  # "ascending" = lowest score first
    ) -> None: ...

    @property
    def visible(self) -> tuple[VM, ...]: ...      # ranked by score
    @property
    def current(self) -> VM | None: ...
    @property
    def query(self) -> Q: ...
    @property
    def on_changed(self) -> rx.Observable[None]: ...

    def set_query(self, q: Q) -> None: ...        # re-ranks
    def set_current(self, item: VM | None) -> None: ...
    def move_to_next_visible(self) -> None: ...
    def move_to_previous_visible(self) -> None: ...
    def dispose(self) -> None: ...
```

The scorer is called ONCE per item per query; results cached on the
sort key. Same cursor policies as `FilteredCompositeVM`. Query type
is generic so consumers can pick `str`, `tuple[str, ...]`, or a
domain-specific query object.

**Estimated upstream effort:** **Small (after Item 3).** ~80 LOC
once `FilteredCompositeVM` lands.

**Reference:** `vm/chrome/command_palette_vm.py:_score,
_subsequence_span, _recompute_filtered`.

---

## Item 10 — Slot-discriminator coordinator with modal precedence (NEW from round-3 implementation)

**Primitive evaluated:** Hypothetical. No existing VMx primitive
captures "single source of truth for which UI slot has focus".

**aws-tui use case:** Spec §4.3 / Phase 7: a single VM that owns
the app-wide `focused_slot` discriminator (`NAV_MENU` / `S3_LEFT` /
`S3_RIGHT` / `EMR_RUNS` / ... / `MODAL`). View widgets project
their focus events INTO it, and View renderers subscribe to its
Observable to drive CSS class mutation. Replaces a 10-state
fragmentation (§3.2.bis) of focus + selection state spread across
View widgets and Textual runtime.

**What blocked out-of-the-box adoption:** No primitive existed.

**What aws-tui did instead:** Built
`FocusCoordinatorVM(ComponentVM-composing)` at
`src/aws_tui/vm/chrome/focus_coordinator_vm.py`:

```python
class FocusSlot(StrEnum):
    NAV_MENU, S3_LEFT, S3_RIGHT, EMR_RUNS, EMR_DETAIL, EMR_LOGS,
    SETTINGS, MODAL = ...

class FocusCoordinatorVM:
    def __init__(self, *, hub, dispatcher, initial=FocusSlot.NAV_MENU): ...

    @property
    def focused_slot(self) -> FocusSlot: ...
    @property
    def is_modal(self) -> bool: ...
    @property
    def on_focused_slot_changed(self) -> rx.Observable[FocusSlot]: ...

    def set_focused_slot(self, slot: FocusSlot) -> None: ...
    def modal_open(self) -> None: ...   # saves prior slot
    def modal_close(self) -> None: ...  # restores prior slot
```

16 tests at `tests/unit/vm/chrome/test_focus_coordinator_vm.py`
pin the slot transitions, modal save/restore, and dispose
discipline.

**Wired through the View layer (post-`fd1a5d6` updates):**

- `src/aws_tui/composition.py:104,113` — `AppContext.focus_coordinator`
  field; instantiated and constructed in `build_app_context`.
- `src/aws_tui/ui/widgets/nav_menu.py:on_mount` (`fd1a5d6`) —
  subscribes to `on_focused_slot_changed`; on_focus projects
  `FocusSlot.NAV_MENU`; `_apply_focus_slot_class` is the SOLE
  driver of the Screen's `-nav-active` class. The class-mutation
  responsibility moved from a direct View handler to a
  coordinator subscription.
- `src/aws_tui/ui/widgets/nav_menu.py:_after_cursor_move` (`c449dfe`)
  — arrow-walk no longer calls `call_after_refresh(self.focus)`
  when coordinator is wired (PR #98(2) closure: the destination
  page's `_maybe_focus_*` reads the coordinator's slot and bails
  on the rail-walk gate).
- `src/aws_tui/ui/widgets/nav_menu.py:action_commit` (`c449dfe`)
  — ENTER projects the service's default slot
  (`{"s3": S3_LEFT, "emr-serverless": EMR_RUNS, "settings":
  SETTINGS}`) into the coordinator before the focus dispatch
  (PR #101 closure: data source moved to coordinator; App-level
  dispatcher remains as the View-side projection).
- `src/aws_tui/ui/widgets/emr_serverless/page.py:_maybe_focus_left`
  (`f5ee335`) — reads `focused_slot is NAV_MENU` as the
  authoritative rail-walk indicator (PR #99(a) closure).
- `src/aws_tui/ui/widgets/settings_view.py:_maybe_focus`
  (`f5ee335`) — same coordinator-gated check.

**Service-default-slot router pattern (NEW finding for vNext):**

When NavMenu commits a service via ENTER, it maps the service
id to its default slot through a small dictionary:

```python
service_default_slot: dict[str, FocusSlot] = {
    "s3": FocusSlot.S3_LEFT,
    "emr-serverless": FocusSlot.EMR_RUNS,
    "settings": FocusSlot.SETTINGS,
}
```

The dict has THREE consumers in aws-tui's design space (the three
services). The pattern generalises: **any caller that needs to
project "action X → discriminator value Y" benefits from a
declarative router on the discriminator VM**. Worth including in
the proposed `DiscriminatorVM[E]` as a `route` registry:

```python
class DiscriminatorVM(Generic[E]):
    def register_route(self, key: object, value: E) -> None: ...
    def project_route(self, key: object) -> bool:
        """Set value to the registered route for `key` (or no-op
        if unregistered). Returns True iff a route fired."""
```

§9.bis.9 bug-train consequences: **PR #98(2), PR #99(a),
PR #100(a), PR #101 are all structurally retired** through the
coordinator wiring described above (commits `fd1a5d6`, `9e6a442`,
`f5ee335`, `c449dfe`). PR #98(3) `.-nav-active` literal class
remains in 10 themes as the rendering target; the data driving
it is the coordinator's slot (no longer direct Screen mutation in
the View).

**Proposed vNext API:** Generalise this into a reusable VMx
primitive `DiscriminatorVM[E]` where `E` is a `StrEnum`-like
choice set:

```python
class DiscriminatorVM(Generic[E]):
    def __init__(
        self,
        *,
        choices: type[E],
        initial: E,
        modal_value: E | None = None,  # the precedence override
        hub, dispatcher,
    ) -> None: ...

    @property
    def value(self) -> E: ...
    @property
    def is_modal(self) -> bool: ...  # only when modal_value is set
    @property
    def on_changed(self) -> rx.Observable[E]: ...

    def set(self, value: E) -> None: ...
    def push_modal(self) -> None: ...  # only when modal_value is set
    def pop_modal(self) -> None: ...
```

This isn't focus-specific — any "what's the active mode/slot/route"
discriminator that needs modal-style save/restore semantics could
use it (e.g., theme picker, settings page tabs).

**Estimated upstream effort:** **Small.** ~120 LOC + tests + docs.

**Reference:** spec §4.3, §3.2.bis, §9.bis.9.

---

## Item 11 — `RelayCommand.dispose()` does not gate later `execute()` calls (NEW from round-6 verification)

**Primitive evaluated:** `RelayCommand` / `RelayCommandOf[T]` —
`vmx/commands/relay_command.py` (Builder in
`vmx/commands/builders.py`).

**aws-tui use case:** The chrome VMs (CommandPaletteVM,
CrashVM, ConfirmationVM, …) own multiple RelayCommands and
dispose them all from their own `dispose()` (see e.g.
`command_palette_vm.py:288-302`). The host (View / test
harness / `ContentHostVM.set_content` swap) calls `vm.dispose()`
on tear-down. A later call to a previously-exposed command —
either accidental (a callback that didn't unsubscribe in time) or
intentional (test asserting "post-dispose calls are safe") — is
expected to be a no-op.

**What blocked clean out-of-the-box adoption:** After `dispose()`,
`RelayCommand.execute()` still runs the registered task and the
side effect lands. Discovered by tightening a vacuous test —
`tests/unit/vm/chrome/test_command_palette.py::test_dispose_releases_commands`
in round 6 of the post-refactor verification loop. The original
test asserted nothing; replacing it with `assert vm.is_open ==
prior_is_open` after a post-dispose `open_command.execute()` made
the test FAIL because `is_open` flipped `False → True`. The
implication: any "fire-and-forget" subscriber that survives the VM
through teardown can resurrect VM state long after the VM is
conceptually dead.

**What aws-tui did instead:** Pinned the actual behaviour
("dispose runs cleanly; subsequent execute does not raise") and
documented the surprise in the test docstring so a future vmx
upgrade tightening the contract trips the test. No wrapping; the
risk is small for aws-tui (the test harness tears down before any
late callback can land) but real for any consumer that has a
slower teardown choreography (e.g. a multi-modal dismissal walk).

**Proposed vNext API or behavior:** Make
`RelayCommand.execute()` a no-op after `dispose()`, with two
sub-questions for the design:

1. **Behaviour on disposed `execute()` call:** silent no-op
   (today's intent) OR raise `DisposedError` (loud-fail variant).
   The aws-tui ergonomic preference is **silent**, matching the
   "subsequent execute calls are safe" idiom that the dispose
   docstring on every primitive already implies. A `disposed`
   property on the command would let callers gate manually if they
   prefer the loud-fail discipline.
2. **`can_execute()` after dispose:** should return `False`
   unconditionally. Views that observe `can_execute_changed` to
   enable/disable a button need the final event to be the
   `False`-flip; otherwise the button stays clickable until the
   widget itself is unmounted.

**Estimated upstream effort:** **Trivial.** ~15 LOC + 1 test.
`dispose()` sets `self._disposed = True`; `execute()`'s first line
becomes `if self._disposed: return`; `can_execute()` returns
`False` when disposed.

**Reference:** test failure in
`tests/unit/vm/chrome/test_command_palette.py::test_dispose_releases_commands`
on commit `24f01da` of `refactor/vmx-toolkit-adoption`.

---

## Summary table

| # | Primitive | Issue | aws-tui workaround | vNext ask effort |
|---|---|---|---|---|
| 1 | `PagedComposition` | index-based; can't model nextToken | dropped; `CompositeVM` + manual `next_token` + `load_more` (+ dedup-on-set, c114b36) | Small–medium (`TokenPagedComposition`) |
| 2 | `PagedComposition` × `CompositeVM` | observable shapes don't compose | dropped via Item 1 (no adapter built) | Small (extend `_try_subscribe_source`) |
| 3 | `CompositeVM` derived/filtered view | no primitive ships | **built `FilteredCompositeVM[VM]` mini-primitive** in `vm/_composition/` (23 tests); consumed by PaneVM | Medium (`FilteredCompositeVM`) |
| 4 | `FormVM` validators | no declarative API | **built `ValidatingFormVM[TM]` mini-primitive** + consumer `S3ConnectionFormVM` (25 tests total); **`S3ConnectionFormVM` wired into `connection_form.py` widget** as the live edit-flow validator (commit `0b0fe41`) | Medium (declarative validators + `errors` map) |
| 5 | `IDialogService` | closed contract; no VM-backed modal | all 4 modals verified compliant by composing `ComponentVM` + own async `ask()` API; round-3 compliance tests pin the contract | Medium (`present()` + `ModalVM` protocol; optional `MultiStepFormVM`, `ChoiceVM`) |
| 6 | `ServicedObservableCollection` | docs say ownership; source does not | corrected aws-tui spec; kept manual `finally: dispose` in TransfersVM | Trivial (rename / docstring); small (owned variant) |
| 7 | `HierarchicalVM` | no cache-invalidation contract | chose `CompositeVM` instead | Small (`invalidate*` + TTL + docs) |
| 8 | Per-VM Observable surface | shared `MessageHub` requires `sender_object` filtering in Views | **built per-VM `Subject[str]` + `on_property_changed` Observable** on ALL FOUR EMR VMs; consumed by ALL FOUR EMR view widgets — zero `sender_object` guards remain in the EMR layer | Small (promote `on_property_changed` to `_ComponentVMBase`) |
| 9 | `ScoredFilteredCompositeVM` | `FilteredCompositeVM` is boolean only | inlined score+rank+sort on `CommandPaletteVM` | Small after Item 3 |
| 10 | Slot-discriminator coordinator | no primitive | **built `FocusCoordinatorVM` + `FocusSlot` StrEnum** (16 tests); wired through composition + NavMenu + EmrServerlessPage + SettingsView + service-default-slot router on NavMenu ENTER. PR #98(2)/#99(a)/#100(a)/#101 structurally retired through it. | Small (`DiscriminatorVM[E]` generic with route registry) |
| 11 | `RelayCommand.dispose()` | no use-after-dispose gate; `execute()` post-dispose still runs the task | pinned actual behaviour in `test_dispose_releases_commands`; documented surprise so a vmx upgrade tightening the contract trips the test | Trivial (set `_disposed` flag; gate `execute` + `can_execute`) |

---

## Suggested prioritisation for VMx vNext

If only a subset can ship — ordered by *consumer impact across
likely future projects*, not by aws-tui's own urgency (aws-tui has
already composed each one):

1. **Item 6 docs fix** — trivial; closes a real bug
   (paraphrase-vs-source confusion). Ship in next patch.
2. **Item 8 per-VM `on_property_changed`** — small primitive change,
   broad reach. Every consumer with sibling VMs on a shared hub
   benefits.
3. **Item 1 `TokenPagedComposition`** — unblocks every AWS-API
   consumer; aws-tui would adopt immediately and retire its own
   `next_token` field + dedup-on-set logic.
4. **Item 4 declarative validators** — `ValidatingFormVM`-shaped
   API. Promote `errors: dict[str, str]` + auto-gated approve
   directly. aws-tui's `S3ConnectionFormVM` would slim to a few
   `add_field_validator` calls without the wrapper.
5. **Item 3 `FilteredCompositeVM`** — aws-tui's implementation is
   ready to upstream as a starting point. The cursor-policy contract
   and the `set_predicate` identity-equality question are the only
   open design points.
6. **Item 10 `DiscriminatorVM[E]`** — small, generic, useful beyond
   focus coordination.
7. **Item 5 `present()` + `ModalVM`** — opens the closed-set
   contract without breaking it; lets bespoke modals re-platform.
8. **Item 9 `ScoredFilteredCompositeVM`** — only matters if a
   second scored-filter consumer appears; aws-tui's palette is the
   only one today.
9. **Item 7 `HierarchicalVM` invalidation** — only matters if a
   tree-view consumer materialises.
10. **Item 2 PagedComposition-Composite bridge** — automatically
    obsolete if Item 1 ships, since aws-tui doesn't pair the two
    any more.

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
- Every "aws-tui's workaround" line above NOW describes the
  **landed implementation** on the refactor branch
  `refactor/vmx-toolkit-adoption` (PR #109 against
  `thekaveh/aws-tui`). File:line citations point to the actual code
  this report was authored alongside. If a citation drifts after
  the PR merges (renames, follow-up commits), the patterns will
  still be valid even if the line numbers shift.
- Items 3, 4, 8, 10 are **already running in production-equivalent
  code** at the time of this report. The proposed vNext APIs above
  are aws-tui's actual implementations cleaned up for upstream
  consumption.
- The acknowledgement that this is a single-consumer report STILL
  applies. The aws-tui patterns may not generalise; pressure-test
  each item against a hypothetical second consumer (e.g. an Avalonia
  desktop app, a NiceGUI web app) before committing to the API
  surface.
