# Iceberg Integration Task 5 Report

## Status

`DONE`

Task 5 adds deterministic, profile-isolated Iceberg demo workflows and complete
Glue -> Athena -> S3 journeys on `codex/aws-service-expansion-study`.

## TDD Record

The initial integrated tests were written before demo production changes:

```bash
uv run pytest tests/integration/test_demo_mode.py -k iceberg \
  tests/e2e/test_journeys.py -k 'journey_9 or iceberg' -q
```

RED result: `6 failed`; all failures reported the absent profile-local Iceberg
tables. A separate snapshot RED run failed because the new Carbon baseline did
not exist.

After implementation, the focused lifecycle and journey matrix passed under
warnings-as-errors:

```text
7 passed, 16 deselected
```

It covers metadata pagination, sanitized retry, stale-load suppression during
source switching, exact time-travel SQL, no auto-run, explicit execution, and
same-profile S3 handoff.

## Implementation

- Added a reusable `InMemoryGlue.add_iceberg_table()` seed primitive with
  profile-specific schemas, partition columns, metadata versions, valid
  Iceberg storage descriptors, strict table-format parameters, and column
  statistics.
- Added context-keyed `InMemoryAthena.add_query_result()` responses. The real
  read-only SQL policy, runner, poller, result pager, and `IcebergInspector`
  remain unchanged.
- Seeded disjoint Iceberg tables for all AWS demo profiles:
  `dev_events_iceberg`, `prod_sales_iceberg`, and
  `shared_metrics_iceberg`.
- Seeded unique snapshots, history, manifests, files, partitions, refs,
  time-travel result rows, result locations, and matching S3 metadata/data
  artifacts for each profile.
- Preserved the existing non-Iceberg Glue, Athena, and S3 examples.
- Replaced the shared profile's blanket Athena denial with an unavailable
  `shared-retired` workgroup plus an enabled `shared-insights` workgroup. Its
  Glue crawler permission failure remains as the partial-access scenario.
- Added profile-switch tests for resolver order, immediate stale-state
  clearing, scoped remembered selections, foreign-identifier exclusion, and
  unavailable-workgroup revalidation.
- Added a complete explicit journey:
  Glue Iceberg table -> older snapshot -> generated `FOR VERSION AS OF` SQL
  -> explicit Athena execution -> deterministic rows -> S3 result artifact.
- Added 15 intentional SVG baselines: snapshots across all ten built-in themes
  plus Carbon history, manifests, files, partitions, and refs views.

## Review Fixes

- Replaced the hard-coded Athena result object with deterministic RFC-style
  CSV serialization of the execution's complete paginated `ResultPage`
  sequence. Headers retain exact order and duplicates, rows retain order,
  `None` is emitted as an empty Athena CSV field, and quoting/newlines are
  handled by Python's structured CSV writer.
- Seeded historical successful executions now expose those same exact CSV
  bytes immediately in S3 and republish them from their canonical result pages
  when read, preventing seed-data placeholders from drifting from Athena
  history.
- Removed fabricated query success. Demo Athena now accepts only exact seeded
  connection, region, enabled workgroup, catalog, database, and normalized SQL
  fixtures. Malformed values and unknown or foreign contexts fail with
  value-free typed domain errors before execution, history, result, or S3
  artifact state is created.
- Replaced reusable numeric result tokens with bounded, exact,
  execution-derived opaque tokens. Empty, malformed, out-of-range, oversized,
  and foreign-execution tokens fail closed.
- Split runtime connection identity from the underlying demo storage
  namespace. Runtime aliases remain visible in `QueryContext` and `TableRef`,
  while database, Iceberg metadata, manifest, and data-file URIs resolve to
  objects that exist in the paired profile-seeded `InMemoryFS`.
- Added targeted Carbon baselines for production snapshots/files and shared
  refs at a constrained `100x30` viewport, preserving the existing all-theme
  development coverage without multiplying every profile/theme combination.
- Journey 9 now opens and reads the generated S3 result object and asserts its
  exact Iceberg headers and profile rows, with explicit guards against the old
  generic `_col0/1` fallback.

## Verification

```text
Focused Athena fake boundary tests:             38 passed
Focused rollback/artifact/Journey 9 tests:       5 passed
Warning-strict Task 3-5 regression matrix:     537 passed
Repeated Journey 9 executions:                   2 passed
Full Task 5 E2E journeys:                       9 passed
Demo snapshot/content suite:                   52 passed
Snapshot baselines compared:                   28 passed
Focused demo coverage run:                    131 passed
Demo package coverage:                         81.83%
  seeds.py:                                    99%
  in_memory_athena.py:                         82%
  in_memory_glue.py:                           79%
Ruff lint and format:                          passed
Mypy (configured source tree, 157 files):       passed
Layer rules:                                   passed
Build (sdist + wheel):                         passed
Pre-commit hooks:                              15 passed
git diff --check:                              passed
```

Snapshot commands explicitly removed inherited color-disabling environment
variables and used `TERM=xterm-256color`.

## Residual Risks

- Seeded metadata SQL intentionally matches the exact public queries generated
  by `IcebergInspector`; future query-template changes must update these demo
  registrations and their exact-SQL tests together.
- Default demo metadata responses fit one bounded page, while the Athena fake's
  focused tests exercise multi-page result retrieval, token ownership, and
  complete artifact assembly.
- Running every legacy E2E test with global `-W error` can surface the
  previously documented real-S3/aiohttp socket `ResourceWarning`. The Task 5
  lifecycle matrix passed with `-W error`, and the complete E2E suite passed
  normally.
- The shell emits the pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; command results are unaffected.
