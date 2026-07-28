# Task 3 Report: Athena Query and Result ViewModels

## Status

Implemented Athena plan Task 3 only on `codex/aws-service-expansion-study`.

## Implementation

- Added `AthenaQueryVM` with VMx async execute/cancel commands, fail-closed SQL
  and context validation, deterministic context-plus-UUID request tokens, and
  double-submit suppression.
- Added app-owned query lifecycle tracking. Explicit cancel, context replacement,
  and shutdown stop only the active query started by this VM; terminal observation
  revokes ownership.
- Added bounded polling at `0.25, 0.5, 1, 2, 4, 5, 5...` seconds, structured
  state/statistics/error detail, and first-page result loading on success.
- Added generation checks after every query and result await. Shutdown, cancel,
  context replacement, query replacement, and disposal invalidate stale work.
  Late submit responses are stopped without publishing stale errors.
- Added `AthenaResultsVM` with VMx `TokenPagedComposition`. It fetches one Athena
  result page at a time, accumulates only pages explicitly requested, preserves
  domain-owned header handling, and rejects column metadata changes between pages.
- Added null-aware render cells. Raw rows preserve `None`, empty strings, and
  literal `"NULL"` independently; render-cell reprs never include result values.
- Added stable pane-local error mapping that never copies provider, SQL, result,
  request-token, or raw exception text into VM errors.
- Added idempotent synchronous disposal of VMx commands, pagers, subjects, and
  component VMs. Graceful remote stop remains in awaited `shutdown()`.

## TDD Evidence

### RED 1: Missing Athena VM modules

The complete query/results tests were added before production code:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q
```

Result:

```text
ERROR tests/unit/vm/athena/test_query_vm.py
ModuleNotFoundError: No module named 'aws_tui.vm.athena'
ERROR tests/unit/vm/athena/test_results_vm.py
ModuleNotFoundError: No module named 'aws_tui.vm.athena'
2 errors
```

### RED 2: Lifecycle review regressions

Requirements review added stale-submit flags and mismatched-context cleanup tests
before changing production code:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py -q
```

Result:

```text
FAILED test_context_replacement_drains_stale_submit_and_clears_submitting
FAILED test_mismatched_execution_identity_fails_closed_before_polling
FAILED test_mismatched_polled_context_never_replaces_active_state
FAILED test_shutdown_stops_query_returned_after_cancelled_submit
4 failed, 17 passed
```

The implementation now clears submission state and asks the authority-enforcing
domain client to stop every out-of-context app-started execution.

### RED 3: Stale cleanup error publication

```text
uv run pytest \
  tests/unit/vm/athena/test_query_vm.py::test_stale_submit_cleanup_failure_cannot_publish_into_new_context \
  -q
```

Result:

```text
FAILED test_stale_submit_cleanup_failure_cannot_publish_into_new_context
AssertionError: visible_errors contained 'Athena query request failed'
1 failed
```

Stale cleanup now remains silent while explicit cancel/shutdown failures retain
normal pane-local feedback.

### RED 4: Result completion after shutdown

```text
uv run pytest \
  tests/unit/vm/athena/test_query_vm.py::test_shutdown_invalidates_result_page_that_ignores_cancellation \
  -q
```

Result:

```text
FAILED test_shutdown_invalidates_result_page_that_ignores_cancellation
AssertionError: rows contained ('STALE_RESULT',)
1 failed
```

Query lifecycle transitions now invalidate the results pager generation before
draining the query task.

### GREEN: Complete Task 3 contract

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q
30 passed in 0.36s
```

## Verification

Focused Athena domain and policy:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py -q
257 passed in 0.76s
```

Full VM regression after final lifecycle fixes:

```text
uv run pytest tests/unit/vm -q
529 passed in 29.61s
```

Privacy and crash-path regression:

```text
uv run pytest tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_notifications.py -q
38 passed in 0.36s
```

The non-overlapping domain, VM, and privacy runs total **824 passing tests**.

Static, formatting, and layer checks:

```text
uv run mypy src/aws_tui/domain/query.py src/aws_tui/domain/athena.py \
  src/aws_tui/vm/athena
Success: no issues found in 6 source files

uv run ruff check src/aws_tui/vm/athena tests/unit/vm/athena
All checks passed!

uv run ruff format --check src/aws_tui/vm/athena tests/unit/vm/athena
6 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --cached --check
```

Every listed command exited 0. The unrelated shell startup warning for the
missing `/tmp/vmx-cargo-182/env` appeared on commands and is omitted above.

## Changed Files

- `src/aws_tui/vm/athena/__init__.py`
- `src/aws_tui/vm/athena/_errors.py`
- `src/aws_tui/vm/athena/query_vm.py`
- `src/aws_tui/vm/athena/results_vm.py`
- `tests/unit/vm/athena/test_query_vm.py`
- `tests/unit/vm/athena/test_results_vm.py`
- `.superpowers/sdd/task-3-report.md`

## Concerns

- If a real `StartQueryExecution` request reaches Athena but local cancellation
  prevents the client from ever returning its execution ID, the VM has no ID to
  stop. The context-scoped client request token prevents accidental duplicate
  execution, and tests cover clients that return after cancellation, but this
  transport-level ambiguity cannot be resolved at the VM boundary alone.
- Result pages intentionally accumulate after the user requests them because VMx
  token paging is forward-only. The VM never prefetches or materializes the full
  Athena result set.
- Structured Athena failure messages, state reasons, and result locations remain
  available as explicit UI properties. They are excluded from reprs and are never
  copied into VM exception/error text or logs.

---

# Iceberg Task 3 Addendum: Connection-Preserving Glue/Athena Navigation

## Status

Implemented Iceberg plan Task 3 on `codex/aws-service-expansion-study`.
The implementation commit is the commit containing this addendum.

## Implementation

- Added frozen, slot-backed `OpenAthenaTableRequest` and
  `OpenGlueTableRequest` VMx envelopes carrying the complete `TableRef`.
- Added `ReadOnlySqlPolicy.table_refs()` using sqlglot scope traversal. It
  resolves current catalog/database defaults, excludes CTE and subquery aliases,
  de-duplicates repeated physical tables, preserves distinct physical sources,
  and fails closed for invalid SQL, writes, multiple statements, or foreign
  catalogs.
- Added exact identifier quoting and bounded starter SQL generation, including
  optional non-negative integer `FOR VERSION AS OF` and mandatory `LIMIT 100`.
- Added Glue and Athena VM source/destination operations. Glue selects exact
  catalog/database/table identities across later provider pages. Athena preserves
  the active workgroup, loads exact later-page catalogs/databases, prefills the
  query editor, and never executes generated SQL. Athena-to-Glue navigation is
  available only from the active query view with one distinct visible table.
- Added app action and command-palette routing for both directions. The
  composition root resolves the exact connection, requires region equality,
  probes auth, transactionally switches/mounts the destination, waits for setup,
  and selects the resource.
- Added generation-owned lifecycle handling so the newest competing handoff
  wins. Missing/deleted connections and region mismatches fail before mutation;
  destination failures restore the prior connection/service/selection and
  redacted Athena editor state.
- Added stable visible failure toasts and structured logs that retain no SQL or
  exception text. The rollback snapshot excludes SQL from repr output.

## TDD Evidence

Initial interface RED:

```text
uv run pytest tests/unit/vm/test_messages.py \
  tests/unit/domain/test_sql_policy.py \
  tests/unit/vm/athena/test_page_vm.py \
  tests/unit/vm/glue/test_page_vm.py -q

4 collection errors: missing messages, starter SQL, and VM interfaces
```

App orchestration RED:

```text
uv run pytest tests/integration/test_glue_athena_navigation.py -q

8 failed: actions unregistered, messages ignored, no rollback/toasts,
and no competing-request ownership
```

Pagination RED:

```text
uv run pytest \
  tests/unit/vm/glue/test_page_vm.py::test_catalog_open_table_selects_exact_table_identity \
  tests/integration/test_glue_athena_navigation.py::test_glue_to_athena_preserves_identity_and_prefills_without_running -q

2 failed: exact targets beyond the first provider page were unavailable
```

Visible-query RED:

```text
uv run pytest \
  tests/unit/vm/athena/test_page_vm.py::test_open_table_in_glue_requires_one_visible_unambiguous_table -q

1 failed: stale editor SQL could navigate while History was active
```

Each RED was followed by the minimal implementation and a focused GREEN run.

## Verification

Final focused contract, warnings treated as errors:

```text
312 passed in 6.43s
```

Final affected Glue/Athena VM and integration suite:

```text
188 passed in 17.13s
```

Broad domain, VM, and integration regression:

```text
1595 passed, 9 deselected in 303.89s
```

Standalone Glue/Athena page and navigation integration:

```text
20 passed in 16.84s
```

Static and release checks:

```text
uv run mypy src/aws_tui
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
386 files already formatted

bash scripts/check-layers.sh
layer rules clean

uv build
Successfully built source and wheel distributions

git diff --check
```

## Changed Files

- `src/aws_tui/app.py`
- `src/aws_tui/domain/sql_policy.py`
- `src/aws_tui/vm/athena/page_vm.py`
- `src/aws_tui/vm/glue/catalog_vm.py`
- `src/aws_tui/vm/glue/page_vm.py`
- `src/aws_tui/vm/messages.py`
- `tests/integration/test_glue_athena_navigation.py`
- `tests/unit/domain/test_sql_policy.py`
- `tests/unit/vm/athena/test_page_vm.py`
- `tests/unit/vm/glue/test_page_vm.py`
- `tests/unit/vm/test_messages.py`

## Concerns

- Athena preserves the selected workgroup and validates catalog/database
  visibility before prefilling. It intentionally does not execute a provider
  query or metadata lookup to re-prove table existence; the source Glue
  `TableRef` remains authoritative and generated SQL stays inert.
- The repository shell emits the pre-existing missing
  `/tmp/vmx-cargo-182/env` startup warning; it did not affect command results.
- `.superpowers/sdd/progress.md` was not edited.

## Review Fix Addendum: 2026-07-25

### Status

All Athena Task 3 review findings are closed on
`codex/aws-service-expansion-study`. This addendum supersedes the first original
concern above: submission ambiguity is now resolved at the VM boundary by
shielding and draining the response-bearing submission task.

### Implementation

- `AthenaQueryVM` now gives each `start_query` call an independently owned,
  shielded task. Shutdown, explicit cancel, context replacement, external task
  cancellation, and synchronous disposal drain that task to either an execution
  reference or an error. Accepted executions are stopped through the
  authority-enforcing domain API even when stale response metadata is
  out-of-context.
- Stale submission errors and cleanup failures remain silent. Shutdown awaits
  submission completion and stop dispatch deterministically; no response task or
  exception is abandoned.
- Context replacement now invalidates old generations and installs the new local
  context before remote cleanup. Old execution references move to a private
  cleanup registry. A failed stop cannot block the new context or publish into
  its pane, and shutdown retries retained cleanup ownership.
- `AthenaResultsVM` now owns one pager, outer load-more command, and active-task
  set per generation. Replacing a query retires the old generation immediately,
  so a cancellation-resistant old page cannot keep the new command disabled.
  `shutdown()` retires and drains every surviving generation.
- Existing request-token derivation, domain stop authority, bounded polling,
  one-page no-prefetch behavior, null rendering, privacy mappings, and
  synchronous disposal surfaces are unchanged.

### TDD Evidence

The review regressions were added before production edits.

Initial RED:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q

8 failed, 30 passed
```

The failures covered all three lifecycle transitions losing a detached accepted
submission, delayed submission-error draining, direct disposal, failed-stop
context replacement, reused result commands, and missing result-worker
shutdown.

An additional ownership edge was pinned during self-review:

```text
uv run pytest \
  tests/unit/vm/athena/test_query_vm.py::test_shutdown_stops_detached_submission_with_mismatched_response_context \
  -q

1 failed
```

Final GREEN:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q

39 passed in 0.41s
```

### Verification

Full VM regression:

```text
uv run pytest tests/unit/vm -q
538 passed in 29.56s
```

Athena domain, query records, and SQL policy:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py -q
257 passed in 0.60s
```

Privacy and crash paths:

```text
uv run pytest tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_notifications.py -q
38 passed in 0.35s
```

The non-overlapping VM, domain/policy, and privacy runs total **833 passing
tests**.

Static, formatting, and layers:

```text
uv run mypy src/aws_tui/domain/query.py src/aws_tui/domain/athena.py \
  src/aws_tui/vm/athena
Success: no issues found in 6 source files

uv run ruff check src/aws_tui/vm/athena tests/unit/vm/athena
All checks passed!

uv run ruff format --check src/aws_tui/vm/athena tests/unit/vm/athena
6 files already formatted

scripts/check-layers.sh
layer rules clean
```

Every listed command exited 0. The unrelated shell startup warning for the
missing `/tmp/vmx-cargo-182/env` appeared on commands and is omitted above.

### Updated Concerns

- Shielding intentionally makes graceful shutdown wait for the submission
  transport to return. This is required to recover the accepted execution ID;
  the VM does not impose a second timeout over the configured AWS transport
  timeout.
- A context-replacement stop failure is retained silently and retried on the
  next lifecycle cleanup. If the final shutdown stop also fails, the existing
  pane-local stable error is shown rather than retrying indefinitely.
- Results still accumulate only pages explicitly requested by the user. Retired
  cancellation-resistant pages are tracked until completion but can never
  publish or disable the current generation.

## Remaining P1 Lifecycle Addendum: 2026-07-26

### Status

The final two Athena Task 3 P1 lifecycle findings are closed on
`codex/aws-service-expansion-study`. The prior result-pager ownership fixes and
all existing query contracts remain unchanged.

### Implementation

- A cancelled start submission now transfers to an independently owned finalizer
  task before the cancelling query worker awaits again. Repeated cancellation of
  that worker cannot sever cleanup ownership. The finalizer retrieves submission
  failures silently and moves accepted execution references through the existing
  retained-stop path.
- `shutdown()` drains every live submission finalizer before retrying retained
  execution stops. Synchronous `dispose()` still cancels and releases VMx
  surfaces immediately, but no longer removes the async finalizer that owns the
  remote submission outcome.
- Out-of-context accepted references enter the cleanup registry before their
  first stop await. A stop failure after context replacement is silent in the
  replacement generation and remains retained for lifecycle retry. Successful
  stops remove ownership, so later shutdown calls do not stop the same execution
  again.

### TDD Evidence

The deterministic regressions cover cancel plus dispose for both accepted and
failed submissions, and an out-of-context accepted stop that fails after a new
context is installed and succeeds during shutdown retry.

The final tests were mutation-checked by temporarily removing the production
ownership changes:

```text
uv run pytest \
  tests/unit/vm/athena/test_query_vm.py::test_cancel_then_dispose_keeps_submission_finalizer_without_stale_publication \
  tests/unit/vm/athena/test_query_vm.py::test_stale_accepted_stop_failure_in_new_context_is_retried_by_shutdown \
  tests/unit/vm/athena/test_query_vm.py::test_mismatched_execution_identity_fails_closed_before_polling \
  -q

3 failed, 1 passed in 1.42s
```

After restoring the implementation:

```text
4 passed in 0.31s
```

Each lifecycle node was also run in its own subprocess with a hard five-second
timeout. The new lifecycle waits are independently capped at one second inside
the tests. All four nodes completed in `0.31-0.35s`; no pytest process remained
after the runs.

Complete Athena query/results VM coverage:

```text
uv run pytest tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_results_vm.py -q

42 passed in 0.38s
```

### Verification

Full VM regression:

```text
uv run pytest tests/unit/vm -q
541 passed in 29.55s
```

Full unit-domain regression:

```text
uv run pytest tests/unit/domain -q
490 passed in 15.72s
```

Privacy and crash paths:

```text
uv run pytest tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_notifications.py -q

38 passed in 0.34s
```

The non-overlapping VM, domain, and privacy runs total **1,069 passing tests**.

Full static, formatting, and layer checks:

```text
uv run mypy src/aws_tui
Success: no issues found in 136 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
354 files already formatted

scripts/check-layers.sh
layer rules clean
```

Every listed verification command exited 0. The unrelated shell startup warning
for the missing `/tmp/vmx-cargo-182/env` appeared on commands and is omitted
above. The final verification harness enforced hard subprocess timeouts of 10
seconds for focused, privacy, static, formatting, layer, and diff checks; 45
seconds for the full domain suite; and 60 seconds for the full VM suite.

### Updated Concerns

- Graceful shutdown still waits for the configured AWS submission transport
  timeout so it can recover an accepted execution ID; the VM does not add a
  second timeout.
- Synchronous disposal cannot await network completion. It preserves the
  finalizer on the running event loop, while deterministic process teardown
  continues to require awaited `shutdown()` before the loop is closed.

## Task 3 Review Corrections: 2026-07-26

### Status

All five Task 3 review findings are closed on
`codex/aws-service-expansion-study`. The corrective commit range is
`da9cc3b..HEAD`; the final commit hash is reported after the one coherent
commit is created.

### Implementation

- Glue/Athena table handoffs now run as serialized transactions. A request
  captures one pre-mutation snapshot, retains rollback ownership under the
  transaction lock, and fully restores the old source before a newer request
  may prevalidate or commit. Tests cover supersession during switch, mount,
  and open by a missing connection, a region mismatch, and a successful newer
  request.
- Rollback is an explicitly tracked, shielded task. Repeated cancellation and
  application shutdown drain that owner instead of detaching it. Restoration
  includes connection, authentication state, service, Athena context/view/SQL,
  and established Glue database/table/view selection.
- Glue database/table discovery and Athena catalog/database discovery reject
  repeated tokens, stop after four consecutive no-progress pages, and enforce
  an absolute 64-request cap. A small finite run of empty continuation pages
  remains valid. Bounds fail with stable `ProviderError` messages outside the
  token-owning stack frame, so provider tokens are absent even from tracebacks
  rendered with captured locals.
- `OpenAthenaTableRequest` and `OpenGlueTableRequest` now validate exact
  `TableRef` identity and exact nonblank string fields before mutation.
  Athena snapshot IDs accept only `None` or exact non-negative integers;
  booleans and integer-like values are rejected. Both messages remain frozen
  and slot-backed.
- Read-only SQL reference extraction now covers EXPLAIN's inner query,
  DESCRIBE targets, and SHOW COLUMNS targets. Context defaults, quoted names,
  CTE visibility, deduplication, and exact-catalog fail-closed behavior are
  preserved through sqlglot AST/token traversal.
- Textual selection workers now defer coroutine creation until the widget
  worker owns execution. Programmatic Athena context synchronization suppresses
  its own stale Select events, while Glue catalog refresh remains safe during
  partial teardown without skipping its initial mount render.

### TDD And Hang Evidence

The initial focused RED run failed only on the newly specified contracts:

```text
79 failed, 305 passed
```

The initial navigation RED run exposed partial destination state,
cancellation loss, and missing rollback ownership:

```text
10 failed, 9 passed
```

The interrupted success-mount probe was terminated by its external process
watchdog. Faulthandler showed the newer navigation blocked on
`_table_navigation_lock` while the older transaction was still draining a
Textual refresh. Teardown then exposed eagerly created Athena selection
coroutines and a stale Glue refresh callback. Coroutine creation is now lazy,
programmatic selection messages are suppressed at their source, and every
adversarial wait is bounded in-test: two seconds for stage events, ten seconds
for service setup, and fifteen seconds for aggregate overlap completion.

The exact success-mount probe subsequently completed under a 30-second
faulthandler watchdog:

```text
1 passed in 1.08s
```

The final changed-file suite ran with reruns disabled and runtime/unraisable
warnings promoted to errors:

```text
404 passed in 18.37s
```

All nine switch/mount/open supersession combinations remain present. No
overlap case was removed or weakened.

### Verification

Broad domain and VM regression:

```text
1494 passed in 45.21s
```

Full in-process integration regression:

```text
195 passed, 9 deselected in 286.23s
```

Affected UI and privacy/crash regressions:

```text
357 passed in 18.45s
38 passed in 0.41s
```

Static, formatting, architecture, diff, and build gates:

```text
uv run mypy src/aws_tui
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
386 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --check
clean

uv build --no-build-isolation
Successfully built aws_tui-0.8.0.tar.gz and aws_tui-0.8.0-py3-none-any.whl
```

Focused runs used external 30-180 second watchdogs; the broad integration run
used a 420-second watchdog, above its observed clean runtime. Process sweeps
after the interrupted probe and after final integration found no stale pytest
or `uv run pytest` process.

### Updated Concerns

- Automatic cross-service discovery intentionally stops after 64 continuation
  requests or four consecutive no-progress pages. A target beyond that bound
  fails visibly instead of issuing an unbounded provider request loop.
- Durable rollback waits for the repository's normal service setup and
  provider transport completion. It does not detach work or impose a second
  production timeout over the configured transport timeout.
- SQL table visibility remains fail-closed: foreign catalogs, malformed
  special statements, and unresolved context defaults produce no table
  references.

## Remaining Task 3 P1 Lifecycle Corrections: 2026-07-26

### Root Cause

Three ownership boundaries were still incomplete after the first review
correction:

1. Table requests had a generation and transaction lock, but ordinary nav-menu
   selections did not participate in that ownership. Cancelling a table
   worker therefore launched its durable rollback after a newer user selection
   and restored the stale pre-handoff service over the user's destination.
2. Shutdown drained table tasks before disposing the service-navigation
   subscription. A message delivered after the drain snapshot could create a
   new unowned task that survived `_aws_tui_shutdown()`.
3. The table rollback snapshot retained only Athena view and SQL. Rebuilding
   Athena disposed the old query/results VMs, so execution identity, detail,
   loaded rows, continuation token, result state, and prior history/saved
   selections were irretrievably cleared.

### Implementation

- One shared navigation epoch now records either `table` or `external`
  ownership. Table-to-table supersession still restores the stable base before
  the newer request validates. User navigation, settings, other services,
  source switching, and S3 handoffs claim external ownership first, cancel
  retained table tasks, and prevent every later table rollback/remount stage
  from overwriting that selection.
- Application shutdown now synchronously closes and disposes the
  service-navigation intake before its first await. It then repeatedly drains
  both retained navigation and rollback sets until neither can publish more
  work. Late hub delivery and direct callback delivery are ignored.
- `AthenaPageVM`, `AthenaQueryVM`, and `AthenaResultsVM` expose frozen,
  slot-backed export/restore snapshots. SQL, execution IDs, provider detail,
  output locations, result columns/rows/tokens, errors, and selected IDs are
  excluded from every snapshot repr.
- A fresh Athena page restores exact connection/region/workgroup/catalog/
  database context, including selections found on bounded continuation pages.
  It then restores active view, SQL, execution reference/detail, loaded result
  state, and history/saved selection. Results are seeded through the existing
  `TokenPagedComposition` command path without an Athena fetch, and SQL is
  never auto-executed.
- Lightweight shutdown harnesses that intentionally construct `AwsTuiApp`
  without `__init__` retain their established behavior through guarded empty
  navigation owners.

### TDD Evidence

The RED phase reproduced each defect independently:

```text
Athena results snapshot import: collection failed (missing API)
Athena page snapshot: AttributeError (missing export API)
user selects S3 during paused switch: final selection was Glue
late shutdown message: navigation task started after first drain
failed handoff rollback: restored execution_ref was None
later-page context rollback: "Athena snapshot context is unavailable"
```

The new adversarial matrix covers switch, mount, and open pauses crossed with
S3, Settings, and EMR Serverless user navigation. Failed, repeatedly
cancelled, and newer-request-superseded handoffs each assert exact Athena
execution ID, query detail, columns, rows, result state, context, and
history/saved selection with no additional `start_query` call.

Final Task 3 contract with runtime/unraisable warnings promoted to errors:

```text
516 passed in 41.01s
```

This includes all new lifecycle tests, message validation, SQL extraction,
Glue/Athena VMs, affected Glue/Athena widgets, and cross-service integration.

### Verification

Broad domain and VM regression:

```text
1498 passed in 45.16s
```

Privacy and crash-dump regression with warnings promoted to errors:

```text
282 passed in 1.28s
```

Full UI regression:

```text
357 passed in 18.53s
```

Full in-process integration regression:

```text
208 passed, 9 deselected in 291.40s
```

No pytest or `uv run pytest` process remained after the integration run.

Static, formatting, architecture, diff, and build gates:

```text
uv run mypy src/aws_tui
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
386 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --check
clean

uv build --no-build-isolation
Successfully built aws_tui-0.8.0.tar.gz and
aws_tui-0.8.0-py3-none-any.whl
```

An additional diagnostic run of the entire unrelated UI suite with every
warning promoted to an error exposed its existing suite-order resource warning
(two sockets and an event loop are collected during the brand-banner test).
The brand-banner test passes in isolation, the affected Glue/Athena UI tests
pass under warnings-as-errors in the 516-test contract above, and the full UI
suite passes under its normal configured warning policy.

## Final Task 3 Snapshot And Rollback Corrections: 2026-07-26

### Root Cause

The final review found three remaining consistency gaps:

1. Rollback ownership checks and host mutation were separate. A rollback could
   pass its ownership check, pause in switch or mount, and overwrite a newer
   S3, Settings, or service selection after that user navigation completed.
2. Athena snapshots represented visible state but not operation ownership.
   Exporting during submission, execution, cancellation, or result pagination
   could therefore recreate `QUEUED`, `RUNNING`, or loading state without the
   task that owned it.
3. Snapshot restore trusted nested records. Cross-execution results, malformed
   rows, invalid cell types, impossible token/loading/error combinations, and
   nested non-snapshot objects could be installed before rejection.

### TDD Evidence

The first snapshot RED run produced the expected failures:

```text
15 failed, 4 passed, 103 deselected
```

The active-operation app probe switched from Athena to Glue instead of failing
before mutation. The rollback probe ended with `athena` selected after a newer
S3 selection and also reproduced concurrent pane mutation. Both probes failed
consistently through the repository's two configured reruns.

After the implementation:

```text
19 focused snapshot tests passed
11 active-preflight and rollback race tests passed
```

The new race matrix pauses rollback at switch, mount, and Athena state restore,
then selects S3, Settings, or EMR Serverless. All nine combinations assert that
the newer user destination wins and that navigation and rollback task sets are
empty. Two additional cases prove query execution and result load-more preflight
without any service switch.

### Implementation

- A shared service-navigation transaction lock now owns rollback, ordinary
  menu navigation, Settings/service mounting, S3 handoffs, and source swaps.
  External navigation claims its generation synchronously, then performs host
  mutation under the lock. A rollback that was already in progress completes
  first; the waiting newer user transaction then reasserts and mounts its exact
  destination. A rollback starting later sees external ownership and performs
  no mutation. The existing table transaction lock remains responsible for
  table-to-table stable-base ordering.
- Athena page capture fails before handoff mutation while query submission,
  execution, cancellation, initial result loading, or continuation loading has
  an owner. The app catches that stable `ValueError`, records no provider
  payload, leaves the current VM and host unchanged, and presents a bounded
  wait-for-completion toast.
- Page, query, and result snapshots now require their exact frozen record
  types. Validation runs before selection, context, pager, query, or host state
  changes. It checks context and execution identity, terminal query state,
  result execution identity, exact tuple structure, row widths, string/null
  cells, continuation/loading/state/error coherence, and nested domain value
  types. `QUEUED`, `RUNNING`, `LOADING`, and ownerless load-more snapshots are
  rejected.
- Legitimate duplicate Athena column names remain valid and round-trip exactly.
  Terminal snapshots restore without query execution or result fetching.
  Snapshot records continue to exclude SQL, execution IDs, rows, tokens,
  provider detail, and error payloads from `repr`.
- Forged nested payload tests render `TracebackException(capture_locals=True)`
  and real `CrashDump` files. Stable rejection text is preserved and neither
  the malicious marker nor valid snapshot SQL appears in either artifact.

### Coverage And Verification

This correction adds 26 collected test cases: 15 domain/VM snapshot cases and
11 integration preflight/race cases. Focused coverage over the three modified
Athena VMs is:

```text
AthenaPageVM     87%
AthenaQueryVM    88%
AthenaResultsVM  80%
Aggregate        86%
165 passed in 79.58s
```

Affected and broad verification:

```text
122 Athena query/results/page VM tests passed
43 Glue/Athena navigation integration tests passed
40 Athena-to-S3 handoff tests passed
18 Glue-to-S3 handoff tests passed
21 Settings navigation tests passed
1513 domain and VM tests passed
119 privacy/crash tests passed with runtime/unraisable warnings as errors
357 UI tests passed
219 integration tests passed, 9 deselected, in 302.61s
```

Static, architecture, packaging, and process gates:

```text
uv run mypy src/aws_tui
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
386 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --check
clean

uv build --no-build-isolation
Successfully built aws_tui-0.8.0.tar.gz and
aws_tui-0.8.0-py3-none-any.whl
```

No pytest, `uv run pytest`, or retained navigation process remained after the
bounded verification runs. `.superpowers/sdd/progress.md` was not modified.

## Final Narrow Snapshot Boundary Corrections: 2026-07-28

### Root Cause

The last Task 3 review exposed three boundary gaps:

1. Query snapshots validated terminal identity but not the complete visible VM
   state. A terminal execution could therefore be paired with an ownerless
   loading/error pane, stale validation text, or impossible query error data.
2. Page restore persisted the target selection and changed workgroup/catalog
   state while it was still discovering whether the complete target context
   existed. A later rejection left a partially restored page.
3. Snapshot records still rendered selected enum/string fields, and public
   restore frames retained arbitrary caller objects while raising. Captured
   traceback locals could therefore expose a hostile `repr`.

### TDD Evidence

The initial focused RED command covered terminal query coherence, normal
terminal preservation, missing context at every level, and arbitrary/exact
payload privacy across page/query/results:

```text
14 failed, 4 passed in 0.65s
```

The failures showed accepted ownerless terminal states, catalog/database
rejection after visible context mutation, and value-bearing snapshot
representations. A subsequent saved-query pair test failed because snapshots
accepted a kind without an ID and an ID without a kind.

After the implementation and the saved-query coherence correction:

```text
145 passed in 1.08s
```

This includes all Athena page/query/results VM tests with runtime and
unraisable warnings promoted to errors.

### Implementation

- `AthenaPageSnapshot`, `AthenaQuerySnapshot`, and `AthenaResultsSnapshot` now
  have entirely value-free generated representations.
- Each public restore API immediately passes its argument through a total,
  non-raising preparation helper, deletes the raw argument, and raises only
  from a value-free boundary frame. Exact hostile records and arbitrary
  hostile objects are absent from captured locals, formatted tracebacks,
  exception graphs, and real crash dumps.
- Query terminal validation now matches restorable VM states: active states
  and owner-like loading states are rejected; terminal executions require an
  idle query pane and no UI/validation error text; success forbids query error
  data; cancellation forbids query error data; failed/success/cancel snapshots
  produced by the VM continue to round-trip.
- Page restore validates the complete nested snapshot and saved-query pair
  before discovery. Workgroups, workgroup detail, catalogs, and databases are
  then fetched into a private value-free stage with bounded/repeated-token and
  empty-page guards. Missing resources, malformed pages, and provider failures
  return a stable error without changing context, lists, sub-VMs, active view,
  or the selection store.
- A successful commit installs validated staged pages through the existing
  `TokenPagedComposition` workers, preserves continuation tokens, restores the
  exact query/results state without provider result fetching, and only then
  persists selections.
- Adversarial tests compare complete observable page state before and after
  workgroup, detail, catalog, and database failures. Duplicate result column
  names and valid succeeded/failed/cancelled snapshots remain supported.

### Verification

Affected snapshot, navigation, privacy, crash-dump, and app sanity matrix:

```text
291 passed in 97.61s
```

Broad domain and VM regression:

```text
1536 passed in 45.61s
```

Full integration regression:

```text
219 passed, 9 deselected in 277.65s
```

Static, architecture, formatting, diff, and packaging gates:

```text
uv run mypy src/aws_tui
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
386 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --check
clean

uv build --no-build-isolation
Successfully built dist/aws_tui-0.8.0.tar.gz
Successfully built dist/aws_tui-0.8.0-py3-none-any.whl
```

No pytest or `uv run pytest` process remained after verification.
`.superpowers/sdd/progress.md` was not modified. This correction is the single
focused commit immediately following `14a6997`.

## Final Transaction-Boundary Closure: 2026-07-28

### Root Cause And Implementation

- Page snapshots carried query/history/saved state but not the context pager and
  workgroup-detail graph. Same-context rollback therefore depended on damaged
  live state after a provider outage. The snapshot now embeds an exact,
  value-free context stage and restores it with zero provider calls.
- Snapshot installation mixed mutation with child notifications. Results,
  query, history, and saved VMs now install silently; the page commits all live
  fields and selection-store values before publishing any child or page
  notification. Per-subscriber isolation ensures throwing observers neither
  interrupt publication nor leak their exception payloads into logs.
- Provider and snapshot checks validated outer dataclass types but not every
  nested field. One shared Athena domain-validation module now enforces exact
  runtime types and invariants for workgroups, catalogs, databases/table refs,
  query history/detail/statistics/errors, saved queries, prepared statements,
  contexts, execution refs, and result columns.
- Direct writes to VMx token-pager internals are centralized in one compatibility
  adapter. Its VMx 3.x contract checks item copying, token/has-more behavior,
  loaded state, no fetch during seed, and loud failure when expected internals
  change.
- Pytest now explicitly preserves function-scoped async fixtures, removing the
  repository's pytest-asyncio configuration deprecation.

### TDD Evidence

Initial outage, observer, and pager probes failed as expected:

```text
2 failed: same-context outage restore and observer-isolated publication
1 collection error: missing Athena VMx pager compatibility adapter
```

Recursive exact-record and staged-provider probes then exposed ten accepted
malformed paths:

```text
10 failed, 1 passed
```

A final observer privacy probe failed because subscriber exception text was
present in captured logs. After the value-free logging correction, all focused
probes passed.

### Verification

```text
191 Athena VM tests passed with warnings as errors
917 expanded Athena/Glue/Iceberg/navigation tests passed with warnings as errors
1561 domain and VM tests passed
229 privacy/crash/Athena tests passed with warnings as errors
219 integration tests passed, 9 external-service tests deselected
185 Athena/Glue snapshot tests passed; 163 visual snapshots matched
Athena VM coverage: 84.96% (70% required)
```

The full warning-strict domain/VM run reached 1,561 cases; 1,560 passed and the
unrelated 16 MiB S3 multipart test promoted an aiohttp `ResourceWarning` to an
HTTP client failure. The same complete behavioral suite passed normally, while
all 917 affected tests and all 229 privacy tests passed with warnings promoted.
The visual tier was run in its required color-enabled environment because
`pytest-textual-snapshot` itself uses deprecated `datetime.utcnow()`.

```text
uv run mypy src/aws_tui
Success: no issues found in 155 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
391 files already formatted

scripts/check-layers.sh
layer rules clean

git diff --check
clean

uv build --no-build-isolation
Successfully built dist/aws_tui-0.8.0.tar.gz
Successfully built dist/aws_tui-0.8.0-py3-none-any.whl
```

No verification process remained running. The implementation preserved child VM
instances and subscriptions, and `.superpowers/sdd/progress.md` remained
untouched.
