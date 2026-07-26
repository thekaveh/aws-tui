# Task 2 Report: Paginated Athena Domain Client

## Status

Implemented Task 2 only on `codex/aws-service-expansion-study`.

## Implementation

- Added `AthenaClient` with one-request page methods for workgroups, catalogs,
  databases, tables, query history, results, named-query IDs, and prepared statements.
- Added exact workgroup, query execution, runtime-statistics, structured Athena error,
  result, named-query, and prepared-statement mappings. Shared catalog records carry the
  active connection name and region; raw boto mappings never leave the client.
- Added 50-ID `BatchGetNamedQuery` batching while preserving each service response's
  order. Empty input opens no SDK client.
- Added `ReadOnlySqlPolicy` validation before session/client acquisition. Start requests
  preserve the client request token, catalog, database, workgroup, optional caller result
  location, and return a connection-scoped `QueryExecutionRef`.
- Added app-owned cancellation authority. Only execution IDs returned by this client's
  successful `start_query` remain stoppable, and terminal observation or successful stop
  revokes that authority. History reads never grant stop authority.
- Added `ResultConfigurationRequiredError` for Athena invalid requests that report a
  missing query result destination.
- Added canonical credential, SSO, access-denied, not-found, throttling, validation,
  service, and transport error mapping through the existing provider taxonomy.
- Added Athena operations to the shared async AWS fake without changing Glue behavior.

## Privacy

- Query text is validated before any SDK access and is never logged.
- Full SQL, request tokens, and caller result locations are removed from mapped
  `StartQueryExecution` errors.
- Raw `ClientError` responses are suppressed as exception causes, so response metadata
  cannot enter formatted tracebacks.
- Query text returned in execution detail is not retained. Exact query text is scrubbed
  from state reasons and structured Athena error messages before mapping.
- Result rows, named-query SQL, prepared-statement SQL, query output locations, and
  workgroup output locations are excluded from reprs.
- Malformed response failures use a stable SQL/result-free message and suppress the
  original response-shape exception.

## TDD Evidence

### RED 1: Missing Athena module

Tests and fake-client operations were added before production code:

```text
uv run pytest tests/unit/domain/test_athena.py -q
```

Result:

```text
ERROR tests/unit/domain/test_athena.py
ModuleNotFoundError: No module named 'aws_tui.domain.athena'
1 error
```

### GREEN 1: Full Athena client contract

```text
uv run pytest tests/unit/domain/test_athena.py -q
59 passed in 0.42s
```

### RED 2: Start error echoed request secrets

Self-review added a traceback test before extending production scrubbing:

```text
uv run pytest \
  tests/unit/domain/test_athena.py::test_access_denied_maps_without_exposing_query_or_raw_response \
  -q
```

Result:

```text
FAILED test_access_denied_maps_without_exposing_query_or_raw_response
AssertionError: REQUEST_TOKEN_SECRET_7F4C2A9D was present in the rendered traceback
1 failed
```

### GREEN 2: Complete start-request scrubbing

```text
uv run pytest \
  tests/unit/domain/test_athena.py::test_access_denied_maps_without_exposing_query_or_raw_response \
  -q
1 passed in 0.27s

uv run pytest tests/unit/domain/test_athena.py -q
59 passed in 0.41s
```

## Verification

Focused domain and policy:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py -q
226 passed in 0.49s
```

Full domain:

```text
uv run pytest tests/unit/domain -q
459 passed in 54.23s
```

Provider regression:

```text
uv run pytest tests/unit/domain/test_glue.py \
  tests/unit/domain/test_emr_serverless.py \
  tests/unit/domain/test_emr_logs.py \
  tests/unit/domain/test_s3_fs_auth_error_helper.py \
  tests/unit/infra/test_aws_session.py -q
131 passed in 3.23s
```

Privacy and crash-path regression:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_app_sanity.py -q
272 passed in 3.15s
```

Static and layer checks:

```text
uv run mypy
Success: no issues found in 132 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
348 files already formatted

./scripts/check-layers.sh
layer rules clean

git diff --check
```

All listed commands exited 0. Every command also emitted the unrelated local shell
startup warning about `/tmp/vmx-cargo-182/env`; it is omitted above.

## Changed Files

- `src/aws_tui/domain/athena.py`
- `tests/unit/domain/test_athena.py`
- `tests/unit/domain/_fake_aws_client.py`
- `.superpowers/sdd/task-2-report.md`

## Concerns

- The Task 2 interface does not carry a previously loaded workgroup detail into
  `start_query`. The client therefore omits `ResultConfiguration` when no caller
  location is supplied, allowing Athena to resolve any workgroup-configured
  destination according to service semantics; the workgroup configuration does
  not have to be enforced for omission to be valid. Only
  `StartQueryExecution`'s precise missing-destination response is mapped to
  `ResultConfigurationRequiredError`.
- `GetQueryRuntimeStatistics` does not expose result-reuse information. Its
  `QueryStatistics.reused_previous_result` is therefore `False`; execution detail
  remains the authoritative source for reuse.

---

## Important Findings Remediation (2026-07-25)

### Status

All Important Athena Task 2 findings were remediated on
`codex/aws-service-expansion-study`. The locked botocore Athena service model is
version `1.40.61`.

### Prepared Statements

- Added immutable `PreparedStatementSummary(name, last_modified_at)` for the real
  `ListPreparedStatements` wire shape.
- `list_prepared_statements_page` now accepts only the summary fields
  `StatementName` and optional `LastModifiedTime`; nonempty real AWS summary pages
  no longer fail because detail-only fields are absent.
- Added `get_prepared_statement(name, workgroup) -> PreparedStatement`, forwarding
  exact `StatementName` and `WorkGroup` values to `GetPreparedStatement`.
- Detail mapping keeps SQL and description out of reprs while preserving name,
  workgroup, description, query text, and modification time for explicit UI use.

### Metadata Workgroup Identity

- `list_catalogs_page`, `list_databases_page`, and `list_tables_page` now accept an
  optional workgroup and forward it exactly as `WorkGroup` when supplied.
- Calls omit `WorkGroup` entirely when absent.
- Tests cover supplied workgroup plus opaque pagination tokens for all three APIs,
  and omitted workgroup behavior for every call.

### Stop Authority Lifecycle

- Start request tokens are associated with active execution IDs, while terminal
  execution IDs form an app-lifetime deny ledger.
- Terminal observation and successful stop retire authority irreversibly and
  remove active token mappings. Replaying an old token or returning a terminal ID
  under another token cannot restore stop authority; a genuinely new token and
  execution ID can.
- Concurrent stop awaiters share one SDK dispatch. A failed dispatch restores
  retry authority unless terminal observation won the race.
- Stop dispatches are shielded from an individual waiter's cancellation. The done
  callback retrieves a later background failure, preventing an event-loop
  `Task exception was never retrieved` path while preserving the exception for
  any live awaiter.
- The read-only review's bookkeeping note was tightened: request-token mappings
  now contain active executions only. Retired execution IDs remain intentionally
  for the client lifetime because removing them would weaken replay denial.

### Privacy And Error Mapping

- Every Athena operation supplies its request tokens, execution/named-query IDs,
  workgroups, catalogs, databases, prepared-statement names, and caller locations
  to error sanitization.
- Pagination `ClientError` messages scrub the exact passed token. Raw boto
  responses remain suppressed as exception causes.
- Unknown `StartQueryExecution` exceptions become a stable
  `ProviderError("Athena request failed")` with no raw cause, SQL, request token,
  or output location in formatted tracebacks or crash dumps.
- `ResultPage.next_token`, prepared SQL, prepared descriptions, result rows, and
  result locations are excluded from reprs.
- A 14-operation parameterized traceback audit verifies request identity and raw
  response metadata do not survive mapped failures.

### Named Query Partial Failures

- Every `BatchGetNamedQuery` batch checks `UnprocessedNamedQueryIds` before
  appending returned rows.
- Partial responses now raise the mapped provider failure:
  `TooManyRequestsException` becomes `ThrottledError`; service failures become
  `ProviderError`.
- Tests exercise an unprocessed entry in both the first and second 50-ID batch and
  verify that raw named-query IDs do not enter tracebacks.

### Result Configuration Classification

- Generic Athena error mapping no longer creates
  `ResultConfigurationRequiredError`.
- Only `start_query` recognizes `InvalidRequestException` containing Athena's
  precise `No output location provided` condition.
- Malformed supplied output locations, botocore parameter validation, and the same
  text from non-start operations remain ordinary `ValidationError` instances.

### TDD Evidence

Prepared summary/detail and metadata interface RED:

```text
uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_athena.py -q
ERROR tests/unit/domain/test_query.py
ERROR tests/unit/domain/test_athena.py
ImportError: cannot import name 'PreparedStatementSummary'
2 errors
```

Prepared summary/detail and metadata GREEN:

```text
80 passed in 0.43s
```

Stop lifecycle RED:

```text
uv run pytest tests/unit/domain/test_athena.py -q -k 'stop or terminal_execution'
2 failed, 5 passed, 61 deselected
```

Privacy, partial-batch, and result-classification RED:

```text
20 failed, 2 passed, 67 deselected
```

Read-only review cancellation RED/GREEN:

```text
test_cancelled_stop_waiter_retrieves_late_dispatch_failure
RED: 1 failed; event loop reported "Task exception was never retrieved"
GREEN: 1 passed in 0.28s
```

Active-token cleanup RED/GREEN:

```text
test_terminal_execution_cannot_be_reauthorized_by_token_or_id_reuse
RED: 1 failed; terminal token mappings remained
GREEN: included in 9 passed stop/lifecycle tests
```

Final focused GREEN:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py -q
257 passed in 0.61s
```

### Final Verification

Full domain:

```text
uv run pytest tests/unit/domain -q
490 passed in 15.91s
```

Provider regression:

```text
uv run pytest tests/unit/domain/test_glue.py \
  tests/unit/domain/test_emr_serverless.py \
  tests/unit/domain/test_emr_logs.py \
  tests/unit/domain/test_s3_fs_auth_error_helper.py \
  tests/unit/infra/test_aws_session.py -q
131 passed in 0.87s
```

Privacy and crash paths:

```text
uv run pytest tests/unit/domain/test_athena.py \
  tests/unit/domain/test_query.py \
  tests/unit/domain/test_sql_policy.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_app_sanity.py -q
303 passed in 0.86s
```

Static and layer checks:

```text
uv run mypy
Success: no issues found in 132 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
348 files already formatted

./scripts/check-layers.sh
layer rules clean

git diff --check
```

All final verification commands exited 0. The unrelated local shell startup
warning for `/tmp/vmx-cargo-182/env` is omitted from the evidence above.

### Remaining Minor Concern

`GetQueryRuntimeStatistics` still has no result-reuse field in botocore
`1.40.61`. `QueryStatistics.reused_previous_result` remains `False` for that
operation, while `GetQueryExecution` detail remains authoritative. No model
change was made because that concern is Minor and the existing mapping is the
lowest-risk plan-compatible behavior.

---

# Iceberg Task 2 Report: Bounded Athena Runner and Metadata Inspector

## Status

Implemented Iceberg plan Task 2 on `codex/aws-service-expansion-study`.
`.superpowers/sdd/progress.md` was not modified.

## Implementation

- Added `AthenaQueryRunner` and immutable `BoundedQueryResult`. The runner
  validates read-only SQL and complete query context, starts exactly one query,
  polls with the existing bounded exponential backoff, validates execution
  identity, maps failed/cancelled terminal states, and fetches only enough
  result pages to satisfy the caller's positive row bound.
- Added cancellation cleanup for active app-started queries. Identity failures
  stop an active execution, while result-shape failures discovered after a
  successful terminal state do not issue a stale stop request.
- Refactored `AthenaQueryVM` to delegate validation, start, poll, sleep, and stop
  operations through the runner while retaining its existing UI generation,
  detached-submission finalizers, ownership, stale-context rejection, and
  paged result-view behavior.
- Updated `AthenaService` and `AthenaPageVM` composition so every page receives
  a fresh runner bound to the same fresh client and policy.
- Added `IcebergInspector` for `$snapshots`, `$history`, `$manifests`, `$files`,
  `$partitions`, and `$refs`. Queries use three-part quote-escaped identifiers,
  deterministic ordering where the metadata shape supports it, and hard limits
  of 100 snapshots/history/refs, 500 manifests/partitions, and 1000 files.
- Added strict typed mappings for timestamps, integers, booleans, nullable
  metrics, summary maps, and all Task 1 Iceberg records. Timestamp mapping
  accepts ISO-8601 `Z`/offset forms and Athena's trailing `UTC` representation.
- Derived partition fields from result metadata in source order while requiring
  every fixed partition metric column. Missing, duplicate, malformed, or
  width-inconsistent metadata raises `IcebergMetadataShapeError`.
- Updated the Athena output-mode source contract test to follow the extracted
  runner while preserving its no-`output_location` invariant.

## Privacy

- `BoundedQueryResult.rows` and all path/partition-bearing Iceberg fields remain
  excluded from reprs.
- Mapping failures replace provider values with stable, value-free typed errors
  and suppress the original conversion traceback.
- A production-frame `TracebackException(capture_locals=True)` oracle and a real
  `CrashDump` write verify malformed metadata row values do not survive into
  crash surfaces.
- Generated request tokens contain no catalog, database, table, SQL, or result
  value. No Task 2 code logs SQL, result rows, or provider objects.

## TDD Evidence

Initial runner/inspector collection failed before production code existed:

```text
uv run pytest tests/unit/domain/test_athena_runner.py \
  tests/unit/domain/test_iceberg.py \
  tests/unit/vm/athena/test_query_vm.py -q
ModuleNotFoundError: No module named 'aws_tui.domain.athena_runner'
ImportError: cannot import name 'IcebergInspector'
3 errors
```

The runner reached green independently:

```text
uv run pytest tests/unit/domain/test_athena_runner.py -q
9 passed
```

The inspector reached green after exact projection, mapping, partition, context,
cancellation, and privacy coverage:

```text
uv run pytest tests/unit/domain/test_iceberg.py \
  tests/unit/domain/test_athena_runner.py -q
28 passed
```

The VM/service RED run failed on the missing runner injection and delegation
contracts, then passed with the extraction:

```text
uv run pytest tests/unit/domain/test_athena_runner.py \
  tests/unit/domain/test_iceberg.py \
  tests/unit/vm/athena/test_query_vm.py \
  tests/unit/services/athena/test_service.py -q
66 passed
```

## Verification

Focused domain, Athena VM, service, and integration coverage passed. The final
repository-wide evidence was:

```text
uv run pytest tests --ignore=tests/snapshot -q -n 8
2225 passed, 2 skipped

env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color \
  uv run pytest tests/snapshot/test_athena.py -q -n 8
120 passed

uv run pytest tests/docs -q -n 4
63 passed, 2 skipped

uv run mypy
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
385 files already formatted

./scripts/check-layers.sh
layer rules clean

git diff --check

uv build
Successfully built dist/aws_tui-0.8.0.tar.gz
Successfully built dist/aws_tui-0.8.0-py3-none-any.whl
```

The two documentation skips are the known local Cairo shared-library
unavailability. The unrelated `/tmp/vmx-cargo-182/env` shell startup warning is
also omitted from the evidence.

## Changed Files

- `src/aws_tui/domain/athena_runner.py`
- `src/aws_tui/domain/iceberg.py`
- `src/aws_tui/vm/athena/query_vm.py`
- `src/aws_tui/vm/athena/page_vm.py`
- `src/aws_tui/services/athena/service.py`
- `tests/unit/domain/test_athena_runner.py`
- `tests/unit/domain/test_iceberg.py`
- `tests/unit/vm/athena/test_query_vm.py`
- `tests/unit/services/athena/test_service.py`
- `tests/docs/test_scaffolding.py`
- `.superpowers/sdd/task-2-report.md`

## Concerns

- Athena documents the six supported Iceberg metadata tables but does not pin
  every returned column in the user guide. The accepted implementation plan is
  therefore the exact column contract; strict shape failures remain scoped and
  visible if a workgroup exposes an incompatible engine shape.
- `$partitions` necessarily uses `SELECT *` because its leading partition
  columns are table-dependent. All fixed metrics are validated and the query is
  still hard-bounded to 500 rows.

---

## Important Review Findings Remediation (2026-07-26)

### Status

All five Iceberg Task 2 review findings were corrected in one TDD fix wave.
The corrective commit is the single commit containing this report update; its
final hash is reported from Git after the commit is written.

### Iceberg Partition Contract

- Corrected `$partitions` handling to match the actual Iceberg metadata-table
  contract: partitioned tables expose a `partition` row/struct and `spec_id`
  before the fixed metrics; unpartitioned tables omit both.
- `sqlglot`, already a runtime dependency, parses Athena's `row(...)` result
  type into ordered partition field names.
- The bounded value parser accepts both Athena's named row rendering
  (`{event_date=..., region_bucket=...}`) and the positional rendering shown in
  Apache Iceberg's official example (`{20211001, 11}`). Mixed, malformed,
  mismatched, duplicate, missing, and unexpected shapes fail closed with
  value-free `IcebergMetadataShapeError` messages.
- `spec_id` is validated as an integer but is intentionally excluded from
  `IcebergPartitionSpec.field_names` and `IcebergPartition.values`.
- Sources checked:
  - https://iceberg.apache.org/docs/latest/spark-queries/#partitions
  - https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg.html

### Query Lifecycle And Privacy

- Submission now runs in a shielded task. Cancellation after remote acceptance
  waits for a finalizer that recovers the execution reference and performs a
  best-effort stop without resubmitting.
- Poll cancellation, provider failures, unexpected failures, and context
  mismatches best-effort stop every known nonterminal execution.
- SQL, request tokens, terminal detail values, result pages, rows, tokens, and
  raw provider exceptions are discarded before a value-free error is raised.
- The privacy oracle checks exception graphs, traceback locals through
  `TracebackException(capture_locals=True)`, ordinary formatted tracebacks, and
  real `CrashDump` output for start, poll, terminal, result-fetch, and
  pagination-shape failures.
- Result pages, columns, rows, cell values, and continuation tokens are
  type-checked before use. Empty continuation pages, repeated tokens, malformed
  tokens, and request counts beyond the row bound raise
  `AthenaResultShapeError`.
- Glue/Athena table identity now requires exact connection, region, catalog,
  and database matches before any query is submitted.

### TDD Evidence

The first review-regression run failed for the requested reasons:

```text
uv run pytest tests/unit/domain/test_athena_runner.py \
  tests/unit/domain/test_iceberg.py -q
22 failed, 30 passed
```

The additional `spec_id` validation test also failed before its implementation:

```text
uv run pytest \
  tests/unit/domain/test_iceberg.py::test_partitions_validate_spec_id_without_exposing_it_as_a_partition_field \
  -q
1 failed
```

### Final Verification

```text
uv run pytest tests/unit/domain/test_athena_runner.py \
  tests/unit/domain/test_iceberg.py \
  tests/unit/vm/athena \
  tests/unit/services/athena/test_service.py -q
170 passed

uv run pytest tests/unit/domain/test_athena_runner.py \
  tests/unit/domain/test_iceberg.py \
  tests/unit/domain/test_athena.py \
  tests/unit/domain/test_glue.py \
  tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/test_app_sanity.py -q
300 passed

uv run pytest tests/unit/domain tests/unit/vm -q
1340 passed

uv run mypy
Success: no issues found in 152 source files

uv run ruff check .
All checks passed!

uv run ruff format --check .
385 files already formatted

./scripts/check-layers.sh
layer rules clean

git diff --check
```

### Changed Files

- `src/aws_tui/domain/athena_runner.py`
- `src/aws_tui/domain/iceberg.py`
- `tests/unit/domain/test_athena_runner.py`
- `tests/unit/domain/test_iceberg.py`
- `.superpowers/sdd/task-2-report.md`

### Concerns

- Athena's user guide documents Iceberg metadata-table availability but does
  not publish the complete `$partitions` result schema. Apache Iceberg's
  official metadata-table contract is therefore the schema authority, with
  realistic Athena `ResultColumn.type_name` fixtures covering engine-v3 row
  metadata. Unknown future renderings fail closed rather than guessing.
- The maximum number of result-page requests is `max_rows`. A continuation page
  must add at least one row, so this request bound cannot truncate a valid
  result before the existing row bound.
