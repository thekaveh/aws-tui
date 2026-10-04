# S3 Object Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The standing goal authorizes unsupervised routine decisions and protected delivery; do not present another execution menu.

**Goal:** Inspect every requested property of one focused S3 object, read-only and on demand, preserving partial results, current selection and exact copyable values.

**Architecture:** An optional domain capability supplies immutable properties without expanding `FileEntry` or the mandatory filesystem protocol. A modal-lifetime VM owns reads and selection generations. A dedicated field table/full-value modal uses the existing app clipboard path, contextual routing and focus containment.

**Tech Stack:** Python 3.11–3.13, existing aioboto3/botocore, VMx, reactivex, Textual/Rich, pytest/Pilot/snapshots; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-03-s3-object-details-design.md` is binding. Current issue and source study are `/tmp/aws-tui-250-issue.json` and `/tmp/aws-tui-250-study.json`.

## 1. Global Constraints

- All eleven current acceptance criteria remain binding; only current-object read-only details are in scope.
- Applicable local checks replace hosted checks, Actions stays disabled, and verification never mutates live AWS resources.
- `FileSystemProvider` and `FileEntry` remain unchanged; directory listings gain no per-row HEAD/tagging requests.
- Existing transfer, revision-token, rename/move, credential-recovery, source navigation, Quick Look and modal containment contracts remain intact.
- Python remains >=3.11,<3.14; coverage floor stays 70%. No dependency/lockfile changes, timeout inflation, assertion weakening or blind golden replacement.
- No worker pushes, PRs, issue/board edits, child agents, remote publication, shared-branch resets or hook bypass. Controller owns all delivery and final full-repository gates.
- Use `/bin/bash`, `login=false`, `.venv/bin/python` and `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib`. Normal hooks use `UV_CACHE_DIR=/tmp/aws-tui-250-uv-cache UV_NO_SYNC=1 UV_OFFLINE=1`.
- Each worker reproduces new behavior as focused RED, iterates focused GREEN, then runs its full appropriate covering scope once. Controller runs the complete suite once on the final clean reviewed head; an actual failure requires diagnosis/repair, not rerun-until-green.
- Keep all reports/logs in this plan's own SDD workspace. Capture exact BASE/HEAD, commands, counts, exit codes, failures/repairs, self-review and concerns. No passing/completion claim based only on an agent's assertion.

## 2. Review Focus

1. Empty string/zero/false versus missing or denied data: values remain faithful and unavailable sections are explicit (Tasks 1/2).
2. Same-named keys on distinct providers and selection A→B→A: old replies cannot overwrite current state (Task 2, actual-app Task 3).
3. Close, source disposal or mount teardown during SDK/clipboard waits: no stale modal, unowned task or unusable focus remains (Tasks 2/3).
4. Very long, bracket-containing and multiline values: literal display, complete copy, safe fixed clipboard labels and redacted errors (Tasks 2/3).
5. Connection/bucket/prefix/local/navigation focus while an old logical pane still selects a file: visible refusal, no wrong-object reads and no listing hot-path work (Tasks 1/3).

## 3. Implementation Tasks

### 3.1. Task 1: Typed on-demand S3 details reads

**Files:** create `src/aws_tui/domain/s3_object_details.py`, `tests/unit/domain/test_s3_object_details.py` and shared recording fixtures `tests/s3_object_details_support.py`; modify only the relevant imports/new method/helper in `src/aws_tui/domain/s3_fs.py`. Do not change existing stat/list/read/write/copy/move/rename behavior.

**Consumes:** existing `PathRef`, provider errors, `S3FS._resolve`, `_client`, `_map_client_error`, `_error_code`, `_auth_error`, `_to_aware` and client context/config.

**Produces:** `S3ObjectDetails`, `S3ObjectDetailsProvider` and `S3FS.read_object_details(path: PathRef) -> S3ObjectDetails`. Shared recorder fixtures support HEAD, tagging, list-buckets/list-objects and explicit read barriers for later real-app tests, recording every attempted operation/kwargs/context exit without network.

The immutable public result/interface is:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from aws_tui.domain.filesystem import PathRef

@dataclass(frozen=True, slots=True)
class S3ObjectDetails:
    bucket: str
    key: str
    content_type: str | None = None
    content_encoding: str | None = None
    size: int | None = None
    modified: datetime | None = None
    storage_class: str | None = None
    etag: str | None = None
    version_id: str | None = None
    encryption: tuple[tuple[str, str], ...] | None = None
    metadata: tuple[tuple[str, str], ...] | None = None
    tags: tuple[tuple[str, str], ...] | None = None
    tags_error: str | None = None
    checksums: tuple[tuple[str, str], ...] | None = None
    checksum_type: str | None = None
    checksums_error: str | None = None

@runtime_checkable
class S3ObjectDetailsProvider(Protocol):
    async def read_object_details(self, path: PathRef) -> S3ObjectDetails: ...
```

- [ ] **Step 1: Build recording stubs and write focused failing behavior tests.** Recorder session/client contexts must be async, count exit on success/error/cancellation, record operation names/kwargs, permit queued response/error/barrier outcomes, and reject unexpected/mutating operations. Add `test_full_details_exact_wire_and_version_tagging`, `test_minimal_head_preserves_missing_zero_false_and_empty`, `test_checksum_denied_keeps_readable_head`, `test_tagging_denied_keeps_other_details`, `test_unsupported_checksum_mode_falls_back_once`, `test_roots_and_bucket_only_paths_issue_no_requests`, `test_details_uses_fixed_prefix_and_bucketless_resolution`, `test_auth_and_notfound_do_not_fallback_or_tag`, `test_cancel_during_read_exits_client` and `test_listing_has_no_per_row_details_calls`.

Use a full HEAD payload containing ContentType, ContentEncoding, ContentLength=0, LastModified, StorageClass, quoted ETag, VersionId="null", ServerSideEncryption="aws:kms", SSEKMSKeyId, BucketKeyEnabled=False, Metadata including empty/long/bracket values, all five locked checksum algorithm keys and ChecksumType. TagSet contains returned pairs. Assert all retained result values, exact Bucket/Key/ChecksumMode and literal VersionId on tagging; no `GetObject` or mutations. A concrete response/value assertion is:

```python
details = await fs.read_object_details(PathRef(("bucket", "alpha.txt")))
assert details.bucket == "bucket" and details.key == "alpha.txt"
assert details.size == 0 and details.version_id == "null"
assert details.etag == '"opaque-etag"'
assert dict(details.metadata or ()) == {"note": "[bold]literal[/bold]", "empty": ""}
assert dict(details.checksums or ())["ChecksumCRC64NVME"] == "reported-crc64"
assert recorded_calls == [
    ("head_object", {"Bucket": "bucket", "Key": "alpha.txt", "ChecksumMode": "ENABLED"}),
    ("get_object_tagging", {"Bucket": "bucket", "Key": "alpha.txt", "VersionId": "null"}),
]
```

- [ ] **Step 2: Run focused RED.** `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib .venv/bin/python -m pytest -q tests/unit/domain/test_s3_object_details.py`. Preserve actual missing-capability/behavior failures before writing production implementation; import failure is acceptable for the new module, followed by meaningful behavior RED if scaffolding is needed.
- [ ] **Step 3: Implement the result and S3FS method.** Reject `path.is_root` and bucketless paths with fewer than two segments before `_client`, then use `_resolve`. Inside one client context, first HEAD with ChecksumMode enabled. On mapped permission/transport/throttling or explicit unsupported-mode errors, perform one ordinary HEAD, remembering the first error only for the checksum section. Authentication/not-found/conflict errors propagate typed, not as fallback loops. If ordinary HEAD fails, propagate its typed error. Successful enabled HEAD with no algorithms is not-returned, not verified. Tag the returned exact version when nonempty, including `null`; each tag failure becomes a separate unavailable section, preserving successful HEAD fields. Unknown whole-read errors are allowed to propagate for VM redaction; CancelledError never becomes a partial success. Copy response collections into tuples. Encryption pairs use reported API keys and string values, preserving false as `"false"`; omit customer key material. Checksum pairs retain API algorithm names, separate ChecksumType and ETag. Do not infer missing STANDARD/unencrypted/default size. Keep existing client config and all old paths untouched.
- [ ] **Step 4: Run focused GREEN, then one covering scope.** `pytest -q tests/unit/domain/test_s3_object_details.py` then `.venv/bin/python -m pytest -q tests/unit/domain/test_s3_fs_with_moto.py tests/unit/domain/test_s3_fs_bucketless_ops.py tests/unit/domain/test_s3_fs_auth_error_helper.py tests/unit/domain/test_s3_fs_pagination_defensive_break.py tests/unit/domain/test_s3_fs_rename.py tests/unit/domain/test_s3_fs_upload.py tests/unit/domain/test_filesystem_types.py tests/unit/domain/test_cross_fs.py tests/unit/domain/test_s3_object_details.py`. Moto endpoints are local test fixtures; never use real credentials/accounts. Preserve existing versioned rename/move tests unchanged. Inspect all output and skips/warnings; do not repeat unaffected controller-wide tests.
- [ ] **Step 5: Static checks, self-review, commit and report.** Scoped Ruff/format, applicable Mypy, `scripts/check-layers.sh`, `git diff --check` and normal hooks. Commit owned changes as `feat: add read-only S3 object details provider`. Full report records exact wire/fallback/error/cancel/hot-path evidence, every actual failed/repaired run and immutable collection behavior; stop for controller review.

### 3.2. Task 2: Current-target details viewmodel

**Files:** create `src/aws_tui/vm/file_manager/s3_object_details_vm.py` and `tests/unit/vm/file_manager/test_s3_object_details_vm.py`; extend the shared `tests/s3_object_details_support.py` with a capability-bearing in-memory provider for VM/Pilot tests. No production app/widget/clipboard/provider changes in this task.

**Consumes:** Task 1's exact result/protocol; real `PaneVM.provider/path/selected_entry/current_connection_key/listing_revision/status/on_property_changed`, `EntryKind`, VMx/observable helpers, `OperationOwner`, and `infra.redaction.redact_text`.

**Produces:** `S3ObjectDetailsState` (LOADING/READY/UNAVAILABLE/ERROR/CLOSED), frozen `ObjectDetailField(label: str, value: str, copy_value: str | None)`, and `S3ObjectDetailsVM(*, pane: PaneVM, hub: MessageHub, dispatcher: Dispatcher)` with `construct()`, `close()`, `shutdown()` async, `dispose()`, readonly `request_generation`, `state`, `title`, `fields: tuple[ObjectDetailField, ...]`, `on_property_changed`, and `async load_revision(generation: int) -> None`. UI observes `"request_generation"` to schedule only a new requested revision, and fields/state/title notifications to redraw; callbacks contain no actual values in the hub.

- [ ] **Step 1: Write focused RED using real PaneVM.** Seed two files with `InMemoryFS.write_stream`, construct a pane using `path_protocol="s3:"`, `connection_key=("aws", "fixture")`, shared hub/NULL_DISPATCHER and `await pane.setup()`. The fixture subclass implements `read_object_details`, records paths and can return held Futures/results/errors for each request. Add `test_all_required_fields_and_missing_values_project_explicitly`, `test_etag_is_separate_and_checksums_never_claim_verification`, `test_empty_value_is_copyable_but_unavailable_is_not`, `test_partial_errors_are_redacted_without_hiding_other_fields`, `test_old_reply_cannot_replace_new_selected_object`, `test_same_named_target_different_provider_is_not_current`, `test_selection_away_and_back_invalidates_first_reply`, `test_pane_completion_closes_before_reply`, `test_close_dispose_drains_and_suppresses_late_errors` and `test_marks_do_not_issue_another_details_read`. A direct ordinary-case assertion is:

```python
vm = S3ObjectDetailsVM(pane=pane, hub=hub, dispatcher=NULL_DISPATCHER)
vm.construct()
await vm.load_revision(vm.request_generation)
assert vm.state is S3ObjectDetailsState.READY
by_label = {field.label: field for field in vm.fields}
assert by_label["Content type"].value == "text/plain"
assert by_label["ETag"].copy_value == '"opaque-etag"'
assert "not performed" in by_label["Checksum verification"].value.lower()
await vm.shutdown()
vm.dispose()
pane.dispose()
```

Held-result tests use asyncio.Event/Future barriers with bounded `wait_until`/timeouts, not sleeps. Move the actual pane with `pane.move_cursor_command.execute(1)`, load the new generation, then resolve the old request (including a cancellation-resistant fixture). Compare final fields/title/identity and recorded paths. Repeat success and secret-bearing late-error forms; test synchronous observer mutation at publication and after shutdown begins. No fixture-only generation assignment.

- [ ] **Step 2: Run focused RED.** `.venv/bin/python -m pytest -q tests/unit/vm/file_manager/test_s3_object_details_vm.py`; preserve missing-module or behavior failures and confirm real selection events drive the repro.
- [ ] **Step 3: Implement the small owner-aware VM.** Subscribe to pane changes and completion. Reconcile provider object identity, source key, path, selected path/kind/parent status, listing revision and live construction state; S3 protocol/source and optional capability are required. Increment generations on changes/invalidations, clear old fields immediately, and close on pane completion. Marks/unchanged notifications do not reload. Capture the requested generation before I/O; obsolete calls return without a read. Own the async read and convert ordinary exceptions into redacted safe outcomes inside the owned coroutine. Propagate caller cancellation; suppress superseded/closed results. Revalidate actual identity/generation at final publication. `close` is synchronous invalidation/cancellation; async `shutdown` drains; `dispose` disposes subscription and component safely. Do not publish after closure.

Project deterministic fields labelled Content type, Content encoding, Size, Last modified, Storage class, ETag, Version ID, Encryption, User metadata, Tags, Checksums, Checksum type and Checksum verification. Size/timestamp presentation is explicit. `value` never blanks a missing field; `copy_value=None` means unavailable/not-copyable, whereas `copy_value=""` is a legitimate returned empty value. Metadata/tag JSON is lossless and literal, with returned empty collections distinct from unavailable. Redact every whole/partial error before it enters displayed/copyable state. Checksum verification always states not performed; reported values are never derived from ETag. Titles preserve legitimate object identity as literal text. Unknown failure is safe error state, not hidden success.
- [ ] **Step 4: Focused GREEN then one covering scope.** `.venv/bin/python -m pytest -q tests/unit/vm/file_manager/test_s3_object_details_vm.py` followed by `.venv/bin/python -m pytest -q tests/unit/vm/file_manager tests/unit/vm/test_operation_owner.py tests/unit/infra/test_redaction.py tests/unit/vm/test_clipboard_vm.py`. No repeat of the unchanged domain covering suite; inspect actual output and lifecycle drain evidence.
- [ ] **Step 5: Static checks, self-review, commit and report.** Scoped Ruff/format/Mypy, architecture, diff check and normal hooks. Commit `feat: own S3 details reads by current pane selection`. Report constructed/disposed VM/subscription ownership, exact RED/GREEN/covering evidence and all race outcomes; stop for controller review.

### 3.3. Task 3: Literal inspector, contextual action and documentation

**Files:** create `src/aws_tui/ui/widgets/s3_object_details.py`, `tests/integration/test_s3_object_details.py`, `tests/unit/ui/test_s3_object_details.py` and `tests/snapshot/test_s3_object_details.py` with their new scoped golden files. Modify relevant portions of `src/aws_tui/app.py`, `src/aws_tui/infra/keymap_store.py`, `src/aws_tui/ui/bindings.py`, canonical `docs/services/s3.md`, `docs/keybindings.md`, `docs/contract-ledger.md`, `CHANGELOG.md` and `tests/docs/test_contract_parity.py`; update directly affected binding expectation tests. Do not restructure app.py or change existing workflows. Avoid unrelated golden changes; substantiate any required existing snapshot change with actual rendered behavior.

**Consumes:** Task 2 exact constructor/lifecycle/generation/state/field interfaces and Task 1 recording/capability fixtures. Existing `_focused_file_pane`, ActionRegistry, curated palette/deferred-dismissal path, `_put_on_clipboard(value, label)`, ModalButton, DeferredWorkerMixin, modal input/focus containment and theme tokens.

**Produces:** `S3ObjectDetailsModal(vm: S3ObjectDetailsVM, *, copy_value: Callable[[str], Awaitable[None]])`, action `pane.object_details`, app handler `action_object_details`, label `S3 object details`, unique default `ctrl+o`. The callback invokes existing `_put_on_clipboard(value, "S3 object detail")` on a worker; no raw values/keys become toast/log labels.

- [ ] **Step 1: Write focused RED against actual apps and widgets.** Use `AppContextBuilder` with isolated temporary configuration and recording S3FS/capability providers; never real credentials or native clipboard. Seed ordinary bucket/object/prefix listings. Add actual Pilot tests for Ctrl+O, Commands and palette on a focused FILE, separate connection/navigation row, bucket and prefix refusals, local/parent/empty refusals, exact focused-row restoration, stale selection/source/away-and-back responses, close/mount cancellation, modal containment, custom remap and duplicate-open prevention. Prove no metadata calls merely listing rows before/after opening. Do not label a generic directory fake as connection evidence: use the real connection/navigation/source control and ensure a previously selected object is not accidentally read.

File rows intentionally do not take Textual focus. When `app.focused is None`, resolve the current active S3 DualPane through `FocusSlot.S3_LEFT` or `FocusSlot.S3_RIGHT` to its actual rendered pane and selected row; a cached logical pane alone is insufficient. An actual navigation, source or input focus refuses the action with zero reads. Supported focused descendants resolve their real owning pane. For unchanged selection, capture and restore coordinator slot, selected EntryVM identity, rendered EntryRow identity and actual Textual focus; comparing `None` alone does not establish row restoration. Commands and palette capture this originating context before their overlay and revalidate after dismissal. Do not change Pane or EntryRow focusability.

Representative open/close assertions, after the fixture explicitly selects its S3 file and records its actual rendered row:

```python
before = app.focused  # None is normal for file rows.
before_slot = focus_coordinator.focused_slot
before_entry = pane.vm.selected_entry
before_row = next(row for row in pane.query(EntryRow) if row.entry_vm is before_entry)
await pilot.press("ctrl+o")
await wait_until(lambda: isinstance(app.screen, S3ObjectDetailsModal), what="details modal")
screen = app.screen
await wait_until(lambda: screen.vm.state is S3ObjectDetailsState.READY, what="details ready")
assert screen.query_one("#s3-details-fields", DataTable).row_count >= 13
await pilot.press("escape")
await wait_until(lambda: not isinstance(app.screen, S3ObjectDetailsModal), what="details close")
assert app.focused is before
assert focus_coordinator.focused_slot is before_slot
assert pane.vm.selected_entry is before_entry
assert next(row for row in pane.query(EntryRow) if row.entry_vm is before_entry) is before_row
```

Copy tests navigate actual field rows then activate Copy/Ctrl+C; the recording clipboard must receive the full untruncated value (including brackets, Unicode, quotes/newlines and >8 KiB text), or losslessly round-trippable metadata/tag JSON. Returned empty values copy empty text; loading/unavailable/stale fields do not copy prior data. Inject helper/terminal failures to preserve truthful existing outcome reporting without exposing values. Assert markup is literal and every required field's missing state visible. Add secret-bearing HEAD/tag/checksum error render tests. Capture actual screenshots/SVGs of ready and partial/error modal at 120×40 and 80×24 for controller inspection; no claim that an unrelated startup screenshot verifies the inspector.
- [ ] **Step 2: Run focused RED.** `.venv/bin/python -m pytest -q tests/integration/test_s3_object_details.py tests/unit/ui/test_s3_object_details.py`; preserve expected missing-action/modal failures before implementation and do not inflate existing timeouts.
- [ ] **Step 3: Implement contextual routing and the modal.** Register the action/default label/key and add contextual/modal guards. Determine actual current pane/row ownership using the focus rules above, including supported focused descendants; never use the last logical DualPane selection alone. On valid target create/construct the VM and push the modal; otherwise visible safe refusal and zero read. Commands/palette defer until dismissal and revalidate origin. Modal composes a literal `DataTable` field/value summary and a wrapping read-only `TextArea` full selected-value viewer, Copy and Close ModalButtons; give stable IDs `s3-details-fields` and `s3-details-value`. Use Rich Text/markup=False for all user data. No content download or editing. Loads are deferred callables keyed by `request_generation`; redraw only current state. Loading/invalid/error/closed paths never retain old values. Copy uses the current field's `copy_value is not None` and a worker, not inline pump awaits. Ctrl+C within the modal copies without quitting or dispatching hidden-pane actions. Escape/Close invalidates VM before dismissal; unmount/cancel/disposal durably shuts down reads and removes subscriptions. Completion/source replacement must dismiss or safely invalidate the view and restore only valid current focus. No stacked inspectors.
- [ ] **Step 4: New snapshots and canonical docs.** Add controlled normal/narrow ready/partial snapshots with long markup values; verify actual display and copy before accepting new baselines. Preserve existing goldens unless the feature demonstrably changes their rendered action list. Document action, Ctrl+O, custom-binding migration, current-object read-only scope, required S3 read/tag/version-tag permissions and optional checksum KMS access, partial sections, unavailable versus empty states, ETag/checksum separation/no content verification, source lifetime, copy controls and no listing hot-path reads. Do not claim all endpoints/OS/runtime combinations or version browsing. Add a local doc contract checking key/action/IAM/partial/no-verification/readonly statements and locked SDK input support for new ChecksumMode/VersionId use. Generate site/wiki locally, check generated parity and run strict docs; no publication.
- [ ] **Step 5: Focused GREEN then one appropriate covering scope.** Run new UI/Pilot tests, then `.venv/bin/python -m pytest -q tests/integration/test_s3_object_details.py tests/unit/ui/test_s3_object_details.py tests/snapshot/test_s3_object_details.py tests/integration/test_quick_look_wiring.py tests/integration/test_keyboard_selection.py tests/integration/test_keybinding_wiring.py tests/integration/test_clipboard_reporting.py tests/integration/test_command_palette_wiring.py tests/integration/test_modal_key_containment.py tests/integration/test_modal_focus_wedge.py tests/unit/ui/test_bindings.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_pane_widgets.py tests/unit/test_composition_initial_theme.py tests/docs/test_contract_parity.py`. Keep all actual failures/repairs in the report; inspect screenshots and snapshots rather than blindly accepting changes.
- [ ] **Step 6: Static/docs gates, self-review, commit and report.** Scoped Ruff/format/Mypy, architecture, generated docs parity/strict build, diff check and normal hooks. Commit `feat: inspect S3 object properties without mutating them`. Report all eleven AC selectors, physical connection/bucket/prefix focus evidence, no-mutation/hot-path records, modal/clipboard teardown, exact tests/outputs, rendered artifacts and concerns. Controller performs independent review, whole-branch review and complete final gates before publishing.

## 4. Controller Completion

- [ ] Generate each full task diff package from its recorded BASE; independent spec/quality review, bounded fix loop and scoped re-review precede the next task. Do not implement product fixes in the controller or spawn duplicate reviewers.
- [ ] Whole-branch review compares actual branch base `226d6a54baec7f0f29d101b02bd404a867aaa3a3` to final head and every AC/spec clause; resolve real findings.
- [ ] On final clean reviewed head run complete default tests/coverage, all-file hooks, docs generation/parity/strict build, locked dependency audit, sdist/wheel build/content/Twine and actual-wheel synthetic details import/read/partial evidence. No live AWS or Actions.
- [ ] Fresh native upstream/protection/ticket checks; protected develop PR then main promotion, attach both, preserve exact reviewed source and verify full-tree parity/ancestry. Run applicable post-main details/provider/VM/Pilot/copy/focus/docs checks.
- [ ] Safely clean only this feature's local/remote branches and owned workspace after evidence export/hash verification. Close #250 with eleven verified ACs and substantive conclusion; board Done. Keep #283 open without genuine required Windows evidence. Only then start #249.
