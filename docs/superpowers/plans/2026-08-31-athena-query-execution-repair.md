# Athena Query Execution Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Glue-generated Athena queries compatible with the resolved query context, expose safe actionable rejection categories, and render stable visually square query controls.

**Architecture:** `sql_policy.py` remains the sole starter-SQL generator and will produce database/table qualification because `AthenaClient` already sends the authoritative catalog/database context. The Athena domain boundary will classify known start-query validation messages into app-owned provider errors whose VM copy contains no raw AWS values. `AthenaQueryView` will lock Run and Stop to fixed terminal geometry.

**Tech Stack:** Python 3.11+, boto/aiobotocore-compatible Athena facade, sqlglot, VMx commands, Textual, pytest, Ruff, mypy.

## Global Constraints

- Generated SQL remains one quoted read-only statement with `LIMIT 5` and optional non-negative Iceberg `FOR VERSION AS OF`.
- Enforced S3 and managed-results workgroups receive no caller-side output override.
- Rejected submissions are never retried automatically.
- User-visible errors never include SQL, S3 URIs, profiles, workgroups, catalogs, databases, tables, or request tokens.
- Run and Stop use exact `6x3` outer terminal geometry in every command state.

---

### Task 1: Context-Relative Glue Starter SQL

**Files:**
- Modify: `src/aws_tui/domain/sql_policy.py`
- Modify: `tests/unit/domain/test_sql_policy.py`
- Modify: `tests/unit/vm/athena/test_page_vm.py`
- Modify: `tests/integration/test_glue_athena_navigation.py`

**Interfaces:**
- Consumes: `TableRef` and optional `snapshot_id`.
- Produces: `quote_athena_query_table_ref(ref: TableRef) -> str` and updated `select_starter_sql(ref: TableRef, snapshot_id: int | None = None) -> str`.

- [ ] **Step 1: Write failing SQL and handoff assertions**

Assert ordinary and hostile identifiers produce:

```python
'SELECT * FROM "database"."table" LIMIT 5'
'SELECT * FROM "db name"."table""name" FOR VERSION AS OF 42 LIMIT 5'
```

Update page-VM and integration expectations so the selected Athena catalog and database remain exact while editor SQL is context-relative.

- [ ] **Step 2: Run the focused tests and observe old three-part SQL failures**

Run:

```bash
uv run pytest tests/unit/domain/test_sql_policy.py tests/unit/vm/athena/test_page_vm.py tests/integration/test_glue_athena_navigation.py -q
```

Expected: assertions fail because generated SQL still starts with the catalog.

- [ ] **Step 3: Implement the minimal generator change**

Add:

```python
def quote_athena_query_table_ref(ref: TableRef) -> str:
    return ".".join(
        quote_athena_identifier(part)
        for part in (ref.database_name, ref.table_name)
    )
```

Use it from `select_starter_sql`; retain `quote_athena_table_ref` for callers that explicitly need a catalog-qualified reference.

- [ ] **Step 4: Re-run focused SQL and handoff tests**

Expected: all selected tests pass and no execution occurs during handoff.

- [ ] **Step 5: Commit the working slice**

```bash
git add src/aws_tui/domain/sql_policy.py tests/unit/domain/test_sql_policy.py tests/unit/vm/athena/test_page_vm.py tests/integration/test_glue_athena_navigation.py
git commit -m "fix: align generated Athena SQL with query context"
```

### Task 2: Safe Actionable Athena Rejections

**Files:**
- Modify: `src/aws_tui/domain/athena.py`
- Modify: `src/aws_tui/domain/athena_runner.py`
- Modify: `src/aws_tui/vm/athena/_errors.py`
- Modify: `tests/unit/domain/test_athena.py`
- Modify: `tests/unit/domain/test_athena_runner.py`
- Modify: `tests/unit/vm/athena/test_query_vm.py`

**Interfaces:**
- Produces: `ResultLocationUnavailableError`, `QueryContextRejectedError`, and `WorkgroupRejectedError`, each deriving from `ValidationError`.
- Consumes: only the AWS error code and lower-cased message for classification; raw message content is not carried into these errors.

- [ ] **Step 1: Write failing domain classification and redaction tests**

Cover representative `InvalidRequestException` messages for an unverifiable output bucket, missing catalog/database, workgroup rejection, and unknown validation. Assert typed errors use fixed messages and exclude every embedded marker, SQL fragment, and S3 URI.

- [ ] **Step 2: Run focused domain tests and observe missing types**

```bash
uv run pytest tests/unit/domain/test_athena.py -q
```

Expected: imports or type assertions fail because the categorized errors do not exist.

- [ ] **Step 3: Add narrow start-query classification**

Classify only `InvalidRequestException` raised by `StartQueryExecution`:

```python
if _message_matches_result_location(message):
    return ResultLocationUnavailableError(
        "Athena cannot access the workgroup result location"
    )
if _message_matches_query_context(message):
    return QueryContextRejectedError("Athena rejected the selected query context")
if _message_matches_workgroup(message):
    return WorkgroupRejectedError("Athena rejected the selected workgroup")
```

Preserve the existing exact missing-output detection first. Unknown validation remains ordinary `ValidationError` with fixed safe copy at the VM boundary.

- [ ] **Step 4: Preserve categories through runner and VM mapping**

Teach `_prepared_provider_error()` to retain each subclass and map them to the same fixed messages in `vm/athena/_errors.py`. Do not carry raw boto text through `_PreparedRunError`.

- [ ] **Step 5: Run domain, runner, and query-VM tests**

```bash
uv run pytest tests/unit/domain/test_athena.py tests/unit/domain/test_athena_runner.py tests/unit/vm/athena/test_query_vm.py -q
```

Expected: all tests pass, including existing auth, permission, throttling, result-configuration, and redaction cases.

- [ ] **Step 6: Commit the error slice**

```bash
git add src/aws_tui/domain/athena.py src/aws_tui/domain/athena_runner.py src/aws_tui/vm/athena/_errors.py tests/unit/domain/test_athena.py tests/unit/domain/test_athena_runner.py tests/unit/vm/athena/test_query_vm.py
git commit -m "fix: explain Athena start-query rejections safely"
```

### Task 3: Visually Square Query Controls

**Files:**
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`
- Modify: `tests/unit/ui/athena/test_page.py`
- Modify: `tests/snapshot/apps/athena.py`
- Modify: `tests/snapshot/__snapshots__/test_athena/*.raw`

**Interfaces:**
- Produces: exact `6x3` `#athena-execute` and `#athena-cancel` regions with centered glyph content.

- [ ] **Step 1: Tighten the rendered-geometry test**

Assert for both buttons:

```python
assert button.region.size == Size(6, 3)
assert button.styles.min_width == Scalar.from_number(6)
assert button.styles.max_width == Scalar.from_number(6)
assert button.styles.min_height == Scalar.from_number(3)
assert button.styles.max_height == Scalar.from_number(3)
assert button.content_region.height == 1
```

Repeat after enabling Run and focusing each button so state styles cannot resize them.

- [ ] **Step 2: Run the focused UI test and observe the `3x3` failure**

```bash
uv run pytest tests/unit/ui/athena/test_page.py::test_query_controls_are_compact_above_editor_and_inside_their_frame -q
```

Expected: width and maximum-bound assertions fail.

- [ ] **Step 3: Lock both dimensions in component CSS**

Set `width`, `min-width`, and `max-width` to `6`; set `height`, `min-height`, and `max-height` to `3`; retain centered compact flat buttons and the existing margin.

- [ ] **Step 4: Run focused UI and snapshot tests**

```bash
uv run pytest tests/unit/ui/athena/test_page.py tests/snapshot/test_athena.py -q
```

Expected: geometry and snapshots pass after intentional snapshot updates.

- [ ] **Step 5: Commit the UI slice**

```bash
git add src/aws_tui/ui/widgets/athena/query_view.py tests/unit/ui/athena/test_page.py tests/snapshot
git commit -m "fix: render square Athena query controls"
```

### Task 4: Documentation And Full Verification

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/contract-ledger.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/superpowers/specs/2026-08-31-athena-query-execution-repair-design.md`
- Modify: `docs/superpowers/plans/README.md`

- [ ] **Step 1: Synchronize user and architecture contracts**

Document context-relative generated SQL, exact request-context ownership,
enforced-workgroup output behavior, safe rejection categories, and `6x3`
control geometry. Mark the design implemented only after verification succeeds.

- [ ] **Step 2: Run formatting, lint, typing, and focused verification**

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest tests/unit/domain/test_sql_policy.py tests/unit/domain/test_athena.py tests/unit/domain/test_athena_runner.py tests/unit/vm/athena tests/unit/ui/athena tests/integration/test_glue_athena_navigation.py -q
```

Expected: all commands exit zero.

- [ ] **Step 3: Run complete tests and documentation verification**

```bash
uv run pytest -q
make docs-check
```

Expected: the complete pytest suite and strict three-surface documentation
checks exit zero.

- [ ] **Step 4: Review the final diff and commit documentation**

```bash
git diff --check
git status --short
git add CHANGELOG.md docs
git commit -m "docs: record Athena query execution repair"
```

- [ ] **Step 5: Push the verified branch**

```bash
git push
```
