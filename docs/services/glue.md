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

Partition and statistics rows show their own loading, access and error states.
A zero count means the read succeeded with no items.
Refresh retries these reads; successful schema and storage remain visible when a supplement fails.

`y` copies the selected table as a fully quoted, source-aware `TableRef` into the VM-owned clipboard. It then hands the reference to the single app-level clipboard writer for the operating-system clipboard. That writer's toast names the channel that
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

Before each metadata read, the footer displays its collection, retained row cap,
SQL limit and scan-cost caveat. The SQL requests one extra row to detect truncation.
The row cap does not bound scanned bytes or charges.
If the complete plan cannot be displayed, the read does not start.

### 3.1. Explain metadata and compare snapshots

On **Snaps**, select a row and open **Info** for its committed time, parent,
operation and historical summary. Each summary key retains its raw values
beside the explained metric and unit. Missing, malformed or ambiguous values
read `unknown`; a known zero remains zero.

To compare two snapshots from the selected table:

1. Select the first snapshot and open **Info**.
2. Choose **Pin selected snapshot as A**, then close Info with `Esc`.
3. Select the second snapshot and reopen **Info**.
4. Choose **Compare A with selected B**.

The comparison displays both values and signed `B - A` deltas for recognized
numeric counters with known units. Numeric-looking IDs and arbitrary properties
do not become quantities. Ancestry requires loaded parent or history links;
missing links, conflicts and cycles remain unresolved.

On **Files** and **Parts**, Info summarizes **current observations** independently
of historical snapshot A. Counts, byte distributions and delete counters cover
all fetched rows, including rows beyond the visible page. Reports include
sample size, known-value counts, coverage, row cap and successful observation time.
An incomplete source has an unknown denominator; the report does not extrapolate.

Quantiles use nearest rank over known values; sums are known-value subtotals.
Content codes retain their raw integers: `0` means data, `1` position deletes,
and `2` equality deletes. Other codes mean `unknown`.
Data-file record counts precede applying deletes, so they do not count surviving rows.

On **Refs**, Info explains the selected reference's raw retention settings.
It does not infer missing table defaults or predict deletion times.
The main reference does not expire; branch-only settings do not apply to tags.

Info and comparison use loaded metadata and issue no additional queries.
Their scrollable, selectable text includes the complete source identity and
observation provenance. `Tab` reaches Info; arrows and `Enter` choose an action.
Within Info, `Tab` reaches the text viewer and `Esc` closes the dialog.
Changing the table or refreshing its metadata invalidates the open report.

### 3.2. Preview table rows

A seventh tab, **Peek**, previews table rows by reading the table's S3 location with local DuckDB instead of Athena.
It incurs no Athena query charge and needs no Athena workgroup or result-output location.
These savings apply to the Peek scan.
Loading **Snaps** or another metadata tab can still run an Athena query.

Direct reads can incur S3 request, retrieval and data transfer charges; see [Amazon S3 pricing](https://aws.amazon.com/s3/pricing/).
Applicable KMS charges are separate.

Select a different snapshot on **Snaps**, then open **Peek** to load its rows.
Reopening Peek with the same snapshot does not repeat the scan.
After a failed or cancelled reload, retained rows keep their successful snapshot label.
Use **Retry** to load the selected snapshot.

Peek needs the optional [`duckdb` extra](../install.md#2-optional-extras) and an AWS profile connection.
It does not appear for `s3-compatible` connections.
Without the extra, the tab stays present and selectable.
Choosing it reports the missing engine with an install prompt.

DuckDB uses the selected profile and region through its credential chain.
The profile's credentials must allow direct source S3 reads and any required KMS decryption.
Athena or Lake Formation query grants alone do not authorize those reads.

S3 and credential-provider endpoints must be reachable.
The first query also needs access to `extensions.duckdb.org` unless compatible extensions are already cached.
See the [Peek prerequisites](../cookbook.md#74-local-row-preview-with-duckdb-peek) for source permissions, encryption access and credential-chain behavior.

Its row limit bounds returned rows, not S3 bytes read, requests or charges.
Retry and load more can read S3 again.
The load-more control runs a new scan at the next row-limit step (100 → 1,000 → 10,000).
This differs from the same control on the six metadata tabs above. See the
[Cookbook](../cookbook.md#74-local-row-preview-with-duckdb-peek) for a full
walkthrough.

## 4. Athena handoffs

Select a database and table, then choose **Athena** from the bottom Commands bar or press `Shift+Q`.
Athena opens with the selected source, region, catalog, database and table, plus a quoted `SELECT * ... LIMIT 5` statement.
`Shift+V` on a visible selected snapshot
adds `FOR VERSION AS OF <snapshot-id>`. Both requests preserve catalog,
database, table, connection, and region in immutable messages. The destination
editor is prefilled for review; neither command executes SQL.

## 5. Compare table definitions

Press `Ctrl+G` or choose **Compare Glue tables** in the command palette. The comparison opens with both tables unset. Each side has its own configured
AWS source, editable region (commit with **Apply region**), database, and table. Catalog is fixed to `AwsDataCatalog`.

Choose each table explicitly; matching
names never select a counterpart. **Pin open → Left/Right** captures the table
that was open when the comparison launched, then fetches it again. The page
behind the comparison keeps its source and selection.

The headers retain each exact source, region, catalog, database and table,
with a separate UTC timestamp sampled after that side's successful fetch.
**Refresh Left/Right** refreshes independently. While refreshing, and after a
failed refresh, the previous successful definition and its original timestamp
remain visible with an explicit freshness status. Changing a source, region,
database or table clears that side's previous definition.

At narrow widths,
controls scroll vertically and long reference fields wrap in independent
read-only viewers; side labels and fetch times stay visible above those viewers.

Changes are directed from **Left to Right**: added means present only on Right,
removed means present only on Left. Columns and partition keys are compared
separately by exact, case-sensitive names. Relative order of common columns
reports reordering without treating an insertion as a reorder. Duplicate names
are shown as unavailable for unambiguous matching.

Type comparison conservatively
ignores ASCII outer whitespace and whitespace around type punctuation outside
quotes; case, quoted content, and malformed type expressions remain significant. This is a metadata difference, not a schema-compatibility verdict.

Storage compares location, input/output formats, SerDe, compression, table type,
and table format. Missing, empty, false and absent values remain distinct.
Compression reflects the provider's boolean: when Glue omits `Compressed`, the
existing provider reports `False`, so absence and explicit false compare equally.
Parameters expose key presence only; their values remain redacted/unavailable
and are never inspected for equality.

`Tab` / `Shift+Tab` visits controls; Enter or Space commits selectors and buttons,
and arrows move within selectors. `Ctrl+1` / `Ctrl+2` focuses the Left/Right source,
`Ctrl+R` refreshes the last focused side (Left initially), and `Ctrl+D` toggles
**Differences only**. Unavailable values remain in that filtered view. `Ctrl+C` or **Copy full summary** always exports the complete comparison with
both references, timestamps and freshness, regardless of the filter.

Copy is
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
