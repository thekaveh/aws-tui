# Athena result exports

## 1. Objective and authority

Deliver #254 from develop5312240d. Local controlled providers are authorized;
unavailable live/native checks were explicitly deferred in the ticket before
implementation. No AWS scope, Actions, workgroup changes or shared infra writes.
Preserve #253 result inspection/projection and #257 query controls.

## 2. Product flow and selected approach

Provide Export from Results and the command palette. A reviewable form chooses
scope (Loaded view or Complete execution), format and destination. Loaded view
means the captured visible rows, including local filter/sort. Complete execution
ignores local transforms and independently reads the captured execution beyond
the 10,000-row UI ceiling. Its total row count is unknown unless authoritative
metadata establishes it; never substitute loaded count for total count.

CSV and JSON use positional columns and normalized row values. Complete mode
also offers Original AWS CSV when authoritative successful DML output identifies
a customer-S3 CSV artifact. That option copies original bytes unchanged; it does
not claim the normalized CSV encoding below. Managed/missing-S3 output uses
Athena row paging for CSV/JSON; original-artifact mode is explicitly unsupported.
This keeps null fidelity without inventing a parser for arbitrary AWS files.

A review names source, region, workgroup/execution, scope, count/unknown, format,
destination and overwrite decision. Existing-file replacement requires an explicit
confirmation of that exact observed destination version. Cancel before publication
leaves the final destination untouched. No StartQueryExecution, UNLOAD or export
SQL is allowed. Results remain usable while staging; leaving the owning result or
context cancels/refuses publication, never retargets another source.

## 3. Encodings

Normalized CSV is UTF-8 with a header preserving original column names and order,
including duplicates. SQL null is the literal field `\N`; empty text is empty.
All backslashes in non-null data are doubled, so a literal `\N` is not null.
CSV quoting handles commas, quotes and real embedded newlines. Header names
are literal strings, not data sentinel fields. Document decoding explicitly.

JSON is one UTF-8 object with ordered `columns` metadata and positional `rows`
arrays. Null uses JSON null; empty text uses an empty JSON string. Do not key rows
by column name, infer scalar types or lose duplicate columns. Original AWS CSV
retains AWS's encoding; users choose it explicitly instead of normalized CSV.

## 4. Boundaries and resource budgets

- domain/athena_export.py: immutable limits/receipts and streaming positional
  serialization/page validation. No UI, filesystem or AWS session dependencies.
- infra/result_export_store.py: private same-directory staging, destination
  version capture, guarded atomic publication, cancellation permit and cleanup.
  No SQL-size/extension limitation and no whole-result buffering.
- domain/athena.py: original artifact reader using captured authoritative
  execution metadata and the same connection/region. Read only the exact S3
  object; pin its observed ETag/version where available, bound bytes, and close
  response streams. Never guess a key from query ID or current workgroup output.
- vm/athena/export_vm.py: captured execution/client/result generation, loaded
  projection snapshot, review preparation, request/page limits and cancellation;
  off-loop local I/O. Attach through existing PageVM/Results lifecycle.
- UI: export form/review with fixed accessible footer and standard ModalButton;
  action catalog/palette and result footer/focus traversal.

Default complete-export caps: 256MiB output, 1,000,000 rows, 10,000 provider
requests, 300 seconds. Stage in at most64KiB write chunks and retain only the
loaded snapshot or current source page/row plus bounded token history. Budgets
are independent of the UI ceiling. Original bytes have unknown row count; byte,
request and time bounds still apply. Bounds are named/injectable for tests.

Malformed page shape, changing columns, oversized/invalid/repeated continuation
tokens, timeouts or exhausted budgets refuse final publication. Empty pages with
advancing tokens count toward request limits. Never report a partial artifact
as complete. Source providers/SDK may materialize one response page; the exporter
does not accumulate complete results. Measure controlled >10k workloads.

## 5. Publication and ownership

Capture a regular local target version without reading unbounded existing data;
reject symlink/nonregular targets and unapproved/changed overwrites. Stage with
private permissions; writes and flush/fsync run off loop. New publication must be
exclusive against a competing created target. Existing targets are rechecked
before atomic replacement. Follow the existing SQL-store limitation: this is not
an OS-wide compare-and-swap guarantee against arbitrary external writers.

Cancellation fences publication through a permit and drains outstanding thread
I/O before cleanup. If publication already won, acknowledge the completed file
truthfully instead of reporting a failed/cancelled write. Retain the captured
source identity for late completion feedback; do not select or mutate new context.
No SQL/cell values/output URLs/tokens in logs, errors, operation records or toasts.
Review widgets may show the information the user needs locally.

## 6. Verification and completion

TDD all codecs/null/duplicates/Unicode, large streaming/resource limits, token and
schema failures; safe publication overwrite/races/cancel; original artifact exact
identity/ETag/stream cleanup; typed Athena/S3/KMS/expiry failures; actual app review,
loaded projection vs complete, zero initiating queries, context changes and
responsive keyboard paths. Test protected connections allow reads/local export.
Update canonical guidance and SDK consumed contracts. Independent review and
applicable local checks precede feature PR develop, promotion PR main, exact tree
parity, owned cleanup and issue closure. Only then #262.
