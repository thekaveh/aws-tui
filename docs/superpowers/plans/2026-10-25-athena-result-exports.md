# Athena Result Exports Implementation Plan

> Use subagent-driven-development for isolated encoding and staging tasks; keep
> integration and lifecycle ownership with the parent. No routine approval pauses.

**Goal:** Complete #254 with reviewed loaded-view and complete-result exports.

**Architecture:** Pure streaming encoders, isolated atomic local store, captured
export coordinator and one Results modal/action. Preserve existing query path.

**Tech Stack:** Python3.11–3.13, Textual, VMx, asyncio/to_thread, pytest fixtures.

## 1. Global constraints

No AWS/Actions/protection/shared infra changes. Follow the design exactly for
CSV null/backslash and positional JSON encodings. Defaults:256MiB/1Mrows/10k
requests/300s;64KiB local chunks. No complete-result list/buffer. No initiating SQL.
Deferred live/native checks are nonblocking, not passed. Preserve #253/#257.

## 2. Ordered tasks

- [x] Pure codec TDD: create domain/athena_export.py and tests/unit/domain/test_athena_export.py. Produce ExportLimits, ExportLimitError, ExportDataError and streaming CSV/JSON serialization for ordered ResultColumn/str|None rows. Verify null/empty/sentinel, duplicate names, Unicode/newlines, empty data, malformed width/types and byte/row/chunk bounds.
- [x] Store TDD: create infra/result_export_store.py and tests/unit/infra/test_result_export_store.py. Produce ExportDestination/ExportFileVersion, ExportPermit, ResultExportStore.inspect/begin and ExportStage.write/publish/abort. Test exclusive creation, acknowledged replacement, changed/symlink/nonregular destinations, failures, cancellation/publication ordering and stage cleanup. Preserve existing file bytes on every prepublication failure.
- [x] Artifact TDD: extend captured Athena client boundary with authoritative successful CSV artifact streaming; exact source/region/execution/output, ETag/version conditional read, byte/time/request caps and stream closure. Add synthetic demo support. Test customer S3 vs managed/missing output, expiry and S3/Athena/KMS denial privacy, zero starts. No guessed URLs.
- [x] Coordinator TDD: new vm/athena/export_vm.py; compose into PageVM/Results lifecycle. Snapshot loaded visible rows, capture original execution metadata/client for complete exports, validate sequential pages and budget/token/schema behavior. Stage off loop and drain cancellation; prevent stale publication/retargeting. Measure >10k streaming memory and responsiveness with controlled providers.
- [x] UI TDD: Export form/review/cancel/progress, explicit overwrite confirmation, result action/palette/footer/focus. Actual app fixtures cover normalized CSV/JSON and original artifact, loaded transforms vs complete, request identity and no starts, source/context/cancel races, safe errors and80x24/120x40 geometry.
- [x] Canonical docs/contracts and final review: document scope/encodings/bounds, original AWS format vs normalized, partial refusal and local output policy. Relevant hooks, lint/type/layers, docs strict build, representative snapshots and native available Python checks. Independent review resolves findings before integration.

## 3. Delivery

Commit/push ticket branch, PR into develop then separate promotion PR into main.
Verify exact reviewed trees/local-origin parity, delete merged owned branches and
worktrees, close/read back #254 with substantive evidence, only then #262.
