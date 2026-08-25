# 1. Overlay pickers, one-line commands, and Glue-to-Athena handoffs design

**Status:** Design approved on 2026-08-24; written specification awaiting final
user review.

This document is the source of truth for the interaction and presentation work
requested after hands-on review of Glue and Athena on `develop`. It builds on the
approved Glue/Athena interaction and segmented-tab designs without changing their
view order, typed focus order, AWS resolver order, or read-only operating model.

## 1.1. Problems and evidence

The current UI has four related shortcomings.

First, `ContextPicker` gives every collapsed selector an accent-colored border
even while it is idle. When focus is inside the Iceberg metadata pane, the idle
AWS source selector therefore looks focused too. The screenshot reads as two
simultaneous selections even though `FocusCoordinatorVM` has only one focused
slot.

Second, the custom selector widgets intentionally expand in normal document
flow. `ContextPicker.-open` changes the picker to `height: auto`, while EMR's
`ApplicationPicker` reveals an in-flow `OptionList` and lets its parent grow.
Opening either selector can move or resize surrounding panes. Closing it requires
another layout pass and can leave visually stale geometry. Textual 0.89.1 already
uses a screen overlay for its native `SelectOverlay`, proving that the desired
non-reflow behavior is supported by the installed UI framework.

Third, `HintLegend` uses an `ItemGrid` whose cells have a 22-column minimum.
Athena's command set therefore wraps at terminal widths where terse,
content-sized commands would fit on one line. The resulting two-row footer is
visually heavy and takes space from the operational surface. Existing hint chips
also have no explanatory tooltip.

Fourth, both Glue-to-Athena handoffs already exist but are exposed
inconsistently:

- `glue.query_in_athena` is registered and available in the command palette, but
  has no default shortcut and no bottom command hint.
- `glue.time_travel_in_athena` is bound to `V`, but is omitted from Glue's
  `HintLegendVM` service actions.
- The Iceberg arrow button invokes the VM path directly while keyboard and
  palette activation use the registered app action, leaving behavior, telemetry,
  advisory feedback, and end-to-end coverage split across entry points.
- Focused VM and widget tests pass, but there is no full-app regression test that
  clicks the real Iceberg button and proves that the resulting Athena editor
  contains the expected snapshot query.

## 1.2. Goals

The change must:

1. make all actual inline dropdowns float above the layout without resizing it;
2. make resting, focused, open, warning, error, loading, and disabled selector
   states visually unambiguous;
3. keep the Commands pane to exactly one content line at every supported width;
4. replace wasteful fixed-width command cells with terse, tightly spaced hints;
5. give every visible command a detailed tooltip that explains both the shortcut
   and the complete effect of invoking it;
6. expose direct, discoverable Glue commands for opening a table or selected
   Iceberg snapshot in Athena;
7. converge button, keyboard, and palette activation on one application action
   and one typed navigation path; and
8. preserve the existing MVVM, VMx, source-resolution, and read-only safety
   boundaries.

## 1.3. Scope and terminology

For this design, an **inline dropdown** is a compact trigger that temporarily
reveals a set of choices. The affected widgets are:

- `ContextPicker`, including Glue and Athena filters and every
  `ServiceSourceHeader` source picker; and
- EMR Serverless `ApplicationPicker`.

Ordinary `OptionList` resource panes, Iceberg metadata tables, the theme-picker
modal, the command palette, and other modal screens are not dropdowns and do not
adopt this behavior.

The handoff work affects Glue Catalog, Glue Iceberg metadata, Athena Query, the
app action registry, the keymap, and the command legend. It does not add an AWS
write operation.

## 1.4. Overlay dropdown contract

### 1.4.1. Stable collapsed geometry

An inline dropdown's trigger retains a fixed compact footprint while closed and
open. For the existing context controls, that footprint remains three terminal
rows including the titled border. Revealing an option list must not change the
trigger's `region`, its parent's `region`, any sibling's `region`, or any service
content column's `region`.

The current `height: auto` expansion behavior is removed. `ContextPicker` and
`ApplicationPicker` keep their specialized presentation and state handling; this
change does not replace them wholesale with Textual `Select`.

### 1.4.2. Screen overlay behavior

The option list uses Textual's proven screen-overlay mechanism:

- it is anchored to the trigger and uses the trigger's available width;
- it is rendered in a dedicated dropdown layer above normal service content;
- it is constrained inside the terminal viewport;
- it uses a bounded maximum height and scrolls internally when necessary;
- it may cover content while open but never displaces that content; and
- it renders below modal screens, the command palette, and notification chrome.

Opening near the right or bottom edge must remain inside the viewport. Closing
must reveal the exact pre-open layout, with no cleanup resize needed.

### 1.4.3. Open and close behavior

Only one inline dropdown may be open at a time. Opening a second picker closes
the first before revealing the second overlay.

A picker closes when the user:

- commits a choice;
- presses `Escape`;
- clicks outside the trigger and overlay;
- advances or reverses the service focus ring; or
- navigates to another service or view that does not retain that picker.

Commit preserves the existing service transaction and focuses the trigger when
the owning page remains mounted. Cancel restores the committed value and returns
focus to the trigger. A source switch or service rebuild may unmount the trigger;
in that case no stale refocus callback may target the old widget.

Mouse and keyboard opening remain equivalent. Existing `Enter`, `Space`, arrow,
commit, cancel, Tab, Shift+Tab, and direct picker-command behavior remains intact.

### 1.4.4. Focus and semantic-state precedence

Selectors use the following visual precedence:

1. error and warning state;
2. disabled or loading state;
3. open state;
4. keyboard focus or focus within the overlay;
5. idle state.

An idle selector uses the subdued pane-rule border and normal title treatment.
The accent border appears only when the trigger owns focus, focus is within its
open overlay, or the picker is open. Warning and error colors are never replaced
by the ordinary accent.

`ServiceSourceHeader` remains the logical source focus target. Its nested picker
does not become an additional service Tab stop. Focus moving into the option
overlay keeps the source control visibly active through `:focus-within`/open
styling, then restores the idle border after focus leaves.

This state model ensures that when Iceberg metadata owns focus, AWS source is
visibly idle rather than appearing selected at the same time.

## 1.5. One-line Commands contract

### 1.5.1. Geometry and packing

`HintLegend` remains a framed Commands pane with one inner content row. Its
height never grows because of command count.

The regular/minimum-width grid is removed. Each command consumes only the width
of its rendered key, one separating space, and its compact label. Adjacent chips
use a single-cell separator or equivalent one-cell gap. There are no equal-width
tracks and no large centering margins around individual commands.

Commands must never wrap, overlap, clip into the border, or create horizontal
scrolling. Width fitting runs when:

- the terminal is resized;
- the active service changes;
- a command's enabled state or label changes; or
- the visible action set changes.

Hover, focus elsewhere, and tooltip display must not recompute or shift the row.

### 1.5.2. Compact labels

Labels are short operational cues rather than descriptions. Representative
Athena labels are:

```text
[1] query | [2] history | [3] results | [4] saved | [W] group |
[C] catalog | [D] database | [i] table | [Ctrl+↵] run | [Esc] stop |
[l] more | [r] refresh | [S] source | [t] themes | [T] next theme |
[?] help | [q] quit
```

The rendered UI uses the repository's approved subtle separator treatment rather
than the ASCII `|` shown above for specification readability.

Representative Glue additions are:

```text
[Q] Athena | [V] snapshot
```

The displayed capital letters mean the existing Textual convention of
`Shift + letter`. Tooltips spell that out; users are not expected to infer it
from case alone.

### 1.5.3. Detailed tooltips

Every visible command chip has a tooltip. A tooltip includes, in this order:

1. canonical shortcut notation, such as `Shortcut: Shift + V`,
   `Shortcut: Control + Enter`, or `Shortcut: Escape`;
2. a complete sentence naming the action;
3. important navigation or mutation effects;
4. whether an AWS call or query execution occurs; and
5. any current prerequisite or disabled reason.

For example, the snapshot tooltip explains that it opens the selected Iceberg
snapshot in Athena, prefills `FOR VERSION AS OF` SQL, does not execute the query,
and requires a visible selected snapshot row.

Tooltips are supplemental mouse affordances. Hint chips remain non-focusable and
do not alter the service Tab order. Keyboard users retain the Help surface and
service-scoped command palette for full descriptions.

`HintAction` gains explicit compact-label and tooltip/help metadata. The metadata
is resolved by `HintLegendVM`; the Textual widget owns only rendering and
geometry-based fitting.

### 1.5.4. Narrow-width fitting and overflow

At the repository's representative wide viewport, all applicable Athena and
Glue commands must fit in one row using the compact labels and tight spacing.

At narrower widths, hints are removed by explicit priority until the row fits.
Removal never disables the shortcut or removes the palette command. The fitting
order is deterministic:

1. retain the actions most relevant to the active operational context, including
   execute/cancel and Glue-to-Athena handoffs when applicable;
2. retain source switching and quit;
3. retain `[:] more` whenever any commands are hidden;
4. remove view-navigation hints already represented in the segmented tab strip;
5. then remove secondary appearance/help hints and lower-priority context hints
   as required.

At the most constrained supported width, `[:] more` and `[q] quit` remain visible.
The command palette is already service-scoped and therefore provides the complete
overflow surface without introducing a second command model.

Disabled commands may remain visible when they fit. Their tooltips explain why
they are disabled. A disabled hint may be removed before an enabled action of the
same priority when space is constrained.

## 1.6. Glue-to-Athena handoffs

### 1.6.1. Table query handoff

`glue.query_in_athena` gains the default shortcut `Q` (`Shift + Q`) and appears
as `[Q] Athena` in the Glue command row when applicable.

Invoking it for a visible selected Glue table publishes the table's exact typed
identity and performs the existing serialized cross-service navigation. Athena
resolves the matching AWS source, workgroup, catalog, and database, selects Query,
and replaces the editor with a bounded read-only query:

```sql
SELECT *
FROM "catalog"."database"."table"
LIMIT 100
```

The handoff never starts a query automatically.

### 1.6.2. Snapshot time-travel handoff

`glue.time_travel_in_athena` retains `V` (`Shift + V`) and appears as
`[V] snapshot` when the Iceberg metadata surface is available.

The action is enabled only when:

- Glue Catalog is the active service view;
- Iceberg metadata is available;
- Snapshots is the active Iceberg metadata view;
- at least one real snapshot row is loaded; and
- the selected snapshot ID belongs to a currently visible loaded row.

The handoff uses the same exact table identity and adds the selected snapshot:

```sql
SELECT *
FROM "catalog"."database"."table" FOR VERSION AS OF <snapshot-id>
LIMIT 100
```

It never starts a query automatically.

When there are no snapshots, the arrow button and command are disabled. Their
tooltip states that a snapshot row must be selected. An empty table header or
placeholder is not treated as a selected snapshot.

### 1.6.3. Unified action dispatch

The Iceberg arrow button, `Shift + V`, and the palette entry converge on the same
registered `glue.time_travel_in_athena` application action. `Shift + Q` and the
table palette entry converge on `glue.query_in_athena`.

The registered action performs availability validation and delegates to the Glue
VM. The Glue VM publishes one typed `OpenAthenaTableRequest`; Athena remains the
only owner of safe SQL composition. Widgets do not construct SQL, resolve AWS
profiles, or implement navigation.

Invalid, empty, stale, or unmounted selections produce the established advisory
behavior and do not navigate or mutate Athena editor state. Superseded handoffs
continue to use the existing generation-based serialized navigation safeguards.

### 1.6.4. Command availability

Glue command-state recomputation covers all three selection-dependent actions:

- copy selected table reference;
- open selected table in Athena; and
- open selected snapshot in Athena.

Availability updates after table selection, metadata-view changes, snapshot-row
selection, pagination, refresh, errors, source changes, and page shutdown. The
legend and button must agree at all times.

## 1.7. Architecture and VMx ownership

The current ownership boundaries remain the best-fitting abstractions:

- `ContextPicker` and `ApplicationPicker` own ephemeral overlay presentation;
- `FocusCoordinatorVM` remains the VMx-backed source of typed focus identity and
  traversal;
- `HintLegendVM` remains the VMx-backed source of command metadata and enabled
  state;
- `HintLegend` owns terminal-width measurement and visual fitting;
- Glue VMs own selected table/snapshot identity and capability checks;
- `ActionRegistry` owns keyboard, button, and palette dispatch;
- `OpenAthenaTableRequest` remains the typed cross-service message; and
- Athena VMs own query composition and editor state.

Overlay position and available terminal width are transient view geometry and do
not belong in a VMx view model. No new VMx state machine, property VM, command
model, parallel focus model, or duplicated selection state is justified.

The implementation should reuse Textual's established overlay properties rather
than reproduce screen positioning manually. It should retain the specialized
custom wrappers because they already encode titled borders, source identity,
loading/error states, EMR state-rich options, and application-specific commit
semantics that native `Select` does not replace without substantial adaptation.

## 1.8. Failure and lifecycle behavior

- A picker whose owner unmounts closes without scheduling focus back to a stale
  widget.
- A source switch closes overlays before the service rebuild begins.
- Option refresh while open preserves the committed value where valid and never
  exposes stale selectable rows.
- Empty, loading, forbidden, unreachable, and error option sets remain
  non-selectable and retain their existing explanatory copy.
- A handoff rejected because its table or snapshot disappeared leaves the user on
  Glue and emits one advisory toast.
- A later navigation request supersedes an older in-flight handoff exactly as it
  does today.
- Tooltip generation must tolerate remapped keys, absent optional bindings, and
  disabled actions without displaying stale default shortcuts.

## 1.9. Verification strategy

Implementation follows test-driven development. Behavioral assertions lead;
snapshots supplement them rather than serving as the sole proof.

### 1.9.1. Picker tests

For both `ContextPicker` and `ApplicationPicker`, verify:

- trigger, parent, sibling, tab-strip, and content regions do not change while
  opening or closing;
- the option list is visible, screen-overlaid, viewport-constrained, and layered
  above service content;
- opening a second picker closes the first;
- commit, Escape, outside click, Tab, Shift+Tab, service switch, and unmount close
  correctly;
- focus restoration never targets an unmounted widget;
- only the active/open picker uses focus emphasis;
- semantic-state color precedence is preserved; and
- repeated open/close cycles leave no geometry or class residue.

Run page-level coverage for Glue Catalog, Jobs, Crawlers, Athena Query, and EMR
Serverless because they exercise distinct picker compositions.

### 1.9.2. Command legend tests

Verify:

- Athena and Glue use one content row at representative wide widths;
- the Commands frame height remains constant;
- chip widths are content-sized and separated by the approved compact gap;
- every visible chip has the canonical shortcut and full explanatory tooltip;
- remapped shortcuts update both the displayed key and tooltip;
- resizing uses deterministic priority and introduces `[:] more` only when
  needed;
- hidden actions remain bound and present in the service-scoped palette;
- disabled prerequisites are accurate; and
- all supported themes preserve identical structural geometry.

### 1.9.3. Handoff tests

Add a full-app integration test that:

1. opens Glue with Iceberg snapshot data;
2. selects a non-default visible snapshot row;
3. clicks the actual `#glue-iceberg-time-travel` button;
4. waits for serialized service navigation;
5. proves the matching Athena source/context is active;
6. proves the exact `FOR VERSION AS OF` SQL is in the editor; and
7. proves no Athena query execution call occurred.

Run equivalent behavior through `Shift + V` and the palette entry. Cover
`Shift + Q` table handoff, source switching, stale/empty selections, disabled
state transitions, pagination, page shutdown, and superseded requests.

### 1.9.4. Visual and manual review

Review Glue Catalog with source idle and Iceberg focused, each Glue filter open,
Athena with each context picker open, and EMR with its application picker open.
Capture wide and narrow terminals across every supported theme.

Manual acceptance must confirm:

- only one focus signal reads as active;
- overlays cover rather than move content;
- closing restores the exact layout;
- commands remain one line with compact spacing;
- tooltips are readable and complete; and
- both Glue-to-Athena actions navigate and prefill without execution.

## 1.10. Documentation

Update canonical documentation wherever it describes picker expansion, command
layout, key bindings, or Glue/Athena workflows. At minimum this includes:

- `docs/keybindings.md`;
- Glue and Athena workflow/UI documentation;
- architecture and service-extension guidance for shared picker and command
  conventions;
- representative screenshots;
- the release changelog when the implementation is merged; and
- generated site/wiki inputs required by the repository's three-surface
  documentation contract.

Documentation must explicitly say that `Shift + Q` and `Shift + V` prefill but do
not execute Athena queries. Generated documentation and screenshots must be
regenerated from the canonical source rather than patched independently.

## 1.11. Out of scope

This change does not:

- add Glue, Athena, EMR, or S3 write operations;
- alter Glue or Athena view order or tab activation semantics;
- add picker option searching or multi-select;
- replace every `OptionList` with a dropdown;
- replace the theme picker or command palette;
- change source resolver order, AWS SDK calls, or credential behavior;
- execute a query as part of a Glue handoff;
- change Iceberg metadata APIs or pagination semantics; or
- introduce a new VMx abstraction where the existing specialized owner already
  fits.

## 1.12. Acceptance criteria

The work is complete when:

1. every scoped inline dropdown overlays the screen with zero surrounding reflow;
2. open/close cycles leave no lingering geometry or focus decoration;
3. idle AWS source no longer appears focused while Iceberg owns focus;
4. the Commands pane renders exactly one compact content row at supported widths;
5. every visible command has an accurate, detailed shortcut/action tooltip;
6. narrow widths use deterministic priority and `[:] more` without wrapping;
7. `[Q] Athena` and `[V] snapshot` are visible, correctly enabled, documented,
   and available through keyboard and palette;
8. the actual Iceberg button successfully drives a tested full-app Athena
   handoff for a selected snapshot;
9. table and snapshot handoffs prefill exact read-only SQL and never execute it;
10. focused unit, integration, snapshot, formatting, lint, and type checks pass;
11. every supported theme and representative terminal size is visually reviewed;
    and
12. canonical and generated documentation surfaces are synchronized.
