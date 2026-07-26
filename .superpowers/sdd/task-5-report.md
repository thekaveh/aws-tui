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

## Final Recovery Findings (2026-07-26)

### Status

`DONE`

Query-view Refresh now repairs a transient selected-workgroup detail failure
without remounting Athena or switching its service source. Successful
continuation retries clear stale pager errors across context, History, Results,
and both Saved-query pagers.

### TDD Record

Focused RED coverage failed before production edits for the absent
`refresh_query_context()` API and stale error text after successful retry in
the workgroup, catalog, database, History, named-query, and prepared-statement
pagers. Results already cleared its error at retry start; its fail-then-retry
regression was green against the existing implementation.

The focused green run passed `8` VM regressions. The broader Athena VM/UI/app
run then passed `128` tests, including real-app checks that Refresh retains the
mounted page/source header and that a recovered continuation control restores
its IDLE styling, default tooltip, and enabled hint.

### Implementation

- Added `AthenaPageVM.refresh_query_context()`: it refreshes workgroups and,
  only when the retained workgroup detail is not healthy, reuses the guarded
  selection pipeline with stored catalog/database preferences. Existing
  generation, cancellation, privacy, and lifecycle boundaries therefore remain
  in force.
- Routed Query-view Refresh through that VM path; healthy contexts retain the
  previous refresh-only behavior.
- Cleared VM-level error text on successful context, History, and Saved pager
  continuations. Results already performed this recovery action.
- Added parameterized context/Saved continuation recovery regressions plus
  History, Results, page lifecycle, UI tooltip/class, and hint coverage.

### Verification

```text
Focused recovery regressions: 8 passed
Athena VM/UI/integration: 128 passed
Unit + in-process integration: 1,869 passed, 9 deselected
Athena snapshots: 120 passed; 108 snapshots passed
Ruff lint: all checks passed
Ruff format: 376 files already formatted
Mypy: no issues in 148 source files
Layer rules: clean
```

### Concerns

- The full snapshot command still reports ten pre-existing
  `test_demo_mode_snapshot` mismatches, one per theme. Athena snapshot coverage
  is green, and this recovery-only change did not regenerate unrelated demo
  baselines.
- The pre-existing `.zshenv` warning for missing `/tmp/vmx-cargo-182/env`
  continues to appear before commands; command exit results are unaffected.

## Final Results Pager Error Timing (2026-07-26)

### Status

`DONE`

Continuation retries now retain the existing Athena results error text while a
retry is in flight and when that retry fails again. The results load-more
control consequently keeps its error class and error tooltip until a
current-generation continuation succeeds. Initial-load clearing and generation
guards are unchanged.

### TDD Record

New RED tests were added before the production edit:

```text
uv run pytest \
  tests/unit/vm/athena/test_results_vm.py::test_results_retry_keeps_error_until_a_continuation_succeeds \
  tests/integration/test_athena_page.py::test_results_retry_keeps_button_error_visible_until_success -q
2 failed, 2 rerun
```

Both failures showed the stale error becoming `None` as soon as the retry
request started. After the writer change, the focused regressions and existing
retry behavior passed:

```text
3 passed
```

### Implementation

- Removed only the continuation pre-request `_error_text` clear from
  `AthenaResultsVM._load_more()`.
- Clear stale error text only after the pager continuation returns and the
  worker is still current, immediately before the successful rows/state
  notifications.
- Added VM timing coverage for failed-then-failed and failed-then-successful
  retries, including the busy interval.
- Added real Athena app coverage for the results button error class and tooltip
  during both retry intervals, after the failed retry, and after success.

### Verification

```text
Athena domain results: 2 passed
Athena VM: 110 passed
Athena UI: 10 passed
Athena integration: 10 passed
All VM tests: 610 passed
Ruff lint: all checks passed
Ruff format: 15 files already formatted
Mypy (Athena VM): no issues in 7 source files
Layer rules: clean
```

### Concerns

- Commands continue to print the pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; all verification commands exited successfully.
