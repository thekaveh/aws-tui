# Bounded structured Quick Look design — issue #252

Status: root-selected architecture, draft for root self-review before implementation.
The binding issue is [#252](https://github.com/thekaveh/aws-tui/issues/252).

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

## 2. Behavior and architecture

Quick Look displays CSV tables, JSON/JSONL tables, and Parquet schema with a capped row sample. Raw text remains available through `r` and reuses the original prefix. Both directions scroll by keyboard; null, empty string, missing, nested and truncated cells remain distinguishable. Filename/MIME hints are checked against a bounded sniff. Malformed or truncated JSON/JSONL falls back to raw. Parquet failures have named messages.

The app supplies a lazy loader in `QuickLookContent`. A domain engine opens one provider session, fetches the bounded raw prefix, detects/parses the format, validates current source identity, and returns an immutable display-neutral result. The widget owns lifecycle and publication. Native Parquet work runs in a child process; a cancelled thread Future is not a decoder deadline. Only the parent owns provider resources and authorizes reads.

This selects the process design over whole-file decoding and an in-process/thread decoder. It adds only the boundary needed to stop native decoding at close, switch and deadline. Local temporary IPC is allowed; no local whole-file copy or decoder-openable user path is supplied.

## 3. Shared limits and provider API

Create `src/aws_tui/domain/preview_limits.py`. Its exact constants are:

```python
RAW_PREVIEW_BYTES = 64 * 1024
PREVIEW_SNIFF_BYTES = 4 * 1024
PREVIEW_MAX_BYTES = 8 * 1024 * 1024
PREVIEW_MAX_REQUESTS = 32
PREVIEW_MAX_RANGE_BYTES = 1024 * 1024
PARQUET_MAX_FOOTER_BYTES = 512 * 1024
PARQUET_MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
PREVIEW_MAX_ROWS = 50
PREVIEW_MAX_COLUMNS = 24
PREVIEW_MAX_CELL_CHARS = 256
PREVIEW_MAX_NESTING = 16
PREVIEW_MAX_NODES = 4096
PREVIEW_MAX_RENDER_CHARS = 256 * 1024
PREVIEW_TIMEOUT_SECONDS = 5.0
PREVIEW_CLEANUP_SECONDS = 1.0
```

Expose `PreviewLimitExceeded(Exception)`, `PreviewRequestKind(StrEnum)` with `OPEN="open"`, `RANGE="range"`, `VALIDATE="validate"`, and mutable `PreviewBudget`. The budget API is:

```python
@dataclass(slots=True)
class PreviewBudget:
    deadline: float
    clock: Callable[[], float]
    requests: int = 0
    bytes_requested: int = 0

    @classmethod
    def start(cls, *, clock: Callable[[], float] = monotonic) -> PreviewBudget:
        return cls(clock() + PREVIEW_TIMEOUT_SECONDS, clock)

    def remaining_seconds(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise PreviewLimitExceeded("Preview timed out")
        return remaining

    def check(self) -> None:
        self.remaining_seconds()

    def charge_request(self, *, kind: PreviewRequestKind, length: int = 0) -> None:
        self.check()
        if length < 0 or length > PREVIEW_MAX_RANGE_BYTES:
            raise PreviewLimitExceeded("Preview range exceeds budget")
        if kind is not PreviewRequestKind.RANGE and length:
            raise ValueError("only range requests may charge bytes")
        reserve = 0 if kind is PreviewRequestKind.VALIDATE else 1
        if self.requests + 1 + reserve > PREVIEW_MAX_REQUESTS:
            raise PreviewLimitExceeded("Preview request budget exceeded")
        if self.bytes_requested + length > PREVIEW_MAX_BYTES:
            raise PreviewLimitExceeded("Preview byte budget exceeded")
        self.requests += 1
        self.bytes_requested += length
```

These methods run in the parent event-loop thread. The absolute deadline starts before open and includes child startup and final validation. The final-validation request slot is reserved on every earlier charge. Providers charge actual file I/O boundaries; the engine must not double-charge. Cache hits charge nothing; repeated wire ranges charge their full requested lengths again. Local/demo open, read and final validation use the same logical boundary accounting.

In `filesystem.py`, add frozen `ReadSnapshot(size: int, revision: str)`, `PreviewSourceChangedError(ProviderError)`, and these protocols:

```python
class PreviewReadSession(Protocol):
    snapshot: ReadSnapshot
    async def read_range(self, offset: int, length: int) -> bytes: ...
    async def validate(self) -> None: ...
    async def aclose(self) -> None: ...

@runtime_checkable
class BoundedPreviewProvider(Protocol):
    async def open_preview(
        self, path: PathRef, *, budget: PreviewBudget
    ) -> PreviewReadSession: ...
```

Use a separate capability so transfer-only fakes do not change. Import the budget for typing without creating circular dependencies. Validate offsets, lengths and checked end against captured size before I/O. Zero-length reads return empty bytes without a request. `aclose` is idempotent. Session methods expose provider errors, source-change errors and budget errors; cancellation propagates.

### 3.1. S3 session

Open one dedicated preview client using the existing endpoint/TLS/addressing settings and a merged config with one total attempt. Keep the normal client configuration unchanged. Before-send request accounting charges the shared budget for every actual HEAD/GET, including redirects; carry request kind/range length in scoped session state and unregister hooks on close. Alternatively reject a redirect before its next send, but never omit an actual send from accounting.

Initial unqualified HEAD captures size, ETag and VersionId, preserving absent versus literal `null` in an opaque revision. Require ETag. Each GET sends exact Range, IfMatch and captured VersionId when present. Check response status, Content-Range, ContentLength, ETag and VersionId before reading; reject a server ignoring Range and close immediately. Never read more than the admitted range. Close every body in `finally`, including validation failure and cancellation.

Final unqualified HEAD compares the latest identity; new version, deletion, missing pinned version, 412 or differing returned identity discards the result as source changed. Two HEADs total and at most 30 GET attempts in the normal session. Latest-version validation is the publication linearization point; changes after that point cannot be detected retroactively. Unversioned ETag/size is the available consistency contract, not an immutable version claim.

### 3.2. Local and demo sessions

Local opens one regular-file descriptor through current root-confined/no-follow/Windows helpers and `_FdClaim`. Compare `_local_etag(fstat(fd))` before/after each serialized bounded seek/read and compare the current path identity on final validation. This catches in-place changes and path replacement. Reuse descriptor ownership rather than closing an fd while an uncancellable worker still uses it. Cancellation disables publication immediately; cleanup retains ownership until the I/O returns. No new path normalization or symlink behavior.

Demo holds immutable bytes and captured `_revision`; reads and final validation reject revision changes. All three providers expose exactly the capability above, without changing transfer `read_stream` behavior.

## 4. Result and engine API

`domain/preview.py` owns these exact public types and entry points:

```python
class PreviewFormat(StrEnum):
    RAW = "raw"
    CSV = "csv"
    JSON = "json"
    JSONL = "jsonl"
    PARQUET = "parquet"

class PreviewCellKind(StrEnum):
    NULL = "null"
    EMPTY = "empty"
    SCALAR = "scalar"
    NESTED = "nested"
    MISSING = "missing"

@dataclass(frozen=True, slots=True)
class PreviewCell:
    text: str
    kind: PreviewCellKind
    truncated: bool = False

@dataclass(frozen=True, slots=True)
class PreviewColumn:
    name: str
    type_name: str | None = None

@dataclass(frozen=True, slots=True)
class PreviewResult:
    format: PreviewFormat
    raw: bytes
    columns: tuple[PreviewColumn, ...]
    rows: tuple[tuple[PreviewCell, ...], ...]
    notes: tuple[str, ...] = ()

async def load_preview(
    provider: FileSystemProvider, path: PathRef, *, name: str, mime: str
) -> PreviewResult: ...

async def load_legacy_preview(
    chunks: AsyncIterator[bytes], *, name: str, mime: str
) -> PreviewResult: ...
```

`load_preview` creates one budget, opens the session, reads at most 65,536 bytes, runs detection/decoding, calls `validate`, checks deadline, and closes in `finally`. Errors from source drift never return stale data. Budget/format failures may return raw plus a stable note only after source validation succeeds and time remains; no extra data read. Timeout stops work immediately. `load_legacy_preview` enforces raw/text parse bounds and deterministic iterator `aclose` but cannot claim source-version guarantees or structured Parquet. Other providers without capability use that legacy path.

Keep `_QUICK_LOOK_PREVIEW_BYTES` as an alias of the shared raw constant and retain all prefix assertions. Stop `_first_bytes` at an exactly-full yield before requesting another chunk. Close each nested generator wrapper explicitly. Direct legacy payloads must cap oversized chunks before extending buffers.

### 4.1. Text detection and cells

Sniff only the first 4 KiB. Parquet magic has precedence; filename/MIME suggests candidates but does not override invalid content. CSV requires a hint or conservative consistent delimited records, and uses strict `csv.reader` over `StringIO(newline="")`. Header is the first complete record; quoted newlines belong to the same cell. Show at most 50 complete data rows and 24 columns. Do not present a cut quoted record as complete.

Decode text candidates strictly with BOM handling. JSON and JSONL must parse the entire captured document and must not be known prefix-truncated; otherwise return raw, including a valid-looking prefix of a larger source. Legacy streams cannot infer total size from a filename; an exactly-full legacy prefix is conservatively treated as potentially truncated for JSON. Depth preflight and bounded node traversal prevent excessive parser/render work. JSON objects/scalars use path/value rows; arrays of objects and JSONL objects use a bounded union of keys.

Display null as `null`, empty string as `""`, missing as `— (missing)`, nested as compact JSON with NESTED kind, and shortened cells with `… [truncated]`. Preserve kind separately from text; quote literal strings that would otherwise look like reserved tokens or nested JSON (for example, string `null` displays as `"null"`, distinct from null `null`). The renderer must visibly retain this distinction, not merely store it in metadata. Escape ESC/C0/C1 and disruptive bidi controls visibly. Raw retains linefeed/tab; structured cells show embedded linefeed/carriage return as escapes. Cap names/types/cells after escaping, and cap total structured output. Brackets remain literal; never interpret file content as Rich markup or ANSI.

### 4.2. Parquet process

`domain/_parquet_preview_worker.py` provides:

```python
async def preview_parquet(
    session: PreviewReadSession, raw: bytes, *, budget: PreviewBudget
) -> PreviewResult: ...

def main() -> None: ...
```

Use a deferred import of `preview_parquet` inside the engine so the worker can import result types without a module-initialization cycle. The module entry point starts only under its explicit `__main__` guard. The parent launches `sys.executable -m aws_tui.domain._parquet_preview_worker`, not a shell or a general executor pool. The child receives only bounded framed metadata/byte segments, never providers, AWS credentials, user paths or executable instructions. Validate frame lengths before reading/allocating. Use JSON plus bounded binary/base64 segments, not untrusted pickle.

Read the final eight bytes, validate magic/length and the 512 KiB footer cap, then fetch the exact footer. Child uses PyArrow with bounded Thrift string/container limits to parse a compact metadata-only buffer. Reject encrypted footer/column metadata and external column-file paths. Return a bounded schema and a plan for the first row groups sufficient for at most 50 rows. Select at most 24 physical leaves; a nested top-level projection is admitted only when all its leaves fit. Cap schema names and type strings.

Validate chunk/dictionary offsets, nonnegative lengths, in-file spans, compressed transport budget, and sum of declared uncompressed selected chunks before fetching. Split spans above 1 MiB and charge provider requests only at I/O. Decode over a sparse original-offset adapter backed only by supplied segments; missing data raises a bounded range request/refusal, never zeros or an unrestricted read. Use supplied metadata, `pre_buffer=False`, `buffer_size=0`, and `iter_batches` with selected row groups/columns, `batch_size=remaining_rows`, `use_threads=False`, `use_pandas_metadata=False`. Do not decode whole row groups then slice, or convert unbounded Arrow data to Python.

Footer sizes are declarations, not hard native-memory guarantees. Reject oversized declarations and implausible counts, but contain native decoding in the deadline-controlled process. A row cap does not cap decompression of a large dictionary/page. No second Parquet decoder or cross-platform RSS framework is added. Large valid samples may show schema with a budget note; empty Parquet shows schema with zero rows.

Stable messages include `Unsupported Parquet codec: <name>`, `Encrypted Parquet preview is not supported`, `Malformed Parquet footer`, `Parquet sample exceeds preview budget`, `Preview timed out`, and `Preview cancelled: source changed`. Codec names are bounded/sanitized; no traceback or raw native error string. Check actual codec availability; unknown failures map to `Parquet preview unavailable`.

On cancellation/deadline, disable publication, cancel provider reads, close current body, terminate then kill the child if needed, reap and close pipes/session. Cleanup uses a separate one-second allowance; retain ownership until drained, and never claim a cancelled thread stopped. Tests must exercise the native-worker termination boundary using a hanging child, not induce a real memory bomb.

## 5. UI lifecycle and compatibility

Add defaulted `QuickLookContent.load_preview: Callable[[], Awaitable[PreviewResult]] | None = None` after the four existing fields. The app binds the domain loader lazily while retaining chunks compatibility. The widget consumes exactly one path: loader if present, otherwise `load_legacy_preview`.

Subscribe to VM content/open changes and unsubscribe on unmount. Increment generation before replacement, close and unmount; cancel/restart the owned deferred worker on content replacement. Every success/error update requires captured generation and content identity to match, VM open and screen mounted. Identity checks alone do not cancel old work. Keep the deferred-callable worker idiom.

Use a two-axis `ScrollableContainer` and literal Rich Text/Table with bounded widths, no structured wrapping and explicit mode status. `r` toggles cached structured/raw content, resets/clamps scroll offsets and performs no I/O. Left/right scroll wide rows; preserve up/down app forwarding and close keys. Sanitize title, columns, notes and raw text as well as cell data. Direct VM/snapshot payloads remain supported.

## 6. Packaging and primary evidence

Require `pyarrow>=25.0.1,<26` in `pyproject.toml` and update `uv.lock`. Root verified release metadata and 15 CPython 3.11/3.12/3.13 wheels for macOS arm64/x86_64, manylinux_2_28 arm64/x86_64 and Windows amd64 in owned `pyarrow-release-verification.json`. Preserve the existing runtime/platform contract; wheel evidence is not native runtime proof. Locally lock/sync/audit, exercise Python 3.11 minimum runtime and test the exact built application wheel. Missing Arrow is a failed required installation, never a test skip.

Primary semantics: [Parquet layout](https://parquet.apache.org/docs/file-format/), [PyArrow ParquetFile](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html), [column metadata](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ColumnChunkMetaData.html), [S3 GET](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html), [botocore config](https://docs.aws.amazon.com/botocore/latest/reference/config.html), [PyArrow installation](https://arrow.apache.org/docs/python/install.html). Footer metadata locates rows in data pages; it does not itself contain the row sample.

## 7. Acceptance and delivery gates

| AC | Required tests | Task |
| --- | --- | --- |
| CSV quoted/multiline/header/row cap | `tests/unit/test_quick_look_content.py` | 2 |
| JSON/JSONL tables and malformed/truncated raw fallback | Same module, including valid-looking truncated prefixes | 2 |
| Multi-row-group Parquet names/types/sample | Same module and `tests/unit/domain/test_parquet_preview.py` using real Arrow fixture | 2 |
| Named codec/encryption/footer messages | Actual or narrowly mocked decoder failures, exact app-owned text | 2 |
| Named enforced byte/request/row/column/time bounds | Recording sessions, wire-attempt accounting, boundary/+1, hanging child | 1–2 |
| Every S3 range pinned and drift cancelled | `tests/integration/test_preview_s3_ranges.py`, moto VersionId/null/412/drift | 1 |
| Closing/switching cancels/closes without writes | Provider cleanup tests and actual app pilot in `test_quick_look_wiring.py` | 1–3 |
| Hint + sniff and control/markup safety | Content units and ANSI/Rich-bracket pilot | 2–3 |
| Toggle, horizontal scroll, cell distinctions, guarded snapshot | Real app pilot and structured modal snapshot with header/value/mode guards | 3 |

The existing 19 passing tests are baseline only. Three tasks execute sequentially with root review between them. After whole-branch review, root runs complete local default discovery/coverage >=70, native cached hooks, typing/layers, strict docs/site plus wiki generation, build/distribution/Twine, exact-wheel tests and final promoted-source checks. Protected feature-to-develop-to-main delivery, manual issue closure/board Done and owned cleanup happen only after these checks. No hosted workflows, live AWS or remote documentation publication.
