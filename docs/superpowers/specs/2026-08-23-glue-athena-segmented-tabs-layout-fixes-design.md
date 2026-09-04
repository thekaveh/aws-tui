# 1. Glue and Athena segmented tabs and layout fixes design

**Status:** Implemented on 2026-08-23. The shared segmented-frame and layout
regression decisions remain authoritative; the later overlay-picker design
refines selector and command-hint behavior without changing tab order.

This design supersedes only the underline tab-rail presentation in
`2026-08-23-glue-athena-tab-rail-design.md`. It also corrects two regressions
revealed by the resulting Glue and Athena layouts: an unintended Glue source
header edge and pathological wrapping of Athena's command legend. All existing
focus order, view ordering, activation, commands, VM ownership, and AWS service
behavior remain authoritative and unchanged.

## 1.1. Problems

The underline-only Glue and Athena view rail reads as a horizontal separator,
not as a tab control. It does not provide a sufficiently complete visual
boundary around the related choices.

Glue's context row is intentionally unframed, but the shared
`ServiceSourceHeader` receives an additional theme-owned left border. That
leaves a detached edge outside the source selector even though the selector's
own complete border is meant to be the only frame.

Athena exposes 17 commands. `HintLegend` currently uses a regular `ItemGrid`;
Textual reduces a regular grid's column count until every row has the same
number of items. Because 17 is prime, a wide Athena legend collapses to one
column and consumes most of the screen vertically.

## 1.2. Decisions

### 1.2.1. Shared segmented tab frame

`ServiceTabStrip` becomes a shared segmented frame:

- one complete outer border surrounds the tab rail;
- vertical dividers separate equal-width tab segments;
- inactive segments use the normal subdued label treatment;
- the active segment retains stronger accent text;
- the active segment receives the approved soft fill when the rail owns focus;
- selection remains visible while focus is elsewhere;
- border, divider, fill, and label changes never resize the rail.

The rail remains one logical focus target. Existing Left/Right, numeric-command,
click, Enter, Space, Tab, and Shift+Tab behavior is unchanged. Tab labels,
ordering, wrapping, and immediate activation are unchanged. No segment becomes
an independent Tab stop.

The old underline-only cue is removed. This is a segmented control presentation,
not a return of the titled `Views` pane: the frame has no title and does not own
or frame the service content below it.

### 1.2.2. Glue source framing

Glue retains its unframed context layout row. The `AWS source` picker keeps its
own complete border and remains an independent focus target. The additional
theme-owned left edge on `ServiceSourceHeader` is removed from Glue by scoping
that decoration to the service layout that requires it. No Glue row border,
title, or parent focus decoration is reintroduced.

Athena's grouped context frame and EMR's existing source-header presentation
remain unchanged.

### 1.2.3. Responsive command legend

`HintLegend` uses non-regular responsive packing. It fills the available width
with as many stable minimum-width command cells as fit, then wraps the remainder
onto compact additional rows. Prime command counts must not force a single
column.

The change is layout-only. Command text, ordering, key bindings, visibility,
view scoping, and `HintLegendVM` ownership do not change. At narrow widths,
commands may use more rows, but every chip must remain within the legend bounds
without overlap or clipping.

## 1.3. Architecture and VMx

The existing ownership boundaries are already the best fit:

- `ServiceTabStrip` owns tab presentation and input translation;
- Glue and Athena pages own view activation and composition;
- `FocusCoordinatorVM` remains the VMx-backed source of typed focus identity;
- `ServiceSourceHeader` and `ContextPicker` retain source-selection behavior;
- `HintLegendVM` remains the VMx-backed source of visible command data;
- `HintLegend` owns responsive command layout.

No new VM, parallel selection state, focus state, command model, or VMx
abstraction is introduced.

## 1.4. Verification

Automated coverage must prove:

1. The shared tab strip has one outer frame and internal segment dividers.
2. The selected segment is distinguishable while unfocused and gains soft fill
   while focused.
3. Glue's source header has no detached theme-owned left edge while its picker
   keeps a complete border.
4. EMR retains any intentionally scoped source-header edge.
5. Athena's 17-command legend forms multiple columns at a wide viewport and
   remains compact rather than becoming 17 rows.
6. Glue and Athena legends remain inside their bounds at representative narrow
   and wide viewports.
7. Existing tab activation, focus traversal, source selection, and command
   behavior continue to pass unchanged.
8. All supported themes compile and preserve the same structural geometry.

Tests should assert widget classes, computed border styles, regions, column
distribution, and row counts. Snapshots supplement these structural assertions
but are not the sole proof.

Manual visual review must cover Glue Catalog, Jobs, and Crawlers plus Athena
Query at wide and narrow terminal sizes. It must confirm that the segmented
frame reads as one control, the active segment remains clear, the Glue source
has exactly one complete frame, and Athena commands flow horizontally.

## 1.5. Scope boundary

This work does not change:

- Glue or Athena view order;
- focus-slot order or the number of focus stops;
- tab activation or wrapping semantics;
- command vocabulary, ordering, or shortcuts;
- source resolver order, AWS calls, or service state;
- Glue/Athena view models or data gateways;
- S3, EMR, navigation rail, or content-pane layouts.

## 1.6. Acceptance criteria

The change is complete when the segmented frame, Glue edge correction, and
responsive Athena command layout match this design; focused behavior and layout
regression tests pass; all supported themes and representative terminal sizes
are verified; and no unrelated service behavior or documentation contract is
changed.
