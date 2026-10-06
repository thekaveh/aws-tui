# Glue comparison implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare two explicitly selected Glue table definitions across configured environments, read-only, with isolated refresh and honest deterministic export.

**Architecture:** A transient modal consumes an independent two-side VM. A small read-only service router binds each explicit source/region, while pure domain functions compare and format immutable snapshots. Existing GluePageVM and recovery contracts stay intact.

**Tech Stack:** Existing Python 3.11+, Textual, VMx, boto3 Glue adapter, pytest/Pilot; no new dependency.

**Spec:** `docs/superpowers/specs/2026-10-06-glue-comparison-design.md`

## 1. Global Constraints

- Hosted GitHub Actions and hosted gates remain disabled. Run applicable checks locally only.
- No live AWS calls, new dependency, dependency/lock/hook change, recovery-schema change, weakened assertion, expanded skip or blind golden replacement.
- Preserve all 13,892 baseline assertions, 792 goldens and six recovery snapshot classes. Any new visual asset requires an independent review of its actual render.
- Both selected TableRefs start unset. Only explicit picker commitment or an explicit Pin open table button selects a side.
- Every snapshot includes the exact five-field TableRef and an aware UTC timestamp sampled after its own successful current comparison fetch.
- Missing, empty, false, an absent item and redacted/unavailable are distinct states. Parameter values are never inspected for equality.
- Cancellation is an optimization; synchronous revision and route validation determine ownership. Left and Right never cancel or erase each other's work.
- Only database/table listing and get_table are allowed comparison provider operations. No Athena, statistics, partitions, writes, releases or external publication.
- The existing normal protected develop PR then main promotion and owned cleanup/closure sequence remains required.

## 2. Review Focus

- Duplicate column names: preserve positional evidence and mark ambiguity instead of inventing matches (Task 1).
- Quoted type identifiers containing delimiters/escapes: preserve literal contents and case (Task 1).
- Same-name source credentials/endpoint replacement while fetching: reject the old route without exposing credentials (Tasks 2/3).
- Superseded work that suppresses cancellation, including stale failures after ABA: no state/error/clock/notification publication (Task 3).
- Global app key routing and long identity at 60x24: selectors/copy remain reachable with both labels and restored page focus (Task 4).

## 3. Files and interfaces

Create `domain/table_comparison.py` (comparison/export), `vm/glue/comparison_ports.py` (ports), `services/glue/comparison.py` (routing), `vm/glue/comparison_vm.py` (state/ownership), and `ui/widgets/glue/comparison_modal.py` (literal UI). Add corresponding domain/service/VM unit tests and `tests/integration/test_glue_comparison.py`. Modify only the GlueService builder, app action, action catalog, keymap, relevant hints/help and service docs; a composition seam only if existing dependency access requires it. No GluePageVM/recovery changes.

Use .venv Python, TMPDIR=/private/tmp, UV_NO_SYNC=1 UV_OFFLINE=1 and PATH including .venv/bin plus /opt/homebrew/bin. Actor reports/logs live in `.superpowers/sdd/2026-10-06-glue-comparison`. Record actual command exit and source identity; preserve failed attempts. Stage only owned files. Commits run normal hooks; authorized Git metadata/cache/network escalation may be needed. No remote actions by implementation actors; root owns delivery.

### 3.1. Task 1: Pure typed comparison and export

**Files:** Create `src/aws_tui/domain/table_comparison.py`; create `tests/unit/domain/test_table_comparison.py`.

**Interfaces:** Produce `Side = Literal["left", "right"]`; frozen `TableSnapshot(ref: TableRef, detail: TableDetail, fetched_at: datetime)`; `ChangeKind` enum with ADDED, REMOVED, TYPE_CHANGED, COMMENT_CHANGED, REORDERED, VALUE_CHANGED, UNCHANGED, UNAVAILABLE; dedicated `ABSENT` sentinel; frozen `ColumnValue(column: Column, position: int)` and `ParameterPresence(present: bool)`; `Scalar = str | bool | TableFormat | None`; `ComparisonRow(section: Literal["columns", "partition_keys", "storage", "parameters"], key: str, changes: tuple[ChangeKind, ...], left: ColumnValue | ParameterPresence | Scalar | AbsentValue, right: same)`; `TableComparison(rows: tuple[ComparisonRow, ...])` with `visible_rows(differences_only: bool) -> tuple[ComparisonRow, ...]`; `normalize_type(type_name: str) -> str`; `compare_tables(left: TableDetail, right: TableDetail) -> TableComparison`; `format_value(value: ColumnValue | ParameterPresence | Scalar | AbsentValue) -> str`; `export_comparison(left: TableSnapshot, right: TableSnapshot, comparison: TableComparison) -> str`.

- [ ] **Step 1: Write independent failing fixtures.** Build details from current immutable data_catalog records. Exercise exact flag sets/row direction/order; common-name order rather than raw indexes; type and comment changes together; ordinary/partition separation; duplicate names preserved with unavailable status. Include this minimal normalization contract:

```python
def test_nested_type_normalization_preserves_quotes_and_case():
    assert normalize_type('array < struct < id : int > >') == 'array<struct<id:int>>'
    assert normalize_type('struct<`a b`:array<int>>') == 'struct<`a b`:array<int>>'
    assert normalize_type('struct<ID:int>') != normalize_type('struct<id:int>')
    assert normalize_type('ARRAY<int>') != normalize_type('array<int>')
    assert normalize_type('array<struct<id:int>') == 'array<struct<id:int>'
```

Add single/double/backtick, doubled/escaped quote, comma/colon inside quotes, decimal/map nesting and whitespace-between-tokens fixtures. Storage tests independently set each of seven fields None/empty; bool/enum cases use localized test-only casts as deliberately malformed boundary fixtures, without widening production data_catalog contracts. Separately assert valid false/true, all TableFormat enums, and typed value preservation. Parameter-both redacted must be UNAVAILABLE, never UNCHANGED; sorted presence union/differences filter and secret-free summary are mandatory. Distinguish literal placeholder strings from missing/empty via quoted export. Deterministic export asserts both refs/times/all rows and no compatibility verdict.

- [ ] **Step 2: Run RED.** `TMPDIR=/private/tmp .venv/bin/python -m pytest tests/unit/domain/test_table_comparison.py -q`; retain missing-module/function failure before production exists.
- [ ] **Step 3: Implement the specified algorithm.** For common names use their common subsequence indexes; exact names only. For duplicate sections emit each side's positional records tagged UNAVAILABLE. Type comparison uses the conservative balanced lexical scan; raw types remain in ColumnValue. Storage uses type-aware equality and exact order. Keys-only parameters never read values. Use JSON quoting for actual strings, dedicated missing/absent markers, aware UTC ISO timestamps and stable labels/direction/policy. Validate snapshot identity and aware time at the domain boundary.

```python
left_common = tuple(c.name for c in left_columns if c.name in right_names)
right_common = tuple(c.name for c in right_columns if c.name in left_names)
left_rank = {name: index for index, name in enumerate(left_common)}
right_rank = {name: index for index, name in enumerate(right_common)}
# In the nonduplicate branch, name changes order only if these ranks differ.
```

- [ ] **Step 4: Run GREEN** for the new domain tests and existing `tests/unit/domain/test_data_catalog.py` if present; run focused Ruff/type checks on owned production. Preserve all old tests/goldens. Self-review exact AC2-5 and report actual commands/output.
- [ ] **Step 5: Commit** owned module/tests as `feat: compare Glue table definitions without losing metadata states` using normal hooks. Report full evidence in task-1-report.md; return short status/commits/test summary/concerns only.

### 3.2. Task 2: Read-only cross-environment routing

**Files:** Create `src/aws_tui/vm/glue/comparison_ports.py`, `src/aws_tui/services/glue/comparison.py`, `tests/unit/services/glue/test_comparison.py`; modify `src/aws_tui/services/glue/service.py` only to add comparison builder.

**Interfaces:** Consume domain TableSnapshot/Side and future VM constructor contract. Produce `ComparisonCatalogClient` Protocol with async `list_databases_page(*, start_token: str | None = None) -> tuple[list[DatabaseSummary], str | None]`, `list_tables_page(database: str, *, start_token: str | None = None) -> tuple[list[TableSummary], str | None]`, `get_table(ref: TableRef) -> TableDetail`. Produce frozen `ResolvedSource(connection: Connection, client: ComparisonCatalogClient)` with both fields repr-hidden, public connection_name/region, and `ComparisonRouter` Protocol `sources() -> tuple[ServiceSourceContext, ...]`, `resolve(connection_name: str, region: str) -> ResolvedSource`, `is_current(source: ResolvedSource) -> bool`. Router implementation `GlueComparisonRouter(*, connections: Callable[[], Sequence[Connection]], client_factory: Callable[[Connection], ComparisonCatalogClient])`. Produce `GlueService.build_comparison_vm(*, connections: Callable[[], Sequence[Connection]], clock: Callable[[], datetime] | None = None) -> GlueComparisonVM`; builder is finalized in Task 3 when the VM exists, so this task may expose `build_comparison_router` first and document its exact signature for Task 3.

- [ ] **Step 1: Write RED tests** for two configured AWS sources, alternate explicit region preserving profile/endpoint/TLS/credentials privately, invalid region, absent/non-AWS source, live route replacement/removal, mismatched catalog/ref validation and zero Athena factory invocations. Use existing Connection construction with local stub clients, not network.

```python
def test_route_is_invalid_after_same_name_endpoint_replacement():
    original = Connection(name='prod', kind='aws', region='us-east-1', source='config')
    sources = [original]
    router = GlueComparisonRouter(connections=lambda: sources, client_factory=lambda conn: stub_client(conn))
    route = router.resolve('prod', 'us-west-2')
    assert route.connection.region == 'us-west-2'
    sources[:] = [replace(original, endpoint_url='https://changed.example.invalid')]
    assert not router.is_current(route)
```

Define stub_client locally and imports; compare private Connection equality rather than client identity. Recording SDK adapter denies every unexpected method: only get_databases/get_tables/get_table can succeed. Drive valid provider get_table mapping to show absent Compressed remains False; compare it with explicit False without claiming missing provenance. Keep this domain-valid fixture separate from Task 1's malformed boundary fixtures.

- [ ] **Step 2: Run RED** `TMPDIR=/private/tmp .venv/bin/python -m pytest tests/unit/services/glue/test_comparison.py -q`.
- [ ] **Step 3: Implement** live source discovery filtered AWS, exact-name resolution, safe region validation and dataclasses.replace, bound injected Glue factory/default GlueClient, full effective route revalidation with no fresh client instantiation needed for is_current. Expose a narrow client wrapper that validates request refs and returned identities; discovery also cannot publish foreign source/catalog refs. Reject unsupported refs safely without touching the active page. No Athena creation or shared global source swap.

```python
connection = replace(configured, region=region.strip())
# Capture this effective Connection privately with its independently bound client.
# is_current re-resolves configuration and compares effective Connection values.
```

- [ ] **Step 4: Run GREEN** new route tests plus existing GlueService unit tests, normal focused Ruff/type checks. Confirm normal/recovery build behavior stays byte-for-byte outside the small builder addition. Self-review route failures and recorded read allowlist.
- [ ] **Step 5: Commit** ports/router/builder/tests as `feat: route Glue comparisons to explicit connections and regions`. Write task-2-report.md with exact produced signatures.

### 3.3. Task 3: Independent comparison state and operation ownership

**Files:** Create `src/aws_tui/vm/glue/comparison_vm.py`, `tests/unit/vm/glue/test_comparison_vm.py`; finalize GlueService comparison builder in `src/aws_tui/services/glue/service.py`.

**Interfaces:** Consume the exact Task 1 functions/records and Task 2 ports. Produce `GlueComparisonVM(*, router: ComparisonRouter, hub: MessageHub[Message], dispatcher: Dispatcher, clock: Callable[[], datetime] | None = None)`. `side(side: Side) -> ComparisonSideState` exposes connection_name/region, selected_database: DatabaseRef|None, selected_table: TableRef|None, databases/tables tuples, database/table continuation availability, limit/loading state, snapshot: TableSnapshot|None, PaneState/error/status and revision. `sources` exposes configured source contexts; `comparison: TableComparison|None`; `differences_only: bool`; observable `on_property_changed` emits property names only. Synchronous `choose_source(side, connection_name, region) -> int`, `choose_database(side, ref) -> int`, `choose_table(side, ref) -> int`, `refresh(side) -> int`, `pin(side, ref) -> int` invalidate before returning a revision. Async `load_revision(side, revision)`, `load_more_databases(side, revision)`, `load_more_tables(side, revision)` do owned work. `toggle_differences_only()`, `summary_text() -> str|None`, `close()`, async `shutdown()` complete the surface. One owner per side and no Textual import.

- [ ] **Step 1: Write RED event-gated tests.** Fake clients use asyncio.Event to control completion and suppress cancellation deliberately. Assert independent Left refresh with unchanged Right snapshot/error/calls; network/permission failures local; queued superseded revision calls zero providers; stale success AND error after A-to-B-to-A and same-ref refresh do not publish or sample clock. Assert replaced/removed route, mismatched response ref, stale database/table list, and source/database selection clearing. Use injected fixed aware UTC clock counted by invocation. Lifecycle test pattern:

```python
request = asyncio.create_task(vm.load_revision('left', vm.choose_table('left', ref_a)))
await client.started.wait()
vm.choose_table('left', ref_b)
client.release.set()
await request
assert vm.side('left').selected_table == ref_b
assert vm.side('left').snapshot is None
assert clock_calls == []
```

Set up vm/ref/client fixtures locally. Add manual pagination forwarding/dedup, repeated token, 1,000-item/64-page/three-empty limits, no overlapping More and no auto-selection. Close/shutdown tests await cancellation-resistant owned drain and assert no post-close hub or observable notification. Copy tests require both successful snapshots, preserve full export under filter, and label previous snapshot after failed refresh.

- [ ] **Step 2: Run RED** `TMPDIR=/private/tmp .venv/bin/python -m pytest tests/unit/vm/glue/test_comparison_vm.py -q`.
- [ ] **Step 3: Implement** typed side records, synchronous revision increments and captured full-route tokens. Use two existing GlueOperationOwners. Guard before work/after await/before every publication/error; clearing/refresh semantics exactly per spec. If route changes, show only that side's safe source-changed guidance. Provider errors use existing safe mappings and AUTH_REQUIRED. Manual capped pagers guard upstream ref/revision and retain truthful partial status. Sample clock only after successful identity/route ownership validation. summary_text atomically builds domain export plus stable Left/Right freshness labels; filter only affects visible rows. close invalidates both and rejects new work; shutdown drains and disposes safely/idempotently.

```python
if closed or revision != state.revision:
    return
# Capture the current route and selected identity before awaiting.
# Recheck revision, identity and router.is_current(route) after every await.
```

- [ ] **Step 4: Run GREEN** new VM tests plus existing Glue lifecycle and catalog/page tests most directly affected; do not run broad suite during each iteration. Run focused Ruff/type checks and report task-specific races/calls/clock evidence.
- [ ] **Step 5: Commit** VM/builder/tests as `feat: isolate Glue comparison refresh and stale responses`; task-3-report.md supplies exact state properties to Task 4.

### 3.4. Task 4: Mounted comparison UI, application wiring and documentation

**Files:** Create `src/aws_tui/ui/widgets/glue/comparison_modal.py`, `tests/integration/test_glue_comparison.py`; modify `src/aws_tui/app.py`, `src/aws_tui/vm/chrome/action_catalog.py`, `src/aws_tui/infra/keymap_store.py`, applicable hint/help metadata, `docs/services/glue.md` and `docs/keybindings.md`. Update expected hint fixture constants in `tests/unit/vm/chrome/test_hint_legend.py` when needed, preserving every original entry and Assert AST. Minimal composition edit only if needed to access the existing GlueService. No GluePageVM/recovery edits.

**Interfaces:** Consume VM contracts from Task 3 and domain format_value/rows. Produce `GlueComparisonModal(vm: GlueComparisonVM, *, pin_candidate: TableRef | None, copy: Callable[[str, str], None], initial_source: ServiceSourceContext | None = None)`; new app action `action_glue_compare_tables` and catalog/keymap ID `glue.compare_tables` default Ctrl+G. Copy callback is existing App.copy_value; no new clipboard backend. Constructor can use keyword-only vm if existing modal conventions demand it; record the exact interface before tests integrate.

- [ ] **Step 1: Write RED real AwsTuiApp tests** with injected source/Glue factory/clock/clipboard. Open through Ctrl+G, assert both tables unset, explicitly choose different source/region/database/table via ContextPicker keyboard, pin each side, assert full five-field ref and actual injected timestamps visible. Verify source change clears old metadata/time. No tiny widget harness substitutes for app routing. Include clipboard test:

```python
await pilot.press('ctrl+d')
await pilot.press('ctrl+c')
await wait_until(lambda: len(clipboard_calls) == 1, what='Glue comparison copied')
filtered_copy = clipboard_calls[0]
await pilot.press('ctrl+d')
await pilot.press('ctrl+c')
await wait_until(lambda: len(clipboard_calls) == 2, what='full comparison copied again')
assert clipboard_calls[1] == filtered_copy
assert 'Left' in filtered_copy and 'Right' in filtered_copy
```

Use the existing wait_until import/helper and recording clipboard seam, not actual OS clipboard. Test picker Escape first; Ctrl+1/2, Tab/Shift+Tab/arrows/Enter, focused-side Ctrl+R and visible fallback buttons. At 120x40 and 60x24 long identities/types stay literal, both headers visible/accessible, controls contained/reachable and focus restored. App shutdown/close during cancellation-resistant request must drain without stale UI/error. Recording Glue SDK and Athena deny-stub assert exact comparison reads and zero writes/queries/partition/statistics for selection, pin, refresh, failures, toggle and copy; isolate preexisting background page calls in a recording epoch without hiding comparison calls. Tests are in-process default tier, not external pytest.mark.integration.

- [ ] **Step 2: Run RED** `TMPDIR=/private/tmp .venv/bin/python -m pytest tests/integration/test_glue_comparison.py -q`.
- [ ] **Step 3: Implement** local ContextPickers/region Apply/More/Refresh, immutable pin capture, literal wrapped headers/results, independent deferred groups, live VM subscriptions and lifecycle cleanup. Implement existing app modal navigation methods so actual global keys delegate correctly. Use Ctrl+C priority binding, Copy full summary, read-only full-summary viewer, differences-only label and disabled incomplete copy guidance. The app filters fresh configured AWS connections and calls the comparison builder; do not select/swap the underlying page's source. Responsive narrow layout stacks selectors in scrolling controls while preserving headers outside scrolling result body. Export copies captured full text and no providers. Update action catalog/availability/keymap/hints consistently.

```python
text = vm.summary_text()
if text is not None:
    copy(text, 'Glue comparison')
# Capture text before dispatching clipboard work; filter never edits export.
```

- [ ] **Step 4: Document and run GREEN.** In Glue service guide explain explicit sides/pin/fetch times, configured source and manual region, fixed default catalog, Left-to-Right direction, exact names/reordering, conservative whitespace normalization/case/quotes, unavailable parameter values and provider default-False provenance, Ctrl+G and modal keys, independent refresh, differences-only and full-summary copy/export. Include Ctrl+G/glue.compare_tables in the complete keybinding inventory and add independent expected hint entries without weakening existing assertions. Synchronize applicable generated docs locally through existing build_docs/check_docs tools; no site/wiki publication. Run new domain/router/VM/Pilot tests plus existing Glue page/routing/Iceberg/credential/clipboard/actions/keymap regressions justified by wiring. Run docs checker and focused Ruff/type checks. Independently inspect actual normal/narrow renders; if adding goldens, preserve all original ones and report assets/hashes for root's visual review.
- [ ] **Step 5: Commit** UI/app/tests/docs as `feat: compare Glue tables from the keyboard`; task-4-report.md maps every AC to named passing tests and records limitations without waiver. Root then runs final review and local lint/types/layers/docs/security/build/package/default coverage gates on exact final source, delivers protected PRs, verifies parity/postchecks, closes and cleans only after promotion.
