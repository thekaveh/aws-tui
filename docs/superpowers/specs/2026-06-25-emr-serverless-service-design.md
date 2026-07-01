# 1. EMR Serverless service — v1 design

**Status:** design accepted in brainstorming session 2026-06-25; PR-A (read-only browser) shipped via PRs #76–#82; the **clone-job-run modal originally scoped to PR-C shipped early in PR #83** alongside the user-feedback batch (it was the most-requested daily-driver action and was small enough to land out-of-band). **PR-B logs surface shipped on branch ``feat/emr-job-run-logs-pane``** (S3 streaming + regex filter + on-demand fetch via `Enter`/`r`/`f` bindings, with cache + progress feedback). PR-B scope: ``stdout`` / ``stderr`` streaming with the default filter set. Out of scope for v1 (logged for v1.1): CloudWatch Logs as an alternative source, tail-follow for live runs, multi-file interleaved view, in-memory re-filter without re-download, slash-search inside the loaded buffer. Remainder of PR-C (vanilla blank-form submit) and PR-D (E2E journey + memory) follow.
**Author:** assistant, 2026-06-25.
**Reviewer:** Kaveh.
**Trigger:** user request — "Please now use your brainstorming skill so we can add the next service that makes most sense to be added. I personally lean toward emr (serverless) since I use it so much on a daily basis."

This spec captures the v1 design for an **EMR Serverless service** in aws-tui. EMR Serverless is the user's daily-driver compute environment for Spark workloads. v1 ships the full daily-driver loop: browse applications + job runs, monitor with log tailing, cancel, lifecycle applications, and submit new runs (either blank-form or cloned-and-edited from an existing run).

The design is the second concrete `Service` in the registry (after S3) and validates that the `Service` protocol scales beyond filesystem-shaped services to a master/detail of API-listed resources. v1 also introduces a reusable `LogSource` Protocol so a future CloudWatch Logs service can graft on without re-implementing the log surface.

---

## 1.1. Architecture

### 1.1.1. Module breakdown

```
src/aws_tui/
├── domain/
│   └── emr_serverless.py        ← NEW: EmrServerlessClient (boto3 facade)
├── services/
│   └── emr_serverless/
│       ├── __init__.py
│       └── service.py            ← Service protocol impl
└── vm/
│   └── emr_serverless/
│       ├── page_vm.py            ← master page VM
│       ├── applications_vm.py    ← dropdown's app list + active-app state
│       ├── job_runs_vm.py        ← LEFT pane: runs for selected app
│       ├── job_run_detail_vm.py  ← RIGHT pane: detail + log surface
│       ├── log_view_vm.py        ← reusable log-stream + chip filter
│       └── submit_form_vm.py     ← submit-modal VM
└── ui/widgets/
    └── emr_serverless/
        ├── page.py               ← top strip + 2-pane container
        ├── application_picker.py ← dropdown widget
        ├── job_runs_pane.py
        ├── job_run_detail_pane.py
        ├── log_view.py           ← chip row + scrollable streamed lines
        └── submit_modal.py       ← ConfirmModal-pattern submission form
```

### 1.1.2. No speculative Protocol layer

The first draft of this design proposed an `EmrServerlessProvider` Protocol "so tests can swap an in-memory fake". On reflection that's over-engineering: the S3 service does NOT have an "S3 provider Protocol" — `S3FS` is a concrete class that owns its `aioboto3.Session` directly, and tests stub `boto3` calls inline. A Protocol with one implementation adds a layer with zero abstraction value.

**One Protocol does earn its place: `LogSource`** (see §5 Logs). EMR's S3 source and a future CloudWatch Logs source will satisfy the same Protocol, justifying the abstraction — same rationale as `FileSystemProvider` for S3FS/LocalFS.

### 1.1.3. Service registration

`EmrServerlessService.supports(connection)` returns `connection.kind == "aws"`. The ⚡ EMR nav-rail icon **automatically appears only on AWS connections**; on an s3-compatible connection it disappears via the existing `NavMenuVM._rebuild_items()` flow. Registration in `composition.build_app_context`:

```python
from aws_tui.services.emr_serverless.service import EmrServerlessService
emr_service = EmrServerlessService(hub=hub, dispatcher=dispatcher)
registry.register(cast("Service", emr_service))
```

### 1.1.4. Service descriptor

```python
ServiceDescriptor(
    id="emr-serverless",
    label="EMR",
    icon="🔥",                   # U+1F525 FIRE — SMP single-codepoint, 2 cells, reliable colour
)
```

> **Post-ship amendment (PRs #77 / #79 / #81 / #83):** the design's
> original `⚡` (bare U+26A1) shipped in PR #76 but rendered as a
> 1-cell text-style stroke in monospace terminals, mis-aligning the
> nav-rail's 2-cell emoji column. PR #77 forced emoji presentation
> with `⚡️` (BMP+VS-16); PR #79 briefly switched to `🔥`; PR #81
> returned to `⚡️`; PR #83 tried `💥` (SMP single-codepoint) but it
> rendered too small beside the S3 bucket icon, so the shipped
> descriptor returned to `🔥`. The documented "icon contract" for
> future services: **SMP single-codepoint, no variation selector** —
> pick a glyph that reliably occupies 2 cells in monospace terminals
> without a VS-16 trick. Symmetric with the rail's literal-object
> naming: 🪣 = bucket, 🔥 = compute/spark, ⚙️ = gear (kept on BMP+VS-16
> because it's worked on the user's stack), 🖥️ = computer (same).

### 1.1.5. VMx lifecycle

`EmrServerlessPageVM` is built fresh per mount inside `EmrServerlessService.build_vm` — never as a singleton in `ContentHostVM` (per the `vmx-content-host-singleton-trap` memo). All sub-VMs (`ApplicationsVM`, `JobRunsVM`, `JobRunDetailVM`, `LogViewVM`, `SubmitFormVM`) construct under the page VM's `construct()` and dispose in reverse-construction order when ContentHost swaps away.

---

## 1.2. Layout & navigation

### 1.2.1. Page composition

```
┌─ EMR Serverless ─────────────────────────────────┐
│ App: [⚡ etl-pipeline-1  ●STARTED]   [⏸ Stop app]    [+ Submit] │  ← top strip
├──────────────────┬───────────────────────────────┤
│ Job runs         │ Job run detail                │
│ State: [✓][●][✗] │ ┌─ j-abc · ✓ SUCCESS ──────┐  │  ← state filter chips
│                  │ │ Duration   4m12s         │  │
│ ▶ j-abc 12:01 ✓  │ │ Started    12:01:34      │  │
│   j-def 12:05 ●  │ │ IAM        arn:aws:...   │  │
│   j-ghi 11:58 ✗  │ │ EntryPoint s3://bucket/… │  │
│   j-jkl 11:30 ✓  │ │ Args       --in s3://… … │  │
│   j-mno 10:45 ✗  │ │ Spark      --conf …      │  │
│   ⤴ load older   │ └──────────────────────────┘  │
│                  │ [stderr][stdout]  ●5s refresh │
│                  │ Levels: [E][W][I][D][T] [grep]│
│                  │ ┌──────────────────────────┐  │
│                  │ │ 24/06/25 12:01:34 ERROR  │  │
│                  │ │ … filtered log lines …   │  │
│                  │ └──────────────────────────┘  │
└──────────────────┴───────────────────────────────┘
```

### 1.2.2. Three resource levels, reachable in two taps max

1. **Application** — top-strip dropdown. Arrow keys + Enter navigate, auto-selects last-used per session. Application state glyph appears next to the name.
2. **Job runs** — LEFT pane. Newest-first sort by `createdAt`. State-glyph chip filter row at top (`✓ SUCCESS`, `● RUNNING`, `⏸ PENDING`, `✗ FAILED`, `⊘ CANCELLED`). Default = all states.
3. **Job-run detail** — RIGHT pane. Master/detail linked to LEFT cursor.

### 1.2.3. Keybindings

| Key | Action | Scope |
|---|---|---|
| `a` | open application picker | page |
| `+` | submit job (vanilla blank form) | page |
| `c` | submit job (clone-and-edit from focused run) | LEFT focus |
| `x` | cancel focused job run | LEFT focus |
| `l` | toggle log-pane focus | RIGHT focus |
| `g` | open custom-regex grep input | RIGHT focus (log) |
| `1`–`5` | toggle state filter chips | LEFT focus |
| `1`–`5` | toggle log-level filter chips | RIGHT focus (log) |
| `r` | refresh focused pane (and retry on error states) | per pane |
| `s` | start / stop application | page |
| `Tab` | cycle LEFT ↔ RIGHT pane | page |

`1`–`5` is intentionally **pane-scoped** (state filters when LEFT is focused, log-level filters when RIGHT is focused). The HintLegend at the footer shows the active set, so the meaning is always visible.

### 1.2.4. Application picker dropdown

Textual ships no native dropdown. v1 uses an **`OptionList`-in-a-popover** widget (~1 day to build) that:
- mounts above the top strip as a floating layer when opened;
- renders an `OptionList` styled identically to the NavMenu rail;
- closes on Enter (commits selection), Esc (cancels), or click-outside.

Alternative considered + rejected: horizontal scrolling app-chip row at the top. Rejected because users typically have 5–20 applications and a chip row past ~5 entries needs horizontal scroll, which is worse UX than a dropdown.

### 1.2.5. Empty states

| Condition | Surface |
|---|---|
| No applications in this account/region | top-strip dropdown shows `(no applications in <region>)`; body shows pane-style placeholder with `aws emr-serverless create-application` hint |
| Selected app has zero job runs | LEFT pane EMPTY state; RIGHT pane prompts `Press [b]+[/] to submit your first job` |
| No `logUri` configured on application | log pane shows `Log destination not configured on this application — enable S3 logging in the application's monitoring config and re-run` |

### 1.2.6. Tab cycle

Two slots exactly: LEFT ↔ RIGHT. Application picker is reached via `a`, never via Tab. This matches PR #66's lesson — a 3-slot cycle reads as "two idle Tab presses inside the rail" and was an active UX bug we fixed on S3.

---

## 1.3. Design language reuse (consistency commitment)

Every EMR surface inherits from existing chrome. No reinvented patterns. If EMR ever drifts from a row in this table, the test for that row is the regression.

### 1.3.1. Pane chrome

| Detail | Rule | Reference |
|---|---|---|
| Resting border | `border: solid $rule-dim` | PR #61 (Settings parity with Pane) |
| Focused border | `:focus-within` → `border: solid $accent` | PR #62 (`:focus-within` pseudo, not the broken Python watcher) |
| Border title | top — live path/identity | matches `Pane._apply_border_title` |
| Border subtitle | bottom — connection identity (e.g. `kaveh-dev · us-east-1`) | matches `Pane._apply_border_title` |

### 1.3.2. Selected-row look

```css
.entry-row.-selected { background: $bg-sel; color: $text; }
```

No `text-style: bold`. Identical to S3 pane rows and NavMenu option highlight per PR #66, with the post-maintenance contrast update that uses `$text` on `$bg-sel`. The chip-filter row uses the same `$bg-sel` + `$text` shape when activated.

### 1.3.3. Tab cycle

Exactly two slots on the EMR page: LEFT (job runs) ↔ RIGHT (detail). No NAV detour, no 3-slot or 4-slot variants. Per PR #66.

### 1.3.4. Shortcut commands

All EMR keys registered via the `ActionRegistry` + `KeymapStore` (same path S3 and Settings use). HintLegend at the footer shows the EMR-page chips when the page is active. The BindingResolver path is still deferred (`deferred-from-m6`); v1 declares keys in the page widget's `BINDINGS` and adds them to the existing global keymap — same trick the S3 page uses today.

### 1.3.5. Dialogs / confirmations

Reuses `ConfirmModal` verbatim. No new modal class.

- **Cancel job run** → `ConfirmRequest(danger=True, paths=[ConfirmPath("Run", "j-abc")], body_lines=("This will signal CANCELLING then CANCELLED.",), confirm_label="Cancel run", cancel_label="Keep running")`
- **Stop application** → `ConfirmRequest(danger=True, paths=[ConfirmPath("Application", "etl-pipeline-1")], body_lines=("Application will stop accepting new runs and idle workers will spin down.",), confirm_label="Stop", cancel_label="Cancel")`
- **Start application** → no confirm modal — starting is reversible and has no destructive side effect.

Buttons follow PR #73's contract: both look neutral at rest; first `right`/`tab` lands on the right button (Confirm/Stop), first `left` lands on the left button (Cancel/Keep). Modal title gets the `margin-bottom: 1` gap from PR #72.

### 1.3.6. Submit modal

`EmrSubmitJobModal` is a **new** widget but **structurally a `ConfirmModal`-shaped layout**: `Container` with `.modal-title`, body inputs labelled like `.modal-path-label` + `.modal-path-value`, footer with `ModalButton`. Uses the same `ModalButton` widget (neutral at rest, `$accent` on focus). Inputs use `ConnectionFormInline`'s validation pattern (`-invalid` class on bad data).

**Pin:** `EmrSubmitJobModal` must remain a snapshot-test sibling of `ConfirmModal` across all 10 themes. Any visual divergence is a regression.

### 1.3.7. Toasts

All EMR notifications go through `aws_tui.ui.notifications` (PR #75). New `Subject` literal entry: `"Job"`. `Transfer` stays scoped to its existing file-transfer overlay meaning.

| Event | Helper | Subject | Example |
|---|---|---|---|
| Job submitted | `success` | `Job` | `✓  Job: submitted j-abc to etl-pipeline-1` |
| Job cancellation requested | `announce` | `Job` | `›  Job: j-abc cancellation requested` |
| Job submission failed | `error` | `Job` | `✖  Job: submit failed: ValidationException — see log` |
| App start initiated | `announce` | `Job` | `›  Job: starting etl-pipeline-1` |
| App stop initiated | `announce` | `Job` | `›  Job: stopping etl-pipeline-1` |
| EMR API unreachable | `advise` | `Source` | `⚠  Source: emr-serverless unreachable — press r to retry` |
| Auth failure on EMR API | `error` | `Auth` | `✖  Auth: emr-serverless requires re-auth — aws sso login --profile X` |
| EMR API throttled | `advise` | `Source` | `⚠  Source: EMR throttled — backing off` |
| Application no longer exists (race with delete) | `advise` | `Job` | `⚠  Job: application no longer exists — picking next` |

### 1.3.8. State indicators

EMR pane state mirrors `PaneState`. Same enum values, same placeholder rendering, same `r`-to-retry contract.

| State | Meaning | Where |
|---|---|---|
| `LOADING` | `list_*` / `get_*` in flight | both panes |
| `IDLE` | data present | both panes |
| `EMPTY` | API returned 0 results | LEFT pane (no runs yet) |
| `UNREACHABLE` | boto3 endpoint failure | both panes; `r` retries |
| `AUTH_REQUIRED` | NoCredentialsError / token expired | both panes; suggests `aws sso login --profile X` |
| `FORBIDDEN` | IAM denied | both panes; surfaces the error text |

### 1.3.9. Theme tokens

Every color in EMR-page CSS is one of: `$bg`, `$bg-elev`, `$bg-sel`, `$accent`, `$accent-soft`, `$rule-dim`, `$success`, `$warning`, `$danger`, `$text`, `$text-muted`. **No hex literals.** No `$accent-hot`. Theme-aware state-glyph colors:

| Glyph | Token | State |
|---|---|---|
| `✓` | `$success` | SUCCESS |
| `●` | `$accent` | RUNNING |
| `⏸` | `$text-muted` | PENDING |
| `✗` | `$danger` | FAILED |
| `⊘` | `$text-muted` | CANCELLED |

Identical to `TransferRowWidget` state colors (PR #50).

### 1.3.10. NavMenu

`EmrServerlessService` registers in `ServiceRegistry`. The rail picks up the entry, the `▌` ribbon prefix on selection, expand/collapse via `m`, and the tooltip-on-hover (PR #61). No new widget code at this layer.

### 1.3.11. Snapshot tests

Every new EMR widget gets a snapshot PLUS a content-presence guard (per PR #53 / #63). Required at minimum: `application_picker`, `job_runs_pane`, `job_run_detail_pane`, `log_view`, `submit_modal` — each across the 10 themes, each paired with `assert "<expected glyph or label>" in svg`.

---

## 1.4. Submit-job-run flow

Two entry points, **one modal**: `+` for vanilla (blank form), `c` for clone-and-edit (pre-filled from focused run).

### 1.4.1. Modal layout

```
┌─ Submit job to etl-pipeline-1 ──────────────────────────┐
│                                                         │
│ Entry point                                             │
│   [ s3://my-bucket/jobs/etl.py                       ]  │
│                                                         │
│ Arguments  (one per line)                               │
│   [ --input  s3://my-bucket/raw/2026-06-25/          ]  │
│   [ --output s3://my-bucket/curated/2026-06-25/      ]  │
│   [ --partitions 200                                 ]  │
│                                                         │
│ Spark submit parameters                                 │
│   [ --conf spark.executor.instances=8                ]  │
│   [ --conf spark.executor.memory=4g                  ]  │
│   [ --conf spark.driver.memory=2g                    ]  │
│                                                         │
│ Execution role  (empty = app default)                   │
│   [ arn:aws:iam::123456789012:role/EmrJobRole        ]  │
│                                                         │
│ Job name  (optional)                                    │
│   [ etl-2026-06-25-rerun                             ]  │
│                                                         │
│                            [ Cancel ]   [ Submit ]      │
└─────────────────────────────────────────────────────────┘
```

### 1.4.2. Fields & validation

| Field | Type | Required | Validation (`-invalid` trigger) |
|---|---|---|---|
| Entry point | `Input` (single-line) | yes | non-empty; warn (not error) if doesn't start with `s3://` |
| Arguments | `TextArea` | no | none — passed as list of non-empty lines |
| Spark submit parameters | `TextArea` | no | none — passed as a single space-joined string |
| Execution role | `Input` | no | if present, must match `arn:aws:iam::\d{12}:role/.+` |
| Job name | `Input` | no | max 256 chars |

Live validation via `Input.Changed` (same path `ConnectionFormInline` uses). Submit button enabled only when entry point is non-empty AND no field has `-invalid`.

### 1.4.3. Clone-and-edit mode

```python
def open_for_clone(self, source: JobRunDetail) -> None:
    self._entry_point = source.entry_point
    self._arguments_text = "\n".join(source.entry_point_arguments)
    self._spark_params_text = " ".join(source.spark_submit_parameters.split())
    self._role_override = source.execution_role_arn
    self._job_name = (
        f"{source.name or source.job_run_id}-clone" if source.name else ""
    )
```

Title changes to `Submit job to <app> · cloned from j-abc`. Behaviourally identical to vanilla beyond pre-filling.

### 1.4.4. Worker pattern

`SubmitJobRunCommand` runs in `run_worker(exclusive=True, group="emr-submit")` so a double-click on Submit can't fire twice. The button transitions to a LOADING look (`text-style: dim`, label `Submitting…`) while in flight.

- **On success:** dismiss the modal → `notifications.success(subject="Job", "submitted <new-run-id> to <app>")` → refresh the job-runs list (new RUNNING row appears within ~1s).
- **On failure:** keep the modal open → inline error banner above footer with boto3's `error_message` → `notifications.error(subject="Job", "submit failed: <code>", action="see log")`. User edits + retries.

### 1.4.5. Boto3 surface

```python
EmrServerlessClient.start_job_run(
    application_id=app_id,
    execution_role_arn=role_override or app.execution_role_arn,
    job_driver={
        "sparkSubmit": {
            "entryPoint": form.entry_point,
            "entryPointArguments": form.arguments_lines,
            "sparkSubmitParameters": form.spark_params,
        },
    },
    name=form.job_name or None,
    tags={"submitted-via": "aws-tui"},
)
```

The `submitted-via=aws-tui` tag gives the user a filter handle in the AWS console and helps distinguish TUI traffic from console traffic.

### 1.4.6. Out of scope for v1

- Configuration overrides (capacity, network, monitoring) — inherited from the application defaults.
- Hive-type jobs — Spark only. Modal title says "Submit Spark job to …" so the constraint is explicit.
- Job-step templates / saved job library — clone-from-run covers the workflow.

---

## 1.5. Logs surface

The "monitor" half of submit/list/monitor. Designed as a reusable primitive: a future CloudWatch Logs service plugs the same `LogViewVM` into a different upstream.

### 1.5.1. Path discovery

```
Application.monitoringConfiguration.s3MonitoringConfiguration.logUri
              │
              └─> <logUri>/applications/<app-id>/jobs/<run-id>/SPARK_DRIVER/{stderr,stdout}.gz
```

If `logUri` is missing (S3 logging disabled), the log pane shows the `Log destination not configured…` placeholder from §2.

### 1.5.2. Auto-refresh strategy (range-GET, not re-download)

```
loop:
  if state in (PENDING, RUNNING):
    head = boto3.s3.head_object(Bucket=b, Key=key)
    if head.content_length > local_offset:
      r = boto3.s3.get_object(
          Bucket=b, Key=key,
          Range=f"bytes={local_offset}-{head.content_length-1}",
      )
      append r.body to local tempfile
      gunzip-decompress incremental chunk
      re-filter (level chips + grep)
      append matching lines to bounded deque
      local_offset = head.content_length
    sleep 5s
  elif state in (SUCCESS, FAILED, CANCELLED):
    # one-shot read, no further polling
    break
```

Range-GET on a growing log means the second tick fetches only the newly-flushed bytes (typically a few KB per 5s). Bandwidth + cost are bounded by job log volume, not by polling frequency. The local tempfile lives at `~/.cache/aws-tui/emr-serverless/logs/<app-id>/<run-id>/stderr.txt` (decompressed plaintext, append-only). Re-toggling chips reads the tempfile, not S3 — instant.

S3 logs are gzipped per-flush; each flush boundary is a self-contained gzip stream. We decompress incrementally; if a byte boundary doesn't land on a flush boundary, we buffer the tail and prepend on the next tick.

### 1.5.3. Chip filter row

```
Levels: [ E ] [ W ] [ I ] [ D ] [ T ]   Grep: [ stage_… ]   [ stderr ▾ ]   ●5s
         ▔▔▔   ▔▔▔
         on    on  (defaults)
```

- **Five level chips**, multi-select. Default `[E][W]`. Hotkeys `1`–`5`.
- **Grep input** — Python regex applied AFTER the level filter. Opens via `g`. Empty = no grep. Persists per session per (app, run).
- **Stream switcher** `[stderr ▾ / stdout]`. Spark driver writes mostly to stderr.
- **Live indicator** `●5s` ↔ `○5s` rotates between refreshing and idle. Hidden for completed runs.

Chip styling follows §3: resting `$bg` + `$rule-dim`; active `$bg-sel` + `$text`.

### 1.5.4. View bounds + scrolling

- Last **2000 matching lines** retained in a deque; older matching lines page out of view but the tempfile retains everything.
- New matching lines auto-scroll if cursor is at the bottom; if the user scrolls up, auto-scroll pauses and `(↓ N new lines — Enter to follow)` appears at the bottom row.
- Deque is mutex-protected — the worker can append while the renderer reads.

### 1.5.5. Filter pipeline

```
S3 gzip chunk
     │
     v
gunzip → utf-8 decode (errors=replace for malformed bytes)
     │
     v
split on '\n'
     │
     v
level filter (regex: \b(ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL)\b)
     │
     v
grep filter (re.search over each line)
     │
     v
last-2000 deque + view
```

**Continuation-line rule.** Lines with no recognized level token (continuation lines of multi-line Java stack traces) inherit the level of the previous line. A full stack trace doesn't get half-filtered when only `[E]` is active.

### 1.5.6. State indicators (log pane bottom strip)

| State | Bottom strip |
|---|---|
| Loading first chunk | `Loading log from s3://… · 0 B` |
| Refreshing | `Last fetched 12:01:38 · 4.2 MB · 1842/12903 lines match` |
| Polling paused (run completed) | `Final · 12:01:42 · 8.4 MB · 2104/15847 lines match` |
| S3 fetch failed | `⚠ s3 unreachable — press r to retry` |
| No log configured | `Log destination not configured on this application` |

### 1.5.7. Reusable hooks for CloudWatch Logs

`LogViewVM` constructor accepts an injected `LogSource` Protocol:

```python
class LogSource(Protocol):
    async def head(self) -> LogHead: ...                   # length, last-modified
    async def fetch_chunk(self, offset: int, length: int | None) -> bytes: ...
    def is_complete(self) -> bool: ...                      # for polling termination
```

`EmrS3LogSource` (lives in `services/emr_serverless/`) implements this against S3. A future `CloudWatchLogSource` implements it against `boto3.client('logs').get_log_events` paging. The chip row, grep input, view bounds, deque, state indicators all stay in `LogViewVM`/`LogView`. This Protocol earns its place because two real sources will satisfy it.

### 1.5.8. Out of scope for v1

- Multi-stream multiplex (merge stderr + stdout chronologically) — single-stream switcher.
- Log download / export-visible-view — defer; users `aws s3 cp` themselves.
- Search-in-page (separate from grep filter) — defer; grep covers it.
- Inline regex tester or syntax help — defer; the input is one-line and assumes Python `re` syntax.

---

## 1.6. Cancel, state filter, lifecycle, auto-refresh

### 1.6.1. Cancel a job run

```
focused run in LEFT pane (state ∈ {PENDING, RUNNING})
       │  x  (hotkey, or click [x] chip on row hover)
       v
ConfirmModal(danger=True)
  paths:  Run = j-abc
  body:   "This will signal CANCELLING then CANCELLED."
  buttons:                         [ Keep running ] [ Cancel run ]
       │ confirm
       v
EmrServerlessClient.cancel_job_run(app_id, run_id)
       │
       ├─ success → announce(subject="Job", "j-abc cancellation requested")
       │            row glyph flips to CANCELLING within 10s
       │
       └─ failure → error(subject="Job", "cancel failed: <code>", action="see log")
```

The `[x]` chip mirrors PR #50's `transfer-cancel` chip — 1-cell tall, no `border: round`, accent glyph + `$danger` on hover.

### 1.6.2. State filter (LEFT pane)

```
State: [✓][●][⏸][✗][⊘]    ← multi-select, all-on default
       1  2  3  4  5      ← hotkeys when LEFT pane has focus
```

Same chip grammar as log-level chips: resting `$bg`+`$rule-dim`, active `$bg-sel`+`$accent-soft`. Glyph colors follow the §3 state table.

### 1.6.3. Application lifecycle (top strip)

| App state | Top-strip button | Hotkey | Confirm? |
|---|---|---|---|
| CREATED, STOPPED | `[⏵ Start app]` | `s` | no — starting is reversible |
| STARTED | `[⏸ Stop app]` | `s` | `ConfirmModal(danger=True)` — in-flight runs disrupted |
| STARTING, STOPPING | button disabled, label shows transition state | — | — |

API: `EmrServerlessClient.start_application(app_id)` / `stop_application(app_id)`. Toast on either: `announce(subject="Job", "starting/stopping <app>")`. App-state glyph next to dropdown updates on the next poll tick (≤30s).

### 1.6.4. Auto-refresh schedule

Three independent VMx-worker pollers under the page VM:

| Surface | Active cadence | Idle cadence | Stops when |
|---|---|---|---|
| Application picker | 30 s | 60 s | page disposed |
| Job-runs list | 10 s when any run ∈ {PENDING, RUNNING} | 60 s otherwise | page disposed |
| Job-run detail + log | 5 s when run ∈ {PENDING, RUNNING} | one-shot then stop | run reaches terminal state |

All three share **one** `EmrServerlessClient` with a 5-second response cache so the detail poller's `get_job_run` doesn't race the list poller's `list_job_runs`. The client is injected via constructor.

### 1.6.5. Throttle handling

On `ThrottlingException` the affected poller backs off exponentially (5s → 10s → 30s → 60s cap) and raises `advise(subject="Source", "EMR throttled — backing off")`. The next successful call clears the back-off and dismisses the toast.

### 1.6.6. Manual refresh

`r` on a focused pane skips the schedule and forces an immediate poll. Same `r`-to-retry contract as the S3 pane. If the pane is in UNREACHABLE/AUTH_REQUIRED, `r` retries from scratch including re-instantiating the boto3 session — handles the "user just did `aws sso login` in another terminal" case.

### 1.6.7. Error states & propagation

| Triggered by | Pane state | Toast | Recovery |
|---|---|---|---|
| `EndpointConnectionError` | UNREACHABLE | `advise(subject="Source", "emr-serverless unreachable", action="press r to retry")` | `r` |
| `NoCredentialsError` / SSO expired | AUTH_REQUIRED | `error(subject="Auth", "emr-serverless requires re-auth", action="aws sso login --profile X")` | `r` after fix |
| `AccessDeniedException` | FORBIDDEN | `error(subject="Auth", "IAM denied: emr-serverless:<action>", action="see policy")` | manual |
| `ResourceNotFoundException` (app deleted mid-session) | LEFT EMPTY, page reverts to picker | `advise(subject="Job", "application no longer exists — picking next")` | auto |
| `ValidationException` on submit | modal stays open, inline banner | `error(subject="Job", "submit failed: <message>")` | edit + retry |
| `ThrottlingException` | (transient) | `advise(subject="Source", "EMR throttled — backing off")` | auto-back-off |

### 1.6.8. Out of scope for v1

- Bulk cancel ("cancel all RUNNING in this app").
- Reading job-run metrics (CloudWatch vCPU-hours / memory-hours).
- Application creation / deletion — v1 assumes apps are managed elsewhere (Terraform, console, scripts).
- Restart-as-new — clone-and-edit covers this.

---

## 1.7. Tests, decomposition, acceptance

### 1.7.1. Test tiers

**Unit tier** (fast, no network):

| Module under test | Coverage |
|---|---|
| `domain/emr_serverless.py::EmrServerlessClient` | every verb mocked via `botocore.stub.Stubber`; pagination; error-code → exception mapping |
| `vm/emr_serverless/page_vm.py::EmrServerlessPageVM` | construct/dispose lifecycle; child-VM disposal order; `r`-refresh routing to focused pane |
| `vm/emr_serverless/job_runs_vm.py::JobRunsVM` | state-filter chip toggles; sort-by-createdAt-desc; selected-row tracking; auto-refresh gating on PENDING/RUNNING presence |
| `vm/emr_serverless/log_view_vm.py::LogViewVM` | level chip toggles against fixture log; grep composed with chips; continuation-line inheritance for stack traces; bounded deque caps at 2000 lines |
| `vm/emr_serverless/submit_form_vm.py::SubmitFormVM` | validation: empty entry point disables Submit; bad IAM ARN flips `-invalid`; clone-mode pre-fills every field; vanilla mode starts blank |

**Integration tier** (full app boot, no network):

- `test_emr_page_mounts_on_aws_connection` — boot with AWS connection, click ⚡ in nav rail, assert `EmrServerlessPage` widget present in `#content-host`.
- `test_emr_page_hidden_on_s3_compatible_connection` — boot with s3-compatible connection, assert ⚡ NOT in nav rail (`Service.supports` filter works).
- `test_cancel_job_shows_confirm_modal_then_calls_boto` — `x` on focused RUNNING row, confirm modal opens with danger styling, Right + Enter, assert `cancel_job_run` called with the right args.
- `test_submit_modal_clone_prefills_from_focused_run` — `c` on focused completed run, assert each input value matches the source run.
- `test_stop_app_requires_confirm_start_does_not` — assert ConfirmModal opens for stop, not for start.
- `test_log_view_level_filter_reduces_visible_lines` — load 1MB fixture log (mixed levels), default `[E][W]`, assert visible-deque length matches expected ERROR+WARN count.
- `test_log_view_grep_composes_with_level_filter` — `[E]` + grep `stage_42` → only ERROR lines mentioning `stage_42`.
- `test_emr_unreachable_falls_back_to_pane_placeholder` — monkeypatch boto endpoint to raise `EndpointConnectionError`, assert UNREACHABLE state + `r`-to-retry placeholder.
- `test_switch_aws_connection_rebuilds_emr_client` — switch active AWS connection mid-session, assert the page's `EmrServerlessClient` rebuilds against the new profile.

**Snapshot tier** (10 themes × N widgets, each paired with a content-presence guard):

- `test_emr_page` — empty / loaded / loading
- `test_emr_application_picker` — collapsed / expanded with state glyph
- `test_emr_job_runs_pane` — populated / empty / loading / unreachable
- `test_emr_job_run_detail_pane` — terminal / running with log
- `test_emr_log_view` — chips off / chips on / grep active / no-log-configured
- `test_emr_submit_modal` — vanilla blank / clone pre-filled / invalid-input state

**E2E** — Journey 6 in `tests/e2e/test_journeys.py`: cold-start with valid SSO → select ⚡ EMR → pick first application → cancel a RUNNING run → confirm via modal → assert state transitions to CANCELLING. No-network mode using mocked client.

### 1.7.2. Decomposition — four shippable PRs

v1 is too big for a single PR. Four pieces, each independently mergeable, each delivering user-visible value.

#### 1.7.2.1. PR-A — Read-only browser (~3 days, ~1.5k LOC)
- `EmrServerlessClient` with `list_applications`, `list_job_runs`, `get_job_run`
- `EmrServerlessService`, page VM, application picker, job-runs pane, job-run-detail pane (no log surface yet)
- State filter chips on LEFT pane
- Auto-refresh (apps 30 s, runs 10/60 s, detail 5 s)
- Service registered in `composition.py` → ⚡ icon appears
- Snapshot + integration tests for the static surface
- Design-language commitments from §3 wired in

**Acceptance:** user can switch to ⚡ EMR, pick an app, browse runs by state, drill into detail. No log, no submit, no cancel. The 80% read path of daily monitoring.

#### 1.7.2.2. PR-B — Log surface + cancel + lifecycle (~3 days, ~1.5k LOC)
- `LogSource` Protocol + `EmrS3LogSource` impl
- `LogViewVM` + `LogView` widget with chip row, grep, range-GET polling, decompressed tempfile cache
- `cancel_job_run` on the client + `x` keybinding + ConfirmModal wiring
- `start_application` / `stop_application` on the client + top-strip button + `s` keybinding + ConfirmModal-on-stop
- Per-event toast notifications via the unified helpers
- LogView snapshot + integration tests

**Acceptance:** user can monitor a running job, cancel a misbehaving run, lifecycle an app.

#### 1.7.2.3. PR-C — Submit (vanilla + clone) (~3 days, ~1.2k LOC)

> **Status (post-PR-83): the clone half shipped early.** PR #83
> landed `JobRunCloneVM` + `JobRunCloneModal` +
> `EmrServerlessClient.start_job_run` + the `c` keybinding +
> the `emr.clone` keymap action + the `"Job"` `Subject` literal,
> ahead of the rest of PR-C, as part of the second post-PR-82
> user-feedback batch. The clone modal pre-fills name / entry
> point / IAM / args / spark params from the focused run and
> fires `start_job_run` on save. What's still outstanding for the
> remainder of PR-C: the vanilla blank-form submit
> (`SubmitFormVM` + `EmrSubmitJobModal` widget + the `+` entry
> point on the top strip) and the `submitted-via=aws-tui` tag
> wiring.

- `SubmitFormVM` with full validation
- `EmrSubmitJobModal` widget — `ConfirmModal`-shaped layout
- `+` (vanilla) — **outstanding**
- `c` (clone-from-focused-run) — **shipped in PR #83** via the
  separate `JobRunCloneVM` + `JobRunCloneModal` (the design's
  original "one modal serving both entry points" simplification
  may be revisited when the vanilla form lands; the two surfaces
  diverged enough during PR #83 that keeping them as two
  modals is the current direction)
- `start_job_run` on the client with `submitted-via=aws-tui` tag
  — the API method shipped in PR #83 without the tag; tag wiring
  is part of the vanilla-submit follow-up
- Snapshot + integration tests for both modes — clone-side
  shipped in PR #83 (`tests/snapshot/test_emr_clone_modal.py` +
  `tests/unit/vm/emr_serverless/test_clone_vm.py`)

**Acceptance:** user submits a brand-new job AND clones-and-edits an existing run. Full daily-driver loop closed. (Half closed today — clone works; vanilla submit pending.)

#### 1.7.2.4. PR-D — E2E + memory updates (~half a day, no production code)
- Journey 6 in `test_journeys.py`
- CHANGELOG entry: ⚡ EMR Serverless service v1.0
- Memory file updates for the new service
- Add `"Job"` to the `Subject` literal in `notifications.py`

### 1.7.3. Open risks

1. **gunzip CPU on every refresh tick.** Decompressing 100 MB of gzip into a Python deque every 5 s would burn CPU. Mitigation: streaming `GzipFile` over the tempfile descriptor, decode line-by-line, never hold a full decompressed copy in memory. Cost amortised across reads.
2. **Application picker dropdown widget.** Textual has no native dropdown. PR-A builds the `OptionList`-in-a-popover (~1 day). Alternative — horizontal chip row — rejected because users with >5 apps would need horizontal scroll.
3. **Boto session sharing across services.** S3 builds its own `aioboto3.Session` per `Connection`. EMR does the same. When the active AWS connection changes, both services rebuild. The existing `ConnectionChangedMessage` hub fanout handles this — regression test `test_switch_aws_connection_rebuilds_emr_client` is in PR-A.
4. **SSO token expiry mid-poll.** A 5-second detail-poll on a 60-minute job could outlive the SSO token. The error-state propagation in §6 covers this, but the in-flight `LogView` needs to pause polling on AUTH_REQUIRED rather than retry-spam. Handled by the §6 back-off; pinned in a test.

### 1.7.4. What v1 ships

- List applications (with state).
- List job runs per application, filtered by state (multi-select chips).
- Drill into job-run detail (state, timings, IAM, entry point, args, Spark params).
- Tail S3 logs with level chips (`E W I D T`) + custom regex grep, range-GET polling, last-2000-matching-lines deque.
- Cancel a focused run (ConfirmModal-confirmed).
- Start / Stop application (ConfirmModal-confirmed on stop only).
- Submit a job — blank form OR cloned-and-edited from any existing run.
- All design-language commitments from §3 wired across all 10 themes.

### 1.7.5. What v1 deliberately does NOT ship

- Hive jobs (Spark only).
- Configuration overrides at submit (capacity, network, monitoring — inherited from app).
- Application creation / deletion.
- CloudWatch Logs as a source (the `LogSource` Protocol makes this a v2 add-on).
- Cost metrics (CloudWatch vCPU-hours / memory-hours).
- Bulk operations.
- Multi-stream log multiplex.
- Job-step templates / saved job library.

---

## 1.8. Next step

Once this spec is approved, the implementation plan (via the writing-plans skill) decomposes PR-A into per-task work items with file paths, line-level edits where applicable, and pass-criteria. PR-A is the first piece; B/C/D follow in sequence.
