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

## Important Findings Follow-up (2026-07-25)

### Status

All Important Athena Task 1 review findings are fixed on
`codex/aws-service-expansion-study`.

- `DESCRIBE` now accepts only sqlglot `Describe` trees using Athena table grammar.
  Non-table roots, `DESCRIBE TABLE`, and any nested DDL, DML, command, or write
  expression are rejected, including through `EXPLAIN`.
- Opaque `SHOW` command bodies now pass a positive token grammar. The v1 allowlist is
  `DATABASES`/`SCHEMAS`, `TABLES`, `COLUMNS`, `PARTITIONS`, `TBLPROPERTIES`, and
  `VIEWS`, with only their documented optional scopes, filters, and property name.
  Unknown, incomplete, appended, and `SHOW CREATE` forms are rejected.
- `UNION`, `INTERSECT`, and `EXCEPT` roots are accepted when their complete trees are
  read-only. Nested set operations and set operations inside a `SELECT` are covered.
- `EXPLAIN` accepts `FORMAT TEXT|GRAPHVIZ|JSON` and
  `TYPE LOGICAL|DISTRIBUTED|VALIDATE|IO`, strips the validated option list, then
  recursively validates exactly one allowed statement. `EXPLAIN ANALYZE`, unknown
  options, and unsafe bodies remain rejected.
- Parser and tokenizer failures use suppressed exception chaining. Raw SQL fragments
  no longer enter formatted exception chains, modal traceback previews, sqlglot log
  capture, or crash dumps.
- `QueryExecutionDetail.state_reason` and `output_location` now use `repr=False`.
  The complete query-model repr audit also confirms that Athena error messages,
  result rows, named-query SQL, and prepared-statement SQL remain excluded.

The earlier concern about a sqlglot `TokenError` retaining a query fragment in a
rendered cause is resolved by this follow-up.

### Focused RED evidence

The `DESCRIBE` bypass matrix failed because the previous policy returned immediately
for every `Describe` root:

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_accepts_athena_table_describe_grammar tests/unit/domain/test_sql_policy.py::test_policy_rejects_non_table_or_write_describe_grammar -q
....FFFFFFF..FFFF                                                        [100%]
E       Failed: DID NOT RAISE QueryRejectedError
11 failed, 6 passed in 0.30s
```

The `SHOW` deny matrix failed because every opaque body except structural `CREATE`
was allowed:

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_accepts_allowlisted_athena_show_grammar tests/unit/domain/test_sql_policy.py::test_policy_rejects_unknown_write_or_incomplete_show_grammar -q
................FFFFFFF..FFF                                             [100%]
E       Failed: DID NOT RAISE QueryRejectedError
10 failed, 18 passed in 0.31s
```

Set-operation roots and option-bearing `EXPLAIN` statements failed before the valid
read implementation:

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_accepts_safe_select_set_operations tests/unit/domain/test_sql_policy.py::test_policy_rejects_write_nested_in_set_operation tests/unit/domain/test_sql_policy.py::test_policy_accepts_valid_explain_options tests/unit/domain/test_sql_policy.py::test_policy_rejects_unknown_explain_options_or_unsafe_body -q
FFFF..FFFF.....                                                          [100%]
8 failed, 7 passed in 1.15s
```

The privacy regression used the unique marker `SQLSECRET_7F4C2A9D`. Before suppressed
chaining, it appeared in the formatted sqlglot `TokenError` chain:

```console
$ uv run pytest tests/unit/infra/test_crash_dump.py::test_rejected_sql_is_absent_from_exception_chains_logs_and_crash_dump -q
F                                                                        [100%]
E       AssertionError: assert 'SQLSECRET_7F4C2A9D' not in 'Traceback (...Athena SQL\n'
1 failed in 0.34s
```

The detail repr regression failed on the Athena state reason before reaching the
result-location assertion:

```console
$ uv run pytest tests/unit/domain/test_query.py::test_query_execution_detail_sensitive_aws_fields_are_excluded_from_repr -q
F                                                                        [100%]
E       AssertionError: assert 'state-reason-secret-7f4c2a9d' not in 'QueryExecut...able=False))'
1 failed in 0.26s
```

Final self-review found that quoted option names could impersonate `TYPE` or `FORMAT`.
The deny cases were added before tightening the token-kind check:

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_rejects_unknown_explain_options_or_unsafe_body -q
..FF...                                                                  [100%]
E       Failed: DID NOT RAISE QueryRejectedError
2 failed, 5 passed in 0.30s
```

### Focused GREEN evidence

```console
$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_accepts_athena_table_describe_grammar tests/unit/domain/test_sql_policy.py::test_policy_rejects_non_table_or_write_describe_grammar -q
.................                                                        [100%]
17 passed in 0.26s

$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_accepts_allowlisted_athena_show_grammar tests/unit/domain/test_sql_policy.py::test_policy_rejects_unknown_write_or_incomplete_show_grammar -q
............................                                             [100%]
28 passed in 0.26s

$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_accepts_safe_select_set_operations tests/unit/domain/test_sql_policy.py::test_policy_rejects_write_nested_in_set_operation tests/unit/domain/test_sql_policy.py::test_policy_accepts_valid_explain_options tests/unit/domain/test_sql_policy.py::test_policy_rejects_unknown_explain_options_or_unsafe_body -q
...............                                                          [100%]
15 passed in 0.23s

$ uv run pytest tests/unit/infra/test_crash_dump.py::test_rejected_sql_is_absent_from_exception_chains_logs_and_crash_dump -q
.                                                                        [100%]
1 passed in 0.50s

$ uv run pytest tests/unit/domain/test_query.py::test_query_execution_detail_sensitive_aws_fields_are_excluded_from_repr -q
.                                                                        [100%]
1 passed in 0.22s

$ uv run pytest tests/unit/domain/test_sql_policy.py::test_policy_rejects_unknown_explain_options_or_unsafe_body -q
.......                                                                  [100%]
7 passed in 0.25s

$ uv run pytest tests/unit/domain/test_query.py tests/unit/domain/test_sql_policy.py tests/unit/infra/test_crash_dump.py -q
........................................................................ [ 58%]
...................................................                      [100%]
123 passed in 0.32s
```

### Final verification

```console
$ uv run pytest tests/unit/domain -q
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 62%]
........................................................................ [ 82%]
............................................................             [100%]
348 passed in 14.95s

$ uv run pytest tests/unit/infra/test_crash_dump.py -q
........                                                                 [100%]
8 passed in 0.30s

$ uv run mypy
Success: no issues found in 131 source files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
346 files already formatted

$ ./scripts/check-layers.sh
layer rules clean

$ uv lock --check
Resolved 176 packages in 4ms

$ uv run pip-audit
No known vulnerabilities found
Name    Skip Reason
------- ----------------------------------------------------------------------
aws-tui Dependency not found on PyPI and could not be audited: aws-tui (0.8.0)

$ git diff --check
```

All requested follow-up verification commands listed above exited 0. As in the
original report, the repeated local `.zshenv` warning is unrelated and omitted. The
dependency-audit skip remains limited to the unpublished local package.

An additional repository-wide `uv run pytest -q` check, beyond the requested Task 1
gate, was interrupted after four minutes once it had established widespread
unrelated failures:

```console
$ uv run pytest -q
109 failed, 289 passed, 2 skipped, 9 deselected, 1 warning in 241.73s (0:04:01)
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! KeyboardInterrupt !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

The first non-snapshot failure was the existing silent-SSO journey receiving one
startup toast instead of zero. The remaining displayed failures were Textual
snapshot comparisons across demo, EMR, EMR logs, and Glue. None exercised the Athena
query models or SQL policy, and the run created no tracked changes.

### Remaining concerns

- No Important Task 1 finding remains open.
- The parser policy remains defense in depth behind IAM, Lake Formation, Athena
  workgroup, and S3 controls.
- The earlier Minor review scope around exact field/`__all__` assertions and
  exhaustive per-model immutability coverage was not expanded in this focused fix.
- The unrelated repository-wide E2E/snapshot failures above remain outside Task 1;
  the requested focused, domain, crash-dump, type, lint, format, layer, lock, audit,
  and diff gates are green.
