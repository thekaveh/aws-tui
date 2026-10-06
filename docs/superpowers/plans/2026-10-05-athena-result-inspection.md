# Athena loaded-result inspection implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task. Follow TDD and independent task/whole-branch review.

**Goal:** Fulfil all eleven #253 ACs with local-only cell inspection/copy/filter/sort over loaded Athena rows.
**Architecture:** VM-owned pure index projection, ordinal selection and generation fencing; current pager is authoritative. Existing app clipboard and command/keymap routing remain the integration seams.
**Tech Stack:** Existing Python 3.11 floor, Textual, vmx, pytest/snapshot tooling. No new dependency.

## 1. Global Constraints

The design's Binding behavior section is binding verbatim for every task. In particular: fetched rows only; no execution/automatic fetch; original string/null values; 10,000-row existing ceiling; duplicate column ordinal identity; Unicode string sort with null last both directions; stable ties; literal casefold filter; JSON copy from originals; transient state excluded from Snapshot; retired generation/page/focus guards; 80×24/120×40 keyboard usability; no payload logs; no hosted CI or protection bypass.

## 2. Environment and evidence

Work in /Users/kaveh/repos/aws-tui on codex/issue-253-athena-result-inspection. Existing .venv Python 3.12.9 and locked dependencies only. Set PATH to .venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin; TMPDIR=/private/tmp; UV_CACHE_DIR=/tmp/aws-tui-253-uv-cache; UV_NO_SYNC=1; UV_OFFLINE=1; DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib. Use .venv/bin/python -m pytest for local tests. Preserve raw command/exit/output under .superpowers/sdd/2026-10-05-athena-result-inspection, report paths named task-N-report.md. Keep original failed logs; no evidence overwrite. Git/network writes may need scoped escalation, not hook bypass. Normal commits with hooks.

### 2.1. Task 1: Pure projection and VM lifecycle

**Files:** Create src/aws_tui/vm/athena/result_projection.py, tests/unit/vm/athena/test_result_projection.py; modify src/aws_tui/vm/athena/results_vm.py, tests/unit/vm/athena/test_results_vm.py. Preserve original Snapshot structures and assertions.

**Interfaces to produce:**
```python
ResultRow = tuple[str | None, ...]
SortDirection = Literal["ascending", "descending"]
def project_row_indices(rows: tuple[ResultRow, ...], filter_text: str = "", sort_column: int | None = None, sort_direction: SortDirection = "ascending") -> tuple[int, ...]: ...
def serialize_cell(value: str | None) -> str: ...
def serialize_row(row: ResultRow) -> str: ...
# AthenaResultsVM read-only properties:
# filter_text:str; sort_column:int|None; sort_direction:SortDirection;
# visible_row_indices:tuple[int,...]; visible_rows:tuple[ResultRow,...];
# selection:tuple[int,int]|None (original loaded row, column);
# projection_generation:int (the current existing generation);
# selected_cell:str|None; selected_row:ResultRow|None.
# Methods:
# set_filter(text:str)->None
# set_sort(column:int|None, direction:SortDirection="ascending")->bool
# select_cell(visible_row:int,column:int,*,generation:int|None=None)->bool
# reset_projection()->None
```

- [ ] Write genuine failing pure tests before implementation, with this exact sorting/serialization oracle:
```python
rows = (("9007199254740993",), (None,), ("9007199254740992",), ("",), ("10",), ("2",))
assert project_row_indices(rows, sort_column=0) == (3, 4, 5, 2, 0, 1)
assert project_row_indices(rows, sort_column=0, sort_direction="descending") == (0, 2, 5, 4, 3, 1)
assert project_row_indices(rows) == tuple(range(6))
assert serialize_cell(None) == "null"
assert serialize_cell("") == '""'
assert serialize_row((None, "", "NULL")) == '[null,"","NULL"]'
```
Add literal/casefold matches, null-search, stable ties, duplicate-column ordinal sort, newline/Unicode serialization and rows byte/value preservation. Run `.venv/bin/python -m pytest tests/unit/vm/athena/test_result_projection.py -q` and retain expected preimplementation failure.

- [ ] Implement pure helpers with this code pattern (format/type-check normally):
```python
needle = filter_text.casefold()
indices = [i for i, row in enumerate(rows)
           if not needle or any(needle in ("null" if v is None else v.casefold()) for v in row)]
if sort_column is not None:
    values = [i for i in indices if rows[i][sort_column] is not None]
    nulls = [i for i in indices if rows[i][sort_column] is None]
    values.sort(key=lambda i: rows[i][sort_column], reverse=sort_direction == "descending")
    indices = values + nulls
return tuple(indices)
# serializers each use json.dumps(value, ensure_ascii=False, separators=(",", ":"))
```
Maintain typed non-null sort key without str()/numeric coercion. Typed helpers do not log payloads.

- [ ] Write VM failing regressions using existing ResultClient/make_results_vm. Seed first page with duplicate columns, select original row/column, filter/sort; compare rows and `export_snapshot().next_token`/has_more before/after. Explicit load_more enters projection without extra calls. Filter hiding selection clears it; sort and page append preserve selected original ordinal; invalid/empty coordinates and stale generation reject selection.
- [ ] Add transient fields initialized before worker creation. Read-only visible rows derive solely from pure indices and original rows. `select_cell` bounds-checks both ordinals/current generation and maps visible ordinal to original ordinal. `selected_cell/selected_row` read originals only when selection valid. No selection value represented/logged.
- [ ] Set filter/sort/reset notify value-free property names for projection and selection. Preserve surviving original selection; clear hidden/out-of-bounds one. Reset both filter and sort, not pager. Sort None restores load order; invalid column returns False without mutation. Current direction property resets to ascending when sort is None.
- [ ] Retire projection state in `_replace_worker` (all initial load/snapshot/clear/shutdown/dispose paths), before notifications. On changed `set_context`, clear current execution/pager/transient state and retire old worker before installing new context; unchanged context is a no-op. Retain `_is_current` fences. New pages do not replace/reset projection state.
- [ ] Write barrier regression in existing results VM test file: old second page ignores cancellation; start old load_more, wait exact fetch_started, change execution/context, assert transient reset, release original fetch, await task, assert retired values never appear. Test same-context preservation, restore_snapshot reset and no extra request. Preserve existing no-execution/privacy/Snapshot tests.
- [ ] Run focused GREEN then `.venv/bin/python -m pytest tests/unit/vm/athena/test_result_projection.py tests/unit/vm/athena/test_results_vm.py tests/unit/vm/athena/test_query_vm.py tests/unit/vm/athena/test_page_vm.py -q`. Run Ruff/format and normal hooks; self-review and commit. Report genuine RED/GREEN/covering commands, exits, log paths, file scope and concerns. Do not run the full repository suite for this task; the controller owns final gate.

### 2.2. Task 2: Cell inspector, filter dialog and configured commands

**Files:** Modify src/aws_tui/ui/widgets/athena/results_view.py, src/aws_tui/app.py, src/aws_tui/infra/keymap_store.py, src/aws_tui/ui/widgets/help_modal.py; create src/aws_tui/ui/widgets/athena/result_cell_modal.py and result_filter_modal.py; extend tests/unit/ui/athena/test_page.py and relevant keymap/help/command tests. Change page.py only if required for current lifecycle routing; no new persistent focus target/enum.

**Consumes:** Task 1 exact VM APIs and serializers. AthenaPage receives the current page VM; App.copy_value(value:str,label:str) owns asynchronous clipboard/status handling. Existing configured keymap registry/palette/service routing and modal focus guards are patterns to reuse.
**Produces:** AthenaResultsView methods `action_inspect_cell`, `action_copy_cell`, `action_copy_row`, `action_filter_results`, `action_sort_results`, `action_reset_results`; app registers the six design action IDs and routes them to current results view only. Inspector/filter modal results have explicit apply/cancel semantics.

- [ ] Before coding, pilot RED in test_page.py: table cursor_type is cell; choose second row/column, open inspector, verify full literal multiline value, Escape, exact original coordinate restored. Add original-copy seam and duplicate-name pilots. Use actual VM calls for data, not table-rendered fixtures as copy authority. Local fake client forbids query starts and unrequested pages.
- [ ] Change DataTable cursor_type to cell. Use original row ordinal as table row key and existing indexed column keys. Build table projection from VM visible_row_indices/visible_rows; render original None as NULL and empty as existing `""` via Rich Text, not markup. Refresh fingerprint includes generation, columns, visible ordinals and rows; rebuild only when it changes. Record table generation before rendering. CellHighlighted handles only live table/current stamped generation, calls VM.select_cell; queued rebuild highlights must not replace a deliberate restored current cell. Preserve valid original cell coordinates across projection refresh, and VM selection None must not become fabricated solely by an empty/default coordinate.
- [ ] Footer code reads only counts/flags, with exact visible/loaded information:
```python
suffix = " · safety limit" if vm.limit_reached else " · more available" if vm.has_more else ""
footer.update(f"{len(vm.visible_rows)} visible / {len(vm.rows)} loaded · local{suffix}")
```
Keep current Load more sync/state/error/busy behavior; local actions never call load_more. Validate refresh against partial unmount and current screen before updating.
- [ ] Inspector uses ModalScreen, read-only scrollable TextArea, literal metadata/explicit null-or-empty status, Close button and Escape. Hold no repr-visible payload fields. Capture VM projection_generation plus original selection; close callback restores original cell only if view mounted/current screen/generation/row remains visible. On execution/context retirement stale overlay invalidates/closes and must not copy retired data. No editable newline handling or query action escapes modal; keyboard arrows/scroll/Tab/ShiftTab work at both sizes.
- [ ] Filter modal uses Input plus Apply/Clear/Cancel buttons and Escape. Enter applies only when Input owns focus; Cancel/escape preserves old text; Clear returns empty filter. Callback checks current mounted view/generation before applying. Copy/inspect/sort use current VM selection with bounds guards. Copy calls App.copy_value with serialize_cell(vm.selected_cell) or serialize_row(vm.selected_row) and fixed labels; null-cell selection is distinguished through vm.selection, not selected_cell is None.
- [ ] Sort cycle uses selected column ordinal: no/current-other sort → ascending; same ascending → descending; same descending → None. Explicit reset_results clears filter and sort. No action assumes labels unique.
- [ ] Register action IDs/configurable default keys exactly as design and relevant Athena-only palette entries. Reuse current command routing rather than parallel key parser. Guard editable overlays/editor and other services. Help uses configured key rows with loaded-only description; remapped keys must be reflected. Preserve all existing defaults and command scopes.
- [ ] GREEN pilots cover original clipboard strings/null/empty/literal NULL/duplicate labels, filter zero-match has_more and ceiling footer, active filter/sort with explicit second page, exact inspector coordinate, stale callback/modal/view retirement, nonnumeric strings and no network actions. Run `.venv/bin/python -m pytest tests/unit/ui/athena/test_page.py tests/integration/test_modal_key_containment.py tests/unit/infra/test_keymap_store.py tests/integration/test_command_palette_wiring.py tests/integration/test_clipboard_reporting.py -q -m ''`. Do not broaden to entire repo. Normal hooks/self-review/commit and raw evidence report.

### 2.3. Task 3: Actual app acceptance, snapshots, documentation and interactive pass

**Files:** Extend tests/snapshot/test_athena.py and its fixture as needed; new tests/integration/test_athena_result_inspection.py; docs/services/athena.md, docs/keybindings.md, docs/cookbook.md. Reuse current actual DemoModeApp and existing UI helpers. No provider execution change.
**Consumes:** Current six configured actions and UI/VM contracts; all eleven intake ACs.

- [ ] Add actual full-app keyboard journeys at 80×24 and120×40, starting in demo results loaded once by explicit user query/action or seeded current fake state. Thereafter start_query_execution and unrequested pages fail. Use named wait_until for live rendered/focused/worker conditions, preserve original assertions/timeouts.
- [ ] Exercise exact second-row/second-column inspector return, multiline/full literal copy, duplicate labels, null/empty/original string JSON, ascending/descending/reset, zero matches with more available, explicit second page enters current projection, context/execution retire late page/overlay. Assert both coordinates and raw App.copy_value payloads. Traverse relevant help and remapped actions in actual app; no modal/editor/global service key regression.
- [ ] Add Carbon/GitHub Light compact/wide full-app inspector/filter/result snapshots with content-presence guards for full values, loaded-only counts and footer/controls. Existing result goldens changed only where cell cursor or truthful projection footer visibly changes. Generate before/after SVGs for individually reviewed changes; all unrelated goldens byte-identical. Do not use blanket snapshot acceptance or treat missing content as intentional.
- [ ] Perform an actual interactive demo TTY pass using the live program, keyboard controls and both relevant modals. Preserve command, keys, observed outcomes and terminal evidence; distinguish interactive observations from automated pilots and do not claim native clipboard delivery unless tested. If environment prevents that required evidence, report blocker rather than substitute a scripted pilot or invent success.
- [ ] Document JSON copy encoding, literal loaded-only filter, lexical/null-last/stable string sort, reset and configured keys; explicit Load more and ceiling warnings; source/execution reset; native clipboard limitations. Explain no full-result/server-side operation; link #254 as separate scope. Generate site/wiki locally and strict docs with existing Cairo env; no remote publication.
- [ ] Focused feature tests and current Athena snapshot tests, all-file hooks as appropriate, self-review and normal commit. Report AC-to-test mapping, actual interactive evidence, visual review artifacts, unchanged/changed golden hashes and limitations. Whole repo full coverage/build gates remain controller-owned after independent whole-branch review.


## 3. Task 1 atomic snapshot integration amendment

Covering tests retained as task-1-covering.log caught premature changed-context notifications during QueryVM._install_snapshot and the original <=64-message bounded-publication regression. Original assertions and notification bound remain binding. Add src/aws_tui/vm/athena/query_vm.py to Task1 files for its minimal existing internal snapshot caller only. ResultsVM._install_snapshot accepts optional prepared context, installs it within validated atomic snapshot installation, and retires/reset state without premature publication; QueryVM passes prepared.context into that installation instead of invoking the public changed-context set_context clear. Public changed-context set_context still clears/retire old execution normally. Snapshot dataclasses, fields and export formats remain unchanged. Preserve existing snapshot notifications and emit exactly visible_rows and selection as the two new coherent snapshot dependencies; original rows notification exposes coherent filter/sort/generation reset to current UI subscribers. Interactive projection setters continue their full value-free projection notifications. This is an ordinary integration scope amendment under standing unsupervised authority, with no AC/assertion/timeout waiver. Retain original failure evidence; amended-source covering gets a new filename. Stage the amended design and plan with the coherent implementation commit.

## 4. Controller review and protected delivery

Record each task base before dispatch; fresh implementer and scoped independent reviewer consume brief/report/unique BASE..HEAD package. Resolve Critical/Important findings and retain Minor roll-up. Check evidence rather than rerunning unchanged task tests. One final broad whole-branch review receives all eleven current ACs/spec, full original-base package and evidence/Minor list. Then one complete applicable local gate with all-file hooks, full coverage, minimum syntax, original Snapshot/golden preservation, local docs, build/package/wheel smoke; audit only if inputs changed/new concern. Failures are diagnosed with retained evidence, not rerun until green.

Only after gate success: normal push, feature PR to develop (Refs #253, no premature closes), protected merge then attached promotion PR to main/protected merge, fresh exact source parity and selected postchecks. Delete only owned merged feature refs; issue conclusion/CLOSED/boardDone; verified archive and exact scratch cleanup. Then begin #245.

## 5. Task3 Load-more physical-alias integration amendment

Actual full-app diagnostic task-3-paging-diagnostic-2.log found the configured default l binding materialized Glue before Athena: with Athena active and its live result table focused, l recorded glue.load_more and did not request the explicit second page despite has_more=True. Preserve this failure and assert actual RootVM service/table focus in the regression. Add src/aws_tui/app.py and src/aws_tui/ui/bindings.py to Task3 scope for a narrow existing dispatch/resolver amendment; tests/unit/ui/test_bindings.py and tests/integration/test_keybinding_wiring.py are permitted named covering extensions as needed.

Resolver carries the normalized physical key for glue.load_more and athena.load_more. Existing action_dispatch contextually routes that pair to the current service only when the pressed key actually overlaps according to _bindings_overlap(key=...). Independently remapped exclusive keys and explicitly named registry/palette actions retain their existing meaning. Preserve modal/editor containment, Glue paging, Athena explicit paging and all defaults. No broad shared-action redesign or handler fallback converting explicitly named Glue operations into Athena calls. The six new inspection/copy/filter/sort/reset actions still never fetch or start a query.

Require actual Athena second-page request/projection at both supported sizes; cover Glue, overlapping/remapped/exclusive keys and named-action/containment contracts with focused tests. Retain genuine RED and fresh changed-source covering command/exits. A coherent local fix commit with normal hooks may precede the final Task3 acceptance/docs commit. Existing assertions/timeouts and all eleven ACs remain binding; this is an authorized routine integration scope amendment, not a requirement waiver.

## 6. Task3 slow keyboard paging lifecycle amendment

Valid-precondition original-awaiting-branch RED task-3-slow-key-valid-red.log exits1 (2failed/16deselected,32.12s): bothsizes explicitly select/assert original cell(1,1), active Athena/live table focus and a started gated second request, but subsequent Alt+Enter cannot open the valid inspector within the unchanged15s wait; releasing the fetch lets both Pilot key tasks finish. After restoring the approved sharedworker path, focusedGREEN passes6cases/12deselected in7.92s, covering responsive inspection and literal-key retirement. The initial task-3-slow-key-red.log lacked selection and is retained as superseded, insufficient causal evidence; the separately terminated incomplete standalone setup probe is also not responsiveness evidence.

Add src/aws_tui/ui/widgets/athena/page.py and results_view.py plus tests/unit/ui/athena/test_page.py as needed to Task3's narrow integration scope. Reuse the existing results-view-owned lifecycle worker used by the Load-more button, factoring its explicit paging trigger into one shared method if needed. Only Page's active-results keyboard paging branch invokes that path without awaiting provider I/O in the key handler. Preserve other pager/view branches, VM busy/error/token/generation fences, current-screen guards, Load-more button behavior and view-owned worker disposal; no additional global worker or implicit request. The six local result controls still never fetch/start a query.

Preserve the genuine gated l→Alt+Enter readiness oracle and unchanged timeout. Require responsive inspector with exactly the authorized second request completing, and literal-key late-page execution/context retirement evidence. Any existing UI assertion whose worker completion becomes explicitly asynchronous must retain its original behavioral assertion and use a named actual readiness barrier. Keep every failed observation/terminal result, cover changed files, normal hooks and coherent local commit. No acceptance waiver or broad paging redesign.

Evidence correction: the initial slow-key inspector timeout lacked an explicitly selected cell and did not by itself establish key-processing causality. Retain it as a superseded probe. The strengthened original-awaiting-branch baseline task-3-slow-key-valid-red.log selects and asserts original cell(1,1), active Athena/live table focus and a started gated second request; bothsizes still fail the unchanged inspector-before-release wait (session46281 terminalEXIT1,32.12s). That stronger valid-precondition RED is authoritative. Restore the approved sharedworker path only after the baseline terminal result and require its fresh focusedGREEN; retain complete handles/source boundary and all prior observations.

## 7. Final whole-branch review fixes

Final review at bc4965c0 found two concrete Important gaps and one actionable Minor: explicit CellSelected at the current coordinate/one-cell result is not admitted; plain filter Input emits private values in Changed/Submitted/Blurred messages before Textual debug logging; repeated Load more starts an exclusive replacement worker while a page is in flight. Add scoped result-view/filter-modal and additive UI/actual-App regressions to repair all three in one final-fix assignment. Explicit user selection shares current generation/revision/key/liveness fences and never fabricates rebuild/empty selection. Filter events are value-free before posting while the Input keeps authoritative text for Apply. Current paging refuses already-busy explicit requests while retaining the existing view-owned responsive worker and retirement. Preserve original assertions, timeouts, goldens, paging token/ceiling and all eleven ACs. Retain genuine RED/GREEN and named covering evidence; normal hooks/coherent commit; focused final re-review before the one full local gate. Existing Material/plugin advisories remain a documented dependency/tooling follow-up without suppression or upgrade.

Clarify the existing Athena service instructions and cookbook loaded-result recipe with Enter/click selecting the current first or sole cell, in addition to arrow movement. These two scoped user-doc edits make the repaired singleton behavior discoverable; final local documentation generation/strict gate still applies.
