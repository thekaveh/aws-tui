# Glue Task 6 Report

## Status

Implemented the immutable Glue-to-S3 navigation request, exact-connection
handoff, advisory failure behavior, command-palette entry, integration/E2E
coverage, and all documentation surfaces named by the Task 6 brief.

The functional, lint, formatting, typing, layer, docs, and build gates pass.
The color-enabled snapshot gate has 75 pre-existing EMR/Glue baseline
mismatches; the untouched parent commit `46c1076` reproduces the identical
result.

## Changed Files

- `.superpowers/sdd/task-6-report.md`
- `CHANGELOG.md`
- `README.md`
- `docs/adding-a-service.md`
- `docs/architecture.md`
- `docs/connections.md`
- `docs/contract-ledger.md`
- `docs/cookbook.md`
- `docs/keybindings.md`
- `src/aws_tui/app.py`
- `src/aws_tui/vm/glue/catalog_vm.py`
- `src/aws_tui/vm/messages.py`
- `tests/docs/test_connections.py`
- `tests/e2e/test_journeys.py`
- `tests/integration/test_command_palette_wiring.py`
- `tests/integration/test_glue_s3_handoff.py`
- `tests/unit/vm/test_messages.py`

## Implementation

- Added frozen, slotted `OpenS3LocationRequest` with explicit
  `connection_name`, `region`, `uri`, requested pane, and the service-neutral
  `service_navigation` sender.
- Added `GlueCatalogVM.open_s3_location()`. It validates the selected table's
  location and publishes plain immutable data without importing UI, S3
  services, or infrastructure.
- Registered `glue.open_s3_location` and exposed it as the palette-only
  **Open table location in S3** command.
- Added one app-owned message subscription. The handler resolves the exact
  connection name, rejects region drift, probes that connection, performs the
  existing `RootVM.switch_connection_and_service()` lifecycle, mounts through
  `build_service_view`, binds and navigates the requested pane, and focuses it.
- Suppressed the ordinary nav-selection mount while that atomic handoff owns
  the mount. This prevents two `DualPane` trees from racing into the same
  Textual host.
- Missing, blank, malformed, missing-connection, region-mismatch, and mount
  failures remain advisory. Toasts and structured logs do not include the
  requested URI.

## TDD Evidence

### Initial message and integration RED

Command:

```text
uv run pytest tests/unit/vm/test_messages.py tests/integration/test_glue_s3_handoff.py -q
```

Captured result:

```text
ERROR tests/unit/vm/test_messages.py
ERROR tests/integration/test_glue_s3_handoff.py
Interrupted: 2 errors during collection
ImportError: cannot import name 'OpenS3LocationRequest' from 'aws_tui.vm.messages'
```

The E2E regression independently failed before action registration:

```text
uv run pytest tests/e2e/test_journeys.py::test_journey_7_glue_table_opens_s3_under_same_profile -q
UnknownAction: glue.open_s3_location
1 failed in 0.66s
```

After adding only the request envelope, the same unit/integration command
reached the expected subscriber/action RED:

```text
5 failed, 19 passed, 10 rerun in 16.96s
```

### Mount-race diagnosis

The first implementation run exposed a real lifecycle race:

```text
2 failed, 23 passed, 2 rerun in 126.30s
```

Both failures were Textual `WaitForScreenTimeout`. The captured app log showed
`app.mount_service_view.mount_failed` with duplicate id
`content-dual-pane`. `RootVM` correctly changed the selected service, but that
selection message scheduled the ordinary nav mount while the handoff worker
was also mounting S3. Gating that subscriber only during the app-owned
handoff removed the duplicate mount.

### Focused GREEN

```text
uv run pytest tests/unit/vm/test_messages.py tests/integration/test_glue_s3_handoff.py tests/e2e/test_journeys.py::test_journey_7_glue_table_opens_s3_under_same_profile -q
25 passed in 3.40s
```

The palette regression was also written before its registry entry:

```text
uv run pytest tests/integration/test_command_palette_wiring.py::test_populate_registers_curated_commands -q
1 failed, 2 rerun in 2.73s
```

After registration:

```text
1 passed in 0.24s
```

## Full Verification

### Functional suite

```text
uv run pytest tests/unit tests/integration tests/e2e -q
1462 passed, 9 deselected in 225.34s (0:03:45)
```

### Color-enabled snapshots

The required variables were explicitly unset:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR uv run pytest tests/snapshot -q
75 failed, 549 passed, 1 warning in 87.96s (0:01:27)
```

Failures are exactly 20 EMR and 55 Glue snapshot cases. No Task 6 snapshot
test, app, theme, stylesheet, EMR widget, or Glue widget was modified.

To isolate the baseline, a detached worktree at the untouched Task 6 parent
`46c1076` was tested with the current locked test environment:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR PYTHONPATH=/tmp/aws-tui-task6-baseline-46c1076/src /Users/kaveh/repos/aws-tui/.venv/bin/pytest tests/snapshot -q
75 failed, 549 passed, 1 warning in 90.24s (0:01:30)
```

The test names and failure sequence were identical. Task 5's report records
that earlier EMR/Glue baselines were generated under `NO_COLOR=1`; its
follow-up commit repaired only demo snapshots. Task 6 does not rewrite those
unrelated visual baselines.

### Static and package gates

```text
uv run ruff check .
All checks passed!

uv run ruff format --check .
341 files already formatted

uv run mypy src
Success: no issues found in 129 source files

./scripts/check-layers.sh
layer rules clean

uv run pytest tests/docs -q
51 passed, 2 skipped in 0.29s

uv build
Successfully built dist/aws_tui-0.8.0.tar.gz
Successfully built dist/aws_tui-0.8.0-py3-none-any.whl

git diff --check
(no output; exit 0)
```

The two docs skips are the existing optional Cairo-renderer skips
(`cairosvg/libcairo unavailable`).

The first docs run found task-caused wording drift:

```text
1 failed, 50 passed, 2 skipped in 0.29s
```

Restoring the established S3-pane reachability phrase exposed a stale test
assertion requiring future `Glue/Athena` wording. The assertion was updated to
the shipped `EMR Serverless and Glue` wording required by this task; the final
docs run above is green and no Athena link was added.

## Documentation

- `README.md` and `CHANGELOG.md` now identify Glue as a first-class read-only
  service, describe its three views, exact-profile S3 handoff, profile
  switching, and demo behavior.
- `docs/architecture.md` and `docs/adding-a-service.md` document the Glue
  service/VM/view factory shape, the service-neutral request, app-owned
  cross-service orchestration, and layer direction.
- `docs/connections.md` documents explicit-config-then-discovered resolver
  order, whole-service profile switching, scoped selection memory,
  service-local access failures, exact handoff resolution, and region
  mismatch rejection.
- `docs/cookbook.md` lists the shipped Glue and STS API permissions and S3
  browsing permissions, read-only workflows, profile isolation, handoff,
  advisories, and offline demo states.
- `docs/contract-ledger.md` records locked botocore `1.40.61`, Glue API model
  `2017-03-31`, every consumed operation, and the exact identity contract.
- `docs/keybindings.md` records `1`/`2`/`3`, `r`, `Shift+S`, focus/navigation
  controls, and the palette-only S3 command.
- No Iceberg metadata or Athena cross-link behavior is documented.

## Self-Review

- Confirmed resolver iteration remains explicit configuration followed by
  non-colliding discovered profiles; the handoff calls `resolve()` for the
  carried name rather than iterating for a substitute.
- Confirmed region equality is checked before root/service mutation.
- Confirmed the selected Glue connection survives through the S3 provider
  binding and active `RootVM` identity.
- Confirmed successful navigation uses
  `PathRef.from_posix(parsed.netloc + parsed.path)` and focuses the requested
  pane.
- Confirmed malformed/missing locations leave Glue mounted and advisories do
  not contain credentials, query tokens, hosts, buckets, or full URIs.
- Confirmed the VM layer emits a plain message and keeps the required
  View -> VM -> Service -> Domain -> Infrastructure direction.
- Confirmed the feature remains read-only and introduces no Athena or Iceberg
  behavior.
- Confirmed all requested documentation surfaces were changed and checked.

## Concerns

- The color-enabled snapshot suite has the 75 proven pre-existing EMR/Glue
  baseline mismatches described above. All 549 other cases pass.
- Every shell command prints a pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; it does not affect command exit status.
