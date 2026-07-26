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
