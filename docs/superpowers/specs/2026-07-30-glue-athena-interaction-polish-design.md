# 1. Glue and Athena interaction polish design

**Status:** Accepted in brainstorming on 2026-07-30. This document refines the
Glue and Athena first-release design after hands-on review of the merged
services. It is the source of truth for the interaction-polish implementation
plan.

## 1.1. Decision summary

- Use the selected "bordered controls plus explicit focus" direction.
- Present AWS source and service context controls as first-class bordered
  fields, following the pane convention already used by S3 and EMR Serverless.
- Make every visible selector keyboard reachable in a deterministic forward and
  reverse focus ring.
- Keep `Shift+S` as the shared source-cycle command and add named commands for
  opening each Glue and Athena selector.
- Make the active AWS source directly focusable and selectable instead of
  rendering it as passive text.
- Preserve Glue as a metadata-discovery and operational-inspection service.
- Add both direct and clipboard-based Glue-to-Athena table handoffs.
- Widen and center the services rail so every supported service label has
  comfortable horizontal space.
- Use VMx 3.1.0's most specialized public abstraction for every changed VM
  responsibility. "Uses VMx" is not sufficient if a more specialized VMx
  primitive fits.
- Track VM/view implementation LOC, test LOC, and coverage changes.

## 1.2. Product outcomes

After this work:

1. A user can visually identify every Glue and Athena selector without guessing
   that an unlabelled top-row value is interactive.
2. `Tab` and `Shift+Tab` traverse the complete current service focus ring in
   stable, documented order.
3. A focused selector opens with `Enter` or `Space`; arrow keys move through its
   values; `Enter` commits; `Escape` cancels.
4. Every selector also has a named command that is discoverable from the command
   palette and represented in the keymap/hint system.
5. Source switching consistently rebuilds dependent service context under the
   selected profile and region.
6. A table discovered in Glue can either open as a bounded Athena starter query
   or be copied as a correctly quoted reference and inserted at the Athena query
   cursor.
7. Glue and Athena visually belong to the same application as S3 and EMR.

# 2. Visual and interaction model

## 2.1. Shared bordered selector field

Add one reusable Textual widget, `ContextPicker`, for source and context
selection. It owns presentation only:

- persistent field label;
- current display value;
- dropdown indicator;
- focus, open, disabled, loading, warning, and error styling;
- an inline-expanding Textual `OptionList`;
- an optional adjacent load-more button;
- a stable minimum width and height.

The picker reuses the proven trigger-plus-inline-list behavior of EMR's
application picker. It does not depend on Textual's detached `Select` overlay,
which can be clipped or visually disconnected from its field. The widget does
not own AWS state, selection policy, profile resolution, or network work. Those
remain in VMs and application actions.

The field uses a visible solid border at rest and the theme accent while focused
or open. Its label is presented as a border title, matching established content
panes. Opening the picker expands its row in place, keeping the list inside the
active pane and avoiding overlay clipping.

## 2.2. Service context panes

Glue and Athena each gain an enclosing `AWS context` pane:

- Glue always shows a focusable `Source` field.
- Glue Jobs also shows `Run state`.
- Glue Crawlers also shows `Crawler state`.
- Athena shows `Source`, `Workgroup`, `Catalog`, and `Database`.
- Athena pagination buttons remain adjacent to the field they extend and are
  included in the focus ring only while enabled.

Controls that are loading, forbidden, empty, or failed remain visible. Their
state is communicated by styling, field value, and tooltip rather than making
the control disappear.

## 2.3. Content borders

Glue and Athena must observe the same enclosing-border convention as S3 and
EMR:

- service context;
- resource lists;
- detail panes;
- metadata panes;
- query editor;
- query status/detail;
- query results;
- history and saved-query panes.

Nested cards are not introduced. Borders describe focusable operational panes
and related tool surfaces only.

## 2.4. Navigation rail

The navigation rail must:

- accommodate `Athena` with at least one cell of left and right breathing room;
- center every service label;
- keep row width stable across selected, focused, and unfocused states;
- preserve settings-row alignment;
- remain usable at the repository's minimum supported terminal size.

# 3. Focus and command behavior

## 3.1. Explicit focus rings

Glue and Athena must not delegate service-level traversal to Textual's raw DOM
order. Each page exposes an ordered tuple of currently valid focus targets and
cycles it directly.

Forward and reverse traversal are exact inverses. Hidden controls and disabled
load-more buttons are omitted. The current target is retained after refresh
when it remains valid; otherwise focus moves to the nearest valid target in the
same ring.

The Glue order is:

1. source;
2. active view's filter selectors, if any;
3. tab strip as one logical target;
4. active view's primary list;
5. active view's secondary list or detail pane;
6. active view's enabled buttons and metadata panes;
7. navigation rail.

The Athena order is:

1. source;
2. workgroup;
3. workgroup load-more when enabled;
4. catalog;
5. catalog load-more when enabled;
6. database;
7. database load-more when enabled;
8. tab strip as one logical target;
9. active view's editor/list/table;
10. active view's enabled buttons and detail panes;
11. navigation rail.

Within a focused tab strip, left/right selects the neighboring tab and
`Enter`/`Space` activates it. Numeric view shortcuts remain available.

## 3.2. VMx focus ownership

`FocusCoordinatorVM` remains the application-wide source of focus identity and
continues to compose VMx `DiscriminatorVM[FocusSlot]`.

Extend the existing `FocusSlot` vocabulary with logical Glue and Athena slots
and keep the existing app-wide `DiscriminatorVM[FocusSlot]` facade. Logical
slots map to the active view's concrete widgets, so a stable slot such as
`GLUE_PRIMARY` can represent Catalog databases, Jobs job list, or Crawlers
crawler list without multiplying slots for every DOM node. Do not add a
service-local discriminator or any parallel plain-string or view-owned focus
state.

Textual remains authoritative for the runtime widget currently holding focus.
The view bridges typed VM focus identity to concrete widget IDs.

## 3.3. Named commands

The following action IDs must exist in `KeymapStore`, `ActionRegistry`, command
palette entries, binding descriptions, and hint labels:

- `app.swap_source`
- `glue.choose_run_state`
- `glue.choose_crawler_state`
- `glue.copy_table_ref`
- `glue.query_in_athena`
- `athena.choose_workgroup`
- `athena.choose_catalog`
- `athena.choose_database`
- `athena.insert_table_ref`

`app.swap_source` retains its existing `Shift+S` binding. Existing service and
view shortcuts retain their bindings. New selector actions must not consume a
printable key while the Athena editor has focus unless Textual gives the editor
priority.

Selector commands focus and open the target field. They do not silently cycle
values because opening the list is clearer for users with many profiles,
catalogs, or databases.

The bottom hint legend remains concise and context-sensitive. It exposes
`switch source`, the active selector command where useful, `copy table` in Glue,
and `insert table` in Athena. The command palette remains the complete
discoverability surface.

# 4. Profile-aware context

## 4.1. Source selection

`ServiceSourceHeader` is replaced or refactored into the shared focusable source
selector field. It displays connection name, profile label, and region without
requiring the user to infer that `Shift+S` is the only way to change it.

Opening the source field presents all AWS connections supported by the active
service. Selecting one dispatches through the existing source-switch
transaction. The existing transaction remains authoritative for cancellation,
page reconstruction, credential state, and stale-data prevention.

## 4.2. Dependent context

Athena context follows resolver order:

1. restore the prior value for the selected connection when still valid;
2. use the service-defined default or AWS primary value when valid;
3. select the first accessible value;
4. show an explicit empty/forbidden/error state when none is usable.

Changing workgroup reloads any workgroup-scoped state. Changing catalog reloads
databases. Changing database updates query context. Old-profile or old-parent
values are cleared before new asynchronous results become visible.

Glue filters are restored per connection and per view only when still valid.

# 5. Glue-to-Athena table transfer

## 5.1. Glue's role

Glue is the metadata discovery and inspection surface for:

- catalogs, databases, tables, columns, partitions, formats, and S3 locations;
- jobs and job runs;
- crawlers and crawler state;
- Iceberg metadata and snapshot inspection.

The catalog table selection exposes both:

- `Query table in Athena`, which navigates to Athena and prepares the existing
  bounded read-only starter query;
- `Copy table reference`, which does not navigate.

## 5.2. Typed application clipboard

Add an application-owned clipboard VM with one immutable payload:

```python
@dataclass(frozen=True, slots=True)
class CopiedTableReference:
    table_ref: TableRef
    sql_identifier: str
```

The clipboard VM is a thin facade over
`ComponentVMOf[CopiedTableReference | None]`, the most specialized VMx modeled
leaf for one replaceable immutable payload. It:

- exposes `copied_table` and an observable change signal;
- exposes a VMx `RelayCommandOf[TableRef]` copy command;
- uses the canonical SQL identifier helper;
- retains connection name and region through `TableRef`;
- contains no OS-specific clipboard calls.

## 5.3. SQL quoting

Promote the existing private identifier quoting behavior into one public domain
function:

```python
def quote_athena_table_ref(ref: TableRef) -> str:
    ...
```

Both `select_starter_sql()` and copied references use this function so direct
handoff and clipboard insertion cannot disagree about quoting.

## 5.4. System clipboard bridge

The Textual application calls `App.copy_to_clipboard(text)` after the typed VM
clipboard accepts a table reference. No subprocess or clipboard dependency is
introduced. OSC 52 support varies by terminal, so system clipboard delivery is
best effort and the typed in-app clipboard remains authoritative.

## 5.5. Athena insertion

`AthenaQueryVM` owns SQL text but not Textual cursor coordinates. Therefore:

- the app clipboard provides the quoted identifier;
- `AthenaQueryView.insert_table_reference(text)` inserts at the editor cursor or
  replaces its active selection using Textual's public `TextArea` API;
- the resulting editor text is immediately projected through
  `AthenaQueryVM.set_sql()`;
- if the query view is not active, the command selects it before insertion;
- if no table has been copied, the command is disabled and produces a concise
  notification when invoked indirectly.

If the copied `TableRef` connection or region differs from the active Athena
source, insertion is refused, the clipboard and editor remain unchanged, and a
visible warning names the copied and active sources. The command never silently
switches profiles because rebuilding Athena could discard unrelated editor
state. Direct `Query table in Athena` remains the source-preserving workflow
when automatic navigation is desired.

# 6. VMx abstraction-selection standard

## 6.1. Mandatory selection process

Before changing or adding each VM responsibility:

1. Define the required behavior without reference to the current implementation.
2. Inspect the complete relevant VMx 3.1.0 public hierarchy.
3. List every credible primitive.
4. Choose the most specialized primitive whose semantics match.
5. Record why more generic and neighboring primitives were rejected.
6. Compose the chosen primitive inside a thin aws-tui facade only when the
   application contract adds behavior VMx does not own.
7. Do not access VMx private state.

## 6.2. Initial candidate matrix

| Responsibility | Preferred VMx candidate | Candidates requiring comparison |
|---|---|---|
| Active focus identity | `DiscriminatorVM[TKey]` | app-wide extension versus service-local discriminator |
| Selector option ownership | `CompositeVMOf[T]` | `GroupVM`, `ObservableList`, plain immutable tuple when no child lifecycle exists |
| Filtered selector options | `FilteredCompositeVM[T]` | unfiltered composite when filtering is only visual |
| Active service sub-view | `DiscriminatorVM[GlueView/AthenaView]` | current facade-owned literal state |
| Async AWS selection work | `AsyncRelayCommand` | VMx 3.1.0 has no parameterized async relay command, so arbitrary-value selection remains a thin facade over the existing lifecycle-safe coroutine |
| Synchronous copy/insert state | `RelayCommand` / `RelayCommandOf` | app callbacks |
| Dependent enabled/visible state | `DerivedProperty` | manual booleans and duplicate notifications |
| Child VM ownership | `AggregateVMn` or `GroupVM` | thin facade with explicit lifecycle when dynamic child counts make an aggregate unsuitable |
| Paginated AWS options/results | `TokenPagedComposition` | existing bespoke token accumulation |
| View subscriptions | `when_property_changed` | raw message filtering |
| User confirmation/modal choice | `DialogService` and `ModalVM` | only when the flow genuinely needs confirmation |

The matrix is a starting point, not permission to force an abstraction. UI
borders, Textual focus calls, cursor insertion, OS clipboard access, and AWS SDK
calls remain outside VMx where the framework has no matching responsibility.

## 6.3. Evidence ledger

Each implementation task records:

| Field | Required value |
|---|---|
| Change ID | Stable name |
| Required behavior | Framework-independent contract |
| VMx candidates | All credible public candidates |
| Selected primitive | Exact VMx type and public APIs |
| Rejected candidates | Concise semantic reason |
| Bespoke code retained | Code and why VMx does not own it |
| VM files | Added, changed, or deleted |
| View files | Added, changed, or deleted |
| Tests | Added, rewritten, or deleted |
| VM LOC | Deleted, added, saved |
| View LOC | Deleted, added, saved |
| Test LOC | Deleted, added, delta |
| Coverage | Before, after, and delta |

### `vmx31-glue-athena-focus-rings`

| Field | Evidence |
|---|---|
| Change ID | `vmx31-glue-athena-focus-rings` |
| Required behavior | Maintain one typed app-wide focus identity while callers supply deterministic, availability-filtered Glue and Athena rings and views project the selected logical slot to Textual widgets. |
| VMx candidates | `DiscriminatorVM[FocusSlot]`, `FilteredCompositeVM`, `GroupVM`, and `HierarchicalVM`. |
| Selected primitive | Existing `DiscriminatorVM[FocusSlot]`, through public `active_key`, `active_changed`, `is_active()`, and `set_active_key()` APIs behind `FocusCoordinatorVM.cycle_focus_ring()`. |
| Rejected candidates | `FilteredCompositeVM` models a filtered component cursor rather than app-to-widget focus identity; `GroupVM` owns component membership without an active discriminator; `HierarchicalVM` models recursive resource ownership, not a flat dynamic focus ring. A service-local discriminator was also rejected because it would create a second focus authority. |
| Bespoke code retained | Glue and Athena views build ordered tuples of currently valid concrete widgets and call Textual `App.set_focus()` for runtime projection. VMx does not own Textual DOM availability, visibility, disabled state, or widget focus. |
| VM files | Changed `src/aws_tui/vm/chrome/focus_coordinator_vm.py`. |
| View files | Changed `src/aws_tui/app.py`, Glue/Athena page and filter views, and the shared service tab strip. |
| Tests | Expanded coordinator composition/ring tests, complete forward/reverse page-ring tests for all seven service views, hidden/disabled omission coverage, and explicit Space activation regression coverage. |
| VM LOC | 39 added, 0 deleted, net +39. |
| View LOC | 306 added, 135 deleted, net +171; 135 lines of raw tab/focus traversal presentation were removed or replaced. |
| Test LOC | 333 added, 6 deleted, net +327. |
| Coverage | The specified focused suite increased from 50 to 64 tests and from 32.10% to 32.56% whole-package coverage, +0.45 percentage points. The changed focus coordinator remained at 95%; Glue page coverage increased from 63% to 66% and Athena page coverage from 57% to 60%. |

### `vmx31-glue-athena-focus-rings-review-fix`

| Field | Evidence |
|---|---|
| Change ID | `vmx31-glue-athena-focus-rings-review-fix` |
| Required behavior | Keep users in one deterministic typed ring that reaches the real navigation rail, includes every visible and enabled Glue/Athena control, synchronizes direct Textual focus back to VMx before cycling, and chooses the nearest surviving slot after refresh. |
| VMx candidates | Existing `DiscriminatorVM[FocusSlot]`, a second service-local `DiscriminatorVM`, `FilteredCompositeVM`, and bespoke widget-index state. |
| Selected primitive | Extended the existing app-wide `DiscriminatorVM[FocusSlot]` facade with concrete control identities and `FocusCoordinatorVM.select_nearest_focus_slot()`. `active_key` remains the only logical focus identity; pages supply current availability and stable order. |
| Rejected candidates | A service-local discriminator or widget index would create parallel focus authority. `FilteredCompositeVM` models a component collection cursor rather than Textual focus. Directly trusting `App.focused` during later cycles would leave VMx stale after pointer or programmatic focus changes. |
| Bespoke code retained | Pages map typed slots to mounted widgets, filter Textual visibility/enabled/focusability, observe child refreshes, synchronize `DescendantFocus`, and call `App.set_focus()`. `GlueIcebergView` exposes its current enabled focus targets. These are DOM/runtime concerns outside VMx. |
| VM files | Changed `src/aws_tui/vm/chrome/focus_coordinator_vm.py`. |
| View files | Changed `src/aws_tui/app.py`, the Glue/Athena pages, Glue detail and Iceberg views, and the shared service tab strip. |
| Tests | Expanded coordinator, Glue, Athena, Iceberg, shared-tab, production-router, and real-nav coverage. Tests prove enabled transient controls enter the ring, direct focus controls the next transition, Nav is reachable in both directions, and unavailable slots fall back by proximity. |
| VM LOC | 59 added, 0 deleted, net +59. |
| View LOC | 290 added, 40 deleted, net +250. |
| Test LOC | 428 added, 19 deleted, net +409. |
| Coverage | Review RED run: 20 failed and 53 passed for the intended missing behavior; the final context-refresh regression also failed RED in isolation. Final combined unit and production integration suite: 112 passed. |

# 7. Testing and acceptance

## 7.1. Test-first requirement

Every behavior change follows red-green-refactor. Production code is not added
until a focused test has failed for the intended missing behavior.

## 7.2. Required coverage

- Shared selector field rendering and focused/open/disabled/error states.
- Complete forward and reverse Glue focus rings for Catalog, Jobs, and Crawlers.
- Complete forward and reverse Athena focus rings for Query, History, Results,
  and Saved.
- Selector keyboard opening, navigation, commit, and cancellation.
- Named action registration, binding resolution, palette visibility, and hint
  labels.
- Profile switching and dependent selector invalidation/restoration.
- Canonical table-reference quoting.
- Typed clipboard replacement and change notification.
- Best-effort system clipboard success and failure.
- Glue copy command enabled state.
- Athena cursor insertion and active-selection replacement.
- Empty clipboard and source-mismatch behavior.
- Existing direct Glue-to-Athena query handoff.
- Navigation width and centered labels.
- Glue/Athena snapshots at narrow and standard terminal sizes.
- S3, EMR, theme, and shared-chrome regressions.
- VMx composition-shape tests for every selected VMx primitive.

## 7.3. Verification commands

At minimum:

```bash
uv run pytest tests/unit/vm/test_vmx_smoke.py -q
uv run pytest tests/unit/vm/glue tests/unit/vm/athena tests/unit/vm/chrome -q
uv run pytest tests/unit/ui/glue tests/unit/ui/athena tests/unit/ui/test_nav_menu.py -q
uv run pytest tests/integration/test_glue_iceberg_lifecycle.py -q
uv run mypy
uv run ruff check
uv run pytest tests/unit tests/integration \
  --cov=aws_tui --cov-report=term-missing --cov-report=xml
uv run pytest tests/snapshot -q
```

Any repository-provided snapshot command that updates expected SVGs must be
followed by visual inspection of the affected artifacts.

## 7.4. Completion criteria

- All outcomes in section 1.2 are demonstrated by tests.
- No selector shown on screen is unreachable by keyboard.
- No Glue/Athena service-level focus traversal depends on raw DOM order.
- Every switchable value has a named command.
- Glue table copy works through the in-app clipboard even when system clipboard
  integration is unavailable.
- Direct Glue-to-Athena handoff remains working and source-preserving.
- The service rail comfortably centers all service labels.
- VMx candidate decisions and LOC/coverage metrics are recorded.
- Full required verification passes.
- The feature branch is merged to `develop` by pull request; `main` remains
  unchanged.
