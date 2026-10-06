# Opt-in local Athena SQL drafts

Date: 2026-10-05. Binding scope: issue #256, as saved in `.superpowers/sdd/2026-10-05-athena-sql-drafts/issue-description.md`.

## 1. Decision and alternatives

Use one private, atomic JSON file per complete five-field query context, an application-owned draft runtime, and explicit recovery into the already selected exact context. Enable persistence from Settings; show a Drafts button and save state inside Athena only while enabled. Never recover SQL automatically.

Alternatives considered:

1. **One JSON document for all drafts:** fewer files, but every edit rewrites every draft and a malformed document makes unrelated drafts unreadable. Locking prevents lost updates but does not isolate corruption.
2. **SQLite:** transactions make coordination straightforward, but schema migration, journal/WAL cleanup, readable SQL sidecars, and bounded shutdown add work disproportionate to fifty records.
3. **Per-context atomic JSON files (selected):** bounded read/validation per record, corruption isolation, deterministic ownership, and no new dependency. A shared configuration transaction serializes all writes and retention checks. A runtime fence handles queued edits and destructive actions.

This is one feature with five independently reviewable implementation tasks. The prior user authorization allows design decisions without a new permission menu; root reviews this design and the implementation plan before execution. No product implementation or test execution is part of this planning deliverable.

## 2. Global Constraints

- Preserve Python support `>=3.11,<3.14`; add no dependency or toolchain installation.
- Persist SQL text, UTC timestamps, a stable draft identifier, the five QueryContext fields, and schema version only; never persist result rows, query execution state, credentials, provider objects, or navigation snapshots.
- Keep AthenaQuerySnapshot and AthenaPageSnapshot fields, representations, and existing export/restore behavior unchanged; no draft read, write, or execution is triggered by snapshot restoration.
- Draft persistence defaults to off; missing configuration and demo mode perform no draft-directory creation, draft reads, or draft writes.
- Use `<config-dir>/athena-drafts/<id>.json`, schema version `1`, at most `50` owned record files, at most `8_388_608` total owned-record bytes, at most `262_144` SQL UTF-8 bytes, and at most `278_528` bytes per record.
- Use a `0.500` second edit debounce and a `2.000` second total draft-flush wait budget per application shutdown; these are constants, not new preferences.
- Existing Athena layouts and behavior remain unchanged when drafts are off; enabled UI must work at `80x24` and with Tab, Shift+Tab, Enter, and Escape.
- Domain code must not import infrastructure; infrastructure must not import domain, VM, services, or UI; preserve all existing layer checks.
- Never log, place in exception text, attach to a crash report, or expose through object repr any draft SQL or untrusted raw draft payload.
- Recovery never submits SQL, loads result rows, creates AWS named queries, or automatically chooses another connection or context.
- Use only applicable local checks on the existing Python 3.12.9 environment; do not claim Windows, multiple-runtime, native-clipboard, live-AWS, or hosted-Actions verification.

## 3. Current code and boundaries

`AthenaQueryVM.set_sql` is synchronous. It remains synchronous and schedules memory-only draft work. `set_context` already has lifecycle locking and query cancellation; add draft hooks without replacing that mechanism. `AthenaPageVM` starts with three blank Athena context fields and discovers defaults asynchronously. Recovery must never call its default-selection functions to repair a missing record context.

`AthenaService` creates both ordinary and staged credential-recovery pages. Composition creates the one shared runtime; pages own short-lived draft sessions. Settings receives the same runtime through `SettingsVM`. `AppContext` owns shutdown and any unfinished I/O handles. Existing snapshot objects remain private in-process transport; they are not draft records.

The infrastructure layer may not import `QueryContext`. Its record uses an exact five-string tuple in the order `connection_name, region, workgroup, catalog, database`; the VM converts using `context.cache_key` and `QueryContext(*record.context)`. A new infrastructure worker owns blocking I/O; no default asyncio executor thread is used for draft work.

### 3.1. Files and responsibilities

- `infra/athena_draft_store.py`: immutable private record/result/permit types, schema validation, deterministic IDs, limits, read/merge/write/delete operations.
- `infra/draft_worker.py`: one lazy dedicated daemon worker, FIFO submission, concurrent-future ownership, stop fencing, safe result transport. It does not know widgets or QueryContext.
- `infra/config_store.py`: `Config.athena_sql_drafts: bool = False`, exact boolean parsing under `[athena].sql_drafts`, serialization and preserving mutations.
- `vm/athena/drafts_vm.py`: shared preference/listing state and session ownership; edit revisions, debounce, save acknowledgments, destructive-action fences, shutdown reports.
- `vm/athena/draft_recovery.py`: fresh exact-context verification using the existing client and domain validators, and safe fixed recovery outcomes.
- Existing Athena query/page/service/composition/app files: narrow lifecycle and execution hooks.
- `ui/widgets/athena/drafts_modal.py`: metadata-only listing and recovery/delete/clear controls.
- `ui/widgets/settings/athena_drafts_panel.py`: path, retention explanation, enable/disable and operation status.

No new domain module is necessary. Do not extend the AWS named-query Saved view.

## 4. Configuration and on-disk contract

The public config setting is:

```toml
[athena]
sql_drafts = true
```

A missing table/key is false. Reject non-bool values with a fixed `ConfigError("[athena].sql_drafts must be a boolean")`; do not interpolate the value. Serialize the table only when true. Add `ConfigStore.set_athena_sql_drafts(enabled: bool) -> None`. All four existing reconstructing mutators (`add_connection`, `update_connection`, `remove_connection`, `set_default_connection`) preserve the new field; prefer `dataclasses.replace` for these targeted updates. Existing theme/keybinding writers must continue to preserve it through their transactions. Tests cover config reload after every mutation, rather than testing serialization alone.

Resolve the path from the actual `config_store.path.parent`, including a supplied `config_dir`; do not independently resolve a second platform path. Constructing store/runtime and displaying this path do not create it. Demo mode ignores a true setting, disables the control with `Unavailable in demo mode`, and does not inspect real draft files. An injected test runtime is allowed in an isolated full-app fixture; production demo never enables persistence.

Record filenames are `<64 lower-case hex characters>.json`. Compute ID as SHA-256 of compact UTF-8 JSON for the five-field array with `ensure_ascii=False`. Do not build paths from raw context strings. Exactly one record per context; subsequent saves preserve `created_at` and update `updated_at`. SQL is retained verbatim, including whitespace; empty or whitespace-only SQL removes that context's saved draft instead of writing an empty record. Editing SQL does not implicitly delete a draft from a different context.

```json
{
  "schema_version": 1,
  "id": "<sha256-of-five-field-array>",
  "created_at": "2026-10-05T16:30:00.000000Z",
  "updated_at": "2026-10-05T16:31:00.000000Z",
  "context": {
    "connection_name": "analytics",
    "region": "us-west-2",
    "workgroup": "primary",
    "catalog": "AwsDataCatalog",
    "database": "default"
  },
  "sql": "SELECT 1"
}
```

Accept exactly these top-level/context keys and exact built-in field types (reject bool as version). Each context field is nonempty, valid UTF-8 and at most 1,024 UTF-8 bytes. Timestamp text must parse as UTC, round-trip to the canonical microsecond `Z` form, and satisfy `created_at <= updated_at`; do not require time to be earlier than the current clock. Filename, ID, and computed context hash must agree. Reject duplicate JSON keys, nonfinite JSON constants, invalid Unicode including surrogate strings, excessive record bytes, empty SQL, and unknown schema versions. Unknown versions and corrupt files are left in place and counted against limits; skip them in listings and expose only a count and a fixed warning code. Other valid files remain readable. The manager preserves the skipped count, displays fixed nonfatal warning copy, and allows confirmed Clear all even when only skipped owned records remain. Warnings do not block valid sibling recovery; safe nonregular-record deletion rules still apply.

Before any ensure_private_dir call, lstat the draft directory and refuse symlinks or existing non-directories. For owned record reads, lstat must show a regular file; open with O_NOFOLLOW and O_NONBLOCK where available, then fstat and compare device/inode with the lstat result and require a regular file again. On platforms without O_NOFOLLOW, retain the before/after identity checks and refuse an observed symlink. Nonregular entries (FIFO, socket, device, directory, symlink) are skipped nonfatally without opening for read, counted conservatively against the record count, and never followed. Read at most `MAX_RECORD_BYTES + 1` from a record; do not load oversized records first and check later. Check the aggregate candidate serialized UTF-8 bytes and owned-file count before creating a temp file. Replacing an existing record subtracts its current size before computing the projected budget. Reject over-limit writes without eviction or modification of previous valid records. Listing order is newest `updated_at` first, with ID as deterministic tiebreaker.

Use `ensure_private_dir(directory)`, temp creation mode `0600`, flush, `os.fsync`, and same-directory `os.replace`; sync the directory on POSIX where supported, following existing config behavior. Harden valid pre-existing records to `0600`. No SQL-containing backup files. Remove owned temp files on normal failure; clean leftover `.draft-*.tmp` files under the same transaction on the next enabled operation, without following symlinks or traversing directories. They are private too. Permission claims are POSIX assertions, with the existing best-effort behavior documented for other filesystems.

### 4.1. Serialization and destructive actions

Use `ConfigStore.transaction()` as the shared process/thread and OS-level lock for every store mutation and enabled listing. It is keyed by the same config path across store instances. Under that lock reload the current setting before every save; an off setting rejects a stale save. Do not nest `ConfigStore.set_athena_sql_drafts` outside its reentrant transaction assumptions incorrectly. `set_enabled` performs preference and deletion under the same lock; the public mutator may be called inside that transaction.

An enabled directory is owned exclusively by this feature. Clear/delete only canonical record filenames and owned temp names, with no symlink traversal. Deleting a symlink with an owned basename removes the link itself, never its target. Never remove arbitrary unrelated files or recursively delete an unknown directory. After the last record, remove owned temps and remove the draft directory if empty. The config store's lock file is configuration infrastructure, outside the draft directory.

Enable: serialize through the runtime; persist true, verify/create private storage, list records, and report enabled only after success. On storage initialization failure, best-effort persist false and expose a fixed failure state; if rollback fails, preserve any confirmed preference readback and keep saving suspended. If readback also fails, explicitly mark the preference unconfirmed while retaining the last confirmed value. Only successful explicit initialization/reconciliation resumes saving; filesystem recovery alone does not. Provide keyboard routes for enable retry and confirmed disable/delete. Never claim the checkbox is off if disk says true. Disable: stop accepting new edits and invalidate all session permits first, wait in FIFO for already started store work, atomically set false and remove owned records under the config transaction. Report success only after disk cleanup succeeds. If cleanup fails, show `Off; draft cleanup failed — retry` and keep saving suspended; retry invokes cleanup even if the setting is already false. A failed config write leaves the prior persisted preference visible with a failure message, while local saving remains suspended.

Delete-one invalidates pending writes for that ID in every session in this app before enqueueing deletion; clear-all does so for every ID. The worker serialization means a started save completes before its delete/clear. Sessions acknowledge a tombstone in memory: unchanged editor text does not immediately recreate a deleted draft during shutdown; only a new user edit may create it again. Records saved by a later explicit edit in a different running app can reappear; deletion is not a cross-process prohibition on future edits. Concurrent saves from separate store instances/processes to different contexts cannot overwrite one another or exceed aggregate limits.

## 5. Exact public interfaces

The implementation plan supplies imports and examples. These names/types are the handoff contract:

```python
# infra/athena_draft_store.py
DraftContext = tuple[str, str, str, str, str]
DraftCode = Literal["disabled", "read_only", "invalid", "unsupported", "limit", "io", "cancelled"]
@dataclass(frozen=True, slots=True)
class SqlDraft:
    id: str = field(repr=False)
    context: DraftContext = field(repr=False)
    sql: str = field(repr=False)
    created_at: datetime = field(repr=False)
    updated_at: datetime = field(repr=False)
@dataclass(frozen=True, slots=True)
class DraftStoreResult:
    records: tuple[SqlDraft, ...] = field(default=(), repr=False)
    code: DraftCode | None = None
    skipped: int = 0
    enabled: bool | None = None
class DraftPermit:
    def cancel(self) -> None: ...
    @property
    def cancelled(self) -> bool: ...
class AthenaDraftStore:
    def __init__(self, *, config: ConfigStore, directory: Path) -> None: ...
    def list(self) -> DraftStoreResult: ...
    def save(self, record: SqlDraft, *, permit: DraftPermit) -> DraftStoreResult: ...
    def delete(self, draft_id: str) -> DraftStoreResult: ...
    def clear(self) -> DraftStoreResult: ...
    def set_enabled(self, enabled: bool) -> DraftStoreResult: ...
def draft_id(context: DraftContext) -> str: ...

# infra/draft_worker.py
class DraftWorker:
    def submit(self, operation: Callable[[], DraftStoreResult]) -> Future[DraftStoreResult]: ...
    def close_intake(self) -> None: ...
    @property
    def pending_count(self) -> int: ...

# vm/athena/drafts_vm.py
DraftState = Literal["off", "empty", "pending", "saved", "error", "context_required"]
@dataclass(frozen=True, slots=True)
class DraftFlushReport:
    unpersisted: int
    timed_out: bool
class AthenaDraftsVM:
    def __init__(self, *, store: AthenaDraftStore, enabled: bool, read_only: bool,
                 directory: Path, hub: MessageHub[Message], dispatcher: Dispatcher) -> None: ...
    def open_session(self, *, active: bool = True) -> AthenaDraftSession: ...
    async def set_enabled(self, enabled: bool) -> bool: ...
    async def refresh(self) -> None: ...
    async def delete(self, draft_id: str) -> bool: ...
    async def clear(self) -> bool: ...
    async def shutdown(self) -> DraftFlushReport: ...
    def dispose(self) -> None: ...
class AthenaDraftSession:
    def activate(self, sql: str, context: QueryContext) -> None: ...
    def edited(self, sql: str, context: QueryContext) -> None: ...
    def context_changed(self, context: QueryContext) -> None: ...
    def recovered(self, record: SqlDraft) -> None: ...
    def deleted(self, draft_id: str | None) -> None: ...
    def has_unsaved_text(self, sql: str, context: QueryContext) -> bool: ...
    async def flush(self, *, deadline: float) -> DraftFlushReport: ...
    def detach(self) -> None: ...

# vm/athena/draft_recovery.py
async def validate_draft_context(*, context: QueryContext, client: Any,
    source_is_current: Callable[[], Awaitable[bool]]) -> bool: ...
# AthenaPageVM
async def restore_draft(self, draft_id: str,
    confirm_replace: Callable[[], Awaitable[bool]]) -> bool: ...
def keep_current_editor(self) -> None: ...
```

`AthenaDraftsVM` exposes read-only properties `enabled`, `read_only`, `directory`, `items: tuple[SqlDraft, ...]`, `busy`, `cleanup_required: bool`, `dispatcher: Dispatcher`, `error_text: str | None`, and value-free `on_property_changed`. Sessions expose `state`, `error_text`, `editor_revision: int`, `bound_context: QueryContext | None`, and `on_property_changed`. `SqlDraft` construction/validation and worker transport never retain raw exceptions.

## 6. Edit/save state and lifecycle

Every user edit increments the session editor revision synchronously and binds a complete context at that edit. Capture immutable `(SQL, five fields, revision, permit)` immediately; never look up whatever page context happens to exist after debounce. Set `pending` immediately, then start/restart the 500 ms timer. Only a completed atomic save for the current revision and current permit may set `saved`. A previous revision completing while newer text is pending does not change the visible pending state. A new editor revision that cannot schedule revokes only that session's still-current pending capture; it preserves another session's newer writer and already committed disk records. A context-selection notification alone preserves the captured payload. A successful acknowledgment retains the context-required warning when the current selection differs from the bound origin, while recording the exact original SQL/context baseline. Save errors keep SQL in memory and expose `Draft not saved` with a fixed reason; retry occurs on the next edit or shutdown.

Editing before all five fields exist sets `context_required`; no disk record uses blank fields. Normal initial discovery may supply a context for subsequent edits, but does not silently rewrite an already captured draft into a different source. Display `Draft pending · select context and edit to save`. A real context change with nonempty bound SQL preserves the last edit's source; suppress new autosaves and execution until the user returns to that exact context or explicitly clears the editor and starts a new query. The enabled-only warning is `Editor belongs to another context. Return to that context or clear the editor.` A transient refresh of the same context may suspend actions while resolving, but must not bind SQL to blank intermediate fields.

Enabling with current nonempty editor SQL schedules that SQL once against its complete current context. Snapshot transport never counts as a user edit and never schedules a save. Newly active ordinary pages attach their current SQL and context only as an unsaved baseline; existing drafts are listed only when requested. Staged credential-recovery pages have inactive sessions: no save, cleanup, preference mutation, or listing as a side effect of staging. `RecoveryServiceVM.commit_selection` activates the successfully installed page session after committing selections; discarded candidates merely detach. Old-page shutdown cannot overwrite a later edit by the active replacement: runtime revisions/permits are monotonically assigned per ID across sessions, and stale queued writes are revoked before the new writer is accepted.

Page shutdown captures/flushed data from the query session, not the page context that `AthenaPageVM.shutdown` has already cleared. Query shutdown preserves its existing execution cleanup. A session detach cancels its debounce handle, submits its final eligible edit once (unless tombstoned), and leaves resulting future ownership with the application runtime. Page navigation can wait using the same bounded flush helper; it must not shut down the shared runtime.

### 6.1. Shutdown and ownership

The application calls shared draft shutdown before hosted-content shutdown. Cache its terminal report: every later query/session flush or detach immediately reuses that report and never reopens intake or waits another budget. The draft runtime shutdown closes edit intake, cancels debounce timers, queues every eligible final edit, and waits against one absolute `loop.time() + 2.000` deadline. Multiple sessions share this budget, not two seconds each. Use `asyncio.wait` over runtime-owned asyncio waiter futures fed by concurrent-future completion callbacks; canceling a waiter must never propagate cancellation to the worker future; do not `wait_for` and then await cancellation-resistant operations, and do not await worker joins. At the deadline revoke outstanding save permits, detach UI observers, and return a conservative count of edits without confirmed saves. Mark no timed-out edit saved, even if an already-entered OS operation completes later.

Current per-ID/editor-revision unconfirmed obligations, including edits that cannot schedule a save because their context is incomplete or differs from their bound origin, belong to the application runtime, independently of presentation sessions. Page disposal removes subscriptions without dropping those obligations or retaining dead UI objects. Confirmed physical success resolves only the matching revision; failures remain unresolved, newer valid writers supersede older revisions, and recovery acknowledgement, blank clearing, and destructive fences remove the affected obligations. Unschedulable edits retain only value-private identity/revision accounting, never UI objects or a write against an invalid context. Completing an unbound context transfers its obligation to the valid origin without double counting. Superseded sessions cannot revive an old obligation. Disabled, demo, and staged sessions remain excluded; saving suspension preserves enabled unsaved obligations without scheduling writes. Shutdown counts each current origin once across live and retired pages; late completion after a terminal report never changes that report.

The lazy worker is a dedicated daemon thread with a FIFO queue and a strong registry of concurrent futures. It owns references through completion, retrieves every result, and drops SQL-bearing callable references after completion. It is not part of asyncio's default executor, so `asyncio.run` does not acquire an unbounded executor-shutdown join. AppContext keeps the runtime/worker until process exit. Completion after VM disposal must never call widgets, dead dispatchers, or a closed loop; it updates only worker bookkeeping and releases owned data. Closing intake queues a stop sentinel after work and returns immediately. Cancellation of an awaiting coroutine does not cancel or orphan worker ownership.

No Python mechanism can forcibly cancel a thread already blocked inside a filesystem syscall. A write can finish after the deadline if it already passed its final permit check and entered `os.replace`. This is reported as unconfirmed, not saved; atomic replace still preserves record integrity. At process termination unfinished daemon I/O ends with the process. The promised bound applies to the **draft-flush wait**, not existing AWS cancellation, total app shutdown, arbitrary kernel/process exit, or guaranteed physical durability under disk failure. Tests must actually stall the synchronous store, ignore permit cancellation until released, and prove the coroutine returns within budget while ownership remains measurable and later completion is observed. Do not substitute a cancellable `sleep` as the only stall test.

App shutdown invokes shared draft shutdown before closing LogSink and records a value-free `athena.drafts.unpersisted` event containing only `count` and `timed_out`. Preserve a fixed user-facing shutdown warning on the app context and print it after the TUI exits: `Some Athena SQL edits were not confirmed saved before exit.` A transient toast during teardown is insufficient evidence of reporting.

## 7. Recovery and source safety

The manager lists all valid records by connection/region/workgroup/catalog/database and saved time; no SQL preview is needed. Selecting a row does nothing. Restore is a separate explicit action, available only when no query is submitting/running, no lifecycle transition is active, and drafts remain enabled. This minimal design does not automatically switch context: users first select the exact source and all three Athena selectors displayed in the record. A mismatch warns and blocks the pending recovery; there is no selection of a first/default account, workgroup, catalog, or database.

`restore_draft` rereads the record, checks all five fields equal the current complete context, then checks the live configured source is still the captured page connection. Composition injects `source_is_current` via AthenaService: asynchronously re-resolve the exact connection name with `ConnectionResolver.resolve_selected` and compare its current routing fields with the captured `Connection` (kind/name/region/profile/endpoint/TLS/path-style and applicable credential-source identity), with no default/environment fallback when the name is missing. Do not log comparison data. A missing/changed source fails. Configuration lookup itself runs off the UI loop with the existing async-to-thread bridge; it is a source/configuration operation, never a SQL-bearing draft I/O job. Require the exact captured routing tuple `(kind, name, region, source, profile, endpoint_url, force_path_style, verify_tls)` and the credential-material-free named-entry tuple `(presence, kind, profile, region, endpoint_url, credentials_selector, force_path_style, verify_tls)` for credential-source routing. Use `(False, None, None, None, None, None, None, None)` for auto-discovered names without an explicit entry; otherwise `presence=True` and `credentials_selector` is exactly the entry.credentials string or None. Do not include access_key_id, secret_access_key, session_token, secret hashes, or other credential material. Create one source-check closure per page and attempt its identity baseline during the first async setup check, before admitting drafts-enabled editing/recovery/execution; compare the captured Connection routing tuple even on that first check. Record the first initialization attempt under the closure's check lock before its awaited source read. If initialization fails or is cancelled, that page/check closure remains unavailable and returns false on subsequent validation; only a fresh page/check closure may establish a new baseline. After successful initialization, a later temporary lookup failure may recover only to that same baseline. Run that first setup check even when persistence starts off; it reads only source/configuration, starts no draft worker, and performs no draft-file I/O. Apply the failed-source editor guard only while drafts are enabled, preserving usable off-mode queries. Capture only the identity tuple in memory; never compare/persist a whole ConnectionEntry. If a name is newly shadowed by explicit configuration, removed, or remapped, validation fails. Do not compare rotating short-lived credential values as a proxy for routing identity.

The setup timing is equivalent to this snippet (source/configuration reads only):

```python
if self._drafts is not None:
    source_current = await self._source_is_current()
    if self._drafts.enabled and not source_current:
        self._draft_recovery_error = "Draft context is unavailable or changed."
        self.query.set_draft_recovery_guard(True)
```

Freshly enumerate the requested workgroup, its catalog, and its database with bounded pagination/shape checks (`64` pages, at most `3` consecutive empty pages, and `1_000` unique rows per collection); call `get_workgroup` and require the exact enabled workgroup. Validate database row connection, region, catalog and database identity. Always do this even when the five fields equal the cached page context. Copy the bounded discovery logic into a focused shared helper only if needed; do not reuse `_preflight_snapshot_context`'s equal-context shortcut. Provider/config failures return false with fixed text; do not pass their raw exceptions into diagnostics.

If current nonempty SQL differs from the exact last acknowledged saved/recovered SQL+context, request `Replace unsaved SQL?` through a callback; the VM decides this, using `set_sql` state, not a TextArea dirty flag. Declining changes neither SQL nor its save state. Capture editor/context/lifecycle revisions and candidate `updated_at` before confirmation; after acceptance reread the record and revalidate exact live context, preference, query idle state, and all captured revisions. Changed editor, record, deletion, disabled setting, source switch, or teardown aborts rather than overwriting newer work. The final install is synchronous under the existing query lifecycle guard with no await between final revision checks and `set_sql`/session acknowledgment. The SQL setter needs a scoped internal suppression of the draft edit hook for this install, without changing snapshot methods. Results/execution state are not restored. SQL remains subject to the existing read-only policy when explicitly run.

While recovery checks are running, execution is blocked. Failed missing/stale-context recovery leaves a visible guard; `Keep current editor` explicitly cancels the failed recovery without changing SQL, or the user can fix the selected context and retry. After successful recovery, the session binds the record context. Any subsequent context change with that SQL blocks execution as described above. At the top of `_run_execution`, before runner start and before `_is_submitting`, drafts-enabled queries freshly validate the configured source and current complete context, then recheck query generation, editor revision and context after await. This covers direct execute-command callers too; checking only a button predicate is insufficient. Off-state execution retains its current path.

The persisted five fields cannot attest that an AWS profile with the same name has been repointed to a different AWS account between restarts. Do not invent account IDs, call STS as attestation, or claim account proof. Detectable live configured routing changes and mismatched/missing five-field contexts are refused; genuinely unobservable credential-account remapping is outside this record scope and is stated in documentation.

## 8. Privacy and diagnostic handling

Use repr-suppressed fields on records, session payloads, futures/job wrappers and results. Never include SQL snippets in listing labels, confirmation text, status, task names, logs or action recording. Format record context as plain text with markup disabled; context fields from disk are untrusted too. Fixed status/error mappings are preferable to raising store exceptions. Never return a JSON decoder exception, Unicode exception, OSError object, `exc_info`, exception cause, or exception notes to the VM. The worker catches operational exceptions into fixed result codes; unexpected store failures also become `io` without stringification.

Where a public validation error must be raised, decide its constant code in a separate helper and raise a fresh constant-message error outside the original except block. `raise ... from None` alone suppresses display but retains `__context__`; tests inspect cause/context/notes as well as rendered traceback. Do not rely on generic redaction to discover arbitrary SQL. Existing crash_dump/log_sink need no SQL-aware global rewriting if boundaries are correct.

The binding issue predates the current `doctor` command. Current doctor is read-only and must continue not to discover/open these draft files. Add a targeted no-read regression if composition paths are touched; no new doctor/report/export integration belongs to this feature.

## 9. User interface

Settings adds an expanded `Athena SQL drafts` section after Connections, containing a plain-text path, retention copy and an `Enable local SQL drafts` button (becomes `Disable and delete drafts`). Copy: `Keep the latest SQL for each query context on this device. Up to 50 drafts and 8 MiB total. SQL is stored as plaintext; results and credentials are not retained.` Show the actual resolved directory and the demo reason when applicable. A separate checkbox is unnecessary; Button fits the existing Settings focus traversal. Disable opens a danger confirmation explaining that all local draft records will be deleted. Operation failure stays visible and retryable.

When enabled, Athena query controls add `Drafts` (`#athena-drafts`) after Cancel. Include `FocusSlot.ATHENA_DRAFTS = "athena.drafts"` in both widget and page/native focus rings. Off means `display=False` and no layout space, no changed status copy, and no extra focus target. The editor border title gains `Draft pending`, `Draft saved`, or `Draft not saved` while enabled; this occupies no extra row and must remain visible at 80x24, with the full reason in detail. Preserve existing execution status text and the compact editor minimum height.

The modal has a scrollable metadata list, a detail area showing all five fields and timestamp, and Restore / Delete / Clear all / Keep current editor / Close controls. Delete and Clear all use explicit danger confirmations. Disable remains in Settings. Tab and Shift+Tab cycle visible enabled controls, Up/Down select records, Enter activates focused buttons (selection alone never restores), Escape closes. Use existing modal/focus coordination; nested confirmation must correctly restore modal focus. The exact result contract is `DraftModalResult = Literal["restored", "closed"]` and `AthenaDraftsModal(ModalScreen[DraftModalResult])`, with constructor `__init__(self, page: AthenaPageVM, *, hub: MessageHub[Message], focus_coordinator: FocusCoordinatorVM | None = None) -> None`. Successful recovery dismisses with `"restored"` and focuses the editor. Ordinary Close/Escape dismisses with `"closed"` and focuses the Drafts button when it remains visible and enabled. The page callback is `Callable[[DraftModalResult | None], None]`; external/default `None` dismissal is treated as closed. After a deferred refresh it rechecks page attachment and that the page owns the current screen, uses the existing `_is_focus_target` availability predicate, and falls back through the existing `focus_default()` route if the requested target is unavailable. Detached pages or pages beneath another modal receive no focus request. Preserve the native focus coordinator routing and do not increment modal depth manually. SQL replacement confirmation uses plain fixed copy only. No SQL previews, automatic restore, or new global keybinding is needed.

Settings goldens that visibly add the real control may be updated after visual review. Existing functional assertions remain. Existing Athena off-state goldens remain byte-identical; new enabled fixtures cover pending, saved, manager, and context warning at 80x24 and 120x40 in carbon and github-light, plus theme coverage where the harness already requires it.

## 10. Acceptance and evidence mapping

| Binding criterion | Required evidence |
|---|---|
| 1. Off by default | Missing-setting Config plus real store/query edit/shutdown leaves draft path absent; store spies show no draft reads. |
| 2. Opt-in names path/retention | Running full-app Settings pilot sees actual temporary path and copy; enables using keyboard. |
| 3. Saved versus pending | Deterministic held-write pilot screenshots and checked-in SVG content guards for both strings and SQL marker. |
| 4. Exit and crash recovery | Two distinct tests: normal shutdown flush then fresh runtime/page; completed debounce file without shutdown then fresh runtime/page. |
| 5. No execution | Fresh PageClient start_calls unchanged on listing, selection, confirmation and restore. |
| 6. Unsaved replacement | Seed through query.set_sql; decline preserves text; accepted recovery revalidates; editor change during prompt aborts. |
| 7. Five fields | Exact serialization round trip with distinct values in every context field. |
| 8. Stale/missing context | Fresh provider/config changes despite cached equal context; no default fallback; execution remains blocked; direct execute-command path tested. |
| 9. Version/corrupt/limits | Invalid/unknown record alongside valid one remains readable; rejected over-limit save preserves original bytes; no eviction. |
| 10. Delete/clear/disable | Disk and listing assertions plus queued/in-flight save races, unchanged-editor shutdown and disabled-setting reload. |
| 11. Privacy | Sentinel valid/malformed SQL/Unicode/OSError; repr, logs, crash dump, exception text, chain and notes; old snapshot sentinel test retained. |
| 12. Permissions | POSIX directory 0700 and record/temp 0600 assertions. |
| 13. Concurrent writes | Separate store instances with coordinated concurrent threads write different contexts and preserve both; retention transaction serialized. |
| 14. Bounded flush | Real stalled synchronous fake ignores cancellation, returns report within the two-second budget, retains owned pending future; release drains without late saved/observer callback. |
| 15. Keyboard | Real AwsTuiApp pilot traverses Settings enable and Athena manager restore, confirms/declines, closes and restores focus; no live AWS. |
| 16. Existing snapshot | Run the exact test named in the issue, unchanged. |

## 11. Review conclusions and limitations

The selected design satisfies the functional scope without account attestation or snapshot persistence. The difficult boundary is physical I/O: the two-second flush-wait contract cannot promise an arbitrary blocked syscall has stopped. That distinction is part of both implementation tests and user documentation, not a weakened fake acceptance. Existing AWS shutdown behavior remains independent. Full-app keyboard evidence must come from a writable temporary configuration and fake Athena client, not from claiming demo's disabled toggle proved persistence. No local test execution has occurred during architecture planning.
