# Athena Eager Glue Prefill Design

**Status:** Approved for implementation on 2026-08-30.

## Problem

The Glue-to-Athena handoff mounts the Athena page and then waits for
`ContentHostVM`'s Athena setup task before calling `AthenaPageVM.open_table()`.
That ordering is invisible with the in-memory demo provider, but real AWS
metadata discovery can take long enough that users see a switched Athena page
whose query editor remains blank. The existing post-`open_table()` refresh
cannot help because execution has not reached `open_table()` yet.

The handoff must show its deterministic, local starter SQL before the first
remote Athena setup await. It must still refuse execution until the destination
workgroup, catalog, and database have been resolved and validated.

## Chosen Design

`AthenaPageVM` remains the owner of starter-SQL generation and Athena context
resolution. It gains a synchronous, idempotent `prime_table_query()` operation
that:

1. validates the table's connection and region without requiring loaded AWS
   metadata;
2. selects and persists the Query view;
3. marks the query context as resolving through `AthenaQueryVM`; and
4. publishes `select_starter_sql(...)` through `AthenaQueryVM.set_sql()`.

The app transaction calls this operation immediately after mounting the Athena
page and before `_wait_for_current_service_setup()`. It explicitly refreshes the
mounted page and waits for a Textual refresh, making both VM SQL and visible
editor text observable while provider setup is still pending.

`AthenaPageVM.open_table()` remains the complete public handoff operation. It
calls the same idempotent prime method for direct callers, performs bounded
remote discovery and exact context selection, and clears the resolving state in
a `finally` block. The app also abandons a prime if setup fails or the handoff is
cancelled before `open_table()` begins. Shutdown and disposal continue to make
all query commands unavailable.

## VMx Command Contract

`AthenaQueryVM` exposes read-only `is_context_resolving` state and owns its
transitions. Its existing VMx `AsyncRelayCommand` remains the execution
abstraction; `_can_execute()` returns false while context resolution is pending.
Property-change publication triggers both command predicate reevaluation and
view refresh. The query controls display `RESOLVING TABLE CONTEXT` while the
gate is active.

No view-local execution boolean, duplicate command, timer, or direct
`TextArea` mutation is introduced. The generated query remains read-only,
quoted, limited to five rows, and is never run automatically.

## Failure, Cancellation, and Supersession

The existing generation guards, source validation, rollback, and toast behavior
remain authoritative. A failed or cancelled handoff abandons the pending query
gate before or during rollback. A superseded handoff cannot publish over the
newer destination because the transaction checks its generation after mount
and before context completion.

The early SQL is provisional UI state inside the destination VM. If the
handoff fails, existing snapshot rollback restores the source service and its
previous state; no provisional query is retained as a successful handoff.

## Verification

An integration regression test blocks real-provider-shaped Athena setup after
the destination page mounts. Before releasing the provider it proves:

- Athena is visible;
- `AthenaQueryVM.sql` contains the exact starter query;
- the mounted `TextArea` contains the same SQL;
- the Query view is active;
- Run is disabled; and
- no query execution was submitted.

After release it proves the exact SQL persists, the intended catalog/database
context is selected, and Run becomes available. Focused unit coverage verifies
the resolving gate and its property/command transitions. Existing fresh,
revisited, Iceberg, rollback, supersession, click, and key-path tests remain
green.
