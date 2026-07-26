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
