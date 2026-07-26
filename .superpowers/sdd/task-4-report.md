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
- Page saved-query selectors capture both page lifecycle and context
  generations before awaiting a child selection. They revalidate both after
  the await and before writing `saved_query_id`, so a detail request that
  survives cancellation cannot repopulate the store after page termination.
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

### Full-suite snapshot diagnostic correction

The earlier optional repository-wide run inherited `NO_COLOR=1` from the Codex
process:

```text
NO_COLOR=1
```

That environment flag disabled Textual color output and caused all 329 snapshot
comparisons to render the default Carbon palette. The pristine-worktree
reproduction inherited the same flag, so it confirmed environment
contamination rather than a bad baseline.

The representative snapshot passes with normal color output:

```text
env -u NO_COLOR uv run pytest \
  'tests/snapshot/test_modals.py::test_command_palette[amber]' -q

1 snapshot passed.
1 passed in 0.49s
```

The complete normal-color snapshot tier also remains green:

```text
env -u NO_COLOR uv run pytest tests/snapshot -q
329 snapshots passed.
624 passed in 88.13s
```

No snapshot baseline changed.

## Changed Files

- `.superpowers/sdd/task-4-report.md`
- `src/aws_tui/domain/query.py`
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
- Snapshot commands launched from this Codex process must unset inherited
  `NO_COLOR=1`; the normal-color baseline itself is green.

## Review Remediation: 2026-07-26

### Status

All Task 4 review findings are fixed on `codex/aws-service-expansion-study`.

### Implementation

- History detail hydration now creates and tracks every current-page detail
  task. The first sibling failure cancels the remaining siblings and drains
  cancellation-resistant calls before the pager operation can finish.
- Prepared-statement detail retrieval now runs in a VM-owned task.
  Workgroup replacement, shutdown, and disposal cancel it; awaited shutdown
  retains ownership until the AWS call exits under the shared botocore
  connect/read timeout and adaptive-retry configuration.
- Historical result opening captures context generation, selected execution,
  page liveness, and caller cancellation. A stale or cancelled load cannot
  select Results or write `active_view`.
- Every public page mutator, including selectors and `construct()`, is inert
  after shutdown or disposal. Calling `shutdown()` after disposal is also
  inert; normal shutdown followed by disposal still cascades resource cleanup.
- Workgroup, catalog, and database restoration now distinguishes successful
  `EMPTY`/`IDLE` pages from `FORBIDDEN`, `UNREACHABLE`, throttled, and generic
  errors. Only successful responses can clear or replace persisted selection.
- Added immutable `NamedQuerySummary`. Named-query batches still hydrate only
  the current token page, but public `named_queries` contains SQL-free
  summaries. Full `NamedQuery` detail and `selected_sql()` become public only
  after explicit selection.

### Focused RED Evidence

History sibling ownership:

```text
uv run pytest \
  tests/unit/vm/athena/test_history_vm.py::test_history_detail_failure_cancels_and_drains_every_sibling \
  tests/unit/vm/athena/test_history_vm.py::test_history_shutdown_cancels_and_drains_all_detail_siblings -q

1 failed, 1 passed in 1.94s
```

Prepared-detail lifecycle:

```text
uv run pytest \
  tests/unit/vm/athena/test_saved_vm.py::test_workgroup_replacement_cancels_and_retains_prepared_detail_until_drained \
  tests/unit/vm/athena/test_saved_vm.py::test_saved_shutdown_cancels_and_drains_prepared_detail_request -q

2 failed in 6.74s
```

Historical result and terminated-page mutation:

```text
uv run pytest \
  tests/unit/vm/athena/test_page_vm.py::test_context_change_during_history_result_load_cannot_switch_to_results \
  tests/unit/vm/athena/test_page_vm.py::test_cancelled_history_result_load_cannot_switch_to_results \
  tests/unit/vm/athena/test_page_vm.py::test_context_selectors_are_noops_after_page_termination \
  tests/unit/vm/athena/test_page_vm.py::test_all_other_public_mutators_are_noops_after_page_termination -q

8 failed, 2 passed in 0.42s
```

Three-level restoration matrix:

```text
uv run pytest \
  tests/unit/vm/athena/test_page_vm.py::test_transient_context_errors_preserve_all_persisted_selections \
  tests/unit/vm/athena/test_page_vm.py::test_successful_empty_context_page_clears_only_confirmed_absence -q

12 failed, 3 passed in 9.93s
```

Named-query public interface:

```text
uv run pytest \
  tests/unit/vm/athena/test_saved_vm.py::test_named_query_list_exposes_sql_free_summaries_before_selection -q

ImportError: cannot import name 'NamedQuerySummary'
1 failed in 1.40s
```

Shutdown after disposal:

```text
uv run pytest \
  'tests/unit/vm/athena/test_page_vm.py::test_all_other_public_mutators_are_noops_after_page_termination[dispose]' -q

reactivex.internal.exceptions.DisposedException: Object has been disposed
1 failed in 0.40s
```

### Focused GREEN Evidence

```text
uv run pytest tests/unit/vm/athena -q
92 passed in 0.56s

uv run pytest tests/unit/domain/test_query.py -q
16 passed in 0.20s
```

### Final Verification

```text
uv run pytest tests/unit/vm -q
exit 0; fresh collection count: 591 tests

uv run pytest tests/unit/domain -q
490 passed in 15.64s

uv run pytest tests/unit/infra/test_redaction.py \
  tests/unit/infra/test_log_sink.py \
  tests/unit/infra/test_crash_dump.py \
  tests/unit/infra/test_config_store.py -q
60 passed in 0.42s

uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
351 files already formatted

uv run mypy src
Success: no issues found in 139 source files

bash scripts/check-layers.sh
layer rules clean
```

The non-overlapping VM, domain, and privacy runs total **1,141 passing tests**.
The separate normal-color snapshot tier adds **624 passing tests**, for **1,765
non-overlapping passing tests** across the recorded final suites. Every command
exited 0.

## Final Prepared-Selection Repair: 2026-07-26

### Correction

The prior review remediation covered ownership and draining of the prepared
detail request, but did not protect `AthenaPageVM`'s continuation after
`await saved.select_prepared_statement(...)`. On synchronous page disposal,
the saved child correctly rejected its late result but retained the
pre-await selected ID; the parent then wrote that stale ID to the
service-owned selection store.

`select_named_query()` and `select_prepared_statement()` now both capture
page lifecycle and context generations before awaiting their saved-child
operation, then require both to remain current before any store mutation.
`open_saved_in_editor()` was audited: its selection-store and query writes
occur before its only await, so it has no post-await store mutation.

### RED Evidence

```text
uv run pytest \
  tests/unit/vm/athena/test_page_vm.py::test_late_prepared_selection_after_page_termination_preserves_page_and_saved_state \
  -q

F.
1 failed, 1 passed in 0.37s
```

The `dispose` parameter failed because the late continuation replaced
`saved_query_id="before-termination"` with `"prepared-1"`; the `shutdown`
parameter already avoided that write because shutdown clears the child
selection before draining it.

### GREEN And Fresh Verification

```text
Focused lifecycle regression: 2 passed in 0.30s
Saved + page VM focused regression: 43 passed in 0.47s
Full Athena VM focus: 92 passed in 0.56s
Full VM regression: exit 0; 591 tests collected in 0.37s
Full domain regression: 490 passed in 15.64s
Privacy regression: 60 passed in 0.42s
ruff check src tests: all checks passed
ruff format --check src tests: 351 files already formatted
mypy src: success; 139 source files
bash scripts/check-layers.sh: layer rules clean
```

The fresh non-overlapping VM, domain, and privacy total is **1,141 passing
tests**. The only recurring environmental noise is the local shell startup
warning for a missing `/tmp/vmx-cargo-182/env`; it did not affect any command
exit status.
