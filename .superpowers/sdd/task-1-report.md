# Task 1 Report: Athena Query Models and Read-Only SQL Policy

## Status

Implemented Task 1 only on `codex/aws-service-expansion-study`. The query domain
values are immutable and the SQL policy fails closed before any Athena SDK boundary
exists.

## Changed Files

- `pyproject.toml`: added the required `sqlglot>=30.13.0,<31` runtime dependency.
- `uv.lock`: locked sqlglot 30.13.0 with registry hashes.
- `src/aws_tui/domain/query.py`: added immutable query execution, result, saved-query,
  and error values.
- `src/aws_tui/domain/sql_policy.py`: added `QueryRejectedError` and AST-based
  `ReadOnlySqlPolicy`.
- `tests/unit/domain/test_query.py`: pinned model fields, states, immutability, cache
  identity, null rows, and repr redaction.
- `tests/unit/domain/test_sql_policy.py`: pinned the read-only allow/deny matrix,
  parser bypasses, nested writes, command forms, and SQL-log redaction.
- `.superpowers/sdd/task-1-report.md`: this report.

## TDD Evidence

Every command also emitted the unrelated local shell-startup warning
`/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env`;
it is omitted from the blocks below.

### Dependency resolution

```console
$ uv add 'sqlglot>=30.13.0,<31'
Resolved 176 packages in 652ms
   Building aws-tui @ file:///Users/kaveh/repos/aws-tui
      Built aws-tui @ file:///Users/kaveh/repos/aws-tui
Prepared 1 package in 84ms
Uninstalled 1 package in 2ms
Installed 2 packages in 8ms
 ~ aws-tui==0.8.0 (from file:///Users/kaveh/repos/aws-tui)
 + sqlglot==30.13.0
```

### RED 1: missing implementation

```console
$ uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q

==================================== ERRORS ====================================
_______________ ERROR collecting tests/unit/domain/test_query.py _______________
E   ModuleNotFoundError: No module named 'aws_tui.domain.query'
____________ ERROR collecting tests/unit/domain/test_sql_policy.py _____________
E   ModuleNotFoundError: No module named 'aws_tui.domain.sql_policy'
=========================== short test summary info ============================
ERROR tests/unit/domain/test_query.py
ERROR tests/unit/domain/test_sql_policy.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
2 errors in 0.30s
```

Exit code: 2. This was the brief's expected missing-module RED.

### GREEN 1: models and initial AST policy

```console
$ uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q
.................................................                        [100%]
49 passed in 0.27s
```

### RED 2: valid SHOW body rejected

Self-review found that reparsing a generic `SHOW` body rejected a valid Athena form.
The regression test was added before changing production code:

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py -q
...F.................................                                    [100%]
=================================== FAILURES ===================================
_ test_policy_accepts_one_read_only_statement[SHOW TBLPROPERTIES analytics.events] _
E   aws_tui.domain.sql_policy.QueryRejectedError: query could not be parsed as Athena SQL
=========================== short test summary info ============================
FAILED tests/unit/domain/test_sql_policy.py::test_policy_accepts_one_read_only_statement[SHOW TBLPROPERTIES analytics.events]
1 failed, 36 passed in 0.49s
```

The fix classifies sqlglot tokens in the opaque command body, rejects structural
`CREATE`, and otherwise preserves the design's allowed `SHOW` root.

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py -q
.....................................                                    [100%]
37 passed in 0.26s
```

### RED 3: sqlglot warning leaked query text

Self-review also found that sqlglot's command fallback warning included full query
text. The privacy regression test was added before changing parser options:

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py -q
.....................................F                                   [100%]
=================================== FAILURES ===================================
_________ test_policy_does_not_log_query_text_during_command_fallback __________
E       assert 'sensitive_customer_secret' not in "WARNING  sq...'Command'.\n"
E         'sensitive_customer_secret' is contained here:
E           IN SELECT sensitive_customer_secret FROM analytics.events' contains unsupported syntax. Falling back to parsing as a 'Command'.
=========================== short test summary info ============================
FAILED tests/unit/domain/test_sql_policy.py::test_policy_does_not_log_query_text_during_command_fallback
1 failed, 37 passed in 0.29s
```

The fix sets sqlglot's per-parse `error_message_context=0`, retaining parser behavior
without logging SQL excerpts.

```console
$ uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q
....................................................                     [100%]
52 passed in 0.27s
```

## Policy Rationale

- Strip only outer whitespace and return the otherwise unchanged query text.
- Parse with `read="athena"` and reject empty, parser/tokenizer failures, null ASTs,
  and any count other than exactly one statement.
- Allow only `Select`, `Describe`, and exact `SHOW`/`EXPLAIN` command verbs.
- Recursively validate the statement represented by `EXPLAIN`; reject
  `EXPLAIN ANALYZE` from sqlglot token types before recursion.
- Reject `SHOW CREATE TABLE` by structural `CREATE` token, including comments and
  casing variants, without SQL string-prefix decisions.
- Walk allowed `Select` trees and reject nested DDL, DML, commands, `INTO`, locks,
  analyze, alter, drop, execute, and transaction nodes. This closes the
  root-`Select` bypass produced by a data-changing CTE.
- Keep named/prepared query text, result rows, and Athena error messages out of
  dataclass reprs. Suppress SQL excerpts in sqlglot fallback warnings.
- Treat this parser policy as defense in depth. IAM, Lake Formation, workgroup, and
  S3 policies remain the security boundary.

## Dependency Lock Evidence

`pyproject.toml` contains exactly:

```toml
"sqlglot>=30.13.0,<31",
```

`uv.lock` resolves:

```toml
[[package]]
name = "sqlglot"
version = "30.13.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/bf/62/6d5bd3169478b7f09e08ab3e50175d486a9e7f3a419b88fc9280ba564ab1/sqlglot-30.13.0.tar.gz", hash = "sha256:f0a6eb79de2fd6efe2689f8cf197caa4f08bfe77c7880315616fa4420b8ba2bf", size = 5932385, upload-time = "2026-07-20T20:16:54.873Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/f2/2f/2076eca54f6a8ed1c86301bdb4bb2ae4181b0c1c4dbc041062ca997dc1b2/sqlglot-30.13.0-py3-none-any.whl", hash = "sha256:08f87ff7b052246d61b731628c8c2db0bc91f2c9e69f5ba68a1d160a9f5b49b1", size = 719120, upload-time = "2026-07-20T20:16:53.248Z" },
]
```

Lock check:

```console
$ uv lock --check
Resolved 176 packages in 5ms
```

## Final Verification

```console
$ uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py -q
....................................................                     [100%]
52 passed in 0.27s

$ uv run pytest tests/unit/domain -q
........................................................................ [ 25%]
........................................................................ [ 50%]
........................................................................ [ 75%]
.....................................................................    [100%]
285 passed in 15.29s

$ uv run mypy
Success: no issues found in 131 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
346 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ uv run pip-audit
No known vulnerabilities found
Name    Skip Reason
------- ----------------------------------------------------------------------
aws-tui Dependency not found on PyPI and could not be audited: aws-tui (0.8.0)

$ git diff --check
```

All commands exited 0. The audit skip is the local project itself, not sqlglot or
another third-party dependency.

## Self-Review

- Compared every public field and enum value with the Task 1 brief.
- Confirmed all public models and policy errors are present in module `__all__`.
- Confirmed no Athena client, SDK call, service, VM, UI, or unrelated refactor was
  introduced.
- Confirmed comments, casing, quoted identifiers, trailing semicolons, nested
  subqueries, valid CTEs, nested-write CTEs, invalid syntax, multi-statements,
  CTAS, DML, UNLOAD, CALL, `SHOW CREATE TABLE`, and `EXPLAIN ANALYZE` are pinned.
- Confirmed the policy uses sqlglot AST/token types and no SQL prefix or regex
  security decision.
- Confirmed the dependency range and resolved lock version match the brief exactly.

## Concerns

- The public `QueryRejectedError` message is stable and SQL-free, and sqlglot warning
  logs no longer contain SQL. In sqlglot 30.13.0, a `TokenError` retained as the
  explicitly required chained cause can still contain a truncated input fragment.
  Application logging must continue to log the stable domain error text rather than
  traceback/cause text for user-entered SQL.
- `pip-audit` cannot audit the local unpublished `aws-tui==0.8.0` package; it found
  no known vulnerabilities in resolved third-party dependencies.
