# DuckDB Iceberg Preview Design

**Status:** Approved for planning on 2026-09-24. Not implemented.

## 1. Problem

Reading five rows of your own Iceberg table currently requires solving an
Athena administration problem first.

The Glue catalog offers "Query table in Athena", which prefills
`SELECT * FROM "db"."tbl" LIMIT 5` and stops. Pressing Run fails on a
workgroup whose result storage is not configured:

```
Athena cannot access the workgroup result location
```

That message comes from `src/aws_tui/domain/athena.py:214`, raised when AWS
rejects `StartQueryExecution` with an `InvalidRequestException` naming the
output location. The query view model never supplies one:
`_output_location` is initialised to `None`
(`src/aws_tui/vm/athena/query_vm.py:153`) and is only ever assigned afterwards,
from a completed execution's detail (`:815`). Every run therefore depends on
the selected workgroup carrying its own result configuration.

The same tax applies to Iceberg metadata. All six existing panes read through
Athena — `IcebergInspector` builds `SELECT ... FROM "db"."tbl$snapshots"` and
runs it through `AthenaQueryRunner` (`src/aws_tui/domain/iceberg.py:240-243`).
Each pane click is a billable query needing a resolved workgroup, which the
Glue service hunts for across up to 64 pages of `ListWorkGroups`
(`src/aws_tui/services/glue/service.py:176-213`) before giving up with
"Athena workgroup unavailable".

DuckDB reads the table directly from S3. No workgroup, no result location, no
bill. For the specific act of looking at rows, it removes the entire class of
failure rather than working around it.

## 2. Reversal of a recorded decision

Four documents in this repository currently forbid this:

| Location | Text |
|---|---|
| `docs/superpowers/specs/2026-07-22-glue-athena-services-design.md:22` | "Do not add PyIceberg, Arrow, DuckDB, or a JVM runtime in the first release." |
| same file `:50` | non-goal: "query Iceberg locally with PyIceberg, Arrow, DataFusion, or DuckDB" |
| `docs/superpowers/plans/2026-07-22-iceberg-cross-service-integration.md:15` | "Do not add PyIceberg, Arrow, DuckDB, DataFusion, or a JVM." |
| `docs/superpowers/plans/2026-07-22-glue-service.md:20` | same sentence |

The qualifier "in the first release" reads as scoping rather than principle,
and those documents are historical records of their own passes. The maintainer
approved the reversal on 2026-09-24. Implementation must add a dated note to
the 2026-07-22 design spec pointing here, and must not silently edit the
historical text.

## 3. Goals and non-goals

**Goal.** A seventh pane in the Glue Iceberg view that previews rows of the
selected Iceberg table by querying S3 locally, pinned to the selected snapshot
when one is chosen.

**Non-goals, each a candidate follow-up.**

- A SQL editor and the DuckDB read-only policy it would require.
- `s3-compatible` connections. AWS profile connections only.
- Replacing the Athena backend behind the six existing metadata panes.
- Fixing the local-window "load more" on those six panes.
- Fixing the Athena workgroup result-location gap from §1. That is a separate
  ticket and must not wait on this work.

## 4. Verified facts

Probed against DuckDB 1.5.5 in a throwaway virtual environment on 2026-09-24.
Neither the `duckdb` binary nor the Python package is installed in this
project or on the maintainer's machine.

| Fact | Evidence |
|---|---|
| `httpfs`, `aws` and `iceberg` extensions install and load | all three returned OK |
| Cancellation is real | `hasattr(con, "interrupt")` is `True`; `duckdb.InterruptException` exists |
| `PROFILE` binds a secret to a named profile | error read the real config: "no profile 'nonexistent-profile' found in config file /Users/kaveh/.aws/config" |
| `snapshot_from_id` is a valid `iceberg_scan` parameter | accepted, failed later on the unreachable path |
| Version guessing is required, not optional | without it: "No version was provided and no version-hint could be found, globbing the filesystem to locate the latest version is disabled by default" |
| S3 failures carry structured fields | `duckdb.HTTPException` exposes `.status_code` (404), `.reason`, `.body`, `.headers` |
| Stable releases satisfy the floor | latest stable 1.5.5; wheels ~13-15 MB per platform |

## 5. Architecture

Four pieces, one per layer.

| Layer | Piece | Responsibility |
|---|---|---|
| `infra/duckdb.py` | `DuckDbPort`, `DuckDbResult`, `NativeDuckDb`, `InMemoryDuckDb` | Protocol, result value, engine, fake |
| `domain/iceberg_preview.py` | `iceberg_preview_sql()` | Builds the only statement that runs |
| `vm/glue/iceberg_preview_vm.py` | `IcebergPreviewVM` | Offloads, classifies, publishes |
| `ui/widgets/glue/iceberg_view.py` | the Peek pane | Binds only |

Three constraints come from existing code, not from preference.

**The port lives in `infra/` and takes plain strings.** `scripts/check-layers.sh`
forbids `infra/` from importing `aws_tui.domain`, which is exactly why
`ClipboardPort` and `ClipboardResult` are defined inside `infra/clipboard.py`.
`DuckDbPort` follows that shape: no `TableRef`, no `QueryContext`.

**Both offloads are mandatory.** The view model uses
`anyio.to_thread.run_sync`, never `asyncio.to_thread`, matching every blocking
call in this repo. The caller reaches it through `_run_lifecycle_worker`. The
clipboard code records why both are needed: one keeps the blocking call off the
event loop, the other keeps it off the App's message pump, "and the pump is
where the next keypress, `ctrl+q` included, is dequeued"
(`src/aws_tui/app.py:2386`). A local scan is CPU-bound and will freeze the
pump harder than a network wait.

**The Iceberg view model must learn the table's location.** `bind_table` takes
only a `TableRef` and a `TableFormat`
(`src/aws_tui/vm/glue/iceberg_vm.py:298-303`). The physical root lives on the
catalog view model as `detail.storage.location`
(`src/aws_tui/vm/glue/catalog_vm.py:659`), already used for the S3 handoff.
Threading it through is a small change to an existing seam.

### 5.1. The generated statement

The generator is the only caller of the port. No user-entered text reaches it.

```sql
SET unsafe_enable_version_guessing = true;
SELECT * FROM iceberg_scan('s3://bucket/warehouse/db/table') LIMIT 100;
```

With a snapshot selected on the Snaps tab, the scan gains
`snapshot_from_id := <id>`. The snapshot identifier is already validated as a
non-negative integer by `OpenAthenaTableRequest.__post_init__`
(`src/aws_tui/vm/messages.py:226-243`); the generator applies the same check,
including rejecting `bool`, which `select_starter_sql` gets right at
`src/aws_tui/domain/sql_policy.py:561`.

Version guessing is enabled because Iceberg's `metadata_location` parameter is
unavailable: `TableDetail.__post_init__` rewrites every parameter value to
`[REDACTED]` (`src/aws_tui/domain/data_catalog.py:145-151`). Passing the exact
pointer instead would need that redaction relaxed, which is deferred to a
follow-up with its own security review. The cost of guessing is an extra S3
listing per query and the risk of reading a slightly stale version under
concurrent writes.

### 5.2. Credentials

Per query, the port issues:

```sql
CREATE OR REPLACE SECRET aws_tui_peek (
  TYPE s3,
  PROVIDER credential_chain,
  PROFILE '<selected connection profile>',
  REGION '<selected connection region>'
);
```

DuckDB resolves the material itself. The app never holds a key, never calls
`get_frozen_credentials()`, and never pays the SSO refresh cost that
`src/aws_tui/app.py:1068-1070` already avoids.

Binding to the selected connection's profile is a correctness requirement, not
a convenience. The ambient default chain used in the original shell sample
could read a different account than the one displayed, which is worse than
failing.

Recreating the secret on every run preserves the recovery contract documented
at `src/aws_tui/vm/athena/query_vm.py:723-724`: `AwsSession.client` builds a
fresh session per call so an expired SSO token is re-read after the user runs
`aws sso login` in another terminal. Peek must behave the same way.

### 5.3. Failure taxonomy

Mapping keys off structured fields, not message text. The Athena equivalent
sniffs casefolded strings (`src/aws_tui/domain/athena.py:204-226`) and is
fragile by nature; this does better.

| Condition | Signal | `PaneState` and message |
|---|---|---|
| Access denied | `HTTPException.status_code == 403` | `FORBIDDEN`, "S3 access is forbidden for this table" |
| Bucket or prefix missing | `status_code == 404` | `ERROR`, "table location not found" |
| Expired or absent SSO | secret validation failure | `AUTH_REQUIRED`, "AWS authentication is required" |
| Not an Iceberg table | version-guess failure | `ERROR`, "not a readable Iceberg table" |
| Cancelled | `duckdb.InterruptException` | back to `IDLE`, not an error |
| Engine absent | `ImportError` | pane disabled, install line shown |

DuckDB echoes the failing statement into its error text, so the S3 path appears
in messages. That is not secret material, but it must pass through
`src/aws_tui/infra/redaction.py` before reaching any log.

## 6. Pane behaviour

Peek reuses the `PaneState` machine of its six siblings, so error, forbidden,
auth-required and unreachable render through the existing placeholder and
styling. It loads on tab activation, like its siblings, which is strictly
cheaper than their behaviour because it fires no billable query.

```
[Snaps][Hist][Mnfst][Files][Parts][Refs][Peek]

  Rows: 100 v        pinned to snapshot 4201

  event_id  event_date   user_id   payload
  8821      2026-09-21   u-4410    {"a":1}
  8822      2026-09-21   NULL      {"a":2}

  100 rows · limit 100 · snapshot 4201
```

**Load more is honest.** The six existing panes widen a local window over rows
already capped, issuing no new query
(`src/aws_tui/vm/glue/iceberg_vm.py:498`), so the cap is invisible and the
control implies data is arriving when none is. Peek does not copy that. The row
limit is a choice, changing it re-runs the query, and the footer states the
limit as a fact.

**Visibility is honest.** On a non-Iceberg table or a non-AWS connection the
tab is absent. With DuckDB not installed the tab is present but disabled,
carrying the install line. A silently absent feature is the failure this
project already fixed once, when clipboard code reported "Copied" after an
OSC 52 write that did nothing.

**NULL stays distinguishable** from the literal string `NULL`, reusing the
text-plus-is-null cell pair from `src/aws_tui/vm/athena/results_vm.py:154-164`.

## 7. Testing

Nothing spawns a real engine or touches S3, matching
`tests/unit/infra/test_clipboard.py:3`. `NativeDuckDb` takes its connection
factory as an injected keyword, the way `NativeClipboard` injects `which` and
`run`, so the port's real logic is testable with DuckDB absent.

| Tier | What it pins |
|---|---|
| `tests/unit/domain/` | The generator as a pure function: root, snapshot, limit in; one string out. |
| `tests/unit/infra/` | Statement order, the secret's profile and region, each error class mapped to its state. |
| `tests/unit/vm/glue/` | States, snapshot pin, cancellation, engine-absent classification. |
| `tests/integration/` | Pilot test with the fake wired: activate the tab, drain workers, assert rows. |
| `tests/snapshot/` | Loaded, empty, forbidden and engine-missing renderings. |

Two cases must be pinned explicitly because they rot quietly: the
**engine-absent path**, simulated by forcing the import to fail, since that is
the state most users start in; and **NULL rendering**.

Waits use `tests/helpers.wait_until` against an observable condition. A count of
`pilot.pause()` calls is not a bounded wait, because a pause yields one
scheduler cycle without advancing the clock.

## 8. Packaging

DuckDB ships as an optional extra. The base install is unchanged; the feature
appears only with `pip install aws-tui[duckdb]`.

Required work, each item enforced by CI:

1. `[project.optional-dependencies]` in `pyproject.toml` with a next-major cap,
   `duckdb>=1.3,<2`. **No optional-dependencies table exists today**, so this is
   new machinery.
2. `uv lock`, with `uv.lock` committed. Every CI job syncs `--locked` and fails
   on a stale lock.
3. A row in the current dated pass of `docs/contract-ledger.md` naming the
   locked version and the consumed surface: the three extensions, `iceberg_scan`
   with `snapshot_from_id`, `CREATE SECRET` with `credential_chain` and
   `PROFILE`, `unsafe_enable_version_guessing`, and `interrupt()`. This is
   machine-checked by
   `tests/docs/test_contract_parity.py:256-295`, whose package tuple must gain
   `duckdb`.
4. pip-audit clearance across the matrix.
5. A decision on the lowest-supported-dependencies job, which installs the
   project **without** extras. A DuckDB floor test cannot sit beside the
   existing ones unless that job learns to install the extra. The plan must
   settle this rather than discover it in CI.

## 9. Open questions for planning

- Where the row-limit control lives and what steps it offers.
- Whether the Peek tab is keyboard-reachable through the existing ten Iceberg
  focus slots (`src/aws_tui/ui/widgets/glue/page.py:67-77`) or needs an
  eleventh.
- Whether a long scan needs a shutdown step in the App's cleanup registry, as
  the Athena path has, given `interrupt()` makes cancellation genuine.
