# Amazon Athena

The Athena service is an AWS-only, read-only query console. It combines local
fail-closed SQL validation with workgroup-aware execution, history, paged
results, saved queries, and exact-source handoffs to Glue and S3.

## 1. Query context

Source, Workgroup, Catalog, and Database are four individually framed,
dependent overlay selectors in one unframed row. `Shift+S` rebuilds the page
under the next supported AWS source. `Shift+W`, `Shift+C`, and `Shift+D` open
the corresponding selector. Changing any parent context retires loaders,
results, and app-owned execution state from the prior identity.

Focus moves through Source, Workgroup, Catalog, Database, the tabs, and the
active view; enabled load-more controls appear immediately after their selector.
The segmented frame contains Query (`1`), History (`2`), Results (`3`), and
Saved (`4`). Unavailable controls are omitted from the active focus ring.

## 2. Read-only execution policy

`Ctrl+Enter` dispatches exactly one statement only after the local SQLGlot
Athena-dialect policy accepts it. The allowlist covers bounded read forms such
as `SELECT`, approved set operations, selected `SHOW` and `DESCRIBE` forms, and
safe `EXPLAIN`. DDL, DML, unload, call, transaction, analysis, and multiple
statements fail closed before `StartQueryExecution`.

The complete grammar and examples are maintained in the
[Cookbook](../cookbook.md#63-exact-read-only-sql-grammar). IAM, Lake
Formation, workgroup, S3, bucket, and KMS policy remain the authorization
boundary after local validation.

## 3. Query lifecycle and results

The Query VM records an app-owned execution identity and polls until a terminal
state. `Esc` interrupts query submission or stops the active app-owned execution. Results load at most
1,000 rows per Athena page and retain the continuation token for `l` to load
more. History hydrates selected execution detail, including bytes scanned and
result reuse, with one `BatchGetQueryExecution` request per bounded 50-row
history page. Saved independently pages named queries and prepared statements;
opening either copies SQL into the editor without executing it.

Workgroup configuration determines customer-S3 output or Athena managed
results. Only a successful customer-S3 execution has an artifact that can be
opened in the S3 service.

The Query view orders Query controls, Query editor, then Execution detail. Run,
Stop, and query status participate in keyboard focus traversal when available.
At the minimum supported terminal size of 80×24, the app uses a one-line banner
that names the service, source, region, and demo status. Compact controls and a
scrollable Execution detail pane leave at least three rows for the SQL editor.
Tab to Execution detail and use the Up and Down arrows to read additional rows.
Resizing keeps typed SQL, context selections, and keyboard focus; taller terminals
restore the spacious banner and query layout.

Glue table and Iceberg snapshot handoffs prefill a quoted
`SELECT *` starter ending in `LIMIT 5`; the transaction explicitly projects the
starter into the mounted editor and never executes it.

## 4. Cross-service handoffs

Glue table and Iceberg snapshot requests prefill the editor under the same
connection and region. `i` inserts a copied table reference only when its
source identity matches the active Athena context. The command palette can
open one unambiguous query table in Glue or a validated successful result
artifact in S3. None of these handoffs substitutes another profile.

## 5. Architecture and verification

`AthenaService` composes `AthenaPageVM` from query, history, results, and saved
VMs. VMx owns commands, observable state, and lifecycle. Athena's app-owned
snapshot pager retains VMx commands while adding immutable hydration and
cumulative ceilings: 1,000 context, history, and saved-query items, and 10,000
result rows. Reaching a ceiling replaces load-more with a visible safety-limit
state that survives refresh and snapshot rollback. SQLGlot owns parsing; the
domain client owns public Athena API calls and response mapping. Generation
and execution identity guards prevent retired async work from publishing into
a replacement context.

Demo mode provides profile-isolated workgroups, query states, result pages,
saved SQL, managed-output cases, customer-S3 artifacts, and scoped denial.
Contract tests validate AWS request names and fields against the locked
botocore model. Policy tests exercise accepted and rejected SQL across the
minimum and locked SQLGlot versions.

## 6. Local SQL draft recovery

Enable **Local SQL drafts** in Settings's **Athena SQL drafts** section using
**Enable local SQL drafts**. Persistence defaults to off and is unavailable in
demo mode. The editor border shows **Draft pending**, **Draft saved**, or
**Draft not saved** from the acknowledged save state. Pending text has not been
confirmed saved. Read the full fixed error or context explanation in Execution
detail; edit again to retry a failed save. **No saved draft** means no current
saved SQL. See [Configuration](../configuration.md#5-local-athena-sql-drafts)
for the plaintext storage location, retention, and deletion behavior.

Tab to **Drafts** and press Enter to open the local manager. Its scrollable list
and detail show metadata and saved timestamps, without SQL previews. Selecting
a row or pressing Enter in the list does not restore it. Tab and Shift+Tab move
between the list, scrollable detail, and actions; Up and Down scroll the focused
detail. **Restore** is a separate action. Close or Escape returns focus to
**Drafts**; a successful restore focuses the editor.

Before restoring, select the exact original connection name and region, then
its workgroup, catalog, and database. Recovery validates the current source and
context afresh and refuses a missing, changed, or inaccessible context. It does
not choose another connection or context. If the editor has unsaved text,
**Replace unsaved SQL?** asks before replacement. **Keep current editor** clears
only a failed recovery decision; it does not bypass the original-context guard
on recovered SQL. Recovery never automatically executes SQL, loads result rows,
or creates AWS named queries. Recovered SQL is validated again before execution.

The five persisted context fields cannot attest same-name AWS credential-account
changes across restarts. Verify the account behind the selected profile before
running recovered SQL. Completed autosaves can be recovered after an abrupt
exit; pending or failed edits are not guaranteed to be recoverable. Follow the
[keyboard recovery recipe](../cookbook.md#66-save-and-recover-local-sql-drafts)
for restart and shutdown details.
