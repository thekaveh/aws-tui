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
