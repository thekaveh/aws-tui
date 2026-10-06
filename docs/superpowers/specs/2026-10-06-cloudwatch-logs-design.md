# EMR Serverless CloudWatch logs design

Issue: [#261](https://github.com/thekaveh/aws-tui/issues/261). Base:
`13b7475fed83528f2395149d8b0fa277b07e2de1`; branch
`codex/issue-261-cloudwatch-logs`. This is the canonical implementation contract.

## 1. Outcome and constraints

An EMR run configured for CloudWatch Logs can load its logs in the existing
logs pane. Users can choose S3 or CloudWatch, choose an exact component/attempt
stream, filter loaded CloudWatch data, and deliberately start or stop following.
The selected connection supplies credentials and region for every request.

Preserve existing S3 discovery, streaming, filtering, response caching, clone
configuration fidelity, credential recovery, pane layout, and focus navigation.
No new dependencies. Python remains `>=3.11,<3.14`, Textual `==8.2.8`, and VMx
`>=3.23.0,<4.0.0`. All applicable checks run locally. Do not invoke hosted CI or
Actions, weaken assertions/snapshot guards, lower the 70% coverage floor, or
relax architecture gates. No AWS mutations, releases, or logging-configuration
changes belong to this feature. Logs Insights, account-wide search, Spark/Tez
dashboards (#262), and enabling logging are outside scope.

## 2. Chosen architecture

### 2.1. Alternatives

1. Extend the current facade with CloudWatch methods and the existing VM/pane
   with source-specific state. Recommended: preserves S3's tested reader and
   shares the actual UI/filter/lifecycle seams.
2. Normalize all S3 files and CloudWatch streams into a new general log-provider
   framework. Rejected: the gzip streaming and event-window APIs differ enough
   that this would refactor working S3 code without benefiting this ticket.
3. Add a separate CloudWatch page. Rejected: duplicates run selection, filters,
   recovery, and navigation, and fails the requested second-source pane.

### 2.2. File responsibilities

| File under `src/aws_tui/` | Responsibility |
| --- | --- |
| `domain/emr_serverless.py` | Immutable CloudWatch monitoring metadata on `JobRunDetail`; shared AWS error classification. |
| New `domain/emr_cloudwatch_logs.py` | CloudWatch records, strict stream classification, bounded discovery/read, safe error conversion. |
| `domain/emr_logs.py` | Preserve S3 functions; add CloudWatch delegates and an explicit facade protocol. |
| `services/emr_serverless/service.py` | Same connection-owned facade; failed-client implementation supports both sources. |
| `vm/emr_serverless/job_run_logs_vm.py` | Source state, generation ownership, CloudWatch buffer/filter/follow; existing S3 branch remains. |
| `vm/emr_serverless/page_vm.py` | Detail-to-logs target synchronization and exact credential-recovery snapshot. |
| `ui/widgets/emr_serverless/job_run_logs_pane.py` | Literal source/stream selectors, body, loaded-data caption, freshness and start/stop controls. |
| `ui/widgets/emr_serverless/page.py` | Lifecycle workers and focused command routing. |
| `app.py`, `infra/keymap_store.py`, `vm/chrome/action_catalog.py`, `vm/chrome/hint_legend_vm.py` | Registered, rebindable source/follow commands and availability. |
| `demo/in_memory_emr.py`, `demo/seeds.py` | Deterministic network-free source metadata, discovered streams and event windows. |

Views perform no AWS operations; VM code receives no session or boto client.
`EmrServerlessLogsClient` owns the session/region/config and delegates CloudWatch
work to the new domain module. The service factory remains the composition seam.

## 3. Metadata and source selection

### 3.1. Domain records

Add `CloudWatchLogConfiguration` in `domain/emr_serverless.py`, before
`JobRunDetail`, and append the defaulted detail field after existing fields:

```python
@dataclass(frozen=True, slots=True)
class CloudWatchLogConfiguration:
    enabled: bool | None
    log_group_name: str | None = None
    log_stream_name_prefix: str | None = None

# JobRunDetail addition; keep existing positional field order intact:
cloudwatch_monitoring: CloudWatchLogConfiguration | None = None
```

Parse `configurationOverrides.monitoringConfiguration.cloudWatchLoggingConfiguration`
alongside the existing S3 URI. Absent block is `None`; an explicit boolean is
preserved; a present block with missing/non-boolean `enabled` produces
`enabled=None` (unknown, not enabled). Validate optional names as nonempty strings
when supplied; malformed values produce an unknown configuration, never an AWS
request or an interpolated error containing the value. Preserve the untouched
deep-copied overrides for clone behavior. Production parsing and demo clone
materialization use the same small parser function:
`parse_cloudwatch_monitoring(overrides: dict[str, Any] | None) -> CloudWatchLogConfiguration | None`.

When enabled and the group is omitted, the effective group is
`/aws/emr-serverless`. An omitted stream prefix uses the documented run root.
Disabled configurations do not call CloudWatch even if names are present.
These defaults and the enable flag follow the [EMR logging guide](https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/logging.html).

### 3.2. VM source contract

Define `LogSource` (`S3`, `CLOUDWATCH`), `LogSourceState` (`CONFIGURED`,
`UNAVAILABLE`, `UNKNOWN`, `ACCESS_DENIED`), and frozen `LogSourceStatus(source,
state, detail)` in the logs VM. `detail` is fixed application copy, never a
provider message. Expose `sources: tuple[LogSourceStatus, ...]` in S3/CloudWatch
order, and `selected_source: LogSource | None`.

Always show both source labels when a run is selected; distinguish an absent or
disabled source with `unavailable · not configured` or `unavailable · disabled`.
This also makes every configured source and its current state visible.

| Observation | Source state | Pane behavior |
| --- | --- | --- |
| Detail is still pending/failed, or CloudWatch enable metadata malformed | unknown | No assertion that logging is absent; retry detail. |
| Explicit configuration known and enabled | configured | Selectable; Enter loads. |
| Successful discovery/read | configured | READY, including an empty existing stream. |
| Group absent, no matching stream yet, or stream removed before read | unavailable | NO_FILES, retryable with Enter/r. |
| Permission denied | access denied | ERROR; fixed permission guidance; other source remains selectable. |
| Auth, throttling, transport or unknown read failure | unknown | ERROR plus typed recovery state and fixed reason. |
| Metadata confirms no S3 URI and no enabled/unknown CloudWatch configuration | unavailable | NO_LOG_CONFIG. |

Initial default is S3 when configured, otherwise enabled CloudWatch. Selecting a
different source is explicit and never triggered by the other source's failure.
An unchanged detail poll preserves source selection, loaded data and following.
Changed configuration invalidates the current read and resets the affected
state. A fresh target defaults again; metadata pending is not NO_LOG_CONFIG.

Extend, retaining compatibility for existing S3 callers:

```python
def set_target(
    self, app_id: str | None, run_id: str | None, log_uri: str | None,
    *, cloudwatch: CloudWatchLogConfiguration | None = None,
    run_created_at_ms: int = 0, metadata_known: bool = True,
) -> None: ...

def select_source(self, source: LogSource) -> None: ...
def select_cloudwatch_stream(self, name: str) -> None: ...
```

`metadata_known=False` is passed during a page run switch before detail refresh;
the default `True` preserves existing direct S3 tests/callers. On successful
detail refresh pass its creation time in UTC milliseconds and its parsed config.
Do not silently query application defaults or guess an unreported destination.

## 4. CloudWatch provider contract

### 4.1. Types and signatures

Records live in `domain/emr_cloudwatch_logs.py`:

```python
@dataclass(frozen=True, slots=True)
class CloudWatchLogStream:
    name: str
    component: str
    attempt: int | None

@dataclass(frozen=True, slots=True)
class CloudWatchLogEvent:
    event_id: str
    timestamp_ms: int
    ingestion_time_ms: int
    message: str = field(repr=False)

@dataclass(frozen=True, slots=True)
class CloudWatchLogSnapshot:
    events: tuple[CloudWatchLogEvent, ...] = field(repr=False)
    bytes_read: int
    end_time_ms: int

async def list_cloudwatch_streams(
    *, session: aioboto3.Session, region_name: str | None,
    configuration: CloudWatchLogConfiguration, application_id: str,
    job_run_id: str, boto_config: BotoConfig | None = None,
) -> tuple[CloudWatchLogStream, ...]: ...

async def read_cloudwatch_events(
    *, session: aioboto3.Session, region_name: str | None,
    log_group_name: str, stream_name: str,
    start_time_ms: int, end_time_ms: int,
    boto_config: BotoConfig | None = None,
) -> CloudWatchLogSnapshot: ...
```

Add `list_cloudwatch_streams(configuration, application_id, job_run_id)` and
`read_cloudwatch_events(log_group_name, stream_name, start_time_ms, end_time_ms)`
as keyword-only facade methods with these return types, delegating session,
region and config internally. `EmrServerlessLogsClientProtocol` in `emr_logs.py`
contains existing `list_files`/`stream` signatures and these two methods. Use it
for VM/service factory annotations, replacing concrete casts where appropriate.
The failed-client and demo facade implement the complete protocol. No client
construction or botocore config moves above the domain/service layers.

### 4.2. Discovery and exact run ownership

Open `session.client("logs", region_name=region_name, config=boto_config)`.
Never infer a region from a group name or use a different region after failure.
Request `DescribeLogStreams` with the exact effective `logGroupName`,
`logStreamNamePrefix`, `orderBy="LogStreamName"`, and `limit=50`. Omit a token
on the first request; propagate it on subsequent requests. Do not use
`LastEventTime` ordering with a prefix. [DescribeLogStreams API](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_DescribeLogStreams.html).

Without a custom prefix, the discovery prefix is
`/applications/{application_id}/jobs/{job_run_id}/`, including the final slash
so run `r` cannot match `r2`. Accept only names beginning with that exact root.
Parse only the run-relative suffix: optional `attempts/<positive integer>/`,
then a worker/component segment. Keep any further suffix verbatim in `name`,
including worker instances and log-type suffixes. Prefer driver components,
then other components, ascending attempt (`None` first), then exact name.
An unfamiliar nonempty component is selectable using its literal suffix; never
invent stdout/stderr choices when AWS exposes a single combined stream.

For a custom prefix, preserve it byte-for-byte and use it as the API's listing
prefix. AWS's guide does not specify the concatenation rule. Therefore do not
construct guessed complete stream names: strip only the exact configured prefix
from each returned name, then find the exact selected-run root at a path boundary
in the remaining suffix. Accept only one such root and parse after it. Also
accept the root without its initial slash only when it starts that suffix (the
literal configured prefix may already end in `/`). Do not use markers inside
the configured prefix to establish run identity. Reject other app/run roots,
ambiguous repeated selected-run roots, and empty/malformed suffixes. This is a
conservative application rule, not a claim about AWS prefix composition. Unknown
layouts produce retryable empty discovery and fixed guidance to inspect the
configured CloudWatch stream names; never widen into account-wide discovery.

The stream's identity is its full name within the effective group, selected
connection, app and run. Component/attempt labels are presentation only. The
selector must preserve two equal component labels with different exact names.

### 4.3. Snapshot read and pagination

Use `FilterLogEvents` with `logGroupName`, `logStreamNames=[stream_name]`,
`startTime`, `endTime`, `limit=1000`, and optional `nextToken`. Do not send
`filterPattern`, `logStreamNamePrefix`, `unmask`, or newer SDK-dependent options.
Freeze the end time once before the first page. Initial/manual read starts at
the selected job's creation timestamp (zero only for legacy direct callers).
All pages use the same endpoints and interval; validate returned stream names
and timestamp range before accepting events.

The interval is inclusive at both endpoints: accept
`start_time_ms <= timestamp_ms <= end_time_ms`. Both the installed model and
the FilterLogEvents API say timestamps before start or later than end are
excluded; do not import GetLogEvents' different end-exclusive wording. Events
exactly at the previous end may appear in the next overlapping read and must
be deduplicated by id. Tests pin both equality cases and one millisecond outside.

Absent `nextToken` is EOF; empty pages with a token continue. Reject a repeated
token, including a cycle A→B→A, with a fixed `ProviderError`. This meets AC4
without misclassifying the normal same-forward-token EOF of `GetLogEvents`.
[FilterLogEvents API](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html),
[GetLogEvents API](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_GetLogEvents.html).

Accumulate one bounded snapshot and publish it atomically after EOF. Count all
returned events and message UTF-8 bytes before duplicate suppression or local
filtering; duplicates cannot bypass budgets. Validate event id, timestamps,
stream identity and message type without embedding response data in errors.
An empty/missing required event id is a validation failure, not a body-based
deduplication hash. Preserve provider event order within a snapshot. Deduplicate
identical ids, keeping their first event. Do not deduplicate by text/timestamp.

### 4.4. Exact budgets

These are application limits, not assertions about AWS service quotas.

| Limit | Value | Boundary behavior |
| --- | --- | --- |
| Discovery pages | 100 total | Page 100 without token succeeds; token requiring page 101 raises before calling it. |
| Discovery returned records | 200 including rejected/duplicate names | Record 201 raises; never silently hide a partial listing. |
| Event pages per read | 100 | Same exact-page/continuation rule. |
| Returned events per read | 10,000 including duplicates | Event 10,001 raises. |
| Message UTF-8 bytes per read | 8 × 1024 × 1024 | Exact cap succeeds at EOF; first excess byte raises. |
| One event message | 1 × 1024 × 1024 UTF-8 bytes | Oversized event raises before retention. |
| Stream name | 512 Unicode codepoints and 2048 UTF-8 bytes | Accept valid multibyte names at the service's character limit; reject surrogates/overlong names. |
| Event id / token | 1024 / 8192 UTF-8 bytes | Longer metadata raises; token sets remain bounded by page cap. |
| One discovery/read operation wall time | 30 seconds | `asyncio.timeout(30)` converts timeout to safe unreachable failure and closes client. |
| Retained CloudWatch raw events | 5,000 and 4 × 1024 × 1024 UTF-8 message bytes | Evict oldest whole events until both fit. |
| Retained CloudWatch display | 5,000 lines and 4 × 1024 × 1024 UTF-8 bytes including display newlines | Keep a bounded tail and surface that older loaded data was dropped. |
| Follow duplicate ids | 20,000 | Prune only outside overlap; overflow stops following with a safe limit failure. |

Count UTF-8 strictly; malformed surrogate strings are safe validation errors.
Use bounded line iteration rather than materializing millions of split substrings
from newline-heavy events. CloudWatch safety failures are ERROR, not successful
truncation; retain the last complete buffer visibly with the error banner.
Display-buffer eviction is not a provider error and has its own retention label.
The existing S3 100 MiB compressed read cap, 5,000 matched-line cap, and five-entry
LRU stay unchanged. CloudWatch has only the current bounded buffer and no LRU.

## 5. Filtering, follow and lifecycle

### 5.1. Loaded-data filtering

CloudWatch retains raw events under the stated bounds. Apply the existing
`LogFilter.matches` to each displayed line, including multiline messages;
preserve existing regex OR, case and passthrough semantics. Filter edits/reset
recompute from the current raw buffer without discovery or network access.
Use the literal caption `filter: loaded data only` before patterns/hints so it
remains visible at compact widths. Explain that evicted data requires reload.
S3 continues its existing streamed filtering/reload behavior; it gains the
caption but no change to fetching or cache semantics.

Expose `available_streams`, `current_stream`, `following`,
`last_successful_read_at_ms`, `last_event_at_ms`, and `buffer_capped` as snapshot
properties. Match counts for CloudWatch refer to the current retained raw
buffer; distinguish displayed capped matches from total matches in that buffer.

### 5.2. Explicit follow

Provide `async follow() -> None` and synchronous `stop_follow() -> None`.
Following is available only for an enabled selected CloudWatch source. Starting
it performs an initial fresh read/discovery if necessary, then serial polls.
Do not issue overlapping polls, background SDK live-tail sessions, or detached
tasks. A page lifecycle worker owns the awaited follow coroutine.

Poll every 2 seconds after completion. Each poll fixes its own end time and
reads from `max(run_created_at_ms, previous_successful_end_ms - 60_000)` through
that end. Keep `(stream_name, event_id) -> timestamp_ms` for duplicate suppression.
For a one-stream buffer, `event_id` alone is sufficient internally because a
stream change clears the entire set. Prune ids older than the new window start;
never LRU-evict ids still inside the overlap. Dedup overflow fails closed rather
than admitting repeats. Merge only after the full poll succeeds. Distinct ids
with identical text/timestamps both remain. Sort newly merged events by
`(timestamp_ms, ingestion_time_ms, event_id)` for stable output; update watermark
only after success, including empty successful polls.

This 60-second overlap catches bounded late arrivals; events ingested later with
older event timestamps may be missed until an explicit reload. Document that
limitation. The fixed end time bounds timestamp eligibility, while page/time
caps also bound late ingestion during pagination; this is not an atomic AWS
snapshot. Follow shows `Following · checked HH:MM:SS UTC`, latest event time,
and retained-buffer limits. Stopped mode shows `Stopped · last checked …`.
The last-successful read time remains unchanged on error; a clock rollback uses
`max(previous_successful_end_ms, now_ms)` for the next end time.

An empty discovery during follow is retryable: remain following and rediscover
at the same cadence until a stream appears, without inventing a selected stream.
Once selected, polls never silently switch streams; a disappeared stream stops
following in NO_FILES with retry guidance. Any other failure stops following,
retains last complete data, and requires deliberate restart. Stop interrupts
both the wait and outstanding read, retaining the last complete buffer.

### 5.3. Ownership and stale work

Increment a monotonic `_generation` on any target/config/source/stream change,
explicit reload, follow start/stop, and shutdown/disposal. Capture it before
awaiting; check it after every await and before all state/cache/diagnostic writes.
A→B→A therefore cannot accept the original A completion. Unchanged metadata
polls do not increment it. S3 loads also use this guard in addition to their
existing identity check, protecting the shared target.

Invalidate synchronously, call the logs `OperationOwner.cancel()`, and retain
durable ownership until tasks drain. Filter changes for CloudWatch do not cancel
follow: they only reproject retained data; use the current filter when merging.
S3 filter changes retain the existing reload path and invalidate that read.
Ensure an old worker's `finally` cannot reset a newer following/state flag.

The page retargets logs immediately at the start of application/run changes,
before awaiting new lists/details. Every read/follow operation runs through the
logs owner; page workers invoke these methods. Page shutdown/disposal and source
replacement stop follow and drain the owner; unmount cancels page workers.
Opening the filter modal does not count as leaving the run. Navigating to another
service does. No global `OperationOwner` refactor is required.

### 5.4. Credential recovery

Extend `EmrCredentialRecoverySnapshot` with defaulted fields
`log_source: LogSource | None`, `cloudwatch_stream_name: str | None`, and
`cloudwatch_configuration: CloudWatchLogConfiguration | None`. Keep S3's exact
file key and filter. Never snapshot log bodies, caches, tokens, duplicate ids or
following state. The recovery candidate has a new connection-owned facade and
empty buffers. Restore app/run, refresh detail, compare effective CloudWatch
group/prefix/enabled identity, restore source and exact stream, and perform one
fresh read. A vanished/config-changed stream fails the recovery candidate;
never substitute another stream/source. Following remains stopped on success.

Same-source authentication refresh cannot reuse CloudWatch data across facade
replacement. Existing source-switch recovery ownership tests remain binding.

## 6. Error and diagnostic boundary

Map SDK exceptions using `map_boto_error` first, then construct a fresh instance
of the resulting permitted provider-error class with fixed copy. Add verified
CloudWatch error codes such as `ServiceUnavailableException` and
`InvalidParameterException` to the common taxonomy with regression tests, without
changing existing codes. Already-typed injected provider failures get the same
sanitization. Never forward the mapper's text, original exception, original
traceback, `args`, response, event repr or token to UI messages or diagnostics.

Use fixed copy for authentication, permission, throttling, unreachable, missing,
validation, cap/token, and unexpected failures. Missing group/stream becomes
retryable NO_FILES in the VM. Other errors use `map_provider_error` to preserve
`PaneState.AUTH_REQUIRED`, `FORBIDDEN`, `UNREACHABLE`, or the existing error
category. Expose `failure_kind: Literal["auth_required", "access_denied",
"throttled", "unreachable", "not_found", "invalid", "limit", "unexpected"] | None`
to distinguish throttling even if `PaneState` uses its existing ERROR bucket.
Use these exact safe error strings: `CloudWatch authentication required`,
`CloudWatch log access denied`, `CloudWatch log requests throttled`,
`CloudWatch logs unreachable`, `CloudWatch logs not created yet`,
`CloudWatch log response invalid`, and `CloudWatch log read failed`.
Cap/token messages are fixed application strings that identify only the exceeded
budget or repeated-token condition. Never interpolate a raw error into them.

Raise converted exceptions outside the original `except` block, with no cause
or context. `raise … from None` hides printed chaining but alone leaves the
original exception reachable as `__context__`; explicitly avoid that retention.
For an unexpected CloudWatch exception at any higher boundary, publish only a
new, never-raised `ProviderError("CloudWatch log read failed")` through
`report_unexpected_service_error`, then set fixed UI text. Do not pass the caught
exception to that helper. Guard client-context exit and cancellation cleanup too:
an SDK cleanup failure must not escape raw into worker/owner diagnostics.

CloudWatch record reprs exclude bodies; notifications remain value-free. All
rendered log bodies, stream names and labels use `markup=False`. Tests inject a
unique arbitrary body sentinel (not a recognizable credential) into AWS errors,
unexpected errors, exception chains, context-exit errors, and event bodies, and
assert its absence from every emitted diagnostic field, logger record and
formatted traceback. Seeing the sentinel in the intended log body is allowed.

## 7. UI and command surface

Keep the existing runs/detail/logs layout and focus ring. A one-line horizontal
source selector above the stream/file choices shows `S3: configured` and
`CloudWatch: configured` (or their other states). Clicking changes only the log
source; it is distinct from the AWS connection/source selector. Stream labels
show component, attempt when present, and sufficient exact-name suffix to
distinguish workers; keyboard left/right cycles exact identities. Horizontal
overflow must not make a source/stream keyboard-inaccessible.

Register `emr.logs.source` with default `ctrl+s` and `emr.logs.follow` with
default `ctrl+l`, after checking the existing default keymap for conflicts.
Source cycles between selectable configured/retryable/error sources; follow
explicitly toggles start/stop. Both routes are scoped to focused EMR logs,
advertised in the command palette, and use resolved keys in pane hints. Start
is disabled for S3/disabled/unknown config; Stop is enabled while following,
including a pending read. Do not steal `Shift+S` (AWS source), `f` (filter),
`Shift+F` (filter reset), left/right, or global focus navigation. Custom binding
collision behavior remains the current whole-overlay validation policy.

At 80×24 the source row, a stream row, a compact loaded-data caption, one body
line and status fit the existing lower pane; omit optional pattern text before
hiding actionable source/filter/follow affordances. Status has clickable literal
Start follow/Stop follow text or an equivalent button without another permanent
row. Reuse one body widget. While following, keep existing lines visible during
polls and show progress in status; error/empty banners coexist with retained data.
Initial CloudWatch-only READY snapshots must contain a known seeded ERROR body,
CloudWatch source label, and loaded-data caption.

## 8. Demo and documentation

Add seed helpers on `InMemoryEmr`:

```python
def add_cloudwatch_stream(
    self, *, application_id: str, job_run_id: str,
    log_group_name: str, stream: CloudWatchLogStream,
    events: tuple[CloudWatchLogEvent, ...],
) -> None: ...

def append_cloudwatch_event(
    self, *, log_group_name: str, stream_name: str,
    event: CloudWatchLogEvent,
) -> None: ...
```

Store immutable events by group/exact stream per profile-isolated fake. Discovery
and reads honor the same run membership, time window, ordering and safety caps.
No timers or network clients are needed in the fake; tests append an event while
the real app follows. Preserve bounded call observations without recording
bodies. Seed an accessible CloudWatch-only run with driver and retry-attempt
streams and a both-sources run. Keep the current first S3 seed stable where
possible, preserving existing startup snapshots.

Update `docs/services/emr-serverless.md` with configuration defaults, source
states, exact stream/attempt selection, local filtering, read/follow bounds,
freshness, late-arrival limits, recovery and demo steps. Explain that the aws-tui
reader identity needs `logs:DescribeLogStreams` and `logs:FilterLogEvents` on the
configured log group, plus existing EMR read permissions. It does not need
`logs:GetLogEvents`, `logs:Unmask`, creation or write permissions. Keep runtime
role logging-writer requirements separate and link the AWS guide. Do not imply
aws-tui enables logging or edits policy.

Update `docs/keybindings.md`, `docs/contract-ledger.md` and consumed SDK contract
tests for the two actual Logs operations/parameters. Regenerate site/wiki with
the existing local docs pipeline; no publishing. The eventual PR description
must link the changed service documentation page. Use hierarchical heading
numbering in all canonical documents, including this specification and plan.

## 9. Verification and acceptance traceability

| AC | Required evidence | Plan task |
| --- | --- | --- |
| 1 | Stubbed GetJobRun parses enabled/default/custom/disabled/absent/malformed CloudWatch metadata alongside S3; clone overrides unchanged. | 1 |
| 2 | VM/pane matrix for source states; CloudWatch-only READY; theme snapshot plus body/source/caption guard. | 2 |
| 3 | Stub session asserts selected region/config; page/event/UTF-8 caps exact boundary and one-over; fixed end time every request. | 1 |
| 4 | Same token twice, A→B→A, empty token-bearing pages, EOF at cap, call-count guards. | 1 |
| 5 | VM selects exact component/retry/worker stream; missing group/stream retry succeeds; custom prefix cannot impersonate app/run. | 1, 2 |
| 6 | LogFilter semantics including multiline/case/passthrough; CloudWatch edit issues no AWS call; literal loaded-data caption. | 2 |
| 7 | Start/stop/empty polls/freshness; bounded UTF-8 buffers/dedup; full-app Pilot leaves followed run and drains workers; blocked read/cleanup and ABA tests. | 2 |
| 8 | Typed auth/denied/throttle/transport/missing/invalid states; sentinel absence in service diagnostics, logger fields and tracebacks. | 1, 2 |
| 9 | Running real demo `AwsTuiApp.run_test` journey with network tripwire: navigate, load, choose attempt, follow append, stop, leave. | 2 |
| 10 | Service docs distinguish reader/writer IAM and configuration; contract/keybindings/docs gates; PR handoff records docs link. | 2 |

Existing S3 tests, source replacement/recovery, clone, focus/navigation, local
coverage, snapshots, docs and architecture checks remain required. No tests
that compare only empty snapshots or remove prior guards count as evidence.

## 10. Delivery boundaries and unresolved facts

Deliverable 1 is a reviewable domain/facade/demo primitive slice with direct
tests, independent of UI decisions. Deliverable 2 consumes those interfaces and
ships the complete user journey, lifecycle/recovery, docs and snapshots. Task 2
depends on Task 1; they are sequential, not parallel editing assignments.

Custom-prefix concatenation and unreported inherited application configuration
are not asserted as facts. Safe bounded discovery and the explicit unknown
state cover those uncertainties; they do not block implementation. No live AWS
calls are required or authorized to settle them. The API request fields must
also be checked against the locked installed botocore model, since the current
online API includes additions newer than this project's supported model.
The architect checked the installed model on 2026-10-06: both chosen operations
support every request field listed here; its FilterLogEvents input has no
`startFromHead` field, which is intentionally unused.
