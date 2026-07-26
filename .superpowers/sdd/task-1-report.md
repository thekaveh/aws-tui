# Task 1 Report: Detect Iceberg Catalog Tables

## Status

Implemented Iceberg cross-service Task 1 on
`codex/aws-service-expansion-study`. This task adds only shared domain format
detection, redacted Glue catalog mapping, and Iceberg metadata values. It adds
no UI, navigation, Athena runner, metadata queries, or local Iceberg runtime.

The previous contents of this exact path described an older Athena Task 1. They
were superseded by the current approved Iceberg task brief.

## Changed Files

- `src/aws_tui/domain/data_catalog.py`
  - Added `detect_table_format(parameters, input_format, table_type)` with
    case-insensitive explicit Iceberg/Hudi/Delta markers, physical-table Hive
    fallback, and conservative `VIRTUAL_VIEW` handling.
  - Kept `TableDetail` immutable while replacing parameter values with a sorted
    redacted tuple and excluding parameter/classification text from `repr`.
- `src/aws_tui/domain/iceberg.py`
  - Added the exact frozen, slot-backed `IcebergSnapshot`,
    `IcebergHistoryEntry`, `IcebergManifest`, `IcebergDataFile`,
    `IcebergPartitionSpec`, `IcebergPartition`, and `IcebergReference` records.
  - Excluded metadata paths, summaries, file/partition complex values, and
    equality IDs from `repr`.
- `src/aws_tui/domain/glue.py`
  - Moved detail-format classification to the centralized detector and calls it
    once with the raw internal parameter map, storage input format, and table
    type.
  - Treats missing, empty, and malformed optional table-detail fields as absent
    so virtual views remain `TableFormat.OTHER`.
  - Converts AWS provider error payloads to stable messages and suppresses raw
    boto exception chains at the domain boundary.
- `tests/unit/domain/test_data_catalog.py`
  - Added detector precedence/casefold coverage plus redacted parameter/repr
    coverage.
- `tests/unit/domain/test_iceberg.py`
  - Added exact field-order, frozen/slots, value, and repr-privacy coverage for
    every new Iceberg record.
- `tests/unit/domain/test_glue.py`
  - Added centralized mapper detection, malformed optional view, parameter
    redaction, and raw-provider-error traceback regressions.

## TDD Evidence

The local shell emitted an unrelated `.zshenv` warning referencing a missing
`/tmp/vmx-cargo-182/env` on every command below. It did not affect command exit
status and is omitted from the captured snippets.

### RED 1: Missing Public Detector And Iceberg Module

Tests were added before production implementation:

```console
$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q

ERROR tests/unit/domain/test_data_catalog.py
ImportError: cannot import name 'detect_table_format' from 'aws_tui.domain.data_catalog'
ERROR tests/unit/domain/test_iceberg.py
ModuleNotFoundError: No module named 'aws_tui.domain.iceberg'
2 errors in 0.34s
```

This confirmed that the tests exercised the new public contracts rather than
existing Glue-local detection behavior.

### RED 2: Raw Provider Payload In A Formatted Error

The error-boundary regression was added before sanitizing Glue AWS errors:

```console
$ uv run pytest tests/unit/domain/test_glue.py::test_get_table_error_hides_provider_payload_and_raw_response -q

FAILED test_get_table_error_hides_provider_payload_and_raw_response
AssertionError: 'PROVIDER_TEXT_SECRET' is contained in
'PROVIDER_TEXT_SECRET raw boto response'
1 failed in 0.29s
```

The production change maps AWS payloads to stable domain messages and raises
them with `from None`, so the original `ClientError` response cannot appear in
formatted tracebacks.

### RED 3: Malformed Optional Glue Detail Field

The virtual-view regression was expanded before adding tolerant table-detail
field readers:

```console
$ uv run pytest tests/unit/domain/test_glue.py::test_get_table_ignores_malformed_optional_fields_for_a_view -q

FAILED test_get_table_ignores_malformed_optional_fields_for_a_view
ValidationError: malformed Glue response: Compressed is not a boolean
1 failed in 0.30s
```

The mapper now treats malformed optional parameter, storage, column,
partition, boolean, integer, and string detail fields as absent. Required Glue
fields retain their existing validation behavior.

### GREEN: Task 1 Focused Contract

```console
$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
83 passed in 0.44s

$ uv run mypy src/aws_tui/domain/data_catalog.py src/aws_tui/domain/iceberg.py src/aws_tui/domain/glue.py
Success: no issues found in 3 source files
```

## Final Verification

```console
$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py tests/unit/vm/glue/test_catalog_vm.py tests/unit/vm/glue/test_page_vm.py tests/unit/ui/glue/test_page.py -q
125 passed in 4.25s

$ uv run pytest tests/unit/domain -q
589 passed in 15.13s

$ uv run pytest tests/unit/vm/glue tests/unit/ui/glue -q
57 passed in 4.07s

$ uv run mypy src/aws_tui/domain/data_catalog.py src/aws_tui/domain/iceberg.py src/aws_tui/domain/glue.py
Success: no issues found in 3 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
383 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ git diff --check
```

All listed final verification commands exited zero.

## Requirements Review

- Central format detection is a public data-catalog function and no view layer
  reads raw Glue parameters.
- Explicit Iceberg markers take precedence over Hudi, Delta, and Hive fallback;
  Hudi precedes Delta; views remain `OTHER` even with an optional input format.
- Glue keeps the unredacted parameter map only long enough to derive
  classification and `table_format`; `TableDetail.parameters` is immutable,
  sorted, and value-redacted.
- Every Task 1 Iceberg record has exactly the requested fields in the requested
  order and is a frozen slot dataclass.
- Table parameters, metadata paths/summaries, raw boto responses, and provider
  error text are excluded from domain record reprs, mapped errors, and formatted
  exception chains covered by this task's tests.
- The dependency direction remains Domain to Infrastructure only where the
  existing Glue client already depended on AWS session/redaction infrastructure;
  the layer gate passed.
- No PyIceberg, Arrow, DuckDB, DataFusion, JVM, UI, or navigation work was
  added.

## Concerns

- `TableDetail.parameters` intentionally exposes parameter keys but redacts
  every value. Any later UI that needs a display value must introduce an
  explicitly reviewed, privacy-safe projection rather than retaining raw Glue
  values on the domain record.

## Review Findings Follow-up (2026-07-26)

### Status

Fixed all Task 1 findings from the review of `3ded08d..39d5b39`.

- Glue now creates mapped errors inside each `except` block and raises them only
  after leaving the active provider-exception scope. The shared raise helper
  also clears context assigned by Python at raise time.
- Every public Glue client method, including both crawler supplement paths, is
  covered by realistic `ClientError` response-secret probes.
- Format detection aggregates every recognized, case-insensitive marker instead
  of collapsing duplicate keys. Exact precedence is Iceberg, Hudi, Delta, Hive,
  then Other, independent of insertion order and marker source.
- Marker keys and values, the external table type, and input format are trimmed.
  Exact matching prevents substring and path false positives; trimmed views stay
  Other and whitespace-only input formats do not imply Hive.
- Recognized `Parameters` mappings and `Columns`/`PartitionKeys` sequences now
  reject malformed entries and required value types with `ValidationError`.
  Empty recognized containers remain valid.
- Iceberg metadata records now have exact annotation, field-order, frozen,
  slots, export, and sensitive-repr contract coverage. Glue calls the central
  detector exactly once per table-detail mapping.

### Changed Files

- `src/aws_tui/domain/data_catalog.py`
- `src/aws_tui/domain/glue.py`
- `tests/unit/domain/test_data_catalog.py`
- `tests/unit/domain/test_glue.py`
- `tests/unit/domain/test_iceberg.py`
- `.superpowers/sdd/task-1-report.md`

`progress.md` was not edited.

### TDD Evidence

The detector regressions failed against the original implementation:

```console
$ uv run pytest tests/unit/domain/test_data_catalog.py -q
15 failed, 33 passed in 0.34s
```

The Glue run reproduced silent recognized-container filtering and raw
`ClientError` reachability across every public method and crawler supplement:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q
18 failed, 57 passed in 0.92s
```

The exported raise helper then received a separate active-exception-scope
regression, which failed through `__context__` before the helper trampoline was
added:

```console
$ uv run pytest tests/unit/domain/test_glue.py::test_raise_mapped_glue_error_severs_active_exception_scope -q
1 failed in 0.31s
```

The privacy assertions traverse context, cause, and exception-valued args;
inspect traceback-frame locals; render
`TracebackException(capture_locals=True)`; render
`traceback.format_exception`; write a real `CrashDump`; and inspect
`str`, `repr`, and `args`.

### Final Verification

```console
$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
131 passed in 0.66s

$ uv run pytest tests/unit/domain -q
637 passed in 14.84s

$ uv run pytest tests/unit/vm/glue tests/unit/ui/glue -q
57 passed in 4.12s

$ uv run mypy
Success: no issues found in 151 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
383 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ git diff --check
```

All listed final verification commands exited zero. A read-only Codex review
also reran the focused tests and static checks and reported no findings.

### Concerns

- Malformed optional table-detail containers as a whole remain absent by the
  established compatibility contract. Once a mapping or sequence is
  structurally recognized, malformed nested entries are rejected rather than
  discarded.

## Remaining Glue Privacy Findings Follow-up (2026-07-26)

### Status

Closed the remaining Task 1 Glue privacy findings without changing detector
precedence, nested-container validation, normal Glue mappings, or public method
signatures.

- Every public `GlueClient` method now enters a shared sanitizing boundary and
  performs its AWS call plus response mapping in a private operation frame.
  Known provider and response-shape exceptions are mapped inside the boundary,
  the raw operation traceback is discarded, and the mapped error is raised only
  after the caught exception scope has ended.
- Crawler core, tag, metric, and STS caller-identity work remains inside the
  private operation graph. Non-permission supplement failures cross the same
  sanitizing boundary; permission failures still become redacted supplemental
  warnings.
- Internal response-shape errors carry only app-owned field and category text.
  Unknown statistics types and malformed STS ARNs now report
  `StatisticsData.Type has unsupported value` and `Arn has invalid format`
  without copying the rejected provider value.
- Generic `KeyError`, `TypeError`, and `ValueError` mapping uses fixed missing
  field, invalid type, and invalid value categories. No mapped
  `ValidationError` formats the caught exception.

### TDD Evidence

The first expanded oracle exposed both the production leaks and test-frame
fixture retention. The test-only false positive was removed before any
production edit by capturing mapped errors in a fixture-free invocation frame.
The corrected RED run was:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q
15 failed, 77 passed in 0.94s
```

The failures covered all ten public methods with malformed response objects,
enum/date/numeric/string/list/map failures, crawler tags, crawler metrics, STS
caller identity, and direct built-in exception mapping. The GREEN run was:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q
92 passed in 0.66s
```

The privacy oracle checks `str`, `repr`, `args`, the complete exception graph,
every mapped traceback frame and its locals,
`TracebackException(capture_locals=True)`, `traceback.format_exception`, and a
real `CrashDump`. It also compares traceback locals by identity with the raw
response, row, table, supplement, metric, and caller-identity objects.

The boundary then received a separate over-catch regression. An arbitrary
`TypeError` raised by the AWS client was initially rewritten as
`ValidationError`; after narrowing runtime mapping to app-owned response errors
and recognized botocore/provider exceptions, both sentinel cases passed:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q -k unrelated_programming_error
1 failed, 1 passed, 91 deselected in 0.34s

$ uv run pytest tests/unit/domain/test_glue.py -q -k unrelated_programming_error
2 passed, 91 deselected in 0.24s
```

The unrelated `RuntimeError` and `TypeError` regressions prove each error
instance is unchanged, its original raising traceback node remains present, and
no cause or context is added.

### Final Verification

```console
$ uv run pytest tests/unit/domain/test_glue.py -q
93 passed in 0.67s

$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
148 passed in 0.70s

$ uv run pytest tests/unit/domain -q
654 passed in 15.52s

$ uv run pytest tests/unit/services/glue tests/unit/vm/glue tests/unit/ui/glue -q
60 passed in 4.15s

$ uv run pytest tests/unit/vm -q
617 passed in 29.83s

$ uv run mypy
Success: no issues found in 151 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
383 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ git diff --check
```

All listed final verification commands exited zero. `progress.md` was not
edited.

### Concerns

- The sanitizing boundary applies only to recognized provider and
  response-shape failures. Unrelated exceptions intentionally retain their
  identity and full private-operation traceback, including diagnostic locals,
  rather than being over-caught as `ValidationError`.
- `_GlueResponseError` field and category values must remain app-owned literals.
  Current call sites satisfy that rule; future mappers must not pass provider
  values into either field.

## Final Iceberg Task 1 Glue Privacy Follow-up (2026-07-26)

### Status

Closed the remaining Glue operation-boundary privacy gaps without changing
normal Glue mappings, public method signatures, Iceberg format detection, or
progress tracking.

- Every `BotoCoreError` recognized by the operation boundary now maps to the
  stable `ProviderError("Glue request failed")` fallback after the existing
  credential, transport, and parameter special cases. This includes
  `ResponseStreamingError` in ordinary calls and crawler STS/metrics
  supplements.
- `ClientError` parsing now validates response and `Error` mappings, accepts
  only plain string code/service indicators, and never stringifies a
  provider-controlled value. Missing or malformed shapes map to the stable
  generic Glue failure; valid Lake Formation indicators retain their
  specialized permission error.
- Added adversarial ordinary-call and crawler STS/metrics tests. They use the
  full existing privacy oracle: exception graph, traceback locals by identity,
  `TracebackException(capture_locals=True)`, formatted traceback, and a real
  `CrashDump`. The existing `RuntimeError` and `TypeError` identity/traceback
  regression remains green.

### Changed Files

- `src/aws_tui/domain/glue.py`
- `tests/unit/domain/test_glue.py`
- `.superpowers/sdd/task-1-report.md`

`progress.md` was not edited.

### TDD Evidence

The adversarial tests were written before the mapper change. After correcting a
test-fixture syntax error, the RED command was:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q -k 'malformed_client_error_shapes or residual_botocore_errors'
12 failed, 93 deselected in 1.16s
```

Malformed `ClientError.response` and `Error` shapes raised sanitizer
`AttributeError` failures; malformed indicator values invoked `str(...)` and
raised the fixture assertion. `ResponseStreamingError` escaped unchanged from
the ordinary public method and both crawler supplement paths.

After the mapper change, the focused GREEN command passed all new cases plus
the unrelated-exception preservation regression:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q -k 'malformed_client_error_shapes or residual_botocore_errors or unrelated_programming_error'
14 passed, 91 deselected in 0.34s
```

### Final Verification

```console
$ uv run pytest tests/unit/domain/test_glue.py -q
105 passed in 0.78s

$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
160 passed in 0.80s

$ uv run pytest tests/unit/domain -q
666 passed in 15.36s

$ uv run pytest tests/unit/vm/glue -q
45 passed in 0.40s

$ uv run mypy
Success: no issues found in 151 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
383 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ git diff --check
```

All listed final verification commands exited zero.

### Concerns

- The boundary intentionally continues to preserve unrelated non-botocore
  `RuntimeError` and `TypeError` instances and their original tracebacks. Only
  recognized provider and app-owned response-shape failures are sanitized.
- `ClientError` code and Lake Formation detection intentionally require plain
  string values. Malformed response values fall back to the generic safe Glue
  failure rather than being coerced or formatted.

## Final Iceberg Task 1 ClientError Attribute Privacy Follow-up (2026-07-26)

### Status

Closed the final `ClientError.response` attribute-access privacy bypass.

- `_client_error_indicators()` now reads `response` through an exception-safe
  generic attribute helper before response mapping validation. The same helper
  backs the existing `operation_name` string reader.
- A hostile `ClientError.response` descriptor that raises either
  `AssertionError` or `RuntimeError` now becomes the stable generic
  `ProviderError("Glue request failed")` without retaining raw `ClientError`
  context or the hostile exception.
- The regression covers an ordinary public method, crawler STS caller identity,
  and crawler metrics. Each case uses the exception graph, traceback-local
  identity checks, `TracebackException(capture_locals=True)`, formatted
  traceback, and a real `CrashDump` oracle.
- Existing valid and malformed `ClientError` mappings, residual `BotoCoreError`
  fallback behavior, and unrelated non-botocore exception identity remain
  covered by the focused suite.

### Changed Files

- `src/aws_tui/domain/glue.py`
- `tests/unit/domain/test_glue.py`
- `.superpowers/sdd/task-1-report.md`

`progress.md` was not edited.

### TDD Evidence

The hostile-descriptor test was added before the mapper change:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q -k hostile_client_error_response_attribute
6 failed, 105 deselected in 0.81s
```

Each failure escaped from the direct `exc.response` lookup, including both
sentinel-bearing `AssertionError` and `RuntimeError` cases. After introducing
the generic helper, the focused regression and retained mapper coverage passed:

```console
$ uv run pytest tests/unit/domain/test_glue.py -q -k 'hostile_client_error_response_attribute or malformed_client_error_shapes or residual_botocore_errors or map_glue_error_uses_canonical_provider_errors or unrelated_programming_error'
32 passed, 79 deselected in 0.38s
```

### Final Verification

```console
$ uv run pytest tests/unit/domain/test_glue.py -q -k 'privacy or hostile_client_error_response_attribute or malformed_client_error_shapes or residual_botocore_errors'
18 passed, 93 deselected in 0.45s

$ uv run pytest tests/unit/domain/test_glue.py -q
111 passed in 0.90s

$ uv run pytest tests/unit/domain/test_data_catalog.py tests/unit/domain/test_iceberg.py tests/unit/domain/test_glue.py -q
166 passed in 0.88s

$ uv run pytest tests/unit/domain -q
672 passed in 15.70s

$ uv run mypy
Success: no issues found in 151 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
383 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ git diff --check
```

All listed verification commands exited zero.
