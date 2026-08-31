# 1. Athena Eager Glue Prefill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Glue-generated Athena starter SQL before remote Athena setup completes while preventing execution against an unresolved context.

**Architecture:** `AthenaPageVM` primes deterministic query state immediately after mount, `AthenaQueryVM` owns a transient context-resolution gate on its existing VMx command, and the app transaction completes remote discovery afterward. Textual remains a projection of VM state.

**Tech Stack:** Python 3.12, Textual, VMx `AsyncRelayCommand`, pytest, asyncio.

## 1.1. Global Constraints

- Keep generated SQL exact, quoted, read-only, and bounded by `LIMIT 5`.
- Never execute a generated starter query automatically.
- Preserve table-handoff generation guards, rollback, and source identity checks.
- Keep command availability in VMx viewmodels rather than Textual widgets.
- Model real AWS latency with a deterministic blocked-provider test.

---

### 1.1.1. Task 1: Query Resolution Gate

**Files:**
- Modify: `src/aws_tui/vm/athena/query_vm.py`
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`
- Test: `tests/unit/vm/athena/test_query_vm.py`

**Interfaces:**
- Produces: `AthenaQueryVM.is_context_resolving: bool`
- Produces: `AthenaQueryVM.begin_context_resolution() -> None`
- Produces: `AthenaQueryVM.end_context_resolution() -> None`

- [x] **Step 1: Write the failing command-state test**

Create a valid SQL query, begin context resolution, and assert
`execute_command.can_execute()` is false; end resolution and assert it becomes
true. Capture `on_property_changed` and assert `is_context_resolving` is
published for both transitions.

- [x] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/unit/vm/athena/test_query_vm.py -k context_resolving -q`

Expected: failure because the resolution API does not exist.

- [x] **Step 3: Add the minimal VM-owned gate**

Initialize `_is_context_resolving = False`, expose the property and idempotent
begin/end methods, publish `is_context_resolving`, and add the state to
`_can_execute()`:

```python
if self._is_context_resolving:
    return False
```

Render `RESOLVING TABLE CONTEXT` before ordinary ready/validation status in
`AthenaQueryView._status_text()`.

- [x] **Step 4: Run the focused unit and query-view tests**

Run: `uv run pytest tests/unit/vm/athena/test_query_vm.py tests/unit/ui/athena/test_page.py -q`

Expected: all selected tests pass.

### 1.1.2. Task 2: Prime Before Provider Setup

**Files:**
- Modify: `src/aws_tui/vm/athena/page_vm.py`
- Modify: `src/aws_tui/app.py`
- Test: `tests/integration/test_glue_athena_navigation.py`

**Interfaces:**
- Consumes: the Task 1 context-resolution API.
- Produces: `AthenaPageVM.prime_table_query(table_ref: TableRef, snapshot_id: int | None = None) -> None`
- Produces: `AthenaPageVM.abandon_table_query_prime() -> None`

- [x] **Step 1: Write the blocked-provider regression test**

Block the Athena client's first setup request with `asyncio.Event`, invoke
`glue.query_in_athena`, and while setup remains blocked assert the mounted
Athena VM and `#athena-editor` both hold:

```sql
SELECT * FROM "AwsDataCatalog"."dev_analytics"."dev_events" LIMIT 5
```

Also assert Query is active, context resolution is pending, Run cannot execute,
and `start_query` has not been called. Release setup and assert the SQL persists,
the exact catalog/database is selected, and Run is enabled.

- [x] **Step 2: Run the regression test and verify RED**

Run: `uv run pytest tests/integration/test_glue_athena_navigation.py -k glue_prefills_before_athena_setup_completes -q`

Expected: timeout or blank VM/editor SQL while Athena setup is blocked.

- [x] **Step 3: Implement synchronous priming in `AthenaPageVM`**

Validate local source identity, persist/select Query without an async setup,
begin context resolution, and call `query.set_sql(select_starter_sql(...))`.
Make `open_table()` invoke the prime method idempotently and end resolution in
`finally`. Add an idempotent abandon method for pre-`open_table()` failure.

- [x] **Step 4: Reorder only the Athena app transaction**

Immediately after `_mount_service_view("athena")`, obtain the current
`AthenaPageVM`, call `prime_table_query()`, refresh the matching mounted page,
and `await wait_for_refresh()`. Then await destination setup and call
`open_table()` for authoritative discovery. Abandon the prime on cancellation,
failure, or supersession before completion.

- [x] **Step 5: Run the focused integration suite**

Run: `uv run pytest tests/integration/test_glue_athena_navigation.py -q`

Expected: all handoff, rollback, stale-request, key, and click tests pass.

### 1.1.3. Task 3: Documentation and Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/superpowers/specs/2026-08-27-athena-ui-contract-repair-design.md`
- Modify: `docs/superpowers/specs/2026-08-29-athena-controls-clickable-commands-design.md`
- Modify: `docs/superpowers/plans/README.md`

**Interfaces:**
- Consumes: verified behavior from Tasks 1 and 2.
- Produces: current three-surface canonical documentation source.

- [x] **Step 1: Correct the documented transaction order**

State that deterministic starter SQL is VM-owned and visibly projected before
remote Athena setup, while execution remains gated until exact context
resolution succeeds. Mark older discovery-first wording as superseded.

- [x] **Step 2: Run formatting, type, focused, and full tests**

Run:

```bash
uv run ruff check .
uv run mypy src
uv run pytest tests/unit/vm/athena/test_query_vm.py tests/integration/test_glue_athena_navigation.py -q
uv run pytest -q
```

Expected: every command exits zero.

- [x] **Step 3: Commit and push**

```bash
git add src/aws_tui/app.py src/aws_tui/vm/athena/page_vm.py src/aws_tui/vm/athena/query_vm.py src/aws_tui/ui/widgets/athena/query_view.py tests/unit/vm/athena/test_query_vm.py tests/integration/test_glue_athena_navigation.py docs
git commit -m "fix: prefill Athena before provider setup"
git push
```
