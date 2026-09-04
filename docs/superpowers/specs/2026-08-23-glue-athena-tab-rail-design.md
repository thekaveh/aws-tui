# 1. Glue and Athena tab rail and context framing design

**Status:** Implemented on 2026-08-23. The underline-only tab presentation in
this document was subsequently superseded by the segmented-frame design; its
context framing and unchanged-navigation decisions remain historical context.

This design supersedes only the Glue context-pane framing and Glue/Athena tab
strip presentation described in
`2026-07-30-glue-athena-interaction-polish-design.md`. The earlier document
remains authoritative for selector behavior, focus ordering, commands,
profile-aware context, Glue-to-Athena transfer, and service behavior.

## 1.1. Problem statement

The current Glue page creates two competing focus signals around AWS source
selection: the enclosing `AWS context` pane and the nested bordered
`AWS source` selector both receive accent treatment. The result is visually
heavy and makes it unclear which control owns keyboard interaction.

The shared Glue and Athena view selector has a related clarity problem. Its
`Views` border makes it resemble a content pane, while the labels inside do not
consistently communicate which view is selected once keyboard focus leaves the
strip. The high-intensity focused fill is also much stronger than the rest of
the operational interface.

The refinement must improve those signals without changing source resolution,
view selection behavior, focus ownership, commands, AWS calls, or view-model
state.

## 1.2. Decision summary

- Remove the enclosing `AWS context` pane presentation from Glue only.
- Keep the bordered `AWS source` selector as Glue's sole source-control frame
  and as its own logical Tab stop.
- Present Glue view-specific filters as adjacent, independently framed controls
  without an outer context frame.
- Keep Athena's grouped context header because its source, workgroup, catalog,
  database, and pagination controls form one related control region.
- Replace the shared Glue/Athena bordered `Views` control with an underline tab
  rail.
- Keep the selected tab visible at all times with an accent underline and
  stronger label.
- When the rail has keyboard focus, add a soft muted fill behind the selected
  tab instead of a heavy outer border or high-intensity block.
- Keep the tab strip as one logical focus stop and retain immediate Left/Right
  activation.
- Reuse the existing `ServiceTabStrip`, `ServiceSourceHeader`, `ContextPicker`,
  and `FocusCoordinatorVM` responsibilities. No new VM or VMx abstraction is
  introduced.

## 1.3. Scope

### 1.3.1. In scope

- Glue context-row structure and styling.
- Shared `ServiceTabStrip` structure and styling used by Glue and Athena.
- Focus and keyboard regression coverage for both services.
- Visual regression coverage across supported themes and representative
  terminal sizes.
- Canonical documentation updates needed to describe the resulting UI.

### 1.3.2. Out of scope

- Source-switch transaction behavior or resolver order.
- Glue or Athena VM state, AWS gateway calls, filtering, pagination, or data
  loading.
- Focus-slot vocabulary or service-level focus order.
- Navigation-rail presentation.
- S3 and EMR context framing.
- Preview-then-confirm tab selection.
- New commands, key bindings, or VMx dependencies.

## 1.4. Glue context controls

### 1.4.1. Catalog view

The Glue Catalog view shows `ServiceSourceHeader` as a standalone, bordered
control. The current `AWS context` border and title are removed. The selector's
own `AWS source` title, current value, dropdown indicator, and focus/open state
provide the complete visual and interaction boundary.

The source selector remains the first Glue page focus target. `Tab`,
`Shift+Tab`, source commands, `Enter`, `Space`, arrow navigation, commit, and
cancel behavior do not change.

### 1.4.2. Jobs and Crawlers views

Jobs and Crawlers use an unframed horizontal control row:

- `AWS source` remains an independently bordered control.
- Jobs adds the independently bordered `Run state` filter.
- Crawlers adds the independently bordered `Crawler state` filter.
- Each visible control remains a separate target in the existing typed focus
  ring.

The row is a layout container, not a pane. It has no border title, no focus
decoration, and no visual state that competes with its children. Existing
relative widths should be preserved unless a small adjustment is required to
keep labels and values readable at supported terminal sizes.

Opening an inline picker may expand the row vertically, but it must not overlap
the tab rail or sibling controls. Closing the picker restores the compact row
without shifting the service's horizontal content columns.

### 1.4.3. Athena and other services

Athena retains its grouped context header. Its source, workgroup, catalog,
database, and conditional load-more controls are an interdependent set and
benefit from the enclosing region.

S3 and EMR retain their existing context presentation. This is an intentional
Glue-specific exception, not a new application-wide rule against context-pane
borders.

## 1.5. Shared tab rail

### 1.5.1. Resting state

`ServiceTabStrip` no longer renders an enclosing border or the `Views` border
title. Its tabs occupy equal, stable horizontal tracks across the available
width.

The selected tab always has:

- an accent underline;
- a stronger text treatment than inactive tabs;
- sufficient contrast in every supported theme.

Inactive tabs use the normal subdued label treatment. Selection must remain
unambiguous after focus moves into page content. The rail therefore does not
depend on focus color to communicate the active view.

### 1.5.2. Focused state

The tab strip remains one `can_focus` widget. When it owns keyboard focus, the
selected tab receives a soft muted fill in addition to its persistent underline
and stronger label. The rail itself does not gain a heavy border.

The fill communicates focus without replacing the selected-state signal. It
must be visibly distinct from inactive tabs but quieter than the current full
accent block. Hover, if rendered, must not be stronger than selected or focused
state.

### 1.5.3. Interaction model

Existing behavior is preserved:

- Left/Right immediately selects and activates the neighboring tab.
- Numeric view commands immediately activate their corresponding views.
- Click activates the clicked tab.
- `Enter` and `Space` activate the current tab.
- `Tab` and `Shift+Tab` enter or leave the rail as one focus stop.

There is no separate preview selection. The internally highlighted item and the
active view remain synchronized after every navigation input.

### 1.5.4. Geometry

The rail has a fixed compact height that does not change across resting,
selected, focused, or hover states. Underline thickness, label weight, and focus
fill must not resize the widget or move adjacent content.

Labels must fit at the repository's supported minimum terminal width. The
shared widget must render Glue's three tabs and Athena's four tabs without
overlap, clipping, or ambiguous truncation.

## 1.6. State ownership and VMx

The change is presentational. `FocusCoordinatorVM` remains the application-wide
source of typed focus identity and continues to map the existing Glue and
Athena focus slots to concrete widgets.

`ServiceTabStrip` continues to own local rendering and input translation only.
The Glue and Athena pages continue to own view activation and dispatch through
their existing page and VM boundaries. `ServiceSourceHeader` and
`ContextPicker` retain their present responsibilities for selector
presentation and interaction.

No new state machine, discriminator, component VM, property VM, or view-owned
parallel focus state is justified. The existing VMx-backed focus coordinator is
already the most specialized applicable abstraction for this work.

## 1.7. State precedence and accessibility

The following presentation precedence applies:

1. Open picker state remains clearly visible and takes precedence over ordinary
   focus decoration.
2. Error, warning, loading, empty, forbidden, and disabled selector states
   retain their established semantic treatment.
3. Keyboard focus supplements those states without hiding their meaning.
4. Resting selected-tab state remains visible independently of focus.

Color is not the only selected-tab cue: the underline and stronger label remain
present together. Focus does not alter label text, tab numbering, or command
semantics. The page's forward and reverse focus order remain exact inverses.

## 1.8. Verification strategy

### 1.8.1. Shared widget tests

Add or update focused tests for `ServiceTabStrip` that verify:

- selected tabs remain visually marked while the strip is unfocused;
- focused selected tabs gain the soft fill without losing the underline;
- inactive tabs do not acquire selected styling;
- Left/Right navigation activates immediately and wraps as currently defined;
- click, `Enter`, and `Space` activation remain intact;
- the strip remains one logical focus target;
- widget height and child tracks remain stable across states.

Prefer assertions against component classes, focus state, messages, and active
IDs. Use snapshots for geometry and theme appearance rather than as the sole
behavioral proof.

### 1.8.2. Glue tests

Glue coverage must verify:

- Catalog renders a standalone source selector with no enclosing context-pane
  border or title;
- Jobs and Crawlers render source and filter controls in an unframed row;
- forward and reverse traversal preserve source, active filter, tab rail, page
  content, and navigation ordering;
- each visible selector opens, changes value, commits or cancels, and returns
  focus correctly;
- numeric Glue view commands still activate their corresponding views;
- inline expansion does not overlap the rail, sibling selector, or content;
- source switching, filtering, refresh, and active-view behavior are unchanged.

### 1.8.3. Athena tests

Athena coverage must verify:

- the grouped context header and its focus order are unchanged;
- the shared tab rail exposes the same selected and focused states as Glue;
- all four tab labels remain readable at supported terminal widths;
- numeric Athena view commands still activate their corresponding views;
- source, workgroup, catalog, database, and pagination interactions remain
  unchanged.

### 1.8.4. Visual regression and manual QA

Capture Glue Catalog, Jobs, and Crawlers plus Athena Query and one non-query
view at representative narrow and wide terminal sizes. Exercise every supported
theme and include both focused and unfocused rail states.

Visual review must confirm:

- there is only one focus frame around Glue's source selector;
- selected tabs remain obvious after focus moves into content;
- focused tabs use the approved soft-fill treatment;
- dropdown expansion does not overlap or clip neighboring UI;
- labels, underlines, borders, and content columns do not shift between states;
- Athena's context grouping remains visually intact.

## 1.9. Documentation

Update canonical widget, workflow, and UI descriptions only where they describe
the removed Glue context frame or the old bordered `Views` strip. Preserve
documentation of source selection, commands, focus ordering, and Glue/Athena
behavior because those contracts do not change.

If a canonical documentation input changes, regenerate and verify all derived
documentation surfaces according to the repository's existing documentation
workflow. Screenshots or diagrams that visibly encode the old framing must be
updated; unrelated illustrations are out of scope.

## 1.10. Acceptance criteria

The implementation is complete when:

1. Glue no longer presents an enclosing `AWS context` border, title, or parent
   focus decoration; an unframed internal layout row may remain.
2. Glue source and view-specific filter controls remain clearly framed and
   independently keyboard accessible.
3. Glue and Athena render the shared underline tab rail without a `Views`
   border or title.
4. The active view remains visually obvious while the rail is unfocused.
5. Rail focus uses the approved soft fill and does not create a second enclosing
   border.
6. Existing source, filter, tab, command, and focus behavior remains intact.
7. Open, loading, warning, error, empty, forbidden, and disabled states remain
   distinguishable.
8. Automated behavior, focus, layout, theme, and snapshot coverage passes.
9. Canonical and generated documentation surfaces do not describe or show the
   superseded framing.
