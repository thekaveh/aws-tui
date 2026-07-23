# 1. AWS Glue and Amazon Athena services design

**Status:** Accepted in brainstorming on 2026-07-22. This document is the
source of truth for implementation planning. It adds AWS Glue and Amazon
Athena as separate first-class services, makes AWS profile switching
consistent across single-context AWS services, and treats Apache Iceberg as a
shared Glue/Athena/S3 capability rather than a third navigation service.

## 1.1. Decision summary

- Add separate `Glue` and `Athena` rows to the service navigation.
- Keep S3's independent source per pane.
- Give EMR Serverless, Glue, and Athena one shared active AWS connection.
- Make `Shift+S` switch the active source consistently across services.
- Move EMR's next-application shortcut to a dedicated EMR action.
- Ship read-oriented Glue catalog, job-run, and crawler visibility.
- Ship Athena workgroup, query-history, saved-query, read-only SQL, and result
  visibility.
- Share catalog identifiers and query models in the domain layer.
- Detect Iceberg tables in Glue and expose Iceberg metadata and time travel
  through Athena.
- Use Glue and Athena APIs remotely. Do not add PyIceberg, Arrow, DuckDB, or a
  JVM runtime in the first release.
- Add `sqlglot>=30.13.0,<31` as the sole new runtime dependency, using its
  Athena dialect for structured read-only SQL validation.
- Never aggregate resources from multiple AWS connections into one list in
  the first release.

## 1.2. Product goal

Turn aws-tui's current S3 and EMR Serverless data-platform surface into a
coherent AWS data operations console. A user with several AWS profiles must
be able to select a profile and region, browse the Glue assets and workloads
that profile can access, inspect and execute read-only Athena queries, follow
an Iceberg table across Glue, Athena, and S3, and switch profiles without
seeing stale data from the previous security context.

## 1.3. Non-goals

The first release does not:

- create, update, or delete Glue databases, tables, jobs, crawlers,
  connections, workflows, triggers, or data-quality rules;
- start or stop Glue jobs or crawlers;
- create or alter Athena workgroups, catalogs, named queries, prepared
  statements, views, databases, or tables;
- execute Athena DDL or mutating DML, including CTAS, INSERT, UPDATE, DELETE,
  MERGE, OPTIMIZE, VACUUM, UNLOAD, or CREATE VIEW;
- provide a generic Iceberg REST catalog backend;
- query Iceberg locally with PyIceberg, Arrow, DataFusion, or DuckDB;
- aggregate resources across accounts, profiles, or regions;
- replace AWS IAM, Lake Formation, workgroup, or S3 bucket policies;
- add CloudWatch Logs as a first-class service;
- redesign the existing S3 dual-pane source model.

# 2. Connection and source model

## 2.1. Two intentional source scopes

S3 remains the only multi-context page. Each S3 pane independently owns a
source and can point at local storage, an AWS S3 profile, or an S3-compatible
connection.

EMR Serverless, Glue, and Athena are single-context AWS services. They share
the active AWS `Connection` held by `RootVM`. Switching the source in any of
these services updates the root connection; moving to another single-context
service retains that connection.

This means a user can select `analytics-prod / us-west-2` in Glue, open an
Athena query for a table, and continue under `analytics-prod / us-west-2`
without another credential or region selection.

## 2.2. Eligible connections

The source ring for EMR Serverless, Glue, and Athena contains every resolved
connection where `connection.kind == "aws"` and the active service's
`supports(connection)` returns true. Auto-discovered AWS profiles and
explicit configured AWS connections participate. S3-compatible connections
do not appear.

Connection identity is the configured connection name plus region, not only
the underlying profile string. Two configured connections may intentionally
reference the same profile in different regions.

## 2.3. Switching behavior

`Shift+S` remains `app.swap_source` and means "switch source" everywhere:

- S3: cycle the focused pane's source, unchanged.
- EMR Serverless: cycle the service-level AWS connection.
- Glue: cycle the service-level AWS connection.
- Athena: cycle the service-level AWS connection.

EMR's current next-application behavior moves from `app.swap_source` to a
dedicated `emr.next_application` action with default key `Shift+A`. The
existing application picker remains available through `a`.

For a single-context service switch, the app must:

1. disable commands that could start work under the old connection;
2. cancel local loaders, pollers, and result fetches;
3. request cancellation of any still-running Athena query started by this
   app page;
4. dispose the current hosted page VM;
5. update root connection and authentication state;
6. publish `ConnectionChangedMessage`;
7. rebuild the same selected service against the new connection;
8. mount the replacement page and focus its default pane;
9. update source labels, status, and hint text.

The page clears old rows before an asynchronous request for the new source is
started. No old-profile rows may remain visible while the new source loads.

## 2.4. Access failures are service-scoped

An `AccessDenied` response from Glue or Athena means the selected connection
lacks access to that service or resource. It does not mark the connection
globally unreachable and does not remove it from S3, EMR, Glue, or Athena
source rings.

Credential expiration and missing credentials continue through the existing
authentication message path. Endpoint and network failures remain retryable
for the active service without poisoning other services.

## 2.5. State and cache isolation

Every cache, selection memory, continuation token, request generation, and
in-flight task is scoped by a stable connection key and region. Athena state
also includes workgroup and catalog where relevant.

Per-connection selection memory is retained for the process lifetime:

- Glue: selected view, database, table, job, crawler, and result filters.
- Athena: selected workgroup, catalog, database, history row, and saved query.

Returning to a connection may restore identifiers, but it must revalidate
them against newly loaded resources before selecting them.

# 3. Layered architecture

## 3.1. Existing boundaries remain authoritative

The implementation follows the repository's enforced dependency direction:

```text
View (Textual) -> ViewModel (VMx) -> Service plugins -> Domain -> Infrastructure
```

`app.py` and `composition.py` remain composition roots. Service modules may
compose domain clients and concrete VMs but may not import Textual or
`aws_tui.ui`. Views do not import boto, services, or domain AWS clients.

## 3.2. Shared domain vocabulary

Glue and Athena share immutable domain values instead of translating between
service-specific dictionaries:

- `CatalogRef(catalog_name, connection_name, region)`
- `DatabaseRef(catalog_name, database_name, connection_name, region)`
- `TableRef(catalog_name, database_name, table_name, connection_name, region)`
- `Column(name, type_name, comment, partition_key)`
- `StorageDescriptor(location, input_format, output_format, serde)`
- `TableFormat` with at least `ICEBERG`, `HIVE`, `HUDI`, `DELTA`, and `OTHER`
- `QueryExecutionRef(execution_id, connection_name, region, workgroup)`
- `QueryState` with `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, and `CANCELLED`
- `QueryStatistics(engine_ms, queue_ms, planning_ms, service_ms,
  bytes_scanned, reused_previous_result)`
- `ResultPage(columns, rows, next_token)`
- Iceberg snapshot, history, manifest, file, reference, and partition summaries

Models expose only fields used by the first-release UI. Raw boto response
objects never escape domain clients.

## 3.3. Client boundaries

`GlueClient` owns Glue API mapping and pagination:

- `list_databases_page`
- `list_tables_page`
- `get_table`
- `list_partitions_page`
- `get_column_statistics`
- `list_jobs_page`
- `list_job_runs_page`
- `list_crawlers_page`
- `get_crawler`
- `get_crawler_metrics`

`AthenaClient` owns Athena API mapping and pagination:

- `list_workgroups_page` and `get_workgroup`
- `list_catalogs_page`
- `list_databases_page`
- `list_tables_page`
- `list_query_executions_page`
- `get_query_execution` and `get_query_runtime_statistics`
- `start_query`
- `stop_query`
- `get_results_page`
- `list_named_queries_page` and `get_named_queries`
- `list_prepared_statements_page` and `get_prepared_statements`

Both clients receive an AWS session configured from the active `Connection`.
They apply the repository's standard botocore retry and timeout posture and
map boto failures into stable domain errors.

`IcebergInspector` is an app-owned domain boundary. It reads format and
storage metadata from Glue and uses read-only Athena metadata-table queries
for `$snapshots`, `$history`, `$manifests`, `$files`, `$partitions`, and
`$refs`. It depends on client protocols, never on Glue or Athena VMs.

## 3.4. Service plugins

`GlueService` and `AthenaService` implement the existing `Service` protocol.
Both return `supports(connection) == (connection.kind == "aws")` and build a
fresh page VM for every `build_vm(connection)` call.

`GlueService` composes a Glue client, an Athena-backed Iceberg inspector, and
`GluePageVM`. Glue remains usable if Athena is unavailable; only Iceberg
inspection and "Query in Athena" report their scoped unavailability.

`AthenaService` composes an Athena client, the read-only SQL policy, and
`AthenaPageVM`.

The service registry remains ordered: S3, EMR Serverless, Glue, Athena.

## 3.5. Cross-service messages

Cross-navigation travels through immutable VMx message envelopes. Messages
carry plain identifiers and connection identity, never clients or VMs:

- `OpenAthenaTableRequest(table_ref, snapshot_id=None)`
- `OpenGlueTableRequest(table_ref)`
- `OpenS3LocationRequest(connection_name, region, uri, preferred_pane)`

The app composition root handles service and view mounting. A destination
must first switch to the message's connection and region, then select the
target resource. If the source connection no longer exists, the request
fails visibly without silently substituting another profile.

# 4. Glue service

## 4.1. Page structure

The Glue page has a compact source header and three views selected through
the established tab/action patterns:

1. Catalog
2. Jobs
3. Crawlers

The source header displays configured connection name, profile when present,
and region. It is status, not a second navigation card.

## 4.2. Catalog view

The Catalog view is a master-detail workflow:

- database list;
- table list for the selected database;
- table detail for the selected table.

Database and table lists are independently paginated. Selection changes
cancel stale detail loads. Table detail contains:

- catalog, database, owner, description, creation and update times;
- classification and detected table format;
- columns and partition keys;
- storage location, formats, SerDe, compression, and bucket count;
- table parameters;
- paginated partitions;
- available column statistics;
- direct "Open S3 location" and "Query in Athena" commands.

For Iceberg tables, additional detail views show snapshot history,
references, manifests, files, and partition summaries. These views are
loaded on demand because metadata tables may be large and Athena queries
incur latency and scan cost.

## 4.3. Jobs view

The Jobs view lists Glue job definitions and recent runs. Selecting a job
loads runs newest first; selecting a run loads detail. The UI surfaces:

- job name, Glue version, command, script location, role, worker type,
  worker count, timeout, retry count, and default arguments;
- run ID, state, attempt, trigger, start/completion timestamps, duration,
  execution class, allocated capacity, arguments, and predecessor runs;
- error message, state detail, execution time, and log group links when
  supplied by AWS.

The view does not start, stop, retry, or edit jobs.

## 4.4. Crawlers view

The Crawlers view lists crawler identity and state, then provides:

- role, database, targets, classifiers, recrawl policy, schema-change policy,
  schedule, security configuration, Lake Formation configuration, and tags;
- latest crawl status, start time, duration, error message, tables created,
  tables updated, and tables deleted;
- navigation to catalog databases and tables named by crawler results.

The view does not run, stop, schedule, or edit crawlers.

# 5. Athena service

## 5.1. Page structure

The Athena page has four views:

1. Query
2. History
3. Results
4. Saved

The header selects workgroup, catalog, and database within the active AWS
connection. Changing any context invalidates result state that is not valid
under the new context.

## 5.2. Query view

The query view provides a practical multiline SQL editor, execute command,
cancel command, and compact execution status. Glue-to-Athena navigation
preselects the table's catalog and database and inserts a bounded starter
query. Iceberg snapshot navigation inserts an Athena engine v3 time-travel
query using `FOR VERSION AS OF`.

Submitting a query records the exact connection, region, workgroup, catalog,
database, and client request token. A repeated command caused by UI retry must
not accidentally start a second execution.

## 5.3. Read-only SQL policy

The app fails closed before calling Athena. SQL is parsed with
`sqlglot>=30.13.0,<31` using `dialect="athena"`; regular expressions or
string-prefix checks are not sufficient. The policy accepts only one
statement whose root is:

- `SELECT`, including CTEs and subqueries;
- `SHOW`;
- `DESCRIBE`;
- `EXPLAIN` of another allowed read-only statement.

The policy rejects multiple statements, DDL, DML, CTAS, UNLOAD, stored
procedure calls, and any unrecognized syntax. Parser failure is rejection.

IAM, Lake Formation, workgroup configuration, and S3 bucket policies remain
the authoritative security boundary. The parser is defense in depth and a
product contract, not a substitute for least privilege.

## 5.4. Query execution

Execution follows this state machine:

```text
VALIDATING -> SUBMITTING -> QUEUED -> RUNNING -> SUCCEEDED
                                      |           |
                                      |           +-> RESULTS
                                      +-> FAILED
                                      +-> CANCELLED
```

After `StartQueryExecution`, polling uses bounded exponential backoff with a
maximum interval and lifecycle cancellation. Terminal state loads execution
statistics and either the first results page or a structured failure.

Only queries started by the active app page are eligible for
`StopQueryExecution` on explicit cancel or source disposal. Viewing history
never grants authority to cancel another actor's query.

## 5.5. History view

History is scoped to the selected workgroup and lists executions newest
first. Detail includes:

- state and state-change reason;
- submitted and completed times;
- catalog, database, workgroup, engine version, and result location;
- queue, planning, engine, and service-processing times;
- bytes scanned and result reuse;
- Athena error category, type, retryability, and message;
- links to results and the result S3 location when permitted.

The UI may display query text returned by AWS but must not write full query
text to application logs or crash reports.

## 5.6. Results view

Results are typed from Athena result metadata and paginated without loading
the complete result set. The first header row is not duplicated as data.
Nulls, empty strings, booleans, numbers, timestamps, arrays, and nested text
must remain distinguishable in rendering.

Export copies or streams an existing Athena result object through the
application's filesystem workflow. Export does not rerun the query and does
not materialize the entire result in memory.

## 5.7. Saved view

Saved exposes named queries and prepared statements for the selected
workgroup. Selecting one shows its name, description where available,
database, workgroup, and SQL. "Open in editor" copies the SQL into the query
view but does not execute it.

# 6. Iceberg integration

## 6.1. Detection

Glue table metadata is mapped to `TableFormat.ICEBERG` using documented table
parameters and storage metadata. Detection logic is centralized and tested
against representative Glue responses. Views never inspect raw parameter
dictionaries.

## 6.2. Metadata queries

Iceberg metadata is queried on demand through Athena using fully quoted
catalog, database, and table identifiers. Supported metadata tables are:

- `$snapshots`
- `$history`
- `$manifests`
- `$files`
- `$partitions`
- `$refs`

Every metadata request is constrained with an explicit column projection and
reasonable row limit where Athena supports it. Large file and partition sets
remain paginated.

## 6.3. Time travel

A user can select a snapshot and open an Athena editor containing a read-only
query using `FOR VERSION AS OF <snapshot_id>`. Timestamp travel may be added
using `FOR TIMESTAMP AS OF` when a committed timestamp is available.

The generated query is never executed automatically. The user reviews and
explicitly runs it.

## 6.4. No separate Iceberg nav item

Iceberg appears as richer behavior on qualifying Glue tables and Athena
queries. S3 remains the byte/object view of the same table location. A third
Iceberg navigation row would duplicate catalog and query state and is out of
scope.

# 7. Data flow and concurrency

## 7.1. Paginated loads

List VMs use VMx's token-paged composition pattern already adopted by EMR job
runs. Each loader returns domain objects plus an opaque next token. Filters
that alter request semantics create a new pager rather than reusing old
tokens.

## 7.2. Request generations

Each page increments a generation when connection, region, workgroup,
catalog, database, or selected parent resource changes. Completion handlers
compare their captured generation before publishing results. A stale
completion is discarded and cannot change selection, state, notifications,
or command availability.

Generation checks complement cancellation because SDK calls may finish after
local task cancellation.

## 7.3. Polling and disposal

VM-owned workers are registered with their owning VM lifecycle. `destruct`
stops subscriptions and workers; `dispose` releases commands, pagers, and
clients. Athena polling and Glue refresh loops cannot survive a page or
connection switch.

# 8. Error model

Glue and Athena map SDK failures to stable categories:

- `AUTH_REQUIRED`: missing, expired, or unloadable credentials;
- `FORBIDDEN`: IAM or service access denied;
- `LAKE_FORMATION_FORBIDDEN`: Lake Formation resource denial;
- `NOT_FOUND`: catalog, database, table, job, crawler, workgroup, or query no
  longer exists;
- `THROTTLED`: retryable rate limiting with backoff;
- `UNREACHABLE`: endpoint, DNS, TLS, or network failure;
- `INVALID_REQUEST`: invalid pagination token or service request;
- `INVALID_QUERY`: Athena parser or service validation failure;
- `RESULT_CONFIGURATION_REQUIRED`: no usable Athena result configuration;
- `QUERY_FAILED`: terminal Athena failure with structured error data;
- `CANCELLED`: local or remote cancellation.

Errors belong to the smallest affected pane. A failed Iceberg metadata query
does not blank the Glue table schema; a failed query result fetch does not
erase query execution detail. `r` retries the focused failed pane.

# 9. Security, privacy, and cost controls

- Reuse the current AWS profile and credential chain. Do not persist resolved
  credentials or session tokens.
- Redact credentials, session material, signed URLs, and security-sensitive
  boto fields from logs and crash dumps.
- Do not log full SQL, query result values, job arguments, prepared statement
  text, table parameters, or raw AWS responses.
- Treat Athena query text and result locations as potentially sensitive.
- Respect Lake Formation row, column, and cell filters by querying through
  Athena under the selected principal.
- Display the active workgroup and its enforced result location before query
  execution.
- Display bytes scanned and result reuse after execution.
- Require explicit user execution for generated queries.
- Give generated starter queries a conservative `LIMIT`.
- Athena `SELECT` is source-read-only but still creates result artifacts.
  Documentation must state this clearly.

# 10. Demo mode

Demo mode supplies deterministic Glue and Athena clients with at least two
AWS profiles whose resources do not overlap.

Profile A includes:

- ordinary Hive/Parquet and Iceberg catalog tables;
- successful and failed Glue jobs and runs;
- ready, running, and failed crawlers;
- successful, running, failed, and cancelled Athena queries;
- named and prepared queries;
- Iceberg snapshots and time-travel results.

Profile B uses different database, table, job, crawler, workgroup, and query
identifiers. Switching profiles must visibly clear Profile A before rendering
Profile B, proving isolation without real AWS access.

# 11. Test strategy

## 11.1. Unit tests

- Glue and Athena response mapping, missing optional fields, enum fallbacks,
  timestamps, pagination tokens, and error mapping.
- Table-format detection for Iceberg, Hive, Hudi, Delta, and unknown tables.
- Structured SQL allow and deny matrices, including CTEs, comments,
  semicolons, nested statements, EXPLAIN, CTAS, UNLOAD, and parser failures.
- Profile and region cache keys.
- VM pagination, selection restoration, command enablement, and empty states.
- Generation-based stale-result suppression even when cancellation loses a
  race.
- Polling backoff, terminal states, explicit cancellation, and disposal.
- Result header removal, null rendering, pagination, and export handoff.
- Cross-service messages preserve connection and resource identity.

## 11.2. Service and integration tests

- Service descriptors, AWS-only support, client construction, and fresh VM
  ownership.
- Root profile switching rebuilds EMR, Glue, and Athena while preserving the
  selected service.
- S3 pane sources remain independent and unchanged by the new generic path.
- Access denial in one service does not mark the connection globally
  unreachable.
- Glue-to-Athena, Glue-to-S3, Athena-to-Glue, and Athena-to-S3 navigation.
- Iceberg inspection remains optional when Athena access is unavailable.
- Deterministic multi-profile fake services prove no cross-profile leakage.

## 11.3. View and end-to-end tests

- Snapshot every new pane and modal state across all shipped themes, with
  paired SVG content-presence assertions.
- Verify narrow and wide terminal layouts without text overlap.
- Pilot journeys cover source switching, Glue catalog browsing, Glue job and
  crawler failures, Athena read-only query execution, history/results,
  Iceberg metadata, time travel, and S3 handoff.
- Update the action registry, keymap, command palette, hint legend, and
  keybinding documentation together.

## 11.4. Contract and live smoke tests

The contract ledger records the locked boto/botocore service models and every
Glue/Athena operation used. An optional, explicitly configured live smoke
test uses a least-privilege profile, a dedicated Athena workgroup, and a
bounded `SELECT 1` or small `LIMIT` query. It never runs in default CI.

# 12. Documentation

Update these surfaces together:

- README feature and demo sections;
- `docs/architecture.md` service and VM trees;
- `docs/adding-a-service.md` references;
- `docs/connections.md` profile/region behavior;
- `docs/keybindings.md` source and EMR application bindings;
- `docs/cookbook.md` Glue/Athena/Iceberg workflows and permissions;
- `docs/contract-ledger.md` API contracts;
- release checklist and changelog;
- generated docs site and wiki surfaces required by the repository's
  documentation policy;
- architecture diagram regenerated after the service and VM topology lands.

# 13. Delivery decomposition

This design is deliberately delivered as four independently reviewable
subprojects. Each produces working, tested software and receives its own
implementation plan and PR-sized checkpoints.

## 13.1. Foundation: shared AWS service source switching

- Add the generic single-context source switch orchestration.
- Move EMR application cycling to `emr.next_application`.
- Adopt profile switching in EMR.
- Add shared connection-scoped request generation and cross-service identity
  values.
- Prove S3 behavior is unchanged.

## 13.2. Glue service

- Add shared catalog models and Glue client.
- Add Catalog, Jobs, and Crawlers VMs and views.
- Register and route Glue.
- Add demo data, tests, and documentation.
- Include Glue-to-S3 navigation.

## 13.3. Athena service

- Add query models, Athena client, and read-only SQL policy.
- Add Query, History, Results, and Saved VMs and views.
- Register and route Athena.
- Add demo data, tests, and documentation.
- Include Athena-to-S3 navigation.

## 13.4. Cross-service Iceberg integration

- Add format detection and `IcebergInspector`.
- Add Glue-to-Athena and Athena-to-Glue navigation.
- Add Iceberg metadata views and generated time-travel queries.
- Complete integrated demo, E2E, docs, and architecture diagram updates.

# 14. Acceptance criteria

The feature is complete when:

1. Glue and Athena are separate first-class services for AWS connections.
2. S3-compatible connections never expose Glue or Athena.
3. `Shift+S` switches the focused S3 pane or the active single-context AWS
   service as appropriate.
4. EMR, Glue, and Athena retain one shared active AWS connection across nav
   changes.
5. Source switching clears old content immediately and stale requests cannot
   repopulate it.
6. Access denial remains service-scoped.
7. Glue Catalog, Jobs, and Crawlers provide all read-only workflows in this
   spec with pagination and retryable pane states.
8. Athena Query, History, Results, and Saved provide all workflows in this
   spec with paginated results and structured execution detail.
9. The app rejects every non-read-only Athena statement before SDK dispatch.
10. Iceberg tables expose metadata and generate, but never automatically run,
    snapshot time-travel queries.
11. Cross-navigation preserves connection, region, catalog, database, table,
    workgroup, and snapshot identity where applicable.
12. No sensitive query, result, job argument, credential, or raw response data
    enters logs or crash dumps.
13. Demo mode proves two-profile isolation and all primary workflows.
14. Unit, integration, snapshot, E2E, layer, type, lint, packaging, dependency
    audit, and documentation checks pass on supported platforms.
15. User-facing docs, generated docs, wiki content, contract ledger, changelog,
    and architecture diagram match the shipped behavior.
