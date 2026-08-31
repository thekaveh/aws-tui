# 1. Athena Query Execution Repair Design

**Status:** Approved for implementation on 2026-08-31.

## 1.1. Problem

The Athena Query view has two related usability failures on real AWS:

1. Run and Stop occupy `3x3` terminal cells, but ordinary terminal cells are
   taller than they are wide. The controls therefore render as narrow vertical
   rectangles even though their cell counts match.
2. A Glue-generated starter query can pass local read-only validation and still
   be rejected by `StartQueryExecution`. The selected workgroup already uses an
   enforced S3 result location, which rules out the app's typed missing-result-
   configuration path. The current error pipeline then discards Athena's
   sanitized validation reason and displays only `Athena rejected the request`,
   preventing useful diagnosis.

The Glue handoff currently repeats catalog authority in two places: as the
first component of a three-part SQL table name and as
`QueryExecutionContext.Catalog`. The generated query only needs the database
and table because the handoff resolves and validates the exact Athena catalog
and database before enabling Run.

## 1.2. Query And Request Contract

Glue-generated starter SQL will use a quoted, context-relative table name:

```sql
SELECT * FROM "database"."table" LIMIT 5
```

Iceberg snapshot handoffs retain the same qualification and append the bounded
`FOR VERSION AS OF` clause. The request continues to send the selected catalog
and database in `QueryExecutionContext`, the selected workgroup in `WorkGroup`,
and a unique 64-character client token.

The shipped query path will not send a caller-side
`ResultConfiguration.OutputLocation`. An enforced S3 workgroup applies its own
authoritative result configuration, while an Athena-managed-results workgroup
must not receive an S3 output override. A workgroup with neither mode remains a
typed, non-retried configuration error.

No rejected submission is retried automatically. A retry with changed SQL or
request identity could make an ambiguous network outcome execute twice.

## 1.3. Actionable Safe Errors

The domain boundary will keep mapping boto errors into app-owned provider
types. Known `InvalidRequestException` families will gain stable, sanitized
categories for:

- an inaccessible or unverifiable result destination;
- unavailable catalog or database context;
- workgroup request rejection; and
- other Athena validation failures.

The UI will display the category-specific copy in Execution detail. It will
not copy raw SQL, result bucket URIs, profile names, workgroup names, catalog
names, database names, table names, request tokens, or unclassified AWS error
text. Authentication, authorization, throttling, reachability, and missing
result configuration keep their existing typed behavior.

## 1.4. Query Controls

Run and Stop will use a fixed `6x3` terminal-cell footprint. Six conventional
half-width character cells approximate the physical width of three terminal
rows, producing a visually square control while retaining a one-row centered
glyph inside the border. Width, height, minimums, and maximums will be locked
so disabled, focused, and hover states cannot alter layout.

Terminal applications cannot know every font's pixel aspect ratio, so the
contract is exact terminal geometry plus a visually square result under normal
monospace terminal metrics, rather than a universal pixel-square guarantee.

## 1.5. Verification

Focused tests will cover:

- context-relative starter SQL, identifier escaping, and Iceberg time travel;
- exact `StartQueryExecution` arguments for enforced S3 workgroups;
- categorized rejection mapping without sensitive-value disclosure;
- query-VM presentation of each actionable category;
- exact `6x3` outer geometry and centered positive-size content for Run and
  Stop; and
- stable geometry while controls are disabled, enabled, and focused.

Glue-to-Athena integration tests and documentation contracts will be updated to
the new starter SQL. The full lint, type-check, unit, integration, snapshot,
documentation, and repository verification suites remain the merge gate.
