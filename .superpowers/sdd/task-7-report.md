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
  `batch_get_named_query`, `list_prepared_statements`, and
  `get_prepared_statement`.
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
| `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib ./.venv/bin/python -m pytest tests/docs -q` | 65 passed, including both diagram-render tests. |
| `uv run python -m scripts.docs.check_docs` | Passed: `check_docs: clean`. |
| `uv lock --check` | Passed. |
| `uv run pip-audit` | No known vulnerabilities. The local unpublished `aws-tui (0.8.0)` package was skipped because it is not on PyPI. |
| `uv build` | Passed: built `aws_tui-0.8.0.tar.gz` and `aws_tui-0.8.0-py3-none-any.whl`. |
| `uv run twine check dist/*` | Passed for both distribution artifacts. |

## Hygiene

`dist/` is ignored by repository policy. The `uv build` artifacts were removed
after the successful build verification. The only intended changes are the
Task 7 docs, documentation test, and this report.

## Concerns

No implementation concerns found. `pip-audit` cannot audit the unpublished
local project package itself, as expected, but reported no known
vulnerabilities in its dependencies. The focused diagram suite skips without a
Cairo loader path on this macOS host; the repository's Cairo-aware command ran
both render tests successfully.

## Review Remediation Evidence

### RED

- `uv run pytest tests/docs/test_scaffolding.py tests/docs/test_render_diagrams.py -q`
  failed as expected before documentation edits: **8 failed, 6 passed, 2
  skipped**. The failures proved missing contracts for `docs/index.md`, the
  exact 15-operation Athena ledger and IAM set, Lake Formation/S3/KMS and
  managed-result facts, implemented SQL grammar, Unreleased v0.9.0 framing,
  current diagram layers/services/lifecycle, and explicit Glue/Athena
  palette-only binding status.
- The two skips were the existing plain-`uv run` Cairo loader limitation on
  macOS. Diagram rasterization is exercised separately through the repository
  `Makefile`, which supplies Homebrew's Cairo library path.
- `uv run pytest
  tests/unit/domain/test_sql_policy.py::test_policy_accepts_values_and_values_set_operations
  -q` then exposed a policy/documentation mismatch: **2 failed, 1 passed**.
  Standalone `VALUES (1)` and `VALUES (1), (2)` were rejected at the root,
  while the same `VALUES` node was already accepted beneath a set operation.
- **Decision:** no SQL implementation or policy-test change is retained.
  Task 7 synchronizes documentation with the approved policy: `SELECT` roots
  and set operations may contain `VALUES` operands, but standalone `VALUES`
  remains rejected. The temporary source and unit-test edits were reverted;
  only documentation contracts describe this distinction.

### GREEN

- Exact contracts are pinned by `tests/docs/test_scaffolding.py`: all 15
  awaited `AthenaClient` boto operations and their IAM action names, including
  `get_prepared_statement` / `athena:GetPreparedStatement`; source-data S3,
  Lake Formation, result multipart, KMS, and managed-result facts; the exact
  approved SQL policy; v0.9.0 Unreleased framing; synchronized canonical
  surfaces; prohibited Iceberg/Glue-to-Athena claims; keymap status; and the
  expanded release smoke.
- `tests/docs/test_render_diagrams.py` pins the landscape orientation, current
  Textual/VM/Service/Domain/Infra layers, S3/EMR Serverless/Glue/Athena
  services, awaited Athena shutdown before disposal, exact-profile S3
  handoff, and absence of premature workflows.
- `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib
  ./.venv/bin/python -m pytest tests/docs -q`: **65 passed**.
- `make docs-check`: diagram regeneration completed, `check_docs: clean`, and
  the strict MkDocs build passed. The generated site and wiki contain the
  synchronized Athena contracts.
- The committed PNG and generated SVG were visually inspected at 1600 px
  source width, 1200 px desktop width, and 720 px narrow documentation width.
  Boxes and labels remain legible; arrows avoid box and label overlap and use
  perpendicular routing where it improves separation.
- `uv run pytest tests/unit tests/integration tests/e2e -q`: **1,995 passed, 9
  deselected**.
- Normal-color `uv run pytest tests/snapshot -q`: **744 passed; 437 snapshots
  passed**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
  `./scripts/check-layers.sh`, and `uv lock --check`: passed.
- `uv run pip-audit`: no known vulnerabilities; only the expected unpublished
  local-package skip.
- `uv build` and `uv run twine check dist/*`: both 0.8.0 artifacts built and
  passed package-metadata checks. No release or package-version bump was made.
- `src/aws_tui/domain/sql_policy.py`,
  `tests/unit/domain/test_sql_policy.py`, and
  `.superpowers/sdd/progress.md` have no diff. No unrelated implementation
  changes remain.
