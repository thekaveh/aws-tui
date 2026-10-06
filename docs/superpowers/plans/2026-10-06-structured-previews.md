# Bounded structured Quick Look Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver every issue #252 acceptance criterion for bounded CSV, JSON, JSONL and Parquet Quick Look previews.

**Architecture:** One parent-owned provider session and shared budget feed bounded parsers and one killable Parquet decoder child. Immutable results keep I/O out of VM state, while the widget controls generation-safe rendering and a cached raw/structured toggle.

**Tech Stack:** Python 3.11–3.13, existing asyncio/aioboto3/VMx/Textual/Rich, required PyArrow 25, pytest/moto/Textual pilot and existing snapshot tooling.

## 1. Global constraints

- Preserve all nine issue #252 acceptance criteria and the raw 65,536-byte prefix contract.
- Support Python >=3.11,<3.14 on the existing macOS, Linux and Windows contract.
- Add required pyarrow>=25.0.1,<26; preserve unrelated runtime pins and dependency requirements.
- Use one parent-owned provider session, one shared PreviewBudget and at most one killable decoder process per preview.
- Enforce 32 actual file requests, 8 MiB requested bytes, 1 MiB per range, 512 KiB footer, 32 MiB declared uncompressed chunks, 50 rows, 24 columns, a 5-second work deadline and a separate 1-second cleanup allowance.
- Never reset accounting for fallback or toggling; charge physical provider requests exactly once, including S3 HEADs, retries and redirects.
- Pin every S3 GET with IfMatch and the captured VersionId when present, including literal null; discard results when final latest-version HEAD detects drift.
- Preserve ordinary S3 retry configuration; only the dedicated preview client disables retries.
- All shipped LocalFS, S3FS and InMemoryFS implement bounded preview sessions; compatibility fallback is only for other providers and legacy chunk payloads.
- Preserve all existing 13,582 assertions, 782 goldens and six recovery-schema ASTs unless a concrete intended visual change is independently reviewed; do not weaken skips, timeouts, coverage or tests.
- Keep reads read-only, resources owned until drained and stale generations unable to publish.
- Do not introduce SQL, editing, whole-object download, external schema lookup, profiling, a general sandbox or a hard RSS framework.
- Do not invoke hosted GitHub Actions or live AWS, change workflows, publish packages/sites/wiki, bypass protections or touch unrelated work.
- Execute the three tasks sequentially with one editing owner; root owns review, full final gates, protected delivery and cleanup.

## 2. Ownership, files and shared interfaces

Read [the canonical design](../specs/2026-10-06-structured-previews-design.md) and owned `.superpowers/sdd/2026-10-06-structured-previews/issue-intake.json` first. Branch is `codex/issue-252-structured-previews`, base `0ceac536`; root commits the approved spec/plan before implementation. No new editor runs concurrently. Root owns review, final gates, GitHub delivery and cleanup. Each implementer owns only its assigned task, normal commits, focused verification and `task-N-report.md` in the owned evidence directory.

File responsibilities are fixed:

| File | Responsibility |
| --- | --- |
| `src/aws_tui/domain/preview_limits.py` | Constants, exception, request enum and mutable shared budget |
| `src/aws_tui/domain/filesystem.py` | Snapshot/session/capability protocols and source-changed error |
| `src/aws_tui/domain/local_fs.py` | Safe local preview descriptor/session |
| `src/aws_tui/domain/s3_fs.py` | Dedicated pinned preview client/session and physical request accounting |
| `src/aws_tui/demo/in_memory_fs.py` | Versioned demo session |
| `src/aws_tui/domain/preview.py` | Immutable results, bounded text parsing, detection and session orchestration |
| `src/aws_tui/domain/_parquet_preview_worker.py` | Framed process protocol, Parquet plan/decode and child supervision |
| `src/aws_tui/vm/chrome/quick_look_vm.py` | Backward-compatible lazy loader field |
| `src/aws_tui/app.py` | Lazy composition and preserved prefix helpers |
| `src/aws_tui/ui/widgets/quick_look.py` | Rendering, generation ownership, toggle and two-axis scroll |
| `pyproject.toml`, `uv.lock` | Required decoder and resolved dependency evidence |
| `README.md`, `docs/keybindings.md`, `docs/cookbook.md` | User-visible formats, limits, controls and failures |

All tasks consume the same exact APIs from design sections 3–5. Do not rename parameters between tasks:

```python
PreviewBudget.start(*, clock=monotonic) -> PreviewBudget
PreviewBudget.check() -> None
PreviewBudget.remaining_seconds() -> float
PreviewBudget.charge_request(*, kind: PreviewRequestKind, length: int = 0) -> None
BoundedPreviewProvider.open_preview(path: PathRef, *, budget: PreviewBudget) -> PreviewReadSession
PreviewReadSession.read_range(offset: int, length: int) -> bytes
PreviewReadSession.validate() -> None
PreviewReadSession.aclose() -> None
load_preview(provider: FileSystemProvider, path: PathRef, *, name: str, mime: str) -> PreviewResult
load_legacy_preview(chunks: AsyncIterator[bytes], *, name: str, mime: str) -> PreviewResult
preview_parquet(session: PreviewReadSession, raw: bytes, *, budget: PreviewBudget) -> PreviewResult
```

The session/engine functions above are async; budget methods are synchronous. `ReadSnapshot` has `size`/`revision`. `PreviewResult` has `format`, `raw`, `columns`, `rows`, `notes`; columns have `name`/`type_name`; cells have `text`/`kind`/`truncated`. Result enums and values are exactly those in design section 4. `QuickLookContent.load_preview` is optional, defaulted, after the existing four fields. Session boundaries charge; orchestration never double-charges. This entire section and Global constraints apply unchanged to every task.

## 3. Sequential implementation tasks

Run ordinary checks with the installed `.venv/bin` and Homebrew tools on PATH, canonical absolute `TMPDIR=/private/tmp` or owned scratch, and `UV_NO_SYNC=1 UV_OFFLINE=1` after dependency setup. Capture raw command output and terminal exits in owned evidence. Explicit lock/sync/audit or isolated minimum-runtime setup may need network: remove the ordinary-check offline/no-sync flags only for that setup, use authorized escalation when required, then restore them. The isolated Python 3.11 run must not replace the primary Python 3.12 virtual environment. For full native documentation checks, invoke `make docs-check DOCS_PY="DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib ./.venv/bin/python"` on this Mac; preserve the existing Cairo/runtime requirements.

### 3.1. Task 1: Shared budgets and shipped provider sessions

**Files:** Create `src/aws_tui/domain/preview_limits.py`, `tests/unit/domain/test_preview_limits.py`, `tests/unit/domain/test_preview_sessions.py`, and `tests/integration/test_preview_s3_ranges.py`. Modify `src/aws_tui/domain/filesystem.py`, `src/aws_tui/domain/local_fs.py`, `src/aws_tui/domain/s3_fs.py`, and `src/aws_tui/demo/in_memory_fs.py`. Extend existing provider tests only where their current behavior is directly affected.

**Interfaces:** Consumes existing PathRef/provider errors/safe local helpers. Produces PreviewBudget/PreviewRequestKind/PreviewLimitExceeded, ReadSnapshot, PreviewReadSession, BoundedPreviewProvider and PreviewSourceChangedError with section 2 signatures. All three shipped providers implement the capability. Transfer `read_stream` callers/fakes stay compatible.

- [ ] **1. Write budget and session RED tests.** Use an injected clock and actual InMemoryFS/local files; the fixture clock avoids extending any timeout.

```python
def test_budget_reserves_final_validation():
    budget = PreviewBudget.start(clock=lambda: 0.0)
    budget.charge_request(kind=PreviewRequestKind.OPEN)
    for _ in range(30):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=1)
    with pytest.raises(PreviewLimitExceeded, match="request budget"):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=1)
    budget.charge_request(kind=PreviewRequestKind.VALIDATE)
    assert budget.requests == 32
    assert budget.bytes_requested == 30

async def test_demo_range_detects_revision_change():
    fs = InMemoryFS()
    path = PathRef(("sample.csv",))
    async def payload():
        yield b"a,b\n1,2\n"
    await fs.write_stream(path, payload())
    session = await fs.open_preview(path, budget=PreviewBudget.start())
    assert await session.read_range(0, 3) == b"a,b"
    await fs.write_stream(path, payload())
    with pytest.raises(PreviewSourceChangedError):
        await session.validate()
    await session.aclose()
    await session.aclose()
```

Add exactly-at/+1 tests for bytes/range/deadline, negative/out-of-file offsets, zero reads, local replacement and in-place modification, cancellation during safe open/read, owned descriptor close and repeated `aclose`. Record mutation methods as forbidden. Run:

```bash
.venv/bin/python -m pytest tests/unit/domain/test_preview_limits.py tests/unit/domain/test_preview_sessions.py -q -m ''
```

Expected RED: missing new API/behavior, not a broken fixture. Retain raw output and exit status.

- [ ] **2. Implement the shared budget and local/demo sessions, then GREEN.** Copy the constants/complete budget logic from design section 3, with its reserve semantics. Implement the exact protocols and regular-file descriptor ownership using existing `_FdClaim` and `_local_etag`. Reads must check budget and revision around I/O; this is the core session sequence:

```python
budget.charge_request(kind=PreviewRequestKind.RANGE, length=length)
before = os.fstat(fd)
if _local_etag(before) != snapshot.revision:
    raise PreviewSourceChangedError("Preview cancelled: source changed")
os.lseek(fd, offset, os.SEEK_SET)
data = os.read(fd, length)
if _local_etag(os.fstat(fd)) != snapshot.revision or len(data) != length:
    raise PreviewSourceChangedError("Preview cancelled: source changed")
```

Run descriptor calls in the existing safe worker/ownership pattern, never on the UI event loop or on a descriptor concurrently being closed. Budget charge runs on the parent loop before worker I/O. Final validation also compares the current path identity, and charges VALIDATE. Demo checks `_revision` before returning a slice. Re-run step 1 until every behavioral check passes.

- [ ] **3. Add S3 RED tests with moto and recording wire/body adapters.** Use the repository's existing moto fixture/session idiom from `tests/unit/domain/test_s3_fs_with_moto.py`, with isolated fake credentials. Create a versioned object, retain its initial VersionId, open a preview, overwrite the key after one range, and assert final validation rejects even though pinned GETs still return the old bytes. Record actual GET keyword parameters:

```python
assert all(call["VersionId"] == first_version for call in range_calls)
assert all(call["IfMatch"] == first_etag for call in range_calls)
assert all(call["Range"].startswith("bytes=") for call in range_calls)
assert head_count == 2
assert budget.requests == head_count + len(range_calls)
assert write_calls == []
```

Add suspended literal-null/unversioned cases, 412, wrong returned version/ETag/range/length, ignored Range, deletion, mid-read cancellation/body close, and a simulated redirect that either consumes another charge or is rejected before send. Assert dedicated preview retry attempts equal one and ordinary client retry attempts remain six. Run:

```bash
.venv/bin/python -m pytest tests/integration/test_preview_s3_ranges.py -q -m ''
```

Expected RED: missing capability or incorrect identity/accounting/cleanup, never live network use.

- [ ] **4. Implement S3 session and GREEN.** Initial HEAD captures identity; the dedicated client's scoped before-send event callback charges every real HEAD/GET. Set per-request kind/length before the call and remove event handlers on session close. Merge preview retry config without mutating `_config`. Each ranged GET uses:

```python
request = {"Bucket": bucket, "Key": key,
           "Range": f"bytes={offset}-{offset + length - 1}", "IfMatch": etag}
if version_id is not None:
    request["VersionId"] = version_id
response = await client.get_object(**request)
```

Validate headers before body consumption; close body in `finally`. Final unqualified HEAD compares latest full identity. Map 412/missing version/drift to PreviewSourceChangedError; other failures keep the provider taxonomy. No conditional retry downgrade. Run step 3 and the combined focused gate:

```bash
.venv/bin/python -m pytest tests/unit/domain/test_preview_limits.py tests/unit/domain/test_preview_sessions.py tests/integration/test_preview_s3_ranges.py tests/unit/domain/test_local_fs.py tests/unit/domain/test_s3_fs_with_moto.py tests/unit/domain/test_filesystem_types.py -q -m ''
.venv/bin/python -m ruff check src/aws_tui/domain src/aws_tui/demo/in_memory_fs.py tests/unit/domain/test_preview_limits.py tests/unit/domain/test_preview_sessions.py tests/integration/test_preview_s3_ranges.py
.venv/bin/python -m mypy src/aws_tui
```

Expected GREEN: all focused tests/type/lint pass; no skipped new acceptance case.

- [ ] **5. Self-review, commit and report Task 1.** Stage only this task's explicit files, inspect `git diff --cached`, then normal commit `feat: add bounded versioned preview sessions`. Report exact commits, tested hashes, RED/GREEN logs/exits, provider cleanup/accounting evidence and limitations in `task-1-report.md`. Root runs a fresh task review; resolve Important/Critical findings before Task 2. Do not push or start another task independently.

### 3.2. Task 2: Bounded formats, Parquet process and required decoder

**Files:** Create `src/aws_tui/domain/preview.py`, `src/aws_tui/domain/_parquet_preview_worker.py`, `tests/unit/domain/test_parquet_preview.py`, `tests/minimum_runtime/test_structured_preview.py`. Modify `tests/unit/test_quick_look_content.py`, `pyproject.toml`, `uv.lock`, and any narrowly necessary PyArrow mypy boundary override. Preserve all existing content assertions.

**Interfaces:** Consumes Task 1's exact shared budget/session API. Produces PreviewFormat/PreviewCellKind/PreviewCell/PreviewColumn/PreviewResult and the three async engine/decoder functions in section 2. Uses one shared budget; session methods charge, child requests do not. No UI or VM imports from domain.

- [ ] **1. Add required packaging and capture installation/audit evidence.** Update only the runtime dependency list with:

```toml
"pyarrow>=25.0.1,<26",
```

Keep Python bounds and unrelated pins. Root's owned release JSON verifies 15 platform/Python wheel combinations; retain that evidence and inspect the new lock diff. Run locally:

```bash
uv lock
uv sync --locked
.venv/bin/python -c 'import pyarrow; import pyarrow.parquet; print(pyarrow.__version__)'
.venv/bin/python -m pip_audit --local
```

Expected: 25.x decoder imports, consistent lock, audited environment. Investigate any findings explicitly; do not claim old dependency hashes remain identical after an intentional new dependency or suppress vulnerabilities/skip required installs. A failed required install blocks Task 2 completion.

- [ ] **2. Write format RED tests against the new engine.** Keep old app helper assertions unchanged and add real demo/recording sessions. The following input must yield one multiline data field:

```python
raw = b'name,note\nAda,"line one\nline two"\nBob,""\n'
async def payload():
    yield raw
fs = InMemoryFS()
path = PathRef(("rows.csv",))
await fs.write_stream(path, payload())
result = await load_preview(fs, path, name="rows.csv", mime="text/csv")
assert result.format is PreviewFormat.CSV
assert tuple(column.name for column in result.columns) == ("name", "note")
assert result.rows[0][1].text == "line one\\nline two"
assert result.rows[1][1].kind is PreviewCellKind.EMPTY
assert result.raw == raw
```

Add headers/quotes/CRLF/cut records, 51 rows/25 columns, null/empty/missing/nested/truncated distinction, excessive cell/node/depth/render size, malformed and truncated JSON/JSONL including parseable prefixes, MIME/name conflicts, magic without suffix, ANSI/Rich markup and legacy exact-cap behavior. Recording session asserts every length/request within budget and `aclose` on every path. Run:

```bash
.venv/bin/python -m pytest tests/unit/test_quick_look_content.py -q -m ''
```

Expected RED only for new engine behavior; original assertions remain passing.

- [ ] **3. Implement bounded text parsing and result orchestration.** Declare exact enums/dataclasses from design section 4. New engine performs this ownership sequence inside its single absolute deadline:

```python
budget = PreviewBudget.start()
session = await provider.open_preview(path, budget=budget)
try:
    raw = await session.read_range(0, min(session.snapshot.size, RAW_PREVIEW_BYTES))
    result = await preview_parquet(session, raw, budget=budget) if raw[:4] in (b"PAR1", b"PARE") else parse_text(raw, name=name, mime=mime, truncated=session.snapshot.size > len(raw), budget=budget)
    await session.validate()
    budget.check()
    return result
finally:
    await session.aclose()
```

`parse_text(raw: bytes, *, name: str, mime: str, truncated: bool, budget: PreviewBudget) -> PreviewResult` is the private/pure parser boundary; include it in this module and its tests. The shown sequence must also be bounded while `open_preview` awaits; wrap the whole operation in the remaining absolute timeout rather than only checking after I/O. Distinguish source drift, timeout, format failure and budget failure as design section 4 requires. CSV uses strict csv.reader, JSON depth preflight plus full bounded parse, JSONL validates all captured records. Normalize/escape/cap cells before rendering, including visible quoting of scalar strings that collide with null/missing/nested display tokens. Import the worker lazily inside the engine to avoid a result-type import cycle. `load_legacy_preview` closes chunks in `finally`, slices oversized chunks, and does not claim structured Parquet without ranges. Re-run step 2 to GREEN.

- [ ] **4. Add actual Parquet fixture and process RED tests.** Write fixture bytes through PyArrow to `BytesIO`, never a live object store:

```python
sink = io.BytesIO()
pq.write_table(pa.table({"id": list(range(120)), "note": ["sample"] * 120}),
               sink, row_group_size=20, compression="snappy")
fixture = sink.getvalue()
assert pq.ParquetFile(io.BytesIO(fixture)).num_row_groups == 6
```

Use a recording session to assert schema names/types, exactly capped ordered values, footer reads and only selected row-group spans. Add nested/null/empty samples, empty file, >24 physical leaves, footer length corruption, `PARE`, unsupported codec, invalid offsets/external file paths and >32 MiB declared uncompressed chunks. For cancellation use a child entry-point test mode/injected executable fixture that waits forever, with no real decompression bomb; assert deadline terminates/reaps it and closes IPC/session. Record requests/bytes at exact limits, oversized frame refusal and missing sparse segment behavior. Run:

```bash
.venv/bin/python -m pytest tests/unit/domain/test_parquet_preview.py -q -m ''
```

Expected RED for missing bounded metadata/range/decode/termination behavior. Decoder absence is a failure, not `importorskip`.

- [ ] **5. Implement the process pipeline, then GREEN and minimum-runtime proof.** Child protocol phases are metadata-plan, admitted byte-segments, decoded-result or app-owned error. Check each declared frame length before allocation. Parent checks every plan offset/size/projection against the same budget; only session reads access data. Footer parsing uses a compact synthetic metadata-only buffer; decode maps supplied original offsets in a sparse adapter. Native iteration is bounded as follows:

```python
for group in selected_groups:
    batches = parquet.iter_batches(batch_size=remaining, row_groups=[group],
                                   columns=selected_names, use_threads=False,
                                   use_pandas_metadata=False)
    for batch in batches:
        append_bounded_cells(batch, remaining)
        remaining -= batch.num_rows
        if remaining == 0:
            break
    if remaining == 0:
        break
```

Implement `append_bounded_cells(batch, remaining)` inside the worker as bounded scalar normalization into the exact result cells; it must cap rows, nesting and text before IPC serialization. Configure `pre_buffer=False`, `buffer_size=0`, Thrift limits; schema planning caps physical leaves, validates dictionary offsets, declares size checks and rejects external files. Map codec/encryption/footer failures to exact named messages. Never use full read_table/Pandas/whole-object buffers. Close native readers explicitly.

Parent supervision invalidates output and closes reads, then terminates/kills/reaps within cleanup handling on cancellation/deadline. Child cannot write UI. Add a minimum-runtime test that imports PyArrow, creates a two-row/multi-group file and obtains schema/sample through the actual engine. Run:

```bash
.venv/bin/python -m pytest tests/unit/test_quick_look_content.py tests/unit/domain/test_parquet_preview.py tests/minimum_runtime/test_structured_preview.py -q -m ''
uv run --isolated --python 3.11 --locked python -m pytest tests/minimum_runtime/test_structured_preview.py -q -m ''
.venv/bin/python -m ruff check src/aws_tui/domain/preview.py src/aws_tui/domain/_parquet_preview_worker.py tests/unit/test_quick_look_content.py tests/unit/domain/test_parquet_preview.py tests/minimum_runtime/test_structured_preview.py
.venv/bin/python -m mypy src/aws_tui
```

Expected GREEN with actual 3.11 evidence and unchanged project Python bounds. Keep runtime/environment output in the task report; do not count wheel availability as executed Windows/Linux testing.

- [ ] **6. Self-review, normal commit and report Task 2.** Review AC1–5/8 and dependency changes against the canonical design, stage only assigned files, inspect staged diff, and commit `feat: add bounded structured preview decoding`. `task-2-report.md` records tested hashes, raw RED/GREEN exits/logs, required decoder lock/sync/audit evidence, minimum-runtime result and child cleanup proof. Root's fresh review gates Task 3.

### 3.3. Task 3: Actual app lifecycle, toggle, scrolling, snapshots and docs

**Files:** Modify `src/aws_tui/app.py`, `src/aws_tui/vm/chrome/quick_look_vm.py`, `src/aws_tui/ui/widgets/quick_look.py`, `tests/unit/test_quick_look_content.py`, `tests/unit/vm/chrome/test_quick_look.py`, `tests/integration/test_quick_look_wiring.py`, `tests/snapshot/apps/modals.py`, `tests/snapshot/test_modals.py`, and targeted Quick Look theme rules only if necessary. Add structured modal goldens under `tests/snapshot/__snapshots__/test_modals/`; retain existing goldens absent reviewed intended changes. Update `README.md`, `docs/keybindings.md`, `docs/cookbook.md`, and focused docs assertions.

**Interfaces:** Consumes the exact engine/results/session behavior in section 2. Produces the defaulted QuickLookContent loader field and generation-safe widget behavior. Uses exactly one loader/chunk path; toggle changes cached rendering only. No provider imports into VM or I/O inside result rendering.

- [ ] **1. Add actual app/VM RED tests.** Preserve four-positional-field construction; add loader identity/default checks. In `test_quick_look_wiring.py`, use existing `AppContextBuilder`, InMemoryFS seeding and `wait_until` readiness. Open CSV/JSON/Parquet through real `AwsTuiApp.action_quick_look`, not only a standalone screen. Assert:

```python
before = tuple(recorded_requests)
await pilot.press("r")
await pilot.pause()
assert "Raw" in str(app.screen.query_one("#quicklook-mode", Static).render())
await pilot.press("r")
await pilot.pause()
assert tuple(recorded_requests) == before
scroll = app.screen.query_one("#quicklook-body-scroll", ScrollableContainer)
await pilot.press("right")
await pilot.pause()
assert scroll.scroll_x > 0
```

Seed a row wider than the viewport and assert both offscreen content and actual offset, not merely the existence of a scrollbar. Seed null/empty/nested/truncated cells, ANSI escapes and bracket names/types. Block a recording provider mid-read then close; block the decoder then replace VM content; wait for cleanup and assert iterator/session/body/child closure, no writes and no stale success/error updates. Add an exactly-full `_first_bytes` source whose next read raises: full cap must not pull that next item. Run:

```bash
.venv/bin/python -m pytest tests/unit/test_quick_look_content.py tests/unit/vm/chrome/test_quick_look.py tests/integration/test_quick_look_wiring.py -q -m ''
```

Expected RED for missing toggle/scroll/cancellation behavior; baseline tests remain intact.

- [ ] **2. Wire lazy loader and owned widget lifecycle, then GREEN.** Add the optional loader field after existing fields and bind `functools.partial(load_preview, provider, path, name=entry.name, mime=mime)` in the app helper. Keep the raw constant alias/chunks and fix nested iterator closure/exact-cap stopping. Widget worker chooses:

```python
result = (await content.load_preview() if content.load_preview is not None
          else await load_legacy_preview(content.chunks, name=content.title, mime=content.mime))
if (generation != self._generation or self._vm.content is not content
        or not self._vm.is_open or not self.is_mounted):
    return
self._result = result
self._render_result()
```

Handle `chunks is None` before this branch without manufacturing data. Define `_render_result()` in QuickLook to render only the cached PreviewResult with literal Text/Table and bounded no-wrap structured width. Add `#quicklook-mode` Static and `r` action. Replace VerticalScroll with ScrollableContainer; implement horizontal actions and preserve app up/down forwarding. Subscribe to VM content/open notifications, increment generation and cancel old worker before new work/close/unmount; unsubscribe deterministically. Guard error publication with the same identity predicate. Re-run step 1 to GREEN, including real app key routing.

- [ ] **3. Add guarded structured snapshot, inspect rendered artifacts and document controls.** Extend `QuickLookApp` with a deterministic structured fixture containing `sample_id`, a distinctive row value, null/empty/nested/truncated cells and visible mode. Wait for actual loaded body before capture. Add snapshot presence guards for all three of header/value/mode, while preserving existing `voidline` guard. A snapshot equality result without these guards is insufficient.

```python
for needle in ("sample_id", "structured-preview-row", "Structured"):
    assert needle in snapshot_text
```

Generate only intended Quick Look snapshots, visually inspect actual output and record every changed golden with its reason before broad tests:

```bash
.venv/bin/python -m pytest tests/snapshot/test_modals.py -k quick_look --snapshot-update -q
.venv/bin/python -m pytest tests/snapshot/test_modals.py -q
```

Update README's preview description, both Quick Look keybinding entries, and cookbook examples with supported formats, `r`, horizontal arrows, raw64KiB versus structured budgets, JSON truncated fallback and named Parquet limitations. Do not claim full-file sampling, native platform tests not run or hard RSS enforcement. Generate local documentation surfaces, never publish:

```bash
.venv/bin/python -m scripts.docs.build_docs --site --wiki --package
.venv/bin/python -m scripts.docs.check_docs
.venv/bin/python -m mkdocs build --strict
.venv/bin/python -m pytest tests/docs -q
```

Expected GREEN; generated package docs are reviewed for intentional feature changes. Fix canonical docs then regenerate, never patch generated output as the source.

- [ ] **4. Run covering checks, self-review and commit Task 3.**

```bash
.venv/bin/python -m pytest tests/unit/test_quick_look_content.py tests/unit/vm/chrome/test_quick_look.py tests/integration/test_quick_look_wiring.py tests/integration/test_modal_key_containment.py tests/snapshot/test_modals.py tests/docs -q -m ''
.venv/bin/python -m ruff check src/aws_tui tests/unit/test_quick_look_content.py tests/unit/vm/chrome/test_quick_look.py tests/integration/test_quick_look_wiring.py tests/snapshot/apps/modals.py tests/snapshot/test_modals.py
.venv/bin/python -m ruff format --check src/aws_tui tests/unit/test_quick_look_content.py tests/integration/test_quick_look_wiring.py
.venv/bin/python -m mypy src/aws_tui
PATH="$(pwd)/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin" UV_NO_SYNC=1 UV_OFFLINE=1 bash scripts/check-layers.sh
```

Inspect every new/changed assertion and golden against AC6–9 and existing behavior. Stage explicit owned files plus generated docs and independently accepted goldens; commit `feat: integrate structured Quick Look controls`. `task-3-report.md` includes tested hashes, RED/GREEN logs/exits, pilot cleanup/scroll/toggle evidence, visual inspection and golden inventory. Root reviews the task and then the entire feature branch; no implementation task performs promotion.

## 4. Root final verification and protected delivery

The root maps all nine ACs through design section 7 and verifies original assertion/golden/recovery-schema preservation against the captured baseline. Fix every Important/Critical finding with focused RED/GREEN and fresh review. Do not duplicate complete gates during each task.

On the final reviewed clean commit, run the full local gate with existing installed/cached native hooks, default pytest discovery and coverage (do not omit `tests/minimum_runtime`), type/layer checks and strict documentation generation. Concrete core commands are:

```bash
uv sync --locked
.venv/bin/python -m pre_commit run --all-files
.venv/bin/python -m pytest --cov=aws_tui --cov-report=term-missing
.venv/bin/python -m mypy src/aws_tui
PATH="$(pwd)/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin" UV_NO_SYNC=1 UV_OFFLINE=1 bash scripts/check-layers.sh
.venv/bin/python -m scripts.docs.build_docs --site --wiki --package
.venv/bin/python -m scripts.docs.build_docs --site --wiki --package --check
.venv/bin/python -m scripts.docs.check_docs
.venv/bin/python -m mkdocs build --strict
uv build --no-build-isolation --out-dir .superpowers/sdd/2026-10-06-structured-previews/dist
```

Resolve exact newly built wheel/sdist paths from that owned directory, assert one of each, and pass those paths explicitly to `scripts/check_dist.py` and `python -m twine check`. Do not use a shared `dist/*`. Create an owned clean venv, install the exact wheel plus test dependencies, run outside the source checkout with source PYTHONPATH cleared, and verify `aws_tui.__file__` points to installed site-packages before running required preview/minimum-runtime tests. Record application wheel SHA256, actual installed PyArrow version and engine/process behavior. Minimum-runtime environment must include the mandatory decoder. No missing-dependency skip or blanket old-lock assertion counts as proof.

Preserve >=70 combined coverage, all required hook results and actual command exits/raw logs. Fresh dependency audit remains required. Root may prepare one concrete local gate runner for reproducible paths/evidence, but it must run these checks rather than substituting a narrower tier.

After local success and reviewed exact head, use protected feature-to-develop and develop-to-main PR merges without admin bypass or Actions. Attach created PRs. Run final promoted-source postchecks at the actual promoted SHA, then manually close #252, mark board Done and remove/archive only owned branches/worktrees/scratch according to the session workflow. No release/package/site/wiki publication. Completion requires postchecks and owned cleanup, not merely a green feature branch.
