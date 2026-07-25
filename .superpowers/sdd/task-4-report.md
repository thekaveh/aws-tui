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
