# Glue Task 4 Report

## Status

Implemented and verified. The commit subject is:

```text
feat: add Glue service page
```

Task 4 adds the Glue Textual page, factory branch, focused actions, keymap and
hint integration, all-theme snapshots, and content guards. It does not register
the Glue service or add demo data; those remain Task 5.

## TDD Evidence

The page composition, action, factory, keymap, hint, and snapshot tests were
written before the Glue widget package existed.

Initial RED command:

```text
uv run pytest tests/unit/ui/glue tests/unit/ui/test_service_view_factory.py \
  tests/unit/infra/test_keymap_store.py \
  tests/unit/vm/chrome/test_hint_legend.py \
  tests/snapshot/test_glue.py -q
```

Observed result:

```text
ModuleNotFoundError: No module named 'aws_tui.ui.widgets.glue'
3 errors during collection
```

The missing package affected the page, service-view factory, and snapshot app
imports, which was the intended pre-implementation failure.

Later focused tests also pinned real key routing and interaction behavior:

- `2` and `3` switch views and trigger lazy loading;
- clicking a tab switches the active view;
- `r` refreshes only the active Glue view;
- job and crawler `Select` changes reach their filtered VM reload paths;
- AWS names containing Rich markup syntax render literally.

## Implementation

- Added `GluePage` with an unframed `ServiceSourceHeader`, one-line three-choice
  tab rail, stable view host, numeric view actions, focused refresh, click
  handling, and nav-rail-aware initial focus.
- Added `GlueCatalogView` with database, table, and scrollable table-detail
  panes using `2fr 3fr 5fr` tracks.
- Added `GlueJobsView` with job list, run-state `Select`, run list, and
  scrollable combined job/run detail using stable `2fr 3fr 5fr` tracks.
- Added `GlueCrawlersView` with crawler-state `Select`, crawler list, and
  scrollable detail using stable `4fr 6fr` tracks.
- Added shared literal-text list/detail widgets, deterministic placeholders,
  item counts, pagination visibility, timestamp/value formatting, and
  state-specific classes.
- Every AWS-controlled list prompt uses `OptionList(markup=False)` and every
  AWS-controlled detail or placeholder `Static` uses `markup=False`.
- Added `build_service_view("glue", ...)` returning
  `GluePage(..., id="content-glue-page")`.
- Added `glue.catalog`, `glue.jobs`, and `glue.crawlers` keymap entries and
  Glue-specific hint chips alongside refresh and source switching.
- Added a Glue theme block to all ten existing themes. Geometry remains in
  `DEFAULT_CSS`; theme files supply existing background, focus, selection,
  warning, error, success, and filter-control tokens.

## Snapshot Matrix

The Glue snapshot directory contains exactly 55 intended `.raw` files:

```text
50 wide snapshots:
  5 states x 10 themes at 150x44

5 compact snapshots:
  populated Catalog, Jobs, Crawlers plus forbidden and empty Catalog at 100x30
```

Wide snapshots cover every built-in theme:

```text
carbon, voidline, lattice, amber, solarized-light,
github-light, one-light, nord, dracula, gruvbox-dark
```

Every theme has SVG content guards for:

- source identity;
- database and table names;
- S3 storage location;
- job name and `RUNNING` state;
- crawler name and `READY` state;
- forbidden Lake Formation copy;
- empty database placeholder;
- absence of populated fixture values in forbidden and empty states.

## Visual Inspection

Raw SVGs were rasterized with `rsvg-convert` and inspected at the exact required
terminal sizes.

`100x30`:

- Catalog keeps database, table, and detail tracks stable.
- Jobs keeps job, filter/run, and detail tracks stable; `All states`, both run
  states, script location, runtime, and log group remain readable.
- Crawlers keeps the filter, list, and detail visible without collision.
- Forbidden copy wraps inside the database pane; empty placeholders and item
  counts remain visible.

`150x44`:

- Dark Carbon and light GitHub Light populated views were inspected.
- The unframed source row and one-line active tab remain distinct.
- Storage location, job state, crawler state, and detail rows fit without
  overlap.
- Vertical detail scrolling is contained within the detail pane.
- Light-theme `SelectCurrent` label and arrow use theme text colors.
- No page section is wrapped in a decorative card and no card/pane is nested
  inside another card.

The final representative rasters were:

```text
/tmp/aws-tui-glue-final-review/jobs-100x30.png
/tmp/aws-tui-glue-final-review/catalog-150x44.png
```

## Verification

Final bounded Task 4 test gate:

```text
uv run pytest tests/unit/ui/glue tests/unit/ui/test_service_view_factory.py \
  tests/unit/infra/test_keymap_store.py \
  tests/unit/vm/chrome/test_hint_legend.py \
  tests/snapshot/test_glue.py -q

101 passed in 20.58s
55 snapshots passed
```

Focused static gates:

```text
uv run mypy src/aws_tui/ui/widgets/glue \
  src/aws_tui/ui/widgets/service_view_factory.py \
  src/aws_tui/infra/keymap_store.py \
  src/aws_tui/vm/chrome/hint_legend_vm.py
Success: no issues found in 9 source files

uv run ruff check <Task 4 source and test paths>
All checks passed!

uv run ruff format --check <Task 4 source and test paths>
16 files already formatted

bash scripts/check-layers.sh
layer rules clean

git diff --check -- . ':(exclude)tests/snapshot/__snapshots__/**'
clean for source, tests, themes, and report; generated snapshots follow the
repository's explicit whitespace-hook exclusion
```

During implementation, the broader source-only static gates also passed:

```text
uv run mypy src/aws_tui
Success: no issues found in 126 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
333 files already formatted
```

## Concerns

- The local shell emits a pre-existing
  `/tmp/vmx-cargo-182/env: no such file or directory` warning. It did not affect
  any test, snapshot, static check, rasterization, or commit operation.
- No Task 4 functional concern remains. Service registration and multi-profile
  demo behavior are intentionally absent until Task 5.

## Visual/Interaction Review Fixes

### Status

All four post-implementation review findings were reproduced, fixed, and
verified on 2026-07-25. The fix commit subject is:

```text
fix: address Glue UI review findings
```

The changes are limited to Glue widgets, the ten Glue theme blocks, focused
regression tests, and the 55 affected Glue snapshots.

### Root Causes

1. Glue pane blocks set `border` but never set `border-title-color`, so inactive
   titles inherited the low-contrast border color.
2. `state_placeholder()` returned `-warning` and `-error`, but
   `ResourceListPane.replace()` discarded that class. Textual then rendered the
   disabled placeholder with its muted `option-list--option-disabled` token.
3. `_ViewTab.action_select()` already existed, but `_ViewTab` had no Enter or
   Space binding.
4. `DetailRows` and its child `VerticalScroll` were both focusable, placing two
   consecutive stops on one detail surface.

### Strict TDD Evidence

Focused DOM/class/binding/focus and all-theme token tests were added before
production changes.

RED command:

```text
uv run pytest tests/unit/ui/glue/test_page.py tests/unit/ui/test_themes.py -q
```

Observed RED result:

```text
24 failed, 166 passed in 3.93s
```

The failures mapped directly to the findings:

```text
2  focused-tab keyboard failures: Enter and Space left Catalog active
1  list placeholder class failure: OptionList lacked -warning
1  detail focus failure: focus_chain contained DetailRows and VerticalScroll
10 inactive/focused pane-title token failures, one per built-in theme
10 warning/error disabled-option token failures, one per built-in theme
```

The focused GREEN command was identical:

```text
uv run pytest tests/unit/ui/glue/test_page.py tests/unit/ui/test_themes.py -q
```

Observed GREEN result:

```text
190 passed in 4.05s
```

### Implementation Evidence

- Every Glue theme now assigns inactive pane titles
  `border-title-color: $text` and focused pane titles
  `border-title-color: $accent`; the existing focused accent border remains.
- `ResourceListPane.replace()` clears stale semantic classes, then applies the
  current placeholder's `-warning` or `-error` class to its `OptionList`.
- Every Glue theme maps warning/error list classes through Textual's disabled
  option component:

```text
GluePage OptionList.-warning > .option-list--option-disabled { color: $warning; }
GluePage OptionList.-error > .option-list--option-disabled { color: $danger; }
```

- `_ViewTab` binds `enter,space` to its existing `select` action.
- `DetailRows` is no longer independently focusable. Its inner
  `VerticalScroll` remains the one useful keyboard scroll target, and
  `DetailRows:focus-within` continues to paint focused pane chrome.

### Snapshot Evidence

Only the Glue snapshot suite was regenerated:

```text
uv run pytest tests/snapshot/test_glue.py --snapshot-update -q

55 snapshots updated.
65 passed in 16.94s
```

`git diff --name-only` confirmed exactly 55 changed files under:

```text
tests/snapshot/__snapshots__/test_glue/
```

No other snapshot family changed. All 55 are affected because explicit pane
title colors alter every Glue view/state at wide and compact sizes; forbidden
snapshots additionally capture the semantic placeholder color change.

### Final Test And Static Gates

Final requested Glue UI/factory/theme/keymap/hint/snapshot gate:

```text
uv run pytest tests/unit/ui/glue \
  tests/unit/ui/test_service_view_factory.py \
  tests/unit/ui/test_themes.py \
  tests/unit/infra/test_keymap_store.py \
  tests/unit/vm/chrome/test_hint_legend.py \
  tests/snapshot/test_glue.py -q

285 passed in 44.65s
55 snapshots passed
```

Final static and layer results:

```text
uv run mypy src/aws_tui
Success: no issues found in 126 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
333 files already formatted

bash scripts/check-layers.sh
layer rules clean

git diff --check -- . ':(exclude)tests/snapshot/__snapshots__/**'
clean
```

### True-Color Raster Review

The local shell exports `NO_COLOR=1`, so visual review renders explicitly
removed it and enabled true color:

```text
env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor ...
```

Eight SVGs were exported, rasterized with `rsvg-convert`, and inspected:

```text
/tmp/aws-tui-glue-task4-review/carbon-populated-100x30.png
/tmp/aws-tui-glue-task4-review/carbon-populated-150x44.png
/tmp/aws-tui-glue-task4-review/carbon-forbidden-100x30.png
/tmp/aws-tui-glue-task4-review/carbon-forbidden-150x44.png
/tmp/aws-tui-glue-task4-review/github-light-populated-100x30.png
/tmp/aws-tui-glue-task4-review/github-light-populated-150x44.png
/tmp/aws-tui-glue-task4-review/github-light-forbidden-100x30.png
/tmp/aws-tui-glue-task4-review/github-light-forbidden-150x44.png
```

Inspection findings:

- Inactive `tables` and detail titles are readable on Carbon and GitHub Light;
  the focused `databases` title and border retain a clear accent distinction.
- Populated values, storage location, counts, and detail rows remain inside
  their panes at both sizes with no text, border, footer, or scrollbar overlap.
- Forbidden copy wraps cleanly at `100x30` and stays on one line plus continuation
  at `150x44`; it is visibly semantic rather than disabled/muted.
- SVG fill extraction confirmed the forbidden copy resolves to the exact theme
  warning token at both sizes:

```text
carbon-forbidden-100x30.svg       #f0c674 == Carbon $warning
carbon-forbidden-150x44.svg       #f0c674 == Carbon $warning
github-light-forbidden-100x30.svg #9a6700 == GitHub Light $warning
github-light-forbidden-150x44.svg #9a6700 == GitHub Light $warning
```

### Fix Concerns

- The pre-existing shell warning
  `/tmp/vmx-cargo-182/env: no such file or directory` still appears before
  commands and did not affect any result.
- The test environment's pre-existing `NO_COLOR=1` setting makes checked-in SVG
  snapshots grayscale. Static token assertions plus the separate true-color
  raster matrix verify the real theme colors.
- No Glue functional or visual concern remains from these four findings.

## Production Router Re-review Fixes

### Status

The production priority-binding and configurable Glue action findings were
reproduced and fixed on 2026-07-25.

### Changed Files

- `src/aws_tui/app.py`
- `src/aws_tui/ui/widgets/glue/page.py`
- `tests/integration/test_glue_page_routing.py`
- `tests/integration/test_keybinding_wiring.py`
- `tests/unit/ui/glue/test_page.py`
- `.superpowers/sdd/task-4-report.md`

### Root Causes

1. `AwsTuiApp` installed priority bindings for Tab, Shift+Tab, Up/Down, Enter,
   Space, and `r`, but its routing branches only forwarded those actions to
   modals, navigation, S3, EMR, and settings. Textual therefore never delivered
   the same keys to focused Glue controls.
2. `glue.catalog`, `glue.jobs`, and `glue.crawlers` existed in `KeymapStore` but
   had no `ActionRegistry` handlers. `BindingResolver` correctly omitted them,
   leaving `GluePage`'s hardcoded `1/2/3` bindings as the only working path and
   preventing configured rebindings.

### Strict TDD Evidence

The production-app regressions were added before production changes.

RED command:

```text
uv run pytest tests/integration/test_glue_page_routing.py -q
```

Observed RED result:

```text
5 failed, 10 rerun in 16.02s
```

The five failures independently covered production Tab traversal, Enter/Space
tab activation, list/filter arrow navigation, `r` refresh, and configured
Glue-view bindings.

Focused GREEN command:

```text
uv run pytest tests/integration/test_glue_page_routing.py \
  tests/integration/test_keybinding_wiring.py \
  tests/unit/ui/glue/test_page.py \
  tests/unit/ui/test_bindings.py \
  tests/unit/infra/test_keymap_store.py -q
```

Observed GREEN result:

```text
42 passed in 7.66s
```

### Implementation

- Registered all three `glue.*` action IDs in `AwsTuiApp` and routed each to the
  mounted `GluePage`.
- Added mounted-Glue routing for forward/reverse Tab traversal, focused
  `OptionList` and `Select` arrow handling, Enter/Space activation, detail
  scrolling, and active-view refresh.
- Preserved modal and navigation precedence before Glue routing.
- Removed page-local `1/2/3/r` bindings so `KeymapStore` is the sole source of
  production shortcuts. The production regression remaps views to `7/8/9` and
  proves the old `2` default no longer switches views.
- Kept the existing page actions as the reusable UI/VM boundary for isolated
  unit tests.

### Visual And Snapshot Impact

There is no visual or snapshot change. No CSS, theme token, layout, pane state,
markup handling, or detail-focus structure changed. The existing `100x30` and
`150x44` snapshot matrix passed without updates:

```text
55 snapshots passed
```

### Final Verification

Production routing, keymap, Glue UI, factory, theme, hint, and snapshot gate:

```text
uv run pytest tests/integration/test_glue_page_routing.py \
  tests/integration/test_keybinding_wiring.py \
  tests/unit/ui/glue \
  tests/unit/ui/test_service_view_factory.py \
  tests/unit/ui/test_themes.py \
  tests/unit/ui/test_bindings.py \
  tests/unit/infra/test_keymap_store.py \
  tests/unit/vm/chrome/test_hint_legend.py \
  tests/snapshot/test_glue.py -q

304 passed in 27.61s
55 snapshots passed
```

Static and architecture gates:

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
334 files already formatted

uv run mypy src/aws_tui
Success: no issues found in 126 source files

bash scripts/check-layers.sh
layer rules clean
```

### Self-review

- Every new app route is gated by a mounted `#content-glue-page`; other service
  behavior is unchanged.
- Modal and navigation handlers still win before Glue focused-control routing.
- Filter overlays continue through Textual's native `OptionList` selection
  actions, including Enter commit and focus return.
- Removing any Glue action registration breaks the runtime binding tests;
  restoring page-local number bindings breaks the stale-default rebinding test.
- Previously approved title contrast, semantic classes and resets, literal AWS
  text, single detail-scroll focus targets, and stable layouts remain covered
  by the unchanged Glue UI/theme/snapshot suites.

### Concerns

- The pre-existing shell warning
  `/tmp/vmx-cargo-182/env: no such file or directory` still appears before
  commands and did not affect tests, static checks, or Git operations.
- No Task 4 functional or visual concern remains.
