# 1. Athena UI contract repair design

**Status:** Approved for implementation on 2026-08-27.

This document is the source of truth for the Athena and shared chrome repairs
requested after hands-on review of `develop`. It supersedes only the conflicting
presentation and starter-query details in the 2026-08-24 overlay-picker design.
The existing read-only operating model, resolver order, source transaction,
segmented tabs, focus coordinator, and VMx-based MVVM boundaries remain intact.

## 1.1. Problems and evidence

The current interface has five related contract failures.

First, `HintLegend` fits commands into one line but anchors the resulting strip
to the left edge. The Commands pane should present the fitted row as one centered
unit at every supported width without changing command priority or overflow
behavior.

Second, Athena wraps its four individually bordered context selectors in a
second bordered `AWS context` container. Glue has already adopted the clearer
unframed-row convention. Athena's extra frame adds visual noise and makes focus
ambiguous. In the reviewed build, the populated Workgroup, Catalog, and Database
selectors also appear unavailable and cannot be opened through expected user
interaction, despite direct method tests passing.

Third, service selection in the left navigation rail is painted only while the
navigation focus slot is active. Moving keyboard focus into the service clears
the selected style, so the rail stops indicating which service owns the content
area.

Fourth, Glue's `Q` handoff reaches Athena with the correct source, catalog, and
database, but the visible query editor does not reliably show starter SQL. The
existing integration test asserts only `AthenaQueryVM.sql`, allowing VM-to-view
synchronization to regress unnoticed. The existing starter uses `LIMIT 100`,
while the reviewed workflow requires a deliberately small five-row preview.

Fifth, Athena places its execute and cancel controls below the editor in a tall
pane titled `query status`. The buttons can extend outside the frame, and the
cancel command's `can_execute` predicate excludes the submitting phase even
though the VM's cancellation method can interrupt that phase. Presentation,
keyboard behavior, and VM state therefore disagree.

## 1.2. Goals

The change must:

1. center every fitted one-line command row without wrapping or layout shift;
2. remove Athena's redundant outer context frame while preserving selector
   borders, resolver order, and Tab order;
3. make every populated and healthy Athena selector openable by mouse, keyboard,
   and named command;
4. keep the active service visibly selected regardless of content focus;
5. prefill the visible Athena editor with safe, quoted, five-row starter SQL
   after a Glue handoff without executing it;
6. move compact query controls above the editor and keep them inside their frame;
7. derive Run and Stop availability from VMx commands that express the true VM
   lifecycle; and
8. update tests, snapshots, and all affected documentation contracts.

## 1.3. Non-goals

This work does not:

- replace `ContextPicker` or `ServiceSourceHeader` with a new selector system;
- change source, workgroup, catalog, or database resolution order;
- add write-capable Athena SQL or automatically execute a handoff query;
- reorder Athena's context selectors, service tabs, or operational views;
- redesign Glue's already approved context row; or
- introduce a new shared context-header abstraction solely for visual reuse.

## 1.4. Command-row alignment

`HintLegend` keeps its existing width measurement, compact labels, priority
fitting, overflow command, and tooltip behavior. After fitting, `#hint-strip`
centers the complete chip sequence within the available inner width.

The row remains exactly one terminal line. Centering must not:

- assign equal-width cells to commands;
- add large per-chip margins;
- change which actions survive narrow-width fitting;
- move when a tooltip opens or focus changes elsewhere; or
- clip either border at the minimum supported width.

When the fitted commands consume the full width, centering naturally has no
visible effect. Geometry tests cover both spare-width and constrained-width
cases.

## 1.5. Athena context row

### 1.5.1. Presentation

Athena's source, workgroup, catalog, and database controls remain children of one
horizontal layout container, renamed or restyled as an unframed context row. The
container has no border, border title, focus border, or extra vertical padding.
Each inner `ServiceSourceHeader` or `ContextPicker` remains a titled, bordered
control and owns its own focus, open, warning, error, loading, and disabled
presentation.

This follows Glue's established convention while retaining Athena-specific
widths and load-more controls. The load-more buttons remain associated with
their selectors and appear only when applicable.

### 1.5.2. Focus and interaction

The semantic order remains:

```text
AWS source -> Workgroup -> Catalog -> Database -> view tabs -> active view
```

For each populated selector in `PaneState.IDLE`:

- Tab and Shift+Tab include the trigger;
- Enter and Space open the overlay;
- clicking the trigger opens the overlay;
- the named Workgroup, Catalog, or Database command focuses and opens it; and
- choosing an option executes the existing VM selection transaction.

Loading, forbidden, error, unreachable, and genuinely empty selectors remain
unavailable according to their semantic state. The View must not infer
availability merely from a displayed selected string. State synchronization must
keep the option collection, selected value, `PaneState`, `disabled` property,
focus ring, and overlay behavior mutually consistent.

Regression tests exercise real clicks and routed keyboard actions in the full
Athena page. Direct calls to `ContextPicker.open()` are insufficient proof.

## 1.6. Persistent service selection

`NavMenuVM.selected_id` remains the canonical active service. Every matching
`NavRow` retains `-selected` whenever its descriptor id equals that value,
independent of `FocusCoordinatorVM.focused_slot`.

The navigation focus slot may add a separate focused/active treatment, but it
must not decide whether the service is selected. Consequently:

- entering Athena leaves Athena visibly selected in the rail;
- moving among Athena controls does not clear that indication;
- arrowing through the focused rail updates selection immediately as before;
- Settings uses the same selected-state rule; and
- theme rules continue to provide readable selected text and background.

Tests cover selection while the rail owns focus and while focus is in each
representative service surface.

## 1.7. Glue-to-Athena starter query

For an ordinary selected Glue table, the handoff produces:

```sql
SELECT * FROM "Catalog"."Database"."Table" LIMIT 5
```

Identifiers continue to use the canonical Athena quoting helper. A selected
Iceberg snapshot keeps the existing `FOR VERSION AS OF <snapshot-id>` clause and
also ends with `LIMIT 5`.

The handoff transaction continues to resolve and select the matching connection,
region, workgroup, catalog, and database before selecting Athena's Query view.
It then updates `AthenaQueryVM.sql` through its public API. The mounted
`AthenaQueryView` must reflect that property change in `TextArea.text` before the
transaction is considered visibly complete.

No `start_query` call occurs. Run becomes enabled only after the populated SQL
passes the existing read-only policy. Tests assert both VM SQL and visible editor
text, including the exact five-row limit and absence of execution.

This section supersedes prior examples and tests that specify `LIMIT 100` for
generated starter SQL. It does not change user-authored query limits or result
pagination limits.

## 1.8. Query controls and editor layout

Athena Query uses the following vertical order:

```text
Query controls
Query editor
Execution detail
```

The `Query controls` pane is a compact framed strip above the editor. Its single
content row contains a stable Run button, a stable Stop button, and one
ellipsis-safe live status field. Buttons have fixed dimensions that fit wholly
inside the pane at all supported widths.

The status field retains useful states such as:

- `Enter a read-only query`;
- `Ready - read-only SQL`;
- `INVALID QUERY`;
- `SUBMITTING`;
- the current Athena execution state and execution id; and
- request failure state.

The controls use existing icon labels and explanatory tooltips. Moving the pane
does not add a new Tab stop. The established keyboard sequence remains editor,
Run, Stop, then execution detail even though the compact controls are rendered
above the editor. This preserves existing keyboard muscle memory while adopting
the approved controls-before-editor visual layout.

## 1.9. VMx command-state contract

The View does not duplicate query lifecycle policy. It continues to set button
availability from VMx command predicates:

- Run is enabled only when SQL is nonblank, valid read-only Athena SQL, and no
  submission or active execution is in progress.
- Stop is enabled whenever the VM can interrupt work, including the submission
  window before an execution reference is available and the owned active-query
  window afterward.

The cancel command predicate and `cancel()` behavior must express the same
interruptibility rule. Keyboard, button, and palette paths therefore cannot
disagree. Command reevaluation is triggered whenever SQL, validation, submission,
ownership, execution reference, or terminal state changes.

The existing VMx `AsyncRelayCommand` remains the best-fitting abstraction; this
work corrects its predicate rather than introducing view-local booleans or a new
command type.

## 1.10. Error handling and lifecycle

Existing provider-error mapping, toast behavior, handoff rollback, cancellation
ownership, generation guards, and shutdown semantics remain unchanged.

If a handoff fails before the starter query is committed, the existing
transaction restores the previous service and context. A superseded handoff must
not overwrite a newer editor value. Deferred view refresh callbacks must verify
that the widget remains mounted and that the VM notification still belongs to
the current page.

## 1.11. Test strategy

Implementation follows focused test-driven development.

Unit and widget tests cover:

- centered command-strip geometry with spare and constrained width;
- Athena's unframed outer row and individually framed selectors;
- selector enabled state, Tab participation, click opening, keyboard opening,
  commit, close, and overlay geometry stability;
- persistent `NavRow.-selected` styling outside the navigation focus slot;
- compact controls-before-editor geometry and border containment;
- Run transitions for blank, invalid, ready, submitting, and active SQL; and
- Stop transitions during submission, active execution, and terminal states.

Integration tests cover:

- Glue `Q` handoff through the registered app action;
- exact source/context preservation;
- exact quoted `LIMIT 5` SQL in both the VM and mounted editor;
- Iceberg snapshot SQL with `FOR VERSION AS OF` and `LIMIT 5`;
- no implicit query execution; and
- interactive Athena context pickers after ordinary entry and after handoff.

Snapshot coverage is regenerated for every built-in theme at representative
wide and narrow terminal sizes. Visual review checks centered commands,
unambiguous selected service, selector borders, compact controls, and absence of
overlap or clipping.

## 1.12. Documentation and compatibility

Affected canonical documentation includes the Athena service guide, keybindings
and focus model, architecture and contract ledger where starter-query behavior
is described, command legend conventions, and any generated three-surface
derivatives required by the repository tooling.

Documentation must no longer describe Athena selectors as living in one framed
`AWS context` pane, and all generated starter-query examples must use `LIMIT 5`.
The change is otherwise backward compatible: action ids, default keybindings,
message types, stored selections, and public VM APIs remain stable.

## 1.13. Delivery

Work is performed on `fix/athena-ui-contracts`, created from synchronized
`develop` commit `49ac96dc`. The implementation, tests, snapshots, and documents
form one focused pull request targeting `develop`.

The branch may use intermediate design and implementation commits during review,
but the pull request is squash-merged so `develop` receives one comprehensive
fix commit, as requested. After merge, the local and remote feature branch are
removed and local `develop` is updated to the merged commit.
