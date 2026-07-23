# Foundation Task 1 Report: Shared Service Source Context

## Status

Complete.

## Implementation

- Added `ServiceSourceContext`, `SelectionScope`, and `ServiceSelectionStore` in
  `src/aws_tui/vm/service_source_vm.py`.
- Added immutable EMR page source identity from the supplied `Connection` and exposed it as
  `EmrServerlessPageVM.source`.
- Added EMR application-selection persistence scoped by service ID, connection name, and region.
  Restores only a stored application ID that is still present after application loading; otherwise
  uses the existing first-sorted application fallback.
- Made `EmrServerlessService` own one long-lived selection store and inject it into every page VM
  it builds, retaining selections across page disposal and rebuilds for the lifetime of the service.

## TDD Evidence

### RED

Initial required focused command:

```text
uv run pytest tests/unit/vm/test_service_source_vm.py tests/unit/vm/emr_serverless/test_page_vm.py -q
```

Result: collection failed as expected with
`ModuleNotFoundError: No module named 'aws_tui.vm.service_source_vm'`.

The service ownership regression also failed before the service-level store wiring:

```text
test_service_reuses_selection_store_across_replacement_pages
AssertionError: assert 'a1' == 'a2'
```

### GREEN

Focused verification:

```text
uv run pytest tests/unit/vm/test_service_source_vm.py tests/unit/vm/emr_serverless/test_page_vm.py -q
18 passed in 3.17s
```

Broader VM and service verification:

```text
uv run pytest tests/unit/vm tests/unit/services -q
475 passed in 28.97s
```

Static checks:

```text
uv run ruff check <changed Python files>
All checks passed!

uv run mypy <changed source files>
Success: no issues found in 3 source files

uv run ruff format --check <changed Python files>
5 files already formatted
```

## Tests Added

- Source context profile/region formatting and matching-profile label omission.
- Selection-store isolation by service, connection, and region.
- EMR page source identity exposure.
- Successful selection persistence.
- Selection restoration for a replacement page when the application remains available.
- Rejection of unavailable stored applications in favor of the normal fallback.
- Service-owned store reuse across page rebuilds.

## Files

- `src/aws_tui/vm/service_source_vm.py`
- `src/aws_tui/vm/emr_serverless/page_vm.py`
- `src/aws_tui/services/emr_serverless/service.py`
- `tests/unit/vm/test_service_source_vm.py`
- `tests/unit/vm/emr_serverless/test_page_vm.py`

## Self-Review

- `ServiceSourceContext` and `SelectionScope` are frozen, slotted dataclasses as required.
- The label uses the exact required separator and suppresses a profile equal to the connection name.
- Selection memory is owned by the service plugin, not by a page VM, so disposal does not erase it.
- A stored ID is only selected after membership in the newly loaded application list is confirmed.
- Changes stay within the required foundation scope; no Glue, Athena, Iceberg, SQL, RootVM, or S3 behavior changed.

## Concerns

No implementation concerns.

The default repository-wide `uv run pytest -q` was attempted twice, but the execution wrapper detached
both runs without a final result; the detached processes were stopped. The focused suite, broader
VM/service suite, and static checks above are the recorded verification evidence.

## Important Task 1 Review Finding Follow-up

### RED

Added regression coverage for empty and failed initial application loads, then ran the required
focused command before changing implementation code:

```text
uv run pytest tests/unit/vm/emr_serverless/test_page_vm.py tests/unit/vm/test_service_source_vm.py -q
```

Result:

```text
..FF................                                                     [100%]
2 failed, 18 passed in 3.29s
```

Both new tests failed with the expected `IndexError: tuple index out of range` at
`await self.select_application(apps[0].id)` in `_select_after_applications_load`.

### GREEN

Added the empty-list guard and reran the same focused command:

```text
uv run pytest tests/unit/vm/emr_serverless/test_page_vm.py tests/unit/vm/test_service_source_vm.py -q
```

Result:

```text
....................                                                     [100%]
20 passed in 3.21s
```

Static checks covering the changed files:

```text
uv run ruff check src/aws_tui/vm/emr_serverless/page_vm.py tests/unit/vm/emr_serverless/test_page_vm.py
All checks passed!

uv run ruff format --check src/aws_tui/vm/emr_serverless/page_vm.py tests/unit/vm/emr_serverless/test_page_vm.py
2 files already formatted

uv run mypy src/aws_tui/vm/emr_serverless/page_vm.py
Success: no issues found in 1 source file

git diff --check
```

### Changed Files

- `src/aws_tui/vm/emr_serverless/page_vm.py`
- `tests/unit/vm/emr_serverless/test_page_vm.py`

### Self-Review

- `_select_after_applications_load` now returns before reading `apps[0]` when the application
  list is empty, covering both `PaneState.EMPTY` and handled provider-error refreshes.
- Non-empty behavior is unchanged: an available stored selection is restored, otherwise the
  first sorted application is selected.
- Regression tests assert that setup preserves the child VM's handled state and leaves both the
  application and dependent job-run selection unset.
- The diff is scoped to the reported defect and its focused tests; no unrelated behavior changed.
