# 1. Demo Mode — Design

## 1.1. Goal

Let anyone run aws-tui end-to-end without real AWS credentials or
a local S3-compatible service. Triggered by an environment
variable OR a CLI flag set before launch, the app boots with
synthetic connections backed by in-memory fakes for every shipped
service (S3 + EMR Serverless). Mutations (delete files, submit
clone jobs, copy across panes) actually work in-session — users
genuinely test every feature, not just browse.

The headline target: `pipx install aws-tui && AWS_TUI_DEMO=1
aws-tui` produces a fully functional app.

## 1.2. Non-goals

- Persistent demo state across launches — fresh per launch.
- Configurable demo seeds (`AWS_TUI_DEMO_SEED=large` etc.) — one
  curated showcase, opinionated.
- Replaying real-world AWS traces as demo data.
- A guided-tour overlay for new users.
- Localized demo content.
- Multi-tenant demo isolation — single-process state.

## 1.3. Architecture — 5 components

### 1.3.1. Public API (`src/aws_tui/demo/__init__.py`)

```python
DEMO_ENV_VAR: Final[str] = "AWS_TUI_DEMO"

def is_demo_mode_enabled(*, argv: Sequence[str] | None = None) -> bool:
    """True when env var is truthy OR ``--demo`` appears in argv."""
```

Truthy values: `"1"`, `"true"`, `"yes"` (case-insensitive); all
others false. The `argv` parameter is for testing; defaults to
`sys.argv`.

### 1.3.2. In-memory fakes (`demo/in_memory_fs.py`, `demo/in_memory_emr.py`)

Moved from `tests/unit/domain/_in_memory_*.py`. Public symbols
renamed: `_InMemoryFS` → `InMemoryFS`, `_InMemoryEmr` →
`InMemoryEmr`. Three production-polish changes from the test
versions:

1. **EMR clone state machine.** `InMemoryEmr.start_job_run()`
   returns a new `job_run_id` immediately AND schedules an async
   walk:
   - `SUBMITTED` (immediately) → `SCHEDULED` (after 1s) →
     `RUNNING` (after 2s) → `SUCCESS` (after 5s).
   - The demo EMR poller (5s cadence) picks up state changes on
     its tick; users see the row's colored glyph change in real
     time.
   - Scheduled tasks are tracked in a set and cancelled on
     `dispose()` so repeated submits don't leak unawaited tasks
     across runs.

2. **Realistic timing.** All async methods get an
   `await asyncio.sleep(0.05)` at entry to simulate ~50 ms
   network latency. Without this the fake responds
   instantaneously and the UI's `loading…` placeholders never
   appear — misleading for a demo.

3. **Cross-pane copy support on InMemoryFS.** The existing test
   fake's `read_stream()` + `write_stream()` already work
   between two `InMemoryFS` instances; the move just renames
   them as public API and adds a small contract test.

Test files become one-line re-export shims; existing
`~140` tests in `tests/unit/domain/_in_memory_*.py` and
downstream paths continue to pass without edits:

```python
# tests/unit/domain/_in_memory_emr.py (after move)
from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr  # noqa: F401
```

### 1.3.3. Demo data seeds (`demo/seeds.py`)

Pure-data fixtures, no logic:

```python
def seed_s3_data(fs: InMemoryFS, *, profile: str) -> None: ...
def seed_emr_data(emr: InMemoryEmr) -> None: ...
def seeded_demo_fs(profile: str) -> InMemoryFS: ...
def seeded_demo_emr() -> InMemoryEmr: ...
```

**S3 seed per profile** (3 profiles → 3 distinct showcases):

| Profile | Buckets | Sample objects |
|---|---|---|
| `demo-dev` | `etl-input/`, `etl-staging/` | 10 objects: `raw/events/2026-06-{25,26,27}.json.gz`, `inbox/manifest.csv`, etc. |
| `demo-prod` | `data-lake/`, `etl-output/` | 10 objects: `silver/customers/year=2026/month=06/part-0000.parquet`, `gold/marts/sales/snapshot.parquet`, mixed sizes 2 KB–510 MB |
| `demo-shared` | `assets/`, `archive/` | 6 objects: `logo.png`, `report-2026Q2.pdf`, `backup-{01..03}.tar.gz` |

**EMR seed** (one fixture, shared across all aws-kind demo
connections — see component 5 for the singleton rationale):

- 2 applications: `etl-pipeline-1` (STARTED),
  `ad-hoc-queries` (STOPPED)
- 10 job runs spread over the last 7 days:
  - 6 SUCCESS (4 on `etl-pipeline-1`, 2 on `ad-hoc-queries`)
  - 2 FAILED without fake S3 log URIs; the UI surfaces typed no-log
    states instead of linking to nonexistent demo logs
  - 1 RUNNING
  - 1 PENDING
- Fake failed-run S3 log URIs are intentionally not seeded in the
  production demo fixture; that earlier design note is superseded by
  the no-log state above.

### 1.3.4. Demo connection resolver (`demo/connections.py`)

```python
def demo_connections() -> tuple[Connection, ...]:
    return (
        Connection(name="demo-dev",    kind="aws",
                   region="us-east-1", source="demo", profile="demo-dev"),
        Connection(name="demo-prod",   kind="aws",
                   region="us-east-1", source="demo", profile="demo-prod"),
        Connection(name="demo-shared", kind="aws",
                   region="us-west-2", source="demo", profile="demo-shared"),
        Connection(name="demo-minio",  kind="s3-compatible",
                   region="us-east-1", source="demo", profile=None),
    )

class DemoConnectionResolver:
    """Drop-in for ``ConnectionResolver`` in demo mode."""
    def list(self) -> tuple[Connection, ...]: return demo_connections()
    def default(self) -> Connection | None:   return demo_connections()[0]
```

Shift+S cycle, Settings panel, and boot-chain all treat these as
real.

### 1.3.5. Composition-root branch (`app.py` + `main()`)

The `main()` entrypoint and `build_app_context()` each get a
single conditional:

```python
def main() -> None:
    demo = is_demo_mode_enabled()
    ctx = build_app_context(demo=demo)
    AwsTuiApp(ctx).run()

def build_app_context(*, demo: bool = False) -> AppContext:
    if demo:
        connection_resolver = DemoConnectionResolver()
        s3_fs_factory      = lambda c: seeded_demo_fs(c.profile or "demo-default")
        emr_client_factory = lambda c: _demo_emr  # per-AppContext singleton
    else:
        connection_resolver = ConnectionResolver(...)  # existing
        s3_fs_factory      = None
        emr_client_factory = None
    # rest of build_app_context unchanged
```

`build_app_context(demo=True)` captures one `_demo_emr` per app
context so the EMR fake stays stable across connection switches.
Without the captured instance, switching from `demo-dev` to
`demo-prod` would re-seed EMR data and jarringly reset the user's
in-flight clone progressions.

**Poller cadence in demo mode.** EMR page's
application/run/detail pollers use 30/30/5 seconds (production uses
60/60/30). Detail stays at 5 seconds so the clone state walk remains
visible on the selected run.

**Visible affordances.**
- BrandBanner subtitle reads
  `"DEMO MODE — no real AWS calls"` in `$warning`. Persistent.
- Boot toast: one-shot Advisory on mount —
  `"Demo mode active — AWS data resets; local pane is real"`.
- `aws-tui --version` prints `demo: enabled` when env var is set,
  so a stuck shell rc surfaces on the first version check.

### 1.3.6. Layer rules update (`scripts/check-layers.sh`)

Add `aws_tui.demo` as an allowed-but-restricted layer:
- `aws_tui.demo` CAN import from `aws_tui.domain`,
  `aws_tui.infra`, `aws_tui.vm`.
- Nothing in `aws_tui.domain`, `aws_tui.infra`, `aws_tui.vm`,
  `aws_tui.services`, `aws_tui.ui` can import from
  `aws_tui.demo`.
- Only `aws_tui.app` and `tests/` are allowed importers.

## 1.4. Triggers

- `AWS_TUI_DEMO=1` env var (truthy values: `1`, `true`, `yes`).
- `--demo` long flag on `aws-tui`.
- Either alone enables demo. No conflict if both set.

## 1.5. Persistence

Fresh AWS/S3/EMR demo state per launch. The S3 and EMR fakes do not
survive exit. The Local file pane is the user's real `LocalFS` root,
so local file operations are real host filesystem operations.

## 1.6. Risks and mitigations

- **User confuses demo for real.** Persistent `DEMO MODE` chip in
  the BrandBanner subtitle (warning color, never goes away), plus
  the boot toast, plus visibly fake connection names
  (`demo-dev`, `demo-prod`, etc.).
- **Demo fakes accidentally consumed in production code paths.**
  Layer-rule entry in `check-layers.sh` blocks the import; CI
  fails.
- **EMR clone state machine leaks `asyncio.Task` references.**
  `InMemoryEmr` tracks scheduled tasks in a set; `dispose()`
  cancels pending tasks. Test pins this.
- **Demo poller cadence burns CPU on long sessions.** Cadence
  decay from PR #93 engages after the one RUNNING demo run
  reaches SUCCESS (~5s after mount). Idle from there.
- **User configures `AWS_TUI_DEMO=1` in shell rc by accident.**
  `DEMO MODE` chip is the visible cue; `aws-tui --version`
  prints `demo: enabled` so a stuck env var surfaces.

## 1.7. Rollback

- Per-session off: `unset AWS_TUI_DEMO && aws-tui`.
- Feature kill switch: hardcode `is_demo_mode_enabled()` to
  `return False`. One-line, immediately blocks all entry points.
- The fakes themselves are useful to tests regardless of whether
  the runtime feature ships; a full rollback of the composition-
  root branch leaves the test shims intact.

## 1.8. Testing strategy

- **Unit (existing 140+ tests)** keep passing via the re-export
  shims, no edits.
- **Unit (new) 6–8 tests** in `tests/unit/demo/` covering
  `is_demo_mode_enabled()` (env var truthy/falsy combos,
  `--demo` flag, both at once), `demo_connections()`, the
  per-AppContext demo EMR stability across connection switches,
  and `seeded_demo_emr()`'s clone state machine with a mocked
  async clock.
- **Integration (new) 1 test** at
  `tests/integration/test_demo_mode.py` that boots
  `AwsTuiApp(build_app_context(demo=True))` headlessly, walks
  Shift+S → all 4 demo connections cycle, presses Enter into
  S3, confirms an `InMemoryFS` file appears in the LEFT pane,
  switches to EMR, confirms the application picker shows 2 apps,
  cursors to a FAILED job and Enter into logs, confirms log
  lines render with grep matches.
- **Snapshot (new) 10 themes × 1 demo-mode-on screen** —
  BrandBanner with DEMO chip + S3 pane populated. Content-
  presence guard asserts `DEMO MODE` text + at least one demo
  bucket name in the SVG.
- **Manual smoke (per release)** — captured in
  `docs/RELEASING.md` as a pre-tag checklist: run
  `AWS_TUI_DEMO=1 aws-tui`, verify the 4 connections, copy
  across panes, submit a clone, watch its state progress, edit
  the filter.

## 1.9. What we have when done

- `aws-tui --version` mentions `demo: enabled` in demo mode.
- `AWS_TUI_DEMO=1 aws-tui` (or `aws-tui --demo`) launches with 4
  visible demo connections, demo S3 contents, demo EMR runs,
  working Clone / Copy / Delete / Logs.
- Persistent `DEMO MODE` chip and a one-shot boot toast.
- Existing tests passing unchanged + 6–8 new unit tests + 1 new
  integration test + 10 snapshot variants.
- Layer rules updated; `check-layers.sh` keeps prod code from
  accidentally importing demo fakes.
- Docs updated (README install section mentions demo mode in a
  callout; `docs/RELEASING.md` includes a manual demo-mode
  smoke).
