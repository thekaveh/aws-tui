# Task 4 Report: Athena History, Saved, and Page ViewModels

## Status

Implemented Athena plan Task 4 on `codex/aws-service-expansion-study`.

## Implementation

- Added `AthenaHistoryVM` with one-page-at-a-time execution ID loading,
  current-page detail hydration, opaque token continuation, summary/detail
  selection, pane-local errors, and generation-safe replacement.
- History never calls `stop_query` and never transfers ownership to
  `AthenaQueryVM`. Reading or selecting a historical execution therefore cannot
  grant cancellation authority.
- Added `AthenaSavedVM` with independent named-query and prepared-statement
  `TokenPagedComposition` instances. Named IDs are hydrated only for the
  current page through `get_named_queries`.
- Prepared statements retain the real summary/detail split:
  `list_prepared_statements_page` loads summaries and explicit selection calls
  `get_prepared_statement`.
- Saved SQL is retrieved only through `selected_sql()` and copied to the editor
  only by `AthenaPageVM.open_saved_in_editor()`. It is never executed
  automatically and is excluded from domain reprs, pager reprs, errors, and
  logs.
- Added `AthenaPageVM` with token-paged workgroup, catalog, and database lists;
  lazy History/Saved setup; Query/History/Results/Saved view selection; and
  scoped selection persistence.
- The page constructs `ServiceSourceContext.from_connection(connection)` and
  `SelectionScope("athena", connection.name, connection.region)`.
- Persisted `active_view`, `workgroup`, `catalog`, `database`,
  `history_execution_id`, and `saved_query_id` values are restored only after
  the current page confirms they still exist. Missing values fall back to the
  first current item and update or clear the store.
- Every workgroup/catalog/database transition increments a context generation
  and synchronously clears result, history, saved, selection, and incompatible
  pager state before awaiting the next API request. Late completions cannot
  publish rows, selection, state, notifications, or store changes.
- Page, History, and Saved track current and retired asynchronous pager work.
  Awaited, idempotent `shutdown()` drains cancellation-resistant work and
  delegates query shutdown; synchronous, idempotent `dispose()` cascades through
  each child and VMx resource exactly once without disposing the service-owned
  selection store.
- Exported `AthenaHistoryVM`, `AthenaSavedVM`, `SavedQueryKind`, and
  `AthenaPageVM` from `aws_tui.vm.athena`.

## TDD Evidence

### RED 1: Missing Task 4 modules

All three focused test modules were added before production code:

```text
uv run pytest tests/unit/vm/athena/test_history_vm.py \
  tests/unit/vm/athena/test_saved_vm.py \
  tests/unit/vm/athena/test_page_vm.py -q
```

Observed:

```text
ModuleNotFoundError: No module named 'aws_tui.vm.athena.history_vm'
ModuleNotFoundError: No module named 'aws_tui.vm.athena.saved_vm'
ModuleNotFoundError: No module named 'aws_tui.vm.athena.page_vm'
3 errors during collection
```

### RED 2: Production constructor default

The page tests stopped injecting a sleep function before the constructor gained
its production default:

```text
uv run pytest \
  tests/unit/vm/athena/test_page_vm.py::test_setup_loads_context_lists_in_order_and_keeps_other_views_lazy \
  -q

TypeError: AthenaPageVM.__init__() missing 1 required keyword-only argument: 'sleep'
1 failed
```

`AthenaPageVM` now defaults to `anyio.sleep`, matching `AthenaQueryVM`, while
retaining injection for deterministic tests.

### RED 3: Public Task 4 exports

```text
uv run pytest \
  tests/unit/vm/athena/test_page_vm.py::test_athena_package_exports_task_4_viewmodels \
  -q

ImportError: cannot import name 'AthenaHistoryVM' from 'aws_tui.vm.athena'
1 failed
```

The package now exports all Task 4 VMs.

### GREEN

```text
uv run pytest tests/unit/vm/athena/test_history_vm.py \
  tests/unit/vm/athena/test_saved_vm.py \
  tests/unit/vm/athena/test_page_vm.py -q

18 passed in 0.35s
```

## Verification

Focused Task 4:

```text
18 passed in 0.35s
```

Full VM regression:

```text
uv run pytest tests/unit/vm -q
559 passed in 29.63s
```

Full domain regression:

```text
uv run pytest tests/unit/domain -q
490 passed in 15.40s
```

Privacy, redaction, logging, crash, and config repr regression:

```text
uv run pytest tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_config_store.py -q

60 passed in 0.38s
```

The non-overlapping full VM, domain, and privacy runs total **1,109 passing
tests**. The 18 focused tests are a subset of the VM total.

Static and layer checks:

```text
uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
351 files already formatted

uv run mypy src
Success: no issues found in 139 source files

bash scripts/check-layers.sh
layer rules clean
```

Every listed verification command exited 0. The unrelated shell startup warning
for missing `/tmp/vmx-cargo-182/env` appeared on commands and is omitted above.

### Full-suite baseline diagnostic

The optional repository-wide completion run was not green:

```text
uv run pytest -q
2142 passed, 329 failed, 2 skipped, 9 deselected in 352.74s
```

All 329 failures were existing Textual snapshot mismatches across demo, EMR,
Glue, main-screen, modal, nav, pane-state, settings, theme-picker, toast, and
transfer snapshots. A representative failure was reproduced against pristine
pre-Task-4 `HEAD` (`5a4deb6`) in a temporary detached worktree:

```text
PYTHONPATH=<pristine-worktree>/src <current-venv>/bin/pytest \
  'tests/snapshot/test_modals.py::test_command_palette[amber]' -q

1 failed
```

The pristine and Task 4 trees both render the requested Amber snapshot with the
default Carbon palette. Task 4 changes no UI, theme, snapshot, or Textual code.
The temporary worktree was removed after the comparison.

## Changed Files

- `.superpowers/sdd/task-4-report.md`
- `src/aws_tui/vm/athena/__init__.py`
- `src/aws_tui/vm/athena/history_vm.py`
- `src/aws_tui/vm/athena/page_vm.py`
- `src/aws_tui/vm/athena/saved_vm.py`
- `tests/unit/vm/athena/test_history_vm.py`
- `tests/unit/vm/athena/test_page_vm.py`
- `tests/unit/vm/athena/test_saved_vm.py`

## Concerns

- Selection restoration intentionally validates only the currently loaded token
  page. It does not fetch all pages to search for an old identifier; an
  identifier outside the current page falls back to the first current item.
- A history page may require up to one detail request per execution ID because
  the completed Task 2 domain boundary exposes singular
  `get_query_execution`. Requests remain bounded to the current API page and no
  later page is prefetched.
- Graceful remote query stop belongs to awaited `shutdown()`. Synchronous
  `dispose()` invalidates and releases local resources, so the hosting lifecycle
  must continue to await shutdown before disposal as required by the Athena
  plan.
- The branch's pre-existing all-theme snapshot baseline is not green under the
  current environment. This is outside Task 4's VM-only scope and is documented
  above with a pristine-HEAD reproduction.
