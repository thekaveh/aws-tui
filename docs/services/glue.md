# AWS Glue and Iceberg Metadata

The Glue service is an AWS-only, read-only operations console for Data Catalog
tables, ETL jobs, crawlers, and bounded Apache Iceberg metadata inspection. It
also provides typed handoffs to S3 and Athena without executing a query or
changing resources automatically.

## 1. Source and views

The standalone source selector chooses one exact configured AWS connection and
region. `Shift+S` cycles resolver order. Switching sources rebuilds the page
and keeps catalog, job, crawler, and remembered selections isolated by source
identity.

The segmented view frame contains Catalog (`1`), Jobs (`2`), and Crawlers
(`3`). Jobs and Crawlers add overlay state selectors. The active tab remains
visually distinct after focus enters the content, while the entire frame is a
single keyboard stop.

## 2. Catalog, jobs, and crawlers

Catalog lists databases and tables, then loads schema, storage, partitions,
and column statistics for the selected table. Jobs lists definitions and
recent runs. Crawlers lists crawler state, configuration, metrics, and latest
crawl detail. Access denial remains scoped to the affected pane and does not
invalidate the AWS source for other services.

`y` copies the selected table as a fully quoted, source-aware `TableRef` into
the VM-owned clipboard, then hands it to the single app-level clipboard writer
for the operating-system clipboard. That writer's toast names the channel that
actually accepted the text and never reports an unacknowledged OSC 52 write as
a copy. The command palette can open a valid selected table location in S3
under the same connection and region.

Lists page through the Glue API 100 rows at a time (200 for job runs). A footer reading
`N items · more available` means another page exists: press `l`, run **Load
more Glue rows** from the command palette, or click the footer. Every list
stops at 1,000 rows and then reads `safety limit`.

## 3. Iceberg metadata

The table detail enables Iceberg controls only when normalized Glue metadata
contains an exact Iceberg marker. Snapshots, History, Manifests, Files,
Partitions, and References load on demand through bounded Athena metadata
queries. Each tab owns its own loading, failure, retry, paging, and selection
state, so one failed query does not erase successful sibling tabs.

The bounded limits and required permissions are documented in the
[Cookbook](../cookbook.md#71-iceberg-detection-and-metadata-views). These
queries incur ordinary Athena workgroup and result-storage behavior.

A seventh tab, **Peek**, previews table rows by querying the table's S3
location directly with a local DuckDB engine instead of Athena — no
workgroup, no query execution, no query bill. It needs the optional `duckdb`
extra (`pip install aws-tui[duckdb]`) and an AWS profile connection; it does
not appear for `s3-compatible` connections, and without the extra installed
it stays present and selectable; choosing it reports the missing engine with
an install prompt rather than disappearing silently. Its row limit is a real
ceiling, not a local-window
widen: the load-more control reruns a genuinely new scan at the next
row-limit step (100 → 1,000 → 10,000), unlike the same control on the six
metadata tabs above. See the
[Cookbook](../cookbook.md#74-local-row-preview-with-duckdb-peek) for a full
walkthrough.

## 4. Athena handoffs

`Shift+Q` opens the selected table in Athena with a quoted
`SELECT * ... LIMIT 5` statement. `Shift+V` on a visible selected snapshot
adds `FOR VERSION AS OF <snapshot-id>`. Both requests preserve catalog,
database, table, connection, and region in immutable messages. The destination
editor is prefilled for review; neither command executes SQL.

## 5. Compare table definitions

Press `Ctrl+G` or choose **Compare Glue tables** in the command palette.
The comparison opens with both tables unset. Each side has its own configured
AWS source, editable region (commit with **Apply region**), database, and table.
Catalog is fixed to `AwsDataCatalog`. Choose each table explicitly; matching
names never select a counterpart. **Pin open → Left/Right** captures the table
that was open when the comparison launched, then fetches it again. The page
behind the comparison keeps its source and selection.

The headers retain each exact source, region, catalog, database and table,
with a separate UTC timestamp sampled after that side's successful fetch.
**Refresh Left/Right** refreshes independently. While refreshing, and after a
failed refresh, the previous successful definition and its original timestamp
remain visible with an explicit freshness status. Changing a source, region,
database or table clears that side's previous definition. At narrow widths,
controls scroll vertically and long reference fields wrap in independent
read-only viewers; side labels and fetch times stay visible above those viewers.

Changes are directed from **Left to Right**: added means present only on Right,
removed means present only on Left. Columns and partition keys are compared
separately by exact, case-sensitive names. Relative order of common columns
reports reordering without treating an insertion as a reorder. Duplicate names
are shown as unavailable for unambiguous matching. Type comparison conservatively
ignores ASCII outer whitespace and whitespace around type punctuation outside
quotes; case, quoted content, and malformed type expressions remain significant.
This is a metadata difference, not a schema-compatibility verdict.

Storage compares location, input/output formats, SerDe, compression, table type,
and table format. Missing, empty, false and absent values remain distinct.
Compression reflects the provider's boolean: when Glue omits `Compressed`, the
existing provider reports `False`, so absence and explicit false compare equally.
Parameters expose key presence only; their values remain redacted/unavailable
and are never inspected for equality.

`Tab` / `Shift+Tab` visits controls; Enter or Space commits selectors and buttons,
and arrows move within selectors. `Ctrl+1` / `Ctrl+2` focuses the Left/Right source,
`Ctrl+R` refreshes the last focused side (Left initially), and `Ctrl+D` toggles
**Differences only**. Unavailable values remain in that filtered view.
`Ctrl+C` or **Copy full summary** always exports the complete comparison with
both references, timestamps and freshness, regardless of the filter. Copy is
disabled until both definitions are available. **View full summary** provides a
read-only selectable version when clipboard delivery is unavailable; clipboard
status uses the existing application writer. Escape closes an open selector
first, then the comparison, restoring the page's focus.

Comparison uses only Glue database listing, table listing and table-detail
reads. It performs no partition/statistics reads, Athena queries or writes.
Each side's listing stops at 1,000 items, 64 page requests, repeated tokens or
three consecutive empty pages. Closing invalidates pending work immediately;
cleanup drains requests that resist cancellation without publishing stale data.

## 6. Architecture and verification

`GlueService` composes `GluePageVM` from Catalog, Jobs, Crawlers, and Iceberg
VMs. VMx token-paged compositions own AWS continuation tokens, and the app
message hub carries typed cross-service requests. The service and VMs depend
on domain protocols rather than Textual widgets.

Demo mode provides disjoint catalogs, jobs, crawlers, Iceberg metadata, and a
scoped access-denied profile. Contract tests validate every consumed Glue and
Athena operation against the locked botocore model. Unit, snapshot,
integration, and end-to-end tests cover paging, stale-source rejection,
partial failures, focus order, and all handoffs.
