# Amazon Athena

The Athena service is an AWS-only, read-only query console. It combines local
fail-closed SQL validation with workgroup-aware execution, history, paged
results, saved queries, and exact-source handoffs to Glue and S3.

## 1. Query context

Choose Source, Workgroup, Catalog and Database in that order. `Shift+S` rebuilds the page
under the next supported AWS source. `Shift+W`, `Shift+C`, and `Shift+D` open
the corresponding selector. Changing any parent context retires loaders,
results, and app-owned execution state from the prior identity.

After a workgroup, catalog or database change, Athena retains the previous table name when available.
Otherwise, it selects the first available table and generates a quoted `SELECT * FROM "database"."table" LIMIT 5`.
An empty database has no selected table or generated query.
Context changes keep the active view open, including History and Saved.

Choosing another table updates the starter query without executing it.
Athena asks before replacing modified SQL; declining keeps the previous selection and editor.

On Query, focus a context selector and press `r` to refresh context metadata and tables.
Refresh reloads workgroup details, catalogs and databases while preserving valid selections, SQL, draft ownership and execution results.
A failed read shows its own error while other refreshed metadata remains usable.
Bounded or incomplete discovery does not prove that a selected resource was removed.

A successful source change opens a fresh editor. If the change fails or is
cancelled, Athena restores the outgoing SQL, context, and save status while that
transaction still owns the page. This also preserves unsaved SQL when local
draft persistence is off. Newer edits or navigation take priority over rollback.

If the original context is no longer available, Athena retains the SQL and
clears its context selectors. Run remains disabled until you select an available
context. Existing draft-origin restrictions still apply.
Refresh and credential retry preserve the requirement to select a context.

Focus moves through Source, Workgroup, Catalog, Database, then Query's available Table selector, the tabs, and the active view.
Enabled load-more controls appear immediately after their selector.
The segmented frame contains Query (`1`), History (`2`), Results (`3`), and
Saved (`4`). Unavailable controls are omitted from the active focus ring.

Context discovery stops at 1,000 items. A visible safety-limit state replaces **Load more** when this ceiling is reached.
That state remains visible after refresh or navigation rollback.

<a id="8-author-sql-and-use-local-files" name="8-author-sql-and-use-local-files"></a>

## 2. Author SQL and use local files

The **SQL** menu on Query provides completion, formatting, local files, and local snippets.
Press `Alt+E` to open it, or use the command palette.
These actions edit SQL without submitting a query. Run still applies the read-only policy before execution.

<a id="81-complete-and-format-sql" name="81-complete-and-format-sql"></a>

### 2.1. Complete and format SQL

1. Select the source, workgroup, catalog, and database.
2. Place the cursor where you need a keyword, table, or column.
3. Press `Ctrl+Space`, then choose one suggestion.

Completion uses the current context and cursor position, including quoted identifiers and nested columns.
It inserts only the selected suggestion. Context changes invalidate suggestions and their metadata cache.
Metadata reads are bounded; the status reports partial or unavailable results.
A slow read can keep completion busy until its underlying request finishes.

Press `Ctrl+Alt+F` to format the current SQL layout.
Formatting preserves comments, literal values, quoted identifiers, and statement meaning.
Incomplete or unsupported SQL stays unchanged, with a reason in the status.
Completion, formatting, and snippet insertion support editor undo.

<a id="82-open-and-save-sql-files" name="82-open-and-save-sql-files"></a>

### 2.2. Open and save SQL files

Use `Ctrl+Alt+O` to open a local UTF-8 `.sql` file.
Use `Ctrl+Alt+S` to save it, or `Ctrl+Alt+Shift+S` to choose another path.
The SQL menu provides the same commands when your terminal does not transmit these shortcuts.
Files must be regular files, at most 1 MiB; symbolic links are refused.

The editor shows the filename or **Untitled**, plus a modified marker.
The tooltip and Execution detail show a file's full path.

The marker compares SQL with the current document baseline.
Opening or saving a file, or loading an AWS query or table query, sets that baseline.
Draft autosaves do not clear the marker. Recovered drafts start as modified untitled documents.

Save asks before overwriting an existing destination.
Changed files, permissions, invalid encoding, and size limits produce recoverable errors without clearing the editor.

Open, saved-query loads, draft recovery, and table replacement ask before replacing modified SQL.
An accepted whole-document load starts a new undo history.
Changing the editor while a decision or load is pending invalidates that replacement.
Saving a file preserves the current undo history.

<a id="83-keep-local-snippets" name="83-keep-local-snippets"></a>

### 2.3. Keep local snippets

Choose **Save local snippet…** in the SQL menu to store the current SQL with a name.
Choose **Insert local snippet…** to insert a selection at the cursor or replace the selected text.
Snippet choices show their local provenance, including the connection name and region.
Provenance is descriptive; it does not switch the active AWS context.

Local snippets are separate from the AWS named queries and prepared statements in Saved.
They use plaintext `sql-snippets.json` in the app's configuration directory.
The library holds at most 100 snippets, with 64 KiB per SQL entry and 8 MiB total.
Do not store secrets in SQL files or snippets. Snippet actions do not create or update AWS saved queries.

### 2.4. Load AWS saved SQL

Saved pages named queries and prepared statements separately.
Opening either copies SQL into the editor without executing it.
Each saved-query collection stops at 1,000 items and shows a safety-limit state instead of **Load more**.
The limit remains visible after refresh or navigation rollback.

<a id="2-read-only-execution-policy" name="2-read-only-execution-policy"></a>

## 3. Read-only execution policy

`Ctrl+Enter` dispatches exactly one statement only after the local SQLGlot
Athena-dialect policy accepts it. The allowlist covers bounded read forms such
as `SELECT`, approved set operations, selected `SHOW` and `DESCRIBE` forms, and
safe `EXPLAIN`. DDL, DML, unload, call, transaction, analysis, and multiple
statements fail closed before `StartQueryExecution`.

The complete grammar and examples are maintained in the
[Cookbook](../cookbook.md#63-exact-read-only-sql-grammar). IAM, Lake
Formation, workgroup, S3, bucket, and KMS policy remain the authorization
boundary after local validation.

<a id="3-query-lifecycle-and-results" name="3-query-lifecycle-and-results"></a>

## 4. Query lifecycle and results

The app records the identity of each query it starts and polls until a terminal
state. **Stop** or `Esc` interrupts submission or requests that Athena stop the
app-owned query. A polling failure does not prove that the remote query stopped:
Stop remains available after the error. Run first requests a stop for that query
before submitting another. If the stop fails, the app keeps the original query
identity, shows the error, and does not submit a replacement. A successful stop
response acknowledges the request; it is not a separate terminal-state check.

Results load at most
1,000 rows per Athena page and retain the continuation token for `l` to load
more. History hydrates selected execution detail, including bytes scanned and
result reuse, with one `BatchGetQueryExecution` request per bounded 50-row
history page. History stops at 1,000 executions and shows a safety-limit state instead of **Load more**.
The limit remains visible after refresh or navigation rollback.

Workgroup configuration determines customer-S3 output or Athena managed
results. Only a successful customer-S3 execution has an artifact that can be
opened in the S3 service.

The Query view orders Query controls, Query editor, then Execution detail. SQL,
Run, Stop, and query status participate in keyboard focus traversal when available. At the minimum supported terminal size of 80×24, the app uses a one-line banner
that names the service, source, region, and demo status. Compact controls and a
scrollable Execution detail pane leave at least three rows for the SQL editor.

Tab to Execution detail and use the Up and Down arrows to read additional rows. Resizing keeps typed SQL, context selections, and keyboard focus; taller terminals
restore the spacious banner and query layout.

<a id="7-inspect-and-control-loaded-results" name="7-inspect-and-control-loaded-results"></a>

## 5. Inspect and control loaded results

On Results, arrow keys select individual cells. Press Enter or click the current cell to select it. This includes the first cell or a result containing only one cell.

`Alt+Enter` opens the complete
original value in a read-only, scrollable inspector. Escape or Close returns
to the same original row and column when that execution and cell remain
available. Null and empty strings have explicit status. Enter in the inspector
does not execute SQL.

`Alt+C` copies the selected cell as JSON; `Alt+Shift+C` copies its row as a
JSON array in column order. Null becomes `null`, an empty string becomes `""`,
and the literal string `NULL` becomes `"NULL"`. JSON escapes newlines and quotes;
Unicode remains readable. Duplicate column labels remain separate array
positions. Copy uses original values, including complete multiline strings.

Clipboard delivery depends on the terminal and platform clipboard helper; a
failed or unacknowledged terminal write is not a confirmed native copy. Check
the app's clipboard outcome message.

`Alt+F` opens a literal, case-insensitive substring filter across every cell
of the loaded rows. Null is searchable as `null`. Apply changes the filter,
Cancel leaves it unchanged, and Clear restores all loaded rows. A zero-match
filter still reports visible and loaded counts and whether more rows are
available. The footer's `local` scope means loaded rows only.

`Alt+S` cycles the selected column through ascending, descending, and original
loaded order. Sorting compares Unicode strings lexically, without converting
numeric-looking text; null stays last in both directions and ties retain
loaded order. `Alt+R` clears both the local filter and sort. Filtering a selected
row out clears its selection; sorting preserves a surviving original cell.

Only explicit **Load more** or `l` fetches the next page. New rows enter the
current filter and order. The 10,000-row safety ceiling can leave unfetched
results: `more available` and `safety limit` are scope warnings, including when
no loaded rows match. Local controls never rewrite or rerun SQL or fetch all
pages.

Source/context changes, execution replacement or reload, and page
shutdown reset selection, filter, and sort and invalidate an open inspector. These settings are transient. Full-execution export is separate scope in
[#254](https://github.com/thekaveh/aws-tui/issues/254).

These six actions can be remapped in `[keybindings]`; Help and the Athena
command palette display their actual configured keys. They apply to the
current Results view and yield while typing in an editor or modal.

## 6. Local SQL draft recovery

Enable **Local SQL drafts** in Settings's **Athena SQL drafts** section using
**Enable local SQL drafts**. Persistence defaults to off and is unavailable in
demo mode. The editor border shows **Draft pending**, **Draft saved**, or
**Draft not saved** from the acknowledged save state. Pending text has not been
confirmed saved.

Read the full fixed error or context explanation in Execution
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
not choose another connection or context.

If the current document is modified,
**Replace unsaved SQL?** asks before replacement. **Keep current editor** clears
only a failed recovery decision; it does not bypass the original-context guard
on recovered SQL. Recovery never automatically executes SQL, loads result rows,
or creates AWS named queries. Recovered SQL is validated again before execution.

Failed navigation and credential refresh preserve the editor's original context,
recovery decision, and save status. Returning to Athena does not make SQL from
another database runnable. Unsaved text remains unconfirmed until its save is
acknowledged. Clearing such text still removes the draft from its original
context, rather than deleting a draft for the currently selected database.
If another editor has replaced the stored draft, restored text is shown as
unsaved and is not saved over that newer draft automatically. Edit to save it,
recover the current stored draft, or delete the draft explicitly.

The five persisted context fields cannot attest same-name AWS credential-account
changes across restarts. Verify the account behind the selected profile before
running recovered SQL. Completed autosaves can be recovered after an abrupt
exit; pending or failed edits are not guaranteed to be recoverable. Follow the
[keyboard recovery recipe](../cookbook.md#66-save-and-recover-local-sql-drafts)
for restart and shutdown details.

<a id="4-cross-service-handoffs" name="4-cross-service-handoffs"></a>

## 7. Cross-service handoffs

Select a table in Glue, then choose **Athena** from the bottom Commands bar.
Athena opens with that table's connection, region, catalog, database and table selected.
Its workgroup and output settings come from the selected Athena source.
Iceberg snapshot requests also prefill the editor under the same source.

`i` inserts a copied table reference only when its
source identity matches the active Athena context. The command palette can
open one unambiguous query table in Glue or a validated successful result
artifact in S3. None of these handoffs substitutes another profile.

Glue table and Iceberg snapshot handoffs fill the editor with a quoted `SELECT *` starter ending in `LIMIT 5`.
They never execute it.

<a id="5-architecture-and-verification" name="5-architecture-and-verification"></a>

## 8. Further reading and demo

The [Architecture guide](../architecture.md#1-layers) explains the query, history, results and saved-query components.
It also describes parsing, paging and execution ownership.
The [Contract ledger](../contract-ledger.md) records locked SDK request shapes and SQL policy compatibility.
Contributors can use the [testing guide](../architecture.md#5-testing-pyramid) for verification workflows.

Demo mode supplies synthetic workgroups, query states, results, saved SQL, managed output, customer-S3 artifacts and scoped denial.
It sends no AWS requests. Local SQL file and snippet actions still use your filesystem.
