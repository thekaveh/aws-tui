# Glue table comparison design

## 1. Goal and scope

Issue #258 adds a read-only comparison of exactly two explicitly selected Glue table definitions, including cross-connection and cross-region choices. Left is the baseline and Right is the compared definition. There is no automatic pairing, account scan, row query, migration, catalog write, parameter unredaction, partition inventory or freshness analysis. Copying a deterministic plain-text full summary supplies export through the existing clipboard facility.

Use a transient comparison modal, an independent VM, a routed read-only client and pure domain comparison. A fourth Glue page would change recovery/focus contracts; reusing the current catalog VM would mix source ownership and initiate partition/statistics/Iceberg work. Both alternatives add unnecessary coupling. Preserve the existing page and its recovery snapshots.

## 2. Binding constraints

- Hosted GitHub Actions and hosted gates remain disabled. Run applicable checks locally only.
- No live AWS calls, new dependency, dependency/lock/hook change, recovery-schema change, weakened assertion, expanded skip or blind golden replacement.
- Preserve all 13,892 baseline assertions, 792 goldens and six recovery snapshot classes. Any new visual asset requires an independent review of its actual render.
- Both selected TableRefs start unset. Only explicit picker commitment or an explicit Pin open table button selects a side.
- Every snapshot includes the exact five-field TableRef and an aware UTC timestamp sampled after its own successful current comparison fetch.
- Missing, empty, false, an absent item and redacted/unavailable are distinct states. Parameter values are never inspected for equality.
- Cancellation is an optimization; synchronous revision and route validation determine ownership. Left and Right never cancel or erase each other's work.
- Only database/table listing and get_table are allowed comparison provider operations. No Athena, statistics, partitions, writes, releases or external publication.
- The existing normal protected develop PR then main promotion and owned cleanup/closure sequence remains required.

## 3. Explicit selection and UI

Register `glue.compare_tables`, label `Compare Glue tables`, default Ctrl+G, available on a live Glue page even before a table is open. The command palette supplies a remapping fallback. Recheck availability in its handler.

Create a fresh VM and capture only a successfully open table's immutable ref as the optional pin candidate. Separate Pin open table to Left/Right buttons initiate fresh comparison-only reads, never invent an old fetch timestamp. The active source/region may seed listing controls but neither table is automatically chosen.

Each side has configured AWS connection picker, region input and Apply, fixed AwsDataCatalog label, explicit database/table pickers with manual More, and Refresh. Source/region changes immediately clear downstream selection/snapshot/timestamp; database changes clear the table. Region is trimmed externally, nonempty and contains no whitespace/control characters internally. Derive a new Connection with the explicit region; preserve profile, endpoint, TLS and credentials without writing settings. Reject unknown/non-AWS sources, foreign catalogs and mismatched response refs. This provider supports AwsDataCatalog only; do not imply arbitrary catalog federation.

Keep both full identity/timestamp headers outside the comparison scrolling body. Show not selected/not fetched honestly. Refresh retains its previous snapshot/time with refreshing or failed status; identity changes clear it. With one successful side, show that side and comparison pending, never classify its fields as all added/removed.

Keyboard: Tab/Shift+Tab traversal; Enter/Space picker commit/button; arrows picker/list navigation; Ctrl+1/Ctrl+2 focus Left/Right connection; Ctrl+R refresh most recently focused side (initially Left); Ctrl+D differences-only; modal-priority Ctrl+C copy full summary; Escape closes an open picker before closing the modal. Supply visible refresh/toggle/copy/close buttons. Inputs keep ordinary editing. Integrate the existing app's modal navigation dispatch and restore the underlying focus on close.

Test normal 120x40 and narrow 60x24 layouts with long identities/types. Stack controls at narrow widths, use a scrolling control area and literal wrapped Left/Right result lines. Keep identities fully accessible without truncation or markup parsing. A read-only full-summary viewer provides selection when clipboard transfer is unavailable. Subscription/task cleanup must run on dismissal, unmount and app shutdown.

## 4. Pure comparison

New `domain/table_comparison.py` owns immutable snapshots, typed row values, flags, normalization, comparison and deterministic export. Columns and partition keys are independent sections. Match exact names; Left order then Right-only order. A common item may have multiple type/comment/reordered flags. Compare relative common-name order, so insertion does not mark all existing columns reordered. Keep original one-based positions. Renames are removal plus addition with no compatibility/breaking/migration verdict. Duplicate names mark their section ambiguous/unavailable and preserve positional rows rather than silently overwriting records.

Conservative type normalization removes outer ASCII whitespace and whitespace adjacent to `<>() ,:` punctuation outside single/double/backtick quoted spans. Preserve case, identifier spelling, quote style, quoted content, escapes/doubled quotes and whitespace between nonpunctuation tokens. Malformed quote/delimiter structure returns original text. No aliases, keyword/field case folding or full SQL parser. Document possible conservative textual differences; never claim broad compatibility.

Storage rows have fixed order: location, input_format, output_format, serde, compressed, table_type, table_format. Compare values with their types, not truthiness/stringification; retain exact strings. Render None as `<missing>`, empty string as `""`, bool false/true, enum value explicitly, and a dedicated ABSENT sentinel as `<absent>`. JSON-quote actual strings in export, preventing placeholder/control/newline collisions.

AC3 literally requests None/empty-string pairs for all seven fields. Five fields are nullable strings; compressed is bool and table_format is TableFormat in the provider contract. Preserve those contracts. The comparator's scalar boundary already admits str/bool/TableFormat/None: test all seven literal pairs as deliberately malformed defensive fixtures, using localized casts only in those fixtures, alongside valid false/true and all enum-value tests. Do not claim those malformed fixtures are provider-produced. Existing Glue mapping defaults omitted/nonboolean Compressed to False, so this feature cannot recover missing-versus-false provenance; document and test that unchanged limitation. This satisfies the explicit comparison/test requirement without relaxing it or fabricating provider metadata.

Compare only parameter key presence using sorted key union. Present values always show `<unavailable: redacted>`; present-both is unavailable, never equal/unchanged, even when both strings are `[REDACTED]`. Unexpected raw values also never enter output. Differences-only retains unavailable rows. Do not report unconditional full-table equality when values are unavailable.

Export labels both five-part identities, successful UTC times, fixed direction, normalization policy, all rows and unavailable values. No export-time timestamp, random ID, focus or terminal size. Filtering never changes the copied full summary. The VM adds deterministic freshness labels. Copy requires two snapshots, captures one immutable text before App.copy_value(text, "Glue comparison"), and performs no provider operation.

## 5. Routing, lifecycle and pagination

`vm/glue/comparison_ports.py` defines small read-only client/router protocols and ResolvedSource; `services/glue/comparison.py` implements live configured-source routing. Keep effective Connection/client privately repr-hidden. Re-resolve the full effective connection before accepting completion, including profile/endpoint/TLS/credentials privately, without comparing freshly constructed client identities. Replaced/removed routes cannot publish; refresh resolves anew. GlueService builds the independent comparison VM using the existing Glue factory and a fresh supported-connection supplier, never its Athena factory.

`vm/glue/comparison_vm.py` has two independent side records, revisions, GlueOperationOwners, snapshots, errors and selector pagers. Every selection/refresh/close invalidates synchronously before deferred work. Guard before provider work, after every await and before publishing success or failure, including ABA, same-ref refresh and cancellation-resistant responses. Validate returned ref equals requested ref. Timestamp only current successful completion. Safe provider/error mapping is side-local; credentials use existing AUTH_REQUIRED guidance without altering recovery transactions.

Manual database/table pagination retains exact refs, deduplicates, never auto-selects, caps 1,000 items and 64 page requests, rejects repeated continuation tokens and stops after three consecutive empty continuing pages with an honest partial-limit state. Prevent overlapping More. Check source/database ownership for list results. Closing rejects new work, invalidates both and drains both owners; no notification or stale error after closure.

## 6. Acceptance evidence

| AC | Required evidence |
|---|---|
| 1 | Real AwsTuiApp Pilot, both unset, explicit cross-source/region selection, pin each side, full refs and injected timestamps rendered |
| 2 | Pure add/remove/type/comment/multiple flags, pure reorder, insertion-not-reorder, partition reorder and duplicate ambiguity fixtures |
| 3 | Each of seven literal None/empty defensive fixtures, five valid nullable pairs, valid bool/enums, provider default-False provenance limit |
| 4 | Nested struct/array/map/decimal spacing, preserved case/quoted spans/escapes/malformed text, rename add+remove and no verdict |
| 5 | Redacted-both unavailable, key union only, unexpected secret values absent from rendering/export, unavailable survives filter |
| 6 | Event-gated independent success/errors/refresh, stale successes/errors, ABA/same-ref, replaced/removed routes, queued supersession, close/drain |
| 7 | Real app keyboard/pickers, side refresh, differences-only, deterministic App clipboard bytes and narrow containment/focus restoration |
| 8 | Recording SDK read allowlist and denied unexpected methods, zero writes/Athena/partition/statistic calls, including mounted copy/refresh paths |

## 7. Delivery

Four sequential reviewed deliverables: pure semantics, routing ports/service, independent VM, modal/app integration plus docs. Meaningful RED then GREEN for each. Root performs one final whole-branch review and applicable local gates before normal protected delivery, promotion, source parity, postchecks and owned cleanup. Leave #283 until #258's full cycle finishes; its real Windows promotion evidence requirement remains unwaived under the no-Actions policy.
