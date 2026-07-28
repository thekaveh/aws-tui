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

## Verification

```text
Warning-strict Glue/Athena/Iceberg/S3 matrix: 640 passed
Full Task 5 E2E journeys:                       9 passed
Demo snapshot/content suite:                   46 passed
Snapshot baselines compared:                   25 passed
Focused demo coverage run:                     75 passed
Demo package coverage:                         80.65%
  seeds.py:                                    99%
  in_memory_athena.py:                         83%
  in_memory_glue.py:                           79%
Ruff lint and format:                          passed
Mypy:                                          passed
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
- Demo metadata responses are one bounded Athena result page. UI pagination is
  still exercised through the production `GlueIcebergVM` local page boundary.
- Running every legacy E2E test with global `-W error` can surface the
  previously documented real-S3/aiohttp socket `ResourceWarning`. The Task 5
  lifecycle matrix passed with `-W error`, and the complete E2E suite passed
  normally.
- The shell emits the pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; command results are unaffected.
