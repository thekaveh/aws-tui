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
