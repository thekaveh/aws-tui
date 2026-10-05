# Durable Transfer History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete all nine ACs of #249: bounded durable history, explicit safe interrupted-work inspection and revalidated user-requested copy retries.

**Architecture:** Versioned immutable safe summaries and diagnostic descriptors; off-loop serialized disk operations; retained endpoint binding identity; an explicit history/recovery VM and overlay-launched modal. No auto replay or inferred success.

**Tech Stack:** Python >=3.11,<3.14, Textual8.2.8, VMx, asyncio.to_thread, existing JSON/fsync/atomic patterns and synthetic SDK/provider pilots. No new dependency.

**Spec:** docs/superpowers/specs/2026-10-04-durable-transfer-history-design.md

## Global Constraints

- All nine current #249 ACs bind; owner's standing unsupervised delivery authorizes routine design and execution without intermediate approval. Scope ends with protected develop/main PRs, local postchecks/parity, cleanup/closure/boardDone.
- Applicable local checks only; Actions stay disabled; no live AWS mutation or remote release/package/docs publication. Python >=3.11,<3.14 and existing dependencies/gates/coverage floor/goldens remain.
- Default history retention is 100 newest summaries, ordered by updated UTC timestamp then id. Unknown and zero/empty values remain distinct. Atomic writes/private permissions/strict bounded parsing; corrupt, truncated, legacy, missing and unreadable files are skipped safely.
- Never persist credentials/raw exceptions/raw endpoints/client objects/multipart IDs in summaries; original connection routing fingerprints exclude rotating credentials but include endpoint/profile/region/source/transport choices. Paths render literally. Only validated owned metadata is cleared; never source/destination bytes or unowned artifacts.
- Writes, trimming, scan and clear run off the event loop in owned workers. Hub/VM mutation stays on the event loop. Begin and attempt durability precede transfer mutation; terminal persistence precedes batch settlement. Drain started disk operations on cancellation/shutdown.
- Bound connection identity follows the provider atomically through build/swap/recovery. Missing/changed identity refuses retry. Demo never creates real AWS providers.
- Recovery distinguishes never_attempted, possibly_published and confirmed_terminal with outcome_unknown preserved. Neither journal existence nor destination existence/size proves success. Terminal summaries dominate duplicate interrupted journals.
- User-requested retry only, copy only, freshly resolve both original endpoints, stat both ends, ask fresh conflict decision, re-resolve/stat after decision and invalidate changed intent. Moves/deletes/completed/remaining ambiguous publications refuse retry. Recheck may prove destination absent for a new retry but never infer prior success. Each retry gets a new id and normal progress/cancel behavior. No automatic/offset/multipart resume or destructive artifact cleanup.
- UI exposes History and Recovery from Transfers overlay and Ctrl+T/palette, including empty/expired overlay; literal full details, safe loading/error states, keyboard accessibility/modal containment/focus restoration, active progress/cancel unchanged.
- Every worker must not spawn subagents. Controller owns reviewers and delivery. No root product-code fixes. Tests reproduce failures before code, meaningful assertions, bounded named readiness waits, no assertion/timeout weakening or unrelated refactor.

## Review Focus

- Crash between summary persist and journal purge: terminal dominates unknown (Task1 duplicate test).
- Secret-bearing endpoint/error and bracketed/spaced filenames: summaries never contain seeded credentials and UI is literal (Tasks1/3 seeded fixtures).
- Registry changes while confirmation is open: abort before mutation after fresh identity/stat check (Task2 barrier test).
- Cancellation during disk durability: drain write and preserve accurate outcome without late updates (Tasks2/3 barrier test).
- Legacy/symlink/unreadable files mixed with valid records: skip without traversal or dropping healthy data; clear preserves unowned bytes (Task1 fixture test).

### Task 1: Durable summary store and versioned journal descriptors

**Files:** Create src/aws_tui/domain/transfer_history.py; modify src/aws_tui/domain/transfer_journal.py; tests/unit/domain/test_transfer_history.py and test_transfer_journal.py.

**Interfaces:** Produce frozen TransferConnectionIdentity(kind:str,name:str,fingerprint:str) and TransferHistoryRecord; explicit fields id, operation, source_connection, destination_connection, source_uri, destination_uri, started_at, updated_at, finished_at, bytes_done, bytes_total, status, publication, failure_reason. Produce TransferHistoryStore(base_dir:Path, retention_limit:int=100) with save(record)->None, load()->tuple[TransferHistoryRecord,...], clear()->None. Journal accepts optional history_store constructor argument, optional safe descriptor in begin, mark_attempted(id), durable terminal recording with explicit status/fixed failure_reason/bytes, and load_history()->tuple[TransferHistoryRecord,...] merging safe current-schema interrupted descriptors with summaries. Existing begin/replay/mark APIs stay usable by old callers; legacy diagnostic replay stays compatible, while new recovery skips descriptors without identity/schema. Domain imports no infra/VM/UI. Choose concrete Literal/enums and validation locally, record deviations in report before controller rulings.

- [ ] Step1: Add meaningful failing tests. Example retention core (fixture helper builds all required fields):
```python
def test_newest_records_survive_reload(tmp_path):
    store = TransferHistoryStore(base_dir=tmp_path, retention_limit=2)
    for record in records_in_time_order(3):
        store.save(record)
    reloaded = TransferHistoryStore(base_dir=tmp_path, retention_limit=2).load()
    assert [r.id for r in reloaded] == [record_ids[2], record_ids[1]]
```
Also parameterize completed/skipped/failed/cancelled restart; all fields/date/zero distinctions; seeded secret credentials/unsafe exception/endpoint absent; atomic failed write preserves previous valid record; healthy/corrupt/truncated/legacy/unreadable/missing/oversized/symlink fixtures; clear preserves unrelated file and destination/source fixtures; begin-only/attempted/terminal categories and terminal-summary+unpurged-journal precedence. Keep existing legacy replay tests.
- [ ] Step2: Run `.venv/bin/python -m pytest tests/unit/domain/test_transfer_history.py -q --no-cov` and retain real RED output under task report/log.
- [ ] Step3: Implement validated JSON encode/decode and atomic per-id storage, private permissions, stable trim and serial lock; versioned safe descriptor/attempt/terminal journal path. Use canonical16hex id validation, strict timestamp/status/connection/bytes checks and no arbitrary filesystem traversal. Persist terminal before purge. No provider mutation or cleanup operation enters store.
- [ ] Step4: Run both domain modules and lint/mypy/import boundary hooks covering touched code; inspect terminal evidence once. Report exact results and caveats, not live-AWS claims.
- [ ] Step5: Self-review all Task1 cases then commit normal scoped files. Full report persists at task-1-report.md. Return status/commit/testsummary/concerns only.

### Task 2: Worker-safe transfer lifecycle and endpoint-bound recovery VM

**Files:** Create src/aws_tui/vm/file_manager/transfer_history_vm.py and bounded helper modules if needed; modify dual_pane_vm.py, pane_vm.py, services/s3/service.py, composition.py and app.py endpoint build/swap/recovery callsites only; relevant VM/service/composition tests and new test_transfer_history_vm.py.

**Interfaces:** Consume Task1 store/record/journal APIs. Produce connection_history_identity(connection, provider)->TransferConnectionIdentity in VM/infra-compatible boundary (hash existing connection_binding_identity without raw endpoint persistence). PaneVM carries optional bound transfer_connection identity through constructor/swap_provider/staged provider recovery. Produce TransferHistoryVM(journal, resolve_endpoint, hub, dispatcher) with records/readable load state; async load(), clear(), recheck(id), retry(id, decide_conflict), shutdown(); notifications on hub must omit record values. resolve_endpoint(identity) returns async fresh provider plus matching original identity or safe refusal; local identity uses fresh LocalFS. Define immutable inspection/retry plans in this module for Task3 callback consumption. Register AppContext.transfer_history_vm, compose it with actual resolver/provider factory and expose it to UI. Retry fresh copy id uses same durable transfer execution/cancel-progress path as ordinary copies, while preserving existing session lists. Optional helper extraction only if directly needed, no whole-app restructure.

- [ ] Step1: Reproduce absent lifecycle functionality with tests covering durable real copy/move success, skip, failure, cancellation and unconsumed pending rows. Test actual identity capture on provider swaps/recovery, never registry-current identity pasted onto old provider. Example safety:
```python
async def test_changed_connection_refuses_retry(history_vm, registry, record, providers):
    registry.change_endpoint(record.source_connection.name)
    with pytest.raises(RecoveryRefused):
        await history_vm.retry(record.id, decide_conflict=choose_error)
    assert providers.mutations == []
```
Add tests per move/delete/ambiguous refusal, fresh source/destination stat calls/original paths despite pane navigation, fresh decision invocation, changed identity/state while decision waits, source missing and permission errors, no startup mutations, recheck existence not success, safe error categories, unknown/null bytes, pending-cancel/settled-race preservation, and disk-write cancellation barrier drained before shutdown.
- [ ] Step2: Run narrow new cases and save real RED evidence. Record task base before changes.
- [ ] Step3: Await threaded journal/store operations from async transfer workers without moving hub messages to threads. Convert private sync helpers only where needed; retain cancellation semantics and bounded batch cleanup. Store errors cannot claim durable success, old callers without history remain compatible. Capture original routing identity on PaneVM/provider construction and all actual source/recovery transitions. Build recovery resolution from live registry, fresh providers and exact PathRef reconstruction; snapshot source/destination observations, ask decision and revalidate before mutation. Fail closed on missing identity, ambiguity or changes. Copy uses standard transfer path, no hidden overwrite or retries. Recheck may update eligibility when destination absence established but remains unknown historically.
- [ ] Step4: Run new VM tests plus existing transfers/dual-pane/cancel/source-swap/credential-recovery/service/composition tests affected by edits; meaningful async thread barriers, no raw sleeps as completion gates. Run normal hooks. Report concrete callable/type interfaces Task3 uses, errors, lifecycle/shutdown and exact test results.
- [ ] Step5: Self-review and normal scoped commit. Write task-2-report.md; controller resolves task review before UI task.

### Task 3: Startup worker, history/recovery modal and documented user journey

**Files:** Create src/aws_tui/ui/widgets/transfer_history_modal.py and tests/unit/ui/test_transfer_history.py; modify app.py, transfers_overlay.py, keymap/action registry, test_transfers_overlay.py; new tests/snapshot/test_transfer_history.py and deterministic harness; cookbook/reference/keybindings/changelog and existing local generated site/wiki sources.

**Interfaces:** Consume Task2 history VM/read state/retry inspection callbacks. App constructs/history subscriptions and starts actual lifecycle worker on mount to load off-loop; shutdown drains history VM. Overlay receives optional history VM/open callback to preserve independent old harness APIs and shows History/Recovery controls when active rows or retained history exist, including after linger expiry; empty history remains reachable via the overlay-backed Ctrl+T/palette action. Global app.transfer_history free Ctrl+T key and palette entry remain remappable under strict collision rules. Modal History/Recovery modes, selectable literal full details and explicit recheck/retry/clear; callback handles fresh conflict policy and clear confirmation. No file I/O in compose/handlers; each operation schedules tracked Textual worker then updates mounted owned UI only.

- [ ] Step1: Write running composed-app RED tests. Example worker assertion instruments actual store save/load/trim and app worker context:
```python
async def test_startup_scan_is_worker_owned(app, seeded_interrupted, disk_probe):
    async with app.run_test(size=(80,24)) as pilot:
        await wait_until(lambda: not app.workers._workers, what="startup history worker drained")
        assert disk_probe.off_main_thread
        assert app._app_ctx.transfer_history_vm.records[0].status == "outcome_unknown"
        assert providers.mutations == []
```
Use actual composed context/history APIs and real worker-manager ownership probes. Pin live overlay opening from Ctrl+T and overlay controls with zero/expired/active rows; history/recovery categories, loading/error/empty, clear store after fresh confirmation, refused copy/move/ambiguous actions, explicit conflict dialog callback, source changes, closing modal before replies, Tab/Escape/Enter key containment and restored focus. Retain existing progress/cancel assertions. Barrier instrument writes/trimming to prove worker ownership and event-loop responsiveness; release/drain in finally. Seed secret-error and path `report [final] with spaces.csv`, verify literal full detail/copy/display.
- [ ] Step2: Run narrow new cases and keep RED output; add deterministic normal120x40 and narrow80x24 history/recovery snapshots with explicit rendered-content guards. New visual artifacts reflect feature; unchanged existing snapshots stay unless intentionally changed UI is substantiated.
- [ ] Step3: Implement accessible bounded modal/overlay/action/starter worker, owned shutdown and safe error notification. Fresh decision chooses ERROR by default (refuse overwrite) explicitly; present SKIP/RENAME/OVERWRITE only with clear fresh user selection and endpoint review. Wire retries through standard registered progress/cancel worker, avoid double-submit/races. No automatic recovery at mount.
- [ ] Step4: Update cookbook100summarylimit/restart and legacy caveats/clear-metadata-only/uncertain-publication/recheck/retry refusal; keybinding/custom migration, reference and changelog. Regenerate local docs per three-surface-docs skill without publishing. Run affected pilots, existing overlay/snapshot/modal key/focus tests, docs tests and hooks. Export actual-app capture artifacts into task-owned scratch with pytest portable paths (never hardcode old SDD folder into tests).
- [ ] Step5: Self-review all nine ACs and commit. Report commands/terminal results/new screenshots or SVG paths, assertion mapping, limitations and shutdown verification in task-3-report.md. Controller owns once-only whole-branch review and final full local gates plus protected delivery, postchecks, cleanup/issueDone before #256.

### Task 0: End sentinel

Extraction sentinel only; no dispatch. Canonical spec/plan are committed with coherent planning before Task1. Review packages use recorded pre-dispatch bases, never HEAD~1. Final evidence/rulings are preserved before exact workspace deletion.
