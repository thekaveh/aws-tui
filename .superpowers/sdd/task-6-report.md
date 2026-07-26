# Athena Task 6 Report

## Status

Implemented multi-profile Athena demo data, deterministic in-memory query
behavior, exact-profile S3 result handoff, stale-row protection during real
profile switches, and the ten normal-color demo snapshot updates.

## TDD Evidence

### Initial RED

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/unit/demo/test_in_memory_athena.py \
  tests/integration/test_athena_s3_handoff.py \
  tests/integration/test_demo_mode.py -q

ERROR tests/unit/demo/test_in_memory_athena.py
ERROR tests/integration/test_athena_s3_handoff.py
ModuleNotFoundError: No module named 'aws_tui.demo.in_memory_athena'
```

After the fake was added, the handoff tests reached the expected action RED:

```text
6 failed
UnknownAction: athena.open_result_location
```

The lifecycle regression test also failed before the queued-callback guard:

```text
pytest tests/unit/ui/athena/test_page.py::test_queued_page_refresh_is_safe_after_descendants_are_removed
NoMatches: No nodes match <class 'AthenaQueryView'> on AthenaPage()
```

The first normal-color demo snapshot run produced exactly the expected Athena
baseline delta:

```text
10 failed, 10 passed
```

### Focused GREEN

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest \
  tests/unit/demo/test_in_memory_athena.py \
  tests/unit/vm/athena/test_history_vm.py \
  tests/unit/vm/athena/test_results_vm.py \
  tests/unit/ui/athena/test_page.py \
  tests/integration/test_athena_s3_handoff.py \
  tests/integration/test_demo_mode.py \
  tests/e2e/test_journeys.py -q

63 passed in 14.74s
```

The final demo integration run promoted leaked-coroutine warnings to errors:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/integration/test_demo_mode.py \
  -W error::RuntimeWarning -q

7 passed in 3.94s
```

## Implementation

- Added a fresh, instance-owned `InMemoryAthena` per demo AWS profile. It
  implements the Task 2 client surface, records repr-safe calls, paginates
  deterministically, keeps request-token idempotency local, and creates no
  background tasks.
- Seeded disjoint development, production, and shared profile workgroups,
  catalogs, databases, tables, query history, saved queries, prepared
  statements, outcomes, result pages, and S3 objects. Scenarios cover running,
  succeeded, failed, cancelled, empty, access denied, and missing output.
- Limited state progression to app-started queries:
  `QUEUED -> RUNNING -> SUCCEEDED`. Historical rows remain static, and stop
  only affects active app-started executions.
- Wired demo Athena clients through `build_app_context()` without module-global
  fake state.
- Added `athena.open_result_location` to the command palette. History uses its
  hydrated execution detail; Results reloads authoritative execution metadata
  before publishing the existing service-neutral `OpenS3LocationRequest`.
- Reused the hardened app-owned S3 transaction unchanged. The request carries
  exact connection, region, URI, and preferred pane; path and identity survive
  the handoff, failures roll back, and malformed or missing locations remain
  advisory without automatic navigation.
- Added teardown guards for queued Athena page refresh/focus callbacks after
  the service view is removed.
- Added integration and E2E coverage for exact-profile handoff, authoritative
  metadata, malformed/missing locations, no automatic handoff, identity
  mismatch rejection, disjoint profile data, and empty new rows while a real
  profile switch is still loading.
- Updated all ten demo snapshot themes under normal color. They now include
  the Athena service row and `athena-results/` bucket.

## Full Verification

### Functional suite

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/unit tests/integration tests/e2e -q

1898 passed, 9 deselected in 275.46s (0:04:35)
```

That run reported two warnings caused by the test forcing a private view
refresh and swapping profiles before the resulting selection worker drained.
The test now drains those workers; the warning-as-error demo run above verifies
the correction.

### Normal-color snapshots

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/snapshot -q

744 passed in 125.84s
437 snapshots passed
```

### Static and layer gates

```text
uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
370 files already formatted

uv run mypy src
Success: no issues found in 149 source files

bash scripts/check-layers.sh
layer rules clean

git diff --check
(no output; exit 0)
```

## Security and Lifecycle Review

- No SQL, result rows, continuation tokens, output URIs, or provider exception
  text is logged. Recorded fake arguments are excluded from repr output.
- Handoff requires a succeeded execution with coherent execution/context
  identity and a valid S3 URI. Results metadata is fetched again at action time.
- Existing app orchestration resolves the exact profile, rejects region drift,
  preserves requested pane/path, contains mount/navigation failures, and
  redacts advisories.
- Demo fake mutable state and app-started lifecycle state are instance-local;
  no fake, task, or query state is shared globally.

## Concerns

- Every shell command prints a pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; it does not affect command exit status.
