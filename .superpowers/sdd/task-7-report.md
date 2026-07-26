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

## Remaining Review Findings Remediation

### Contract Corrections

- The consumed-contract ledger now distinguishes the lower-level
  `AthenaClient.start_query(...)` facade from the shipped query path. The
  facade accepts optional `output_location` and sends
  `ResultConfiguration.OutputLocation` only when supplied; `AthenaQueryVM`
  omits that argument and relies on the selected workgroup's enforced
  customer-S3 or managed-results configuration.
- The cookbook now states every accepted `SHOW` form and scope, the bounded
  `DESCRIBE` partition and complex-selector grammar, all concrete `EXPLAIN`
  option values, and the `SELECT` / set-operation / `VALUES` root behavior.
  It also includes explicit accepted and rejected SQL blocks.
- Documentation tests extract every SQL example and pass it through the real
  `ReadOnlySqlPolicy`. Retained policy tests now pin `VALUES` below `SELECT`
  and set-operation roots and rejection of standalone `VALUES`; policy source
  and behavior remain unchanged.
- Architecture prose and the landscape diagram now assign runtime AWS and
  filesystem I/O to domain adapters. Infrastructure owns sessions,
  credentials, configuration, SDK client construction, and OS-backed stores.
  Claims that Infrastructure alone touches external systems were removed.
- The split-line duplicate `read read` in `docs/adding-a-service.md` was fixed.
  The regression assertion normalizes blockquote whitespace so that form is
  detected in future.

### RED / GREEN Evidence

- Initial focused RED:
  `pytest` over the new output-mode, SQL, architecture-prose, and diagram
  contracts reported **4 failed, 7 passed**. The failures were the intended
  missing ledger distinction, exact grammar, architecture ownership, and
  diagram labels. The seven new `VALUES` characterization cases passed against
  unchanged policy behavior.
- Final focused SQL/docs/diagram run:
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib
  ./.venv/bin/python -m pytest tests/docs/test_scaffolding.py
  tests/docs/test_render_diagrams.py tests/unit/domain/test_sql_policy.py -q`
  reported **175 passed**.
- After the split-line typo correction, the five directly affected
  docs/diagram assertions reported **5 passed**; focused Ruff lint and format
  checks passed.

### Diagram Inspection

- `make docs-diagrams` regenerated
  `docs/diagrams/img/architecture.png` from the self-contained landscape HTML.
- Desktop inspection at 1440x1100 showed the full diagram with no page-level
  horizontal overflow; the SVG rendered at 1342x764 inside its frame.
- Narrow inspection at 720x1000 showed no page-level horizontal overflow. The
  980px SVG scrolls inside its 686px diagram frame, and the three summary cards
  collapse to one 688px column.
- Desktop, narrow, and the committed 1600px PNG were visually inspected.
  Labels fit, connectors remain orthogonal and unobstructed, and no component
  or text overlaps were found.

### Final Gates

| Command | Result |
|---|---|
| `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib ./.venv/bin/python -m pytest tests/docs -q` | 65 passed. |
| `make docs-check` | Diagram regenerated; `check_docs: clean`; strict MkDocs build passed. |
| Normal-color `uv run pytest tests/unit tests/integration tests/e2e -q` | 2,002 passed, 9 deselected in 291.46s. |
| Normal-color `uv run pytest tests/snapshot -q` | 744 passed; 437 snapshots passed in 124.13s. |
| `uv run ruff check .` | Passed. |
| `uv run ruff format --check .` | Passed: 381 files formatted. |
| `uv run mypy src` | Passed: no issues in 150 source files. |
| `./scripts/check-layers.sh` | Passed: layer rules clean. |
| `uv lock --check` | Passed. |
| `uv run pip-audit` | No known vulnerabilities; expected unpublished local-package skip only. |
| `uv build` and `uv run twine check dist/*` | Both 0.8.0 artifacts built and passed metadata checks. |

`.superpowers/sdd/progress.md`, Athena implementation sources, and
`src/aws_tui/domain/sql_policy.py` remain untouched. The retained
`tests/unit/domain/test_sql_policy.py` change is test-only.

## Final Grammar Findings Remediation

### TDD Evidence

- Added the executable policy characterization before updating the cookbook:
  all 30 combinations of `UNION`, `INTERSECT`, and `EXCEPT` with the current
  default/`ALL`/`DISTINCT`, `BY NAME`, `CORRESPONDING`, and `STRICT
  CORRESPONDING` parser forms passed, as did four rejected malformed matching
  forms and two rejected identifier/backtick `SHOW TBLPROPERTIES` selectors.
  This confirms the existing policy behavior without changing its source.
- The expanded cookbook contract test then ran red before the documentation
  update: `uv run pytest
  tests/docs/test_scaffolding.py::test_athena_sql_grammar_matches_policy_tests
  -q` reported **1 failed** because the new string-literal-only property
  selector contract was absent.
- After the cookbook update, that focused documentation test passed. It now
  extracts every accepted and rejected SQL example and validates it through
  `ReadOnlySqlPolicy`.

### Contract Corrections

- The cookbook now documents every current set-operation family for `UNION`,
  `INTERSECT`, and `EXCEPT`: default/`ALL`/`DISTINCT`, `BY NAME` with optional
  `ON (...)`, and `CORRESPONDING` or `STRICT CORRESPONDING` with optional
  `ON (...)` or `BY (...)`. It states the enforced ordering constraint that
  the quantifier precedes the matching modifier and does not claim semantic
  validation of the parser-accepted matching-column list.
- Accepted and rejected cookbook examples cover the `UNION BY NAME`, `UNION
  BY NAME ON (...)`, `UNION CORRESPONDING`, `UNION CORRESPONDING ON (...)`,
  `UNION CORRESPONDING BY (...)`, and `UNION STRICT CORRESPONDING` forms.
  The policy tests cover those forms across all three set-operation tokens so
  parser drift cannot silently narrow the documented grammar.
- `SHOW TBLPROPERTIES` now accurately specifies that its optional property
  selector is a string literal only. The examples accept a backtick-quoted
  table with a string selector and reject both regular and backtick-quoted
  selector identifiers.

### Scope Correction

`src/aws_tui/domain/sql_policy.py` has no diff; there is no runtime or source
behavior change. `tests/unit/domain/test_sql_policy.py` intentionally has a
test-only diff that characterizes the existing grammar and selector boundary.
This supersedes the earlier report wording that said both files had no diff.
