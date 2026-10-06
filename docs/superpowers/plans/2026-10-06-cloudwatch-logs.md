# EMR Serverless CloudWatch Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The owner has authorized routine decisions and unsupervised execution; no design approval pause is required. The architecture assignment itself makes no commits or product edits.

**Goal:** Read and explicitly follow the selected EMR run's CloudWatch logs in the existing pane while preserving its S3 behavior.

**Architecture:** Add bounded discovery/event methods to the current connection-owned logs facade. Extend its VM and pane with exact source/stream identity, bounded raw events and local filtering, explicit follow, and safe lifecycle/recovery. Keep S3's reader and existing layout intact.

**Tech Stack:** Python, aioboto3/botocore, VMx, Textual, pytest/pytest-asyncio, real Textual Pilot, local snapshot and documentation tooling.

**Canonical spec:** [CloudWatch logs design](../specs/2026-10-06-cloudwatch-logs-design.md).

## 1. Global constraints

- Preserve existing S3 discovery, streaming, filtering, response caching, clone configuration fidelity, credential recovery, pane layout, and focus navigation. The existing S3 cache key gains the parsed bucket alongside app/run/file/size/filter to prevent same-key cross-bucket reuse; app clearing and the five-entry LRU remain unchanged, including temporary unknown metadata and return to the same source.
- No new dependencies. Python remains `>=3.11,<3.14`, Textual `==8.2.8`, and VMx `>=3.23.0,<4.0.0`.
- All applicable checks run locally. Do not invoke hosted CI or Actions, weaken assertions/snapshot guards, lower the 70% coverage floor, or relax architecture gates.
- No AWS mutations, releases, or logging-configuration changes belong to this feature.
- Logs Insights, account-wide search, Spark/Tez dashboards (#262), and enabling logging are outside scope.
- Preserve all existing assertions, snapshot content guards and unrelated recovery schemas, with one expected-behavior correction: `test_failed_detail_refresh_retargets_logs_away_from_previous_run` must assert IDLE and UNKNOWN for both sources after failed detail metadata, retaining and strengthening its stale-run/data guards. A failed GetJobRun cannot prove no logging is configured. Expand only the EMR credential-recovery snapshot, with exact-target tests; the five existing Athena snapshot classes are unchanged.
- Every domain request uses the selected connection's region and credentials. Bodies, response reprs, and original exception text/chains never reach CloudWatch diagnostics.
- Work on `codex/issue-261-cloudwatch-logs` from base `13b7475fed83528f2395149d8b0fa277b07e2de1`. Inspect current changes before editing; retain other authorized work.
- Two sequential implementation tasks are the reviewable units. Tests, demo, and documentation are part of their corresponding deliverables, not independent cleanup tasks.

## 2. Task 1: CloudWatch metadata, bounded provider and facade primitives

**Deliverable:** Directly testable CloudWatch discovery/read methods and a network-free fake behind the current facade. Existing UI remains S3-compatible. This task can be reviewed independently of follow/rendering.

**Files to create:**

- `src/aws_tui/domain/emr_cloudwatch_logs.py`
- `tests/unit/domain/test_emr_cloudwatch_logs.py`
- `tests/unit/demo/test_in_memory_emr_cloudwatch.py`

**Files to modify:**

- `src/aws_tui/domain/emr_serverless.py`: `JobRunDetail`, metadata parser and classified AWS errors.
- `src/aws_tui/domain/emr_logs.py`: protocol and CloudWatch facade delegates; no rewrite of S3 functions.
- `src/aws_tui/services/emr_serverless/service.py`: protocol annotations/factory and failed logs facade.
- `src/aws_tui/vm/emr_serverless/job_run_logs_vm.py`, `src/aws_tui/vm/emr_serverless/page_vm.py`: facade protocol annotations only in this task, so the service type-checks before Task 2 behavior changes.
- `src/aws_tui/demo/in_memory_emr.py`: metadata seeding/clone materialization and CloudWatch methods.
- `tests/unit/domain/test_emr_serverless.py`, `tests/unit/services/test_emr_serverless_service.py`.
- `docs/contract-ledger.md`, `tests/docs/test_contract_parity.py`: consumed Logs operations and locked SDK fields.

**Consumes:** Existing `LogFilter`, `map_boto_error`, `ProviderError` taxonomy,
`Connection`, `EMR_BOTO_CONFIG`, `EmrServerlessLogsClient` and `InMemoryEmr`.

**Produces:** Exact signatures and records in design sections 3.1 and 4.1.
`parse_cloudwatch_monitoring(overrides)`; `JobRunDetail.cloudwatch_monitoring`;
`CloudWatchLogStream(name, component, attempt)`;
`CloudWatchLogEvent(event_id, timestamp_ms, ingestion_time_ms, message)`;
`CloudWatchLogSnapshot(events, bytes_read, end_time_ms)`;
`EmrServerlessLogsClientProtocol` with existing S3 methods and keyword-only
`list_cloudwatch_streams(configuration, application_id, job_run_id)` and
`read_cloudwatch_events(log_group_name, stream_name, start_time_ms, end_time_ms)`.
The demo implements all four methods and the two seed helpers in design section 8.

### 2.1. Metadata test and implementation cycle

- [ ] Add a stubbed `get_job_run` test using the existing EMR client fixture.
  Supply this monitoring payload alongside its usual required run fields:

```python
monitoring = {
    "s3MonitoringConfiguration": {"logUri": "s3://bucket/logs"},
    "cloudWatchLoggingConfiguration": {
        "enabled": True,
        "logGroupName": "/custom/emr",
        "logStreamNamePrefix": "literal-prefix/",
    },
}
# Assert on the actual result returned by client.get_job_run("a", "r"):
assert detail.s3_monitoring_log_uri == "s3://bucket/logs"
assert detail.cloudwatch_monitoring == CloudWatchLogConfiguration(
    True, "/custom/emr", "literal-prefix/"
)
assert detail.configuration_overrides["monitoringConfiguration"] == monitoring
```

- [ ] Parameterize absent block, `enabled=False` with names still present,
  enabled-only default configuration, omitted/non-boolean enabled, malformed
  group/prefix and empty names. Verify original overrides survive unchanged and
  caller mutation cannot mutate the detail's preserved clone configuration.
- [ ] Run `uv run --locked pytest tests/unit/domain/test_emr_serverless.py -q`.
  New metadata tests fail before implementation; existing tests remain unchanged.
- [ ] Implement the frozen configuration record and append the defaulted field.
  Extract monitoring once in `get_job_run`; use the parser below as the policy,
  with the existing file's typing/import conventions:

```python
def parse_cloudwatch_monitoring(overrides):
    if overrides is None:
        return None
    monitoring = overrides.get("monitoringConfiguration", {})
    if not isinstance(monitoring, dict):
        return CloudWatchLogConfiguration(None)
    config = monitoring.get("cloudWatchLoggingConfiguration")
    if config is None:
        return None
    if not isinstance(config, dict):
        return CloudWatchLogConfiguration(None)
    enabled = config.get("enabled")
    enabled = enabled if isinstance(enabled, bool) else None
    group = config.get("logGroupName")
    prefix = config.get("logStreamNamePrefix")
    if any(value is not None and (not isinstance(value, str) or not value)
           for value in (group, prefix)):
        return CloudWatchLogConfiguration(None)
    return CloudWatchLogConfiguration(enabled, group, prefix)
```

The implementation must have the annotated signature in the spec, validate
SDK-supported name length/characters before requests, and classify malformed
names as unknown. A disabled valid record stays disabled. Extend demo
`add_job_run_detail` with `cloudwatch_monitoring=None`, preserving both monitoring
blocks. Demo `start_job_run` materializes metadata from the same parser so cloning
a CloudWatch run does not lose its log destination.
- [ ] Rerun the metadata suite and `tests/unit/vm/emr_serverless/test_clone_vm.py`.

### 2.2. Provider contract tests

- [ ] Build a small async context stub in the new provider test module. No
  moto/network server is needed. Its session captures service name, region and
  config; methods return queued responses or raise queued exceptions. A usable
  foundation is:

```python
from unittest.mock import AsyncMock

class LogsStub:
    def __init__(self, *, listings=(), reads=()):
        self.describe_log_streams = AsyncMock(side_effect=list(listings))
        self.filter_log_events = AsyncMock(side_effect=list(reads))
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

class SessionStub:
    def __init__(self, client):
        self.logs = client
        self.calls = []

    def client(self, service, **kwargs):
        self.calls.append((service, kwargs))
        return self.logs

def event(event_id, message, *, timestamp=1000):
    return {
        "eventId": event_id,
        "message": message,
        "timestamp": timestamp,
        "ingestionTime": timestamp + 1,
        "logStreamName": "/applications/a/jobs/r/SPARK_DRIVER",
    }
```

- [ ] Add request-shape tests for default group/run root, selected `us-west-2`,
  explicit `EMR_BOTO_CONFIG`, `limit=50`, name ordering, and exact stream event
  requests. Assert `filterPattern`, `unmask`, and `startFromHead` are absent.
- [ ] Add default/custom prefix classification tests: S3-like reserved worker
  markers inside a prefix, `r` versus `r2`, another app, retry 1/2, executor
  instance suffixes, unknown components, malformed attempt, ambiguous roots,
  literal trailing slash and no slash. All selection identities are full names.
- [ ] Add an empty-page continuation regression test:

```python
async def test_empty_page_with_token_continues():
    stub = LogsStub(reads=[
        {"events": [], "nextToken": "next"},
        {"events": [event("e1", "ERROR café")]},
    ])
    result = await read_cloudwatch_events(
        session=SessionStub(stub), region_name="us-west-2",
        log_group_name="/aws/emr-serverless",
        stream_name="/applications/a/jobs/r/SPARK_DRIVER",
        start_time_ms=0, end_time_ms=2000,
    )
    assert [item.event_id for item in result.events] == ["e1"]
    assert result.bytes_read == len("ERROR café".encode("utf-8"))
    assert stub.filter_log_events.await_count == 2
    assert stub.closed
    for call in stub.filter_log_events.await_args_list:
        assert call.kwargs["endTime"] == 2000
        assert call.kwargs["logStreamNames"] == [
            "/applications/a/jobs/r/SPARK_DRIVER"
        ]
        assert "filterPattern" not in call.kwargs
```

- [ ] Add safety tests with monkeypatched small constants plus at least one
  production-boundary assertion for every limit in spec section 4.4. Test exact
  final page/event/UTF-8 byte budgets, one over, duplicate-heavy pages, multi-byte
  Unicode, giant event, metadata sizes, and many rejected discovery names. Assert
  page 101 is never requested. A terminal empty page at page 100 succeeds.
  Stream names permit 512 Unicode codepoints and up to 2048 UTF-8 bytes: accept a
  valid run-qualified 512-codepoint multibyte name, reject 513 codepoints and
  lone surrogates. Do not incorrectly cap valid names at 512 encoded bytes.
- [ ] Test repeated token twice and cycle A→B→A for both operations; assert exact
  call counts and `ProviderError`. Test a token-free empty page as successful EOF.
- [ ] Test missing event id, wrong returned stream, out-of-window event, bad
  timestamps/message types, invalid UTF-8 and malformed response containers;
  errors contain only fixed copy. Test that distinct ids with the same content
  survive and repeated ids within a response are suppressed only after budgeting.
- [ ] Pin inclusive window boundaries in provider and fake tests: with
  start=1000/end=2000, timestamps 1000 and 2000 are accepted, while 999 and 2001
  are excluded by the fake and treated as invalid provider responses by the
  real reader. The installed FilterLogEvents model excludes only timestamps
  before start/later than end; GetLogEvents has a different end boundary.
- [ ] Run `uv run --locked pytest tests/unit/domain/test_emr_cloudwatch_logs.py -q`
  and observe the new tests fail on the missing module/methods before coding.

### 2.3. Implement bounded discovery/read and the safe boundary

- [ ] Add the records, constants and run-relative classifier. Implement exact
  prefix validation from spec section 4.2; count every returned record before
  filtering. Return a sorted immutable tuple, not a partial result at a limit.
- [ ] Implement the event pagination loop with this invariant order:

```python
# Inside one 30-second timeout and one async client context:
events = []
seen_ids = set()
seen_tokens = set()
next_token = None
page_count = returned_count = bytes_read = 0
while True:
    if page_count == MAX_EVENT_PAGES:
        raise ProviderError("CloudWatch log page limit exceeded")
    kwargs = dict(logGroupName=log_group_name,
                  logStreamNames=[stream_name], startTime=start_time_ms,
                  endTime=end_time_ms, limit=1000)
    if next_token is not None:
        kwargs["nextToken"] = next_token
    response = await client.filter_log_events(**kwargs)
    page_count += 1
    # Validate response/list/event fields without including their values in errors.
    for raw in response.get("events", []):
        returned_count += 1
        message_bytes = len(raw["message"].encode("utf-8"))
        bytes_read += message_bytes
        if (returned_count > MAX_EVENTS or bytes_read > MAX_READ_BYTES
                or message_bytes > MAX_EVENT_BYTES):
            raise ProviderError("CloudWatch log read limit exceeded")
        if raw["eventId"] not in seen_ids:
            seen_ids.add(raw["eventId"])
            events.append(CloudWatchLogEvent(
                raw["eventId"], raw["timestamp"], raw["ingestionTime"],
                raw["message"],
            ))
    next_token = response.get("nextToken")
    if next_token is None:
        break
    # Validate nonempty string and 8192-byte token cap before retaining.
    if next_token in seen_tokens:
        raise ProviderError("CloudWatch repeated a continuation token")
    seen_tokens.add(next_token)
return CloudWatchLogSnapshot(tuple(events), bytes_read, end_time_ms)
```

`MAX_EVENT_PAGES=100`, `MAX_EVENTS=10_000`, `MAX_READ_BYTES=8*1024*1024`,
`MAX_EVENT_BYTES=1024*1024`; corresponding discovery constants are 100 pages
and 200 returned records. Validation precedes the shown budget/append code and
implements the exact list of malformed cases in step 2.2. The discovery loop
uses the same token and page guard order. The 30-second deadline wraps client
entry, all pages and normal exit. Requests freeze start/end across pages.

- [ ] Implement a module-local safe exception converter. Its classification
  calls `map_boto_error(error)` for SDK exceptions, or uses an already typed
  `ProviderError`; it emits only a fresh whitelisted class and fixed message.
  Keep cap/token errors distinguishable by fixed application copy. Ensure all
  raises occur after leaving the original exception handler:

```python
failure = None
try:
    result = await bounded_operation()
except asyncio.CancelledError:
    raise
except Exception as error:
    classified = error if isinstance(error, ProviderError) else map_boto_error(error)
    failure = safe_cloudwatch_error(classified)
if failure is not None:
    raise failure from None
return result
```

Here `bounded_operation` is the domain function's private operation closure;
`safe_cloudwatch_error` is the new module-local helper with a fixed table for
`AuthRequiredError`, `PermissionDeniedError`, `ThrottledError`,
`ProviderUnreachableError`, `NotFoundError`, `ValidationError` and `ProviderError`.
Timeout maps to unreachable. Do not attach the original exception to the new one.
Wrap SDK context cleanup too; cancellation must close the client, and a cleanup
exception must be converted before any owner logger can observe it.

- [ ] Test SDK auth/access/throttling/service-unavailable/invalid-parameter
  codes and missing credentials/network failures. Add needed CloudWatch codes
  to the existing shared map, preserving all existing mappings and tests.
- [ ] Add adversarial sentinel assertions against `str`, `repr`,
  `traceback.format_exception`, `__cause__` and `__context__`; include unknown
  exceptions and context-exit failures after successful reads and cancellation.
  Assert event/snapshot repr excludes the message sentinel.
- [ ] Run the provider tests; all request, budget, error and cleanup cases pass.

### 2.4. Facade, demo and model-contract completion

- [ ] Add the protocol and delegates; type VM/service input annotations against
  it without changing behavior. Complete `_FailedEmrLogsClient` with safe fresh
  failures from both CloudWatch methods. Test session creation failures still
  construct a readable typed failure surface and never touch boto from the VM.
- [ ] Add group/stream-keyed in-memory event storage and the seed/append helpers.
  Implement deterministic discovery, time-window reads, ids, exact UTF-8 budgets
  and missing group/stream errors. Preserve the source fake's profile isolation,
  existing call-log bounds and S3 methods. Test no body enters call observations.
- [ ] Verify the current locked model locally:

```bash
uv run --locked python - <<'PY'
import botocore.session
model = botocore.session.get_session().get_service_model("logs")
required = {
    "DescribeLogStreams": {"logGroupName", "logStreamNamePrefix", "orderBy", "limit", "nextToken"},
    "FilterLogEvents": {"logGroupName", "logStreamNames", "startTime", "endTime", "limit", "nextToken"},
}
for name, fields in required.items():
    assert fields <= model.operation_model(name).input_shape.members.keys()
print("CloudWatch request fields supported")
PY
```

- [ ] Add `logs: {DescribeLogStreams, FilterLogEvents}` to the modeled operation
  ledger/test, point `_OPERATION_SOURCES["logs"]` at the new module, and specify
  the consumed request members above. Document EOF/token and time/budget behavior
  alongside the API model version. Do not add unused operations or new SDK fields.
- [ ] Run the Task 1 gate:

```bash
uv run --locked pytest tests/unit/domain/test_emr_serverless.py tests/unit/domain/test_emr_logs.py tests/unit/domain/test_emr_cloudwatch_logs.py tests/unit/services/test_emr_serverless_service.py tests/unit/demo/test_in_memory_emr_cloudwatch.py tests/unit/demo/test_provider_parity.py tests/unit/vm/emr_serverless/test_clone_vm.py tests/docs/test_contract_parity.py -q
uv run --locked ruff check src/aws_tui/domain src/aws_tui/services/emr_serverless src/aws_tui/demo tests/unit/domain tests/unit/services/test_emr_serverless_service.py tests/unit/demo
uv run --locked mypy
./scripts/check-layers.sh
```

Expected: all selected tests pass, mypy and ruff report no errors, architecture
check passes. Inspect the diff against original assertions. Record the results
for the task review before starting Task 2; do not hide pre-existing failures.
Commit only under the enclosing ticket workflow's authorization, not as part of
the architecture assignment. Suggested implementation commit: `feat(emr): add bounded CloudWatch log reader`.

## 3. Task 2: Integrated source selection, follow, recovery and demo journey

**Deliverable:** All ten ACs work through the real EMR pane, with source/stream
selection, bounded local filtering, cancellable follow, typed safe errors,
credential replacement, documentation and guarded snapshots.

**Files to modify:**

- `src/aws_tui/vm/emr_serverless/job_run_logs_vm.py`
- `src/aws_tui/vm/emr_serverless/page_vm.py`
- `src/aws_tui/ui/widgets/emr_serverless/job_run_logs_pane.py`
- `src/aws_tui/ui/widgets/emr_serverless/page.py`
- `src/aws_tui/app.py`
- `src/aws_tui/infra/keymap_store.py`
- `src/aws_tui/vm/chrome/action_catalog.py`
- `src/aws_tui/vm/chrome/hint_legend_vm.py`
- `src/aws_tui/demo/seeds.py`
- `tests/unit/vm/emr_serverless/test_job_run_logs_vm.py`
- `tests/unit/vm/emr_serverless/test_page_vm.py`
- `tests/unit/ui/emr_serverless/test_job_run_logs_pane.py`
- `tests/unit/ui/emr_serverless/test_page_focus.py`
- `tests/unit/infra/test_keymap_store.py`
- `tests/unit/vm/chrome/test_action_catalog.py`, `tests/unit/vm/chrome/test_hint_legend.py`
- `tests/unit/demo/test_seeds.py`, `tests/integration/test_emr_page.py`
- `tests/integration/test_swap_source_recovery.py`
- `tests/snapshot/apps/emr_logs.py`, `tests/snapshot/test_emr_logs.py` and their reviewed goldens
- `docs/services/emr-serverless.md`, `docs/keybindings.md`, generated site/wiki outputs as required by the docs tooling

**Files to create:** `tests/integration/test_emr_cloudwatch_follow.py`.

**Consumes:** Task 1 protocol and records exactly as declared in section 2;
the current `OperationOwner`, value-free VM notifications, `LogFilter` and S3
load path. The injected facade already fixes connection/region identity.

**Produces:** The expanded `set_target` and selector methods from design section
3.2; `async follow() -> None`, `stop_follow() -> None`; source/freshness/stream
properties in design sections 3.2 and 5.1; expanded EMR-only recovery snapshot;
focused registered `emr.logs.source` and `emr.logs.follow` commands.

### 3.1. VM source and load cycle

- [ ] Add tests using Task 1's `InMemoryEmr` and current VM hub fixture:
  CloudWatch-only target → IDLE → READY with a seeded ERROR; both configured
  defaults S3; disabled CloudWatch makes zero CloudWatch calls; metadata pending
  is unknown; denied CloudWatch leaves S3 available; missing group/stream gives
  NO_FILES, then seeded retry reaches READY. Assert literal source states.
- [ ] Add exact stream selection tests for driver, two same-component worker
  suffixes and attempts 1/2. A fresh listing must not silently replace an explicitly
  restored credential target; ordinary user reload may clear a vanished selection
  into NO_FILES. Add custom-prefix cases produced by the provider tests.
- [ ] Run `uv run --locked pytest tests/unit/vm/emr_serverless/test_job_run_logs_vm.py -q`
  and confirm only new cases fail before implementation.
- [ ] Add source enums/status records and the extended target. Introduce one
  monotonic generation and reuse the existing owner for cancellation. Implement
  invalidation using this ordering, applied to target/source/stream/config and
  load/follow control changes:

```python
def _invalidate(self):
    self._generation += 1
    self._operations.cancel()
    self._following = False
    return self._generation

def _current(self, generation):
    return not self._disposed and generation == self._generation
```

The actual helpers are typed. Do not call `_invalidate` from inside the owned
operation being cancelled: public mutators reserve the generation before
dispatching the operation. Stop the old run at the beginning of page selection,
before awaiting application/job/detail reads. Equal target metadata is a no-op.

- [ ] Branch `load` by selected source. S3 retains its parser/discovery/stream
  algorithm and cache key behavior, plus generation checks. CloudWatch discovers
  and reads one bounded snapshot through the facade and only then commits new
  events, source state and freshness. Capture current config/group and exact
  stream before every read; never read by a component label. Clear CloudWatch
  buffer/dedup on identity change; no CloudWatch LRU is introduced.
- [ ] Add CloudWatch projection from a deque of raw events. Enforce 5,000 events
  and 4 MiB raw UTF-8, then incrementally iterate message lines and retain at most
  5,000 display lines / 4 MiB including separators. Never use a whole-message
  `splitlines()` list for a newline-heavy 1 MiB input. Count matches against the
  current retained raw events and expose `buffer_capped` separately from S3's
  provider truncation. Filter/reset immediately reproject without any AWS call.
- [ ] Test regex OR/case/passthrough, multiline/empty content, arbitrary Rich
  brackets, Unicode byte boundaries, millions of newline separators, oldest
  event eviction, no-call filter edits, and exact event/display limits. Rerun
  existing S3 cap/cache/stream/filter tests without deleting assertions.

### 3.2. Follow ownership and failure tests

- [ ] Build controllable blocked-read and clock fixtures using `asyncio.Event`
  and injected/monkeypatched module clock/sleep helpers; avoid real two-second
  sleeps. Test start, stop during wait, stop during provider read, double start,
  empty successful poll freshness, missing discovery then appearance, disappearing
  selected stream, error stop and explicit restart. Test two distinct ids with
  equal messages/timestamps and a repeated id across page/window boundaries.
- [ ] Implement the follow loop as an awaited operation with a captured
  generation. Its algorithm is:

```python
# First discover/read, then initialize IDs from the full successful snapshot.
# last_end is the end of that successful read, not its largest event timestamp.
while self._current(generation) and self._following:
    await sleep(2.0)
    if not self._current(generation):
        return
    start = max(run_created_at_ms, last_end - 60_000)
    end = max(last_end, now_ms())
    snapshot = await client.read_cloudwatch_events(
        log_group_name=group, stream_name=stream_name,
        start_time_ms=start, end_time_ms=end,
    )
    if not self._current(generation):
        return
    retained_ids = {key: timestamp for key, timestamp in seen.items()
                    if timestamp >= start}
    incoming = [event for event in snapshot.events
                if event.event_id not in retained_ids]
    for event in incoming:
        retained_ids[event.event_id] = event.timestamp_ms
    if len(retained_ids) > 20_000:
        raise ProviderError("CloudWatch follow duplicate-id limit exceeded")
    # Atomically merge/sort/trim events, reproject current filter, then commit IDs.
    seen = retained_ids
    last_end = end
    # Commit both last-successful-read time and last-event timestamp if present.
```

The loop starts only for enabled CloudWatch. Initial missing discovery repeats
discovery at the same cadence until a stream appears; after selection it polls
only that exact stream. The loop has no detached task and no overlap. The
snapshot/merge preparation is bounded and must fail before replacing the last
complete buffer. A failed poll never advances `last_end` or freshness.
- [ ] Test dedup exactly 20,000 and overflow, pruning only timestamps older than
  the overlap start, late events within/before the overlap, wall-clock rollback,
  repeated token/page cap while following, and last complete data visible on error.
  Include an event exactly at the previous end: returning it in both inclusive
  overlapping windows renders it once, while a new distinct id at that timestamp
  renders separately. The fake uses the same inclusive endpoint comparisons.
- [ ] Test run/source/stream/config A→B→A with an old provider that suppresses
  cancellation before returning. Assert no old lines, source states, freshness,
  cache writes, diagnostics or old `finally` writes reach the new generation.
- [ ] Sanitize every CloudWatch failure again at the VM's defensive boundary.
  Keep the typed taxonomy for recovery and expose a fixed failure category for
  throttling. Unexpected diagnostics must receive a fresh never-raised
  `ProviderError("CloudWatch log read failed")`. Test body sentinels in normal
  events, raw injected errors, chained errors and failed client cleanup against
  actual `ServiceOperationFailedMessage` fields and captured logger records.
- [ ] Run the VM suite and provider cleanup tests; verify owner tasks drain and
  cancellation propagates to the caller without leaving LOADING/following stuck.

### 3.3. Credential recovery and exact identity

- [ ] Add defaulted `log_source`, `cloudwatch_stream_name`, and
  `cloudwatch_configuration` fields to `EmrCredentialRecoverySnapshot` only.
  This schema expansion is necessary because an S3 file key cannot identify a
  CloudWatch group/stream; it is not a relaxation of snapshot guards. Existing
  S3 positional construction remains valid. No Athena schema changes.
- [ ] Export identity/filter only; rebuild the candidate with its new service
  facade, restore run and refreshed config, require equal effective group/prefix/
  enable identity, choose the exact source/stream, and perform one uncached read.
  Following is false. No data/token/cache moves across clients.
- [ ] Extend `tests/integration/test_swap_source_recovery.py` with CloudWatch
  auth failure → replaced credentials → exact attempt restored; denied/throttled
  typed failure; absent stream/config drift rejects candidate; same run name
  under another connection does not share events. Assert old VM owner drains,
  failed candidate never commits, and repeated recovery never resumes follow.
- [ ] Run existing EMR page and recovery tests plus all five Athena snapshot
  schema guards unchanged. Check both failed and successful candidate paths.

### 3.4. Pane controls and registered commands

- [ ] Add pane tests for the four source labels, CloudWatch-only READY content,
  source/stream click messages, component/attempt labels, literal body rendering,
  the exact caption `filter: loaded data only`, freshness, and visible start/stop.
  Include 80×24 pane-in-page tests that actually activate the controls.
- [ ] Implement one horizontally scrollable source row and keep the existing
  file/stream row, body widget and status row. Reuse left/right for exact stream
  choices. Hide optional pattern text before the loaded-data caption or controls.
  A follow poll keeps the body visible; failures render a fixed banner with the
  last complete buffer. Preserve existing S3 error/truncation copy and guards.
- [ ] Register `emr.logs.source` (`ctrl+s`) and `emr.logs.follow` (`ctrl+l`) in
  KeymapStore, app action registration/routing, catalog and hint labels. These
  keys are unused in the baseline default map. Add focused page routes using
  the same boolean handled convention as `open_focused_log_filter`:

```python
# App registrations:
self._actions.register("emr.logs.source", self.action_cycle_emr_log_source)
self._actions.register("emr.logs.follow", self.action_toggle_emr_log_follow)
# Keymap entries:
"emr.logs.source": ("ctrl+s",),
"emr.logs.follow": ("ctrl+l",),
```

Implement the named app methods and matching focused page methods; gate the
catalog with `focus_required` outside logs and `selection_required` when start
is unavailable. Stop remains available while a read is pending. Pane messages
route through the same methods, so clicks/key/palette share availability.
Source cycling changes log destinations, not the connection. Resolved custom
keys drive hints; existing collision validation remains intact.
- [ ] Page source/stream/reload handlers stop and invalidate the old operation
  before scheduling the next `emr-logs` lifecycle worker. Toggle start awaits
  `vm.follow`; toggle stop calls `vm.stop_follow` and cancels/drains its worker.
  Filter modal apply/reset for CloudWatch calls only `set_filter`; S3 retains
  its existing load worker. Preserve page unmount, source replacement and
  operation-owner shutdown contracts.
- [ ] Test default and rebound keys, disabled entries, mouse hints and palette
  invocation from logs, no invocation from another service, and Tab/Shift+Tab/
  arrows at compact size. Rerun clone and existing focus tests.

### 3.5. Real demo journey and snapshots

- [ ] Add profile-isolated seeds: an accessible CloudWatch-only run with known
  `ERROR CloudWatch demo failure`, a retry-attempt driver stream and another
  worker; a both-sources run; leave the first existing S3 seed stable. Assert
  two demo profiles cannot see each other's streams/events.
- [ ] Create the new full-app test using `AwsTuiApp` and the existing demo
  composition fixtures. Patch real AWS session/client creation to raise an
  assertion. Run `async with app.run_test(size=(80, 24)) as pilot`, navigate to
  EMR and the CloudWatch-only run, focus logs and press Enter, assert READY and
  seeded rendered content, choose attempt, edit filter, start follow, append
  one event, verify it appears once with new freshness, and stop explicitly.
- [ ] In a second real Pilot case start following a blocked read, navigate to a
  different run/service, and drain dynamically scheduled workers exactly as
  required by AC7:

```python
async with asyncio.timeout(5):
    while app.workers._workers:
        await app.workers.wait_for_complete(list(app.workers._workers))
await pilot.pause()
assert not old_logs.following
assert not old_logs._operations.tasks
assert blocked_client.closed
assert "old-run-sentinel" not in new_logs.lines
```

Retain handles before navigation. A late provider completion must not mutate
the replacement pane. Test cleanup that awaits an event, not only immediate
cancellation. This running app is the required demo exercise, not direct fake
method calls masquerading as a journey.
- [ ] Add CloudWatch-only READY, both-source, missing-stream, denied and follow
  snapshot apps at 120×40; add a full-page compact case at 80×24 in Carbon and
  GitHub Light. Parameterize the isolated READY case over existing `THEMES`.
  Add positive content guards for the seeded body/source/caption/freshness and
  expected controls. Keep every prior content-presence guard intact.
- [ ] Generate only the affected goldens using the relevant node ids with
  `--snapshot-update`; visually inspect changed SVGs/screenshots. Then rerun
  `uv run --locked pytest tests/snapshot/test_emr_logs.py tests/snapshot/test_emr.py -q`
  without the update flag. Do not accept blank/parity-only results or unrelated
  golden drift.

### 3.6. User documentation and final local gate

- [ ] Update numbered service docs with this read-only configuration example,
  source state descriptions and the source/filter/follow interaction:

```json
{
  "monitoringConfiguration": {
    "cloudWatchLoggingConfiguration": {
      "enabled": true,
      "logGroupName": "/example/emr",
      "logStreamNamePrefix": "example"
    }
  }
}
```

Document `enabled` and default group/run stream names; distinguish the custom
prefix discovery rule from a documented AWS join convention. State that logs
must already be configured, that not-created streams are retryable, that local
filtering covers only retained loaded data, and give exact read/buffer/follow
limits and the 60-second late-arrival limitation. Include explicit stop and
credential-recovery behavior.
- [ ] Document the reader's `logs:DescribeLogStreams` and `logs:FilterLogEvents`
  permissions scoped to the configured group, plus existing EMR read permissions.
  Separately link runtime logging-writer permissions in the AWS guide; do not
  tell reader identities to grant write/create/unmask access. Add new command
  bindings to keybindings and describe custom-key conflict behavior.
- [ ] Run local docs regeneration and checks, preserving numbered headings:

```bash
uv run --locked python -m scripts.docs.build_docs --site --wiki
uv run --locked python -m scripts.docs.check_docs
uv run --locked pytest tests/docs -q
uv run --locked mkdocs build --strict
```

Use `make docs-check` for hero/diagram checks under its configured Cairo
environment; do not install dependencies merely to bypass a tooling failure.
Inspect generated diffs and include only changes caused by the canonical docs.
No wiki/site push or hosted build is authorized by this plan.
- [ ] Task 2 implementer runs the focused handoff checks below after its feature
  tests and reviewed snapshots pass, then makes its normal implementation commit
  under the enclosing workflow authorization:

```bash
uv run --locked pytest tests/unit/vm/emr_serverless tests/unit/ui/emr_serverless tests/unit/infra/test_keymap_store.py tests/unit/vm/chrome/test_action_catalog.py tests/unit/vm/chrome/test_hint_legend.py tests/unit/demo/test_seeds.py tests/integration/test_emr_page.py tests/integration/test_emr_cloudwatch_follow.py tests/integration/test_swap_source_recovery.py tests/snapshot/test_emr_logs.py tests/snapshot/test_emr.py -q
git diff --check
```

- [ ] Root, after task/spec/code review fixes are committed, owns one broad local
  gate on the final reviewed clean HEAD. Task 2 reports its scoped evidence and
  does not also run this unchanged whole-suite gate. Root's prepared local runner
  may execute these equivalent commands and capture the results:

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
./scripts/check-layers.sh
uv run --locked pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml
uv run --locked pytest tests/snapshot -q
uv run --locked pytest tests/e2e -q
git diff --check
```

Expected from root's final gate: passing tests, coverage at least the unchanged 70% floor, no lint/type/
architecture/doc findings, reviewed nonblank snapshots, and no stray worker
warnings. External testcontainer integration is unrelated to this feature;
retain it unchanged and run only if the enclosing local verification policy
requires that tier. Do not dispatch any hosted workflow to replace local work.
- [ ] Review the diff against the saved baseline assertion/snapshot/schema
  evidence; verify only the explicitly planned EMR schema expansion changed.
Record every AC's concrete test/result in the handoff, distinguishing implementer
scoped results from root-owned final verification. Suggested implementation
  commit after the enclosing workflow review: `feat(emr): integrate CloudWatch log sources and follow`.
- [ ] The eventual PR description links
  `docs/services/emr-serverless.md` in the PR branch, names the two read-only Logs
  operations, reports local validation, and describes custom-layout/late-arrival
  limits. No claim of live AWS validation or CI execution is made.

## 4. Plan self-review and handoff

All ten ACs map to tasks in the canonical spec's section 9. Task 1 produces the
metadata/provider/fake interfaces consumed verbatim by Task 2. Task 2 owns the
entire user-visible journey and its recovery/lifecycle tests; no split task can
ship a half-wired follow control. No extra log framework or S3 reader rewrite is
needed. The only unresolved AWS fact is custom-prefix composition; literal
bounded discovery and conservative run validation are the defined behavior.

Implementation readiness checks: review the spec and task interface blocks;
preserve existing test assertions; confirm branch/base and clean ownership;
execute Task 1, review it, then execute Task 2. The owner already authorized
routine execution choices, so do not introduce a new approval pause.
