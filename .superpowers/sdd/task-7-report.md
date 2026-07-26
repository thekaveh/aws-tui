# Task 7 Report: Standalone Athena Documentation

## Status

Completed. Public documentation, release smoke guidance, the consumed-contract
ledger, and documentation assertions now describe the shipped standalone Athena
service only. `.superpowers/sdd/progress.md` was not edited.

## Documentation Evidence

- `README.md`: adds Athena as an AWS-only standalone service, summarizes the
  four views, read-only policy, cost signals, result-artifact S3 handoff, demo
  state, source switching, and the absence of Iceberg metadata and
  Glue-to-Athena navigation.
- `docs/cookbook.md`: adds the end-to-end Athena workflow: minimum IAM, Lake
  Formation, and S3 result-prefix permissions; workgroup result configuration
  and `EnforceWorkGroupConfiguration`; exact parser allow/reject behavior;
  lifecycle, app-owned cancellation, paged results, bytes-scanned/reuse cost
  signals; named/prepared queries; S3 artifact handoff; demo scenarios; and
  troubleshooting.
- `docs/contract-ledger.md`: records the exact boto operation methods consumed
  by `AthenaClient`: `list_work_groups`, `get_work_group`,
  `list_data_catalogs`, `list_databases`, `list_table_metadata`,
  `list_query_executions`, `get_query_execution`,
  `get_query_runtime_statistics`, `start_query_execution`,
  `stop_query_execution`, `get_query_results`, `list_named_queries`,
  `batch_get_named_query`, and `list_prepared_statements`.
- `docs/connections.md`, `docs/architecture.md`, and
  `docs/adding-a-service.md`: describe AWS-only construction, resolver/source
  order, profile/region-scoped state, page replacement, result-handoff
  identity checks, and the `AthenaService` / `AthenaPageVM` boundaries.
- `docs/keybindings.md`, `docs/RELEASING.md`, and `CHANGELOG.md`: document
  shipped actions and palette handoff, the demo release smoke, and the
  standalone release boundary.
- `tests/docs/test_scaffolding.py`: added content assertions across all public
  surfaces. The new test was run before documentation edits and failed because
  `README.md` did not yet mention Athena; it passes after the edits.

## Verification Evidence

| Command | Result |
|---|---|
| `uv run pytest tests/docs/test_scaffolding.py -q` before docs edits | Expected red: 1 failed, 2 passed; missing Athena content in `README.md`. |
| `uv run pytest tests/unit tests/integration tests/e2e -q` | 1,995 passed, 9 deselected in 339.78s. |
| `env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR TERM=xterm-256color uv run pytest tests/snapshot -q` | 744 passed; 437 snapshots passed in 126.45s. |
| `uv run ruff check .` | Passed. |
| `uv run ruff format --check .` | Passed: 381 files already formatted. |
| `uv run mypy src` | Passed: no issues in 150 source files. |
| `./scripts/check-layers.sh` | Passed: layer rules clean. |
| `uv run pytest tests/docs -q` | 56 passed, 2 skipped. The two Cairo diagram-render skips are the documented optional dependency skips. |
| `uv run python -m scripts.docs.check_docs` | Passed: `check_docs: clean`. |
| `uv lock --check` | Passed. |
| `uv run pip-audit` | No known vulnerabilities. The local unpublished `aws-tui (0.8.0)` package was skipped because it is not on PyPI. |
| `uv build` | Passed: built `aws_tui-0.8.0.tar.gz` and `aws_tui-0.8.0-py3-none-any.whl`. |

## Hygiene

`dist/` is ignored by repository policy. The `uv build` artifacts were removed
after the successful build verification. The only intended changes are the
Task 7 docs, documentation test, and this report.

## Concerns

No implementation concerns found. The only non-pass outcomes are the existing
optional Cairo diagram-render skips and pip-audit's expected inability to audit
the unpublished local project package; neither indicates an Athena failure.
