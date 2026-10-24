# Glue to Athena handoff and generated-query coherence

Approved in chat on 2026-10-24. This repair addresses failures reproduced on
the current checkout using normal application launch and controlled providers.

## Behavior

Selecting an Iceberg table in Glue and invoking Athena from the bottom command
bar opens Athena with the exact connection, region, catalog, database and table.
Athena resolves the applicable workgroup and output settings for that source.
The visible editor contains a quoted, read-only `SELECT * FROM … LIMIT 5`.
Navigation and selection never execute a query automatically.

When an Athena database or catalog changes, retain the previous table name if
it exists in the resulting table list. Otherwise select the first table in the
displayed order and populate its starter query. An empty database has no
selected table, no stale generated query, and a visible empty-table state.
An explicit table choice updates the generated query. Existing confirmation
protects manually edited SQL before replacement; cancelling preserves the
document and coherent selection. Late metadata results cannot overwrite a
newer selection, authored edit, or navigation intent.

## Repair boundaries

Keep RootVM and ContentHostVM responsible for adoption and lifecycle. Identify
programmatic navigation projections by explicit publication provenance rather
than the current asyncio task. A real later user navigation must still
supersede the handoff. Ownership must survive synchronous/reentrant hub
delivery and both eager and deferred task startup.

Replace App-level refresh waits that depend on Textual's test-only private
task field with supported refresh synchronization on the mounted screen or
widget. Preserve shutdown, cancellation and rollback behavior. Do not assign
private Textual task fields or add arbitrary sleeps as a repair.

Use existing authoring/document provenance to distinguish untouched table SQL
from edited SQL. Update context, selected table and generated SQL together;
metadata failures must remain visible, without substituting an unrelated
default context or leaving obsolete generated SQL marked ready.

## Verification and delivery

Add regression tests before production changes and record their failing
assertions. Cover normal App.run_async/CLI behavior in addition to run_test,
eager/deferred scheduling, bottom-command handoff, quoted identifiers,
workgroup/output context, first-table fallback, retained table names, empty
databases, edits, failure, cancellation and stale-result ownership.

Only applicable local checks are authorized. No GitHub Actions or hosted CI
gates. Use isolated configuration and controlled providers; no live AWS
without explicit account/resource authorization. Preserve Atlas, Docker,
Ollama and shared infrastructure. No dependency or global-launcher changes.

Review and commit repairs on `codex/glue-athena-handoff-repair`, push, merge a
PR into develop, promote develop into main through another PR, verify matching
contents, and remove only the merged repair branch locally and remotely.
Preserve repository protections and unrelated work/PRs.
