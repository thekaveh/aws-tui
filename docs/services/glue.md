# 1. AWS Glue and Iceberg metadata

The Glue service is an AWS-only, read-only operations console for Data Catalog
tables, ETL jobs, crawlers, and bounded Apache Iceberg metadata inspection. It
also provides typed handoffs to S3 and Athena without executing a query or
changing resources automatically.

## 1.1. Source and views

The standalone source selector chooses one exact configured AWS connection and
region. `Shift+S` cycles resolver order. Switching sources rebuilds the page
and keeps catalog, job, crawler, and remembered selections isolated by source
identity.

The segmented view frame contains Catalog (`1`), Jobs (`2`), and Crawlers
(`3`). Jobs and Crawlers add overlay state selectors. The active tab remains
visually distinct after focus enters the content, while the entire frame is a
single keyboard stop.

## 1.2. Catalog, jobs, and crawlers

Catalog lists databases and tables, then loads schema, storage, partitions,
and column statistics for the selected table. Jobs lists definitions and
recent runs. Crawlers lists crawler state, configuration, metrics, and latest
crawl detail. Access denial remains scoped to the affected pane and does not
invalidate the AWS source for other services.

`y` copies the selected table as a fully quoted, source-aware `TableRef` into
the VM-owned clipboard and, on a best-effort basis, the operating-system
clipboard. The command palette can open a valid selected table location in S3
under the same connection and region.

## 1.3. Iceberg metadata

The table detail enables Iceberg controls only when normalized Glue metadata
contains an exact Iceberg marker. Snapshots, History, Manifests, Files,
Partitions, and References load on demand through bounded Athena metadata
queries. Each tab owns its own loading, failure, retry, paging, and selection
state, so one failed query does not erase successful sibling tabs.

The bounded limits and required permissions are documented in the
[Cookbook](../cookbook.md#171-iceberg-detection-and-metadata-views). These
queries incur ordinary Athena workgroup and result-storage behavior.

## 1.4. Athena handoffs

`Shift+Q` opens the selected table in Athena with a quoted
`SELECT * ... LIMIT 5` statement. `Shift+V` on a visible selected snapshot
adds `FOR VERSION AS OF <snapshot-id>`. Both requests preserve catalog,
database, table, connection, and region in immutable messages. The destination
editor is prefilled for review; neither command executes SQL.

## 1.5. Architecture and verification

`GlueService` composes `GluePageVM` from Catalog, Jobs, Crawlers, and Iceberg
VMs. VMx token-paged compositions own AWS continuation tokens, and the app
message hub carries typed cross-service requests. The service and VMs depend
on domain protocols rather than Textual widgets.

Demo mode provides disjoint catalogs, jobs, crawlers, Iceberg metadata, and a
scoped access-denied profile. Contract tests validate every consumed Glue and
Athena operation against the locked botocore model. Unit, snapshot,
integration, and end-to-end tests cover paging, stale-source rejection,
partial failures, focus order, and all handoffs.
