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
  location is supplied and relies on Athena's enforced workgroup configuration.
  Athena's missing-output response is mapped to the typed
  `ResultConfigurationRequiredError`.
- `GetQueryRuntimeStatistics` does not expose result-reuse information. Its
  `QueryStatistics.reused_previous_result` is therefore `False`; execution detail
  remains the authoritative source for reuse.
