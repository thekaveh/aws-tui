# Iceberg metadata coverage (#236)

The six Athena metadata inspections currently return capped tuples. The UI
mistakes exhaustion of a local reveal window for exhaustive source coverage.
This change carries evidence about coverage separately from cached pagination.
The user's standing goal authorizes routine design and implementation decisions.

Use a frozen generic `IcebergInspection[T]` containing rows and an immutable
`IcebergCoverage` with status (`complete`, `truncated`, `unknown`), metadata row
limit, collection name, and table reference. The Athena runner also records
whether its result pagination was exhausted, defaulting to unknown for callers
that provide no evidence. Each inspector requests LIMIT/display cap + 1 and
fetches at most that number into its bounded result. Seeing the extra row proves
truncation; the extra row is removed before mapping/rendering. Without it,
exhausted pagination proves completeness, otherwise coverage remains unknown.
Exactly the display cap is never labelled truncated without sentinel evidence.

Alternative: leave SQL unchanged and mark every full batch unknown. This is
honest but cannot provide known truncation. Alternative: count metadata rows
separately; that adds requests, races, and scan work. One sentinel preserves
finite bounds and gives useful evidence in the existing request.

Caps stay snapshots/history/refs 100, manifests/partitions 500, files 1000.
Coverage describes the selected table's named metadata collection at inspection
time, never table data, scan bytes, or a future whole-table scan.

Each VM pane stores coverage beside cached rows, including its stable state.
Retry/provider errors preserve last known rows and coverage; cancellation and
stale responses obey existing generation guards. Table rebinding clears both.
Structured responses must match the bound table, view and cap, and must have
consistent counts. Legacy bare tuples may remain accepted with unknown coverage
to avoid inventing evidence; built-in providers use structured results.

The metadata footer reports visible count, fetched display-row count, source
coverage, collection and metadata row limit independently. Warnings persist
when Load more exhausts cached rows, including during failed refresh. Load more
only reveals cached rows; retry/fresh inspection remains explicit. Preview's
separate DuckDB workflow keeps its existing behavior.

Acceptance evidence: domain boundary tests (0, cap-1, cap, cap+1) and SQL/fetch
bounds; runner pagination exhaustion tests; all-six-view VM coverage, retry,
error, cancellation and stale binding tests; running Textual pilot footer tests
and a Glue snapshot; built-in demo coverage; cookbook §7.1 wording and scan-cost
limitations. Local gates only, with no GitHub Actions dispatch.
