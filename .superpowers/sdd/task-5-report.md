# Task 5 Report: Build and Register the Athena Textual Service

## Status

`DONE`

Athena is registered as the fourth AWS-only service after Glue. Production
composition builds the real Athena client and read-only SQL policy. The Textual
page provides operational Query, History, Results, and Saved views with stable
keyboard routing, focus behavior, lazy view setup, and awaited shutdown.

## TDD Record

RED was captured before production modules existed:

```bash
env -u NO_COLOR TERM=xterm-256color uv run pytest \
  tests/unit/services/athena tests/unit/ui/athena \
  tests/integration/test_athena_page.py tests/snapshot/test_athena.py -q
```

Result: collection stopped with four expected `ModuleNotFoundError` errors for
`aws_tui.services.athena` and `aws_tui.ui.widgets.athena`.

The final focused command was:

```bash
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/unit/services/athena tests/unit/ui/athena \
  tests/integration/test_athena_page.py tests/snapshot/test_athena.py -q
```

Result: `122 passed in 37.73s`; `96 snapshots passed`.

## Implementation

- Added `AthenaService` with AWS-only support, injectable client/policy
  factories, one service-owned `ServiceSelectionStore`, and fresh disposable
  page dependencies.
- Registered services in exact order: S3, EMR Serverless, Glue, Athena.
- Added the `content-athena-page` factory route and production client
  composition.
- Added a compact source/context header, four-view tab strip, multiline SQL
  editor, execute/cancel icon controls, stable status/detail rows, typed result
  table, history detail, and named/prepared saved-query workflows.
- Preserved AWS, SQL, and result values through Rich `Text` or widgets with
  markup disabled. Null and empty string results remain distinct.
- Routed focus, cursor, Enter, Backspace, refresh, view selection, execute, and
  cancel through the real `AwsTuiApp`.
- Added exact Athena defaults (`1`-`4`, `ctrl+enter`, `escape`). Configured
  rebindings replace Athena defaults.
- Made bare printable app bindings non-priority so the SQL editor receives
  letters, digits, punctuation, and spaces. Named and modified navigation keys
  retain priority; Athena Escape yields to focused widgets and modals first.
- Preserved Glue's shared `1`-`3` defaults through context-aware routing without
  changing either service's configured keymap.
- Added Athena hints and command-palette actions.
- Added token-based Athena styling to all ten built-in themes.

## Verification

```text
Focused Athena UI/integration/snapshot: 122 passed
Shared keymap/factory/hints/Glue regression: 45 passed
Ruff lint: all checks passed
Ruff format: 366 files already formatted
Mypy: no issues in 147 source files
Layer rules: clean
Non-snapshot git diff --check: clean
```

Snapshot coverage contains 96 SVG baselines:

- 8 required states across all 10 themes at `150x44` (80)
- 8 required states in Carbon and GitHub Light at `100x30` (16)
- 10 all-theme literal-content guard tests

`NO_COLOR`, `CLICOLOR`, and `CLICOLOR_FORCE` were explicitly unset for the
final snapshot pass. Pixel inspection covered Carbon and GitHub Light at both
sizes for empty query, populated results, and failure detail. Context controls,
tabs, editor, buttons, table headers/cells, status, and structured errors were
readable without overlap. The compact source label wraps within its fixed
three-row header.

## Concerns

- Deterministic multi-profile Athena demo data and Athena-to-S3 handoff belong
  to Task 6. Task 5 intentionally registers production composition; integration
  tests inject a deterministic client.
- The shell emits a pre-existing `.zshenv` warning for the missing
  `/tmp/vmx-cargo-182/env`; it does not affect command results.

## Review Findings Fix Wave (2026-07-26)

### Status

`DONE`

All Athena Task 5 review findings are fixed. Glue routing and the existing
Athena query ownership, stale-response, shutdown, and privacy boundaries remain
covered.

### TDD Record

Focused RED runs reproduced each finding before its production change:

- duplicate result aliases raised Textual `DuplicateKey`;
- pagination controls/action/binding/hint and continuation busy properties were
  absent;
- app teardown closed AWS clients and logs before hosted Athena shutdown;
- rebound tabs still rendered `1`-`4`;
- workgroup managed-result detail and page lifecycle state were absent;
- execute/cancel command predicates and hints accepted invalid or unowned state;
- all ten focused-tab token guards found `$accent-soft` instead of `$text`;
- rebound-tab snapshots were absent and result fixtures had no duplicate alias.

Each focused regression was then run green before continuing.

### Implementation

- Result columns now use index-derived internal keys while retaining literal,
  duplicate AWS labels and all row cells.
- Added one-page continuation controls for workgroups, catalogs, databases,
  History, Results, named queries, and prepared statements. Controls expose
  visible, enabled, busy, retry/error, keymap, app-routing, and hint state
  without reloading page one or fetching all pages.
- Hosted Athena shutdown and remote-query cleanup now complete before AWS
  clients, sessions, or the log sink close.
- Tab captions resolve from the current `KeymapStore`; configured bindings
  replace visible defaults.
- Selected workgroups load `get_workgroup` detail before catalogs can form an
  executable context. Enforced S3 output and Athena-managed results are shown
  before execution with stable privacy-safe errors and stale-response guards.
- Execute, cancel, and load-more hints now follow live command/pager predicates.
  Execute requires valid read-only SQL and complete context; visible cancel
  requires an owned active query while lifecycle cleanup can still interrupt a
  pending submission.
- Focused Athena tabs use `$text` on `$bg-sel` in all ten themes. Programmatic
  WCAG contrast guards require at least `4.5:1`.
- Athena snapshots now cover duplicate aliases, empty strings, rebound focused
  tabs, and compact content at `150x44` and `100x30`.

### Verification

```text
Focused Athena VM/service/UI/integration/snapshots: 260 passed
Shared keymap/factory/hints/Glue regression: 48 passed
Unit + in-process integration + e2e: 1,865 passed, 9 deselected
Athena snapshots: 120 passed, 108 snapshots passed
Full snapshot command: 734 passed; 10 pre-existing demo snapshots mismatched
Ruff lint: all checks passed
Ruff format check: 376 files already formatted
Mypy: no issues in 148 source files
Layer rules: clean
Non-snapshot git diff --check: clean
```

Only Athena snapshots were regenerated: 9 fixtures across 10 themes at
`150x44` (90) and Carbon/GitHub Light at `100x30` (18). Visual inspection of
dark/light focused-tab and duplicate-result fixtures at both sizes found no
overlap, clipping, blank content, or unreadable focus state.

### Concerns

- The full snapshot command has ten pre-existing `test_demo_mode_snapshot`
  mismatches. Current demo output includes the Athena navigation row registered
  by the original Task 5 commit, while those checked-in non-Athena baselines do
  not. They were left untouched to honor the instruction to regenerate only
  Athena snapshots; all other full-suite snapshot comparisons pass.
- The shell still emits the pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; command results are unaffected.
