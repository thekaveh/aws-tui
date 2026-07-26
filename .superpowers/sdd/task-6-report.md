# Glue Task 6 Report

## Status

Implemented the immutable Glue-to-S3 navigation request, exact-connection
handoff, advisory failure behavior, command-palette entry, integration/E2E
coverage, and all documentation surfaces named by the Task 6 brief.

The Important review follow-up documented at the end of this report resolves
the 75 color-enabled baseline mismatches, contains handoff failures and mount
concurrency, and corrects the stale shipped-behavior docs. All requested gates
now pass. The original investigation and failing snapshot evidence remain
below as historical RED evidence.

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

- Every shell command prints a pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; it does not affect command exit status.

## Important Review Follow-up

### Scope and root causes

- Regenerated only the 20 EMR and 55 Glue goldens that had been captured under
  `NO_COLOR=1`; CI and the normal local command leave all color-control
  variables unset.
- Changed `_mount_service_view()` from an ambiguous `None` result to a reliable
  `bool`: `True` only after a view is mounted, `False` after contained
  switch/missing-VM/mount failures. Existing callers may continue to ignore the
  return value.
- Contained S3 mount, bind, navigation, and focus failures behind one
  stage-specific advisory/log helper. It records connection name, stage, and
  exception type only; it never retains the request URI or exception text.
- Moved handoff workers from `service-navigation` to the same exclusive
  `content-mount` group used by ordinary service navigation. The internal
  RootVM selection message remains suppressed during the atomic connection
  switch, while a later user navigation cancels and supersedes the in-flight
  handoff through the existing Textual worker semantics.
- Corrected current-behavior claims in `README.md`, `docs/cookbook.md`,
  `docs/keybindings.md`, and the Unreleased `CHANGELOG.md` section. Historical
  specs/changelog entries were not rewritten, and no Athena or Iceberg behavior
  was added.

### RED evidence

Tests were added before production or documentation edits:

```text
uv run pytest \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_mount_failure_is_reported_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_bind_failure_is_contained_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_navigation_failure_is_contained_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_overlapping_handoff_and_nav_mounts_are_serialized \
  tests/docs/test_shipped_behavior.py -q
```

Captured result:

```text
8 failed, 8 rerun in 15.16s
```

The failures proved each reported defect:

- mount returned `[None]` instead of `[False]`;
- bind and navigation raised the injected `RuntimeError` out of the handoff;
- overlapping handoff/nav work produced a cancelled/racing
  `content-mount` worker;
- all four docs tests found the cited stale runtime/palette/demo claims.

Each injected exception included
`s3://private-bucket/events/?token=HANDOFF_SECRET` so the GREEN assertions
prove neither the URI nor token reaches the advisory or durable log.

### Focused GREEN evidence

```text
uv run pytest \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_mount_failure_is_reported_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_bind_failure_is_contained_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_navigation_failure_is_contained_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_overlapping_handoff_and_nav_mounts_are_serialized -q
4 passed in 1.96s

uv run pytest tests/docs/test_shipped_behavior.py -q
4 passed in 0.01s

uv run pytest tests/unit/vm/test_messages.py \
  tests/integration/test_glue_s3_handoff.py \
  tests/integration/test_command_palette_wiring.py \
  tests/docs/test_shipped_behavior.py -q
36 passed in 6.15s
```

The overlap regression gates the first S3 mount, selects Glue while it is in
flight, and proves the latest ordinary navigation wins with one active mount,
one `GluePage`, zero `DualPane` widgets, unique top-level IDs, and matching
nav/content/root connection state.

### Snapshot regeneration and inspection

The update command explicitly removed every supported color-control variable:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  uv run pytest tests/snapshot/test_emr.py tests/snapshot/test_glue.py \
  --snapshot-update -q
75 snapshots updated.
105 passed in 24.28s
```

Post-update inventory:

```text
changed snapshot files: 75
tests/snapshot/__snapshots__/test_emr: 20
tests/snapshot/__snapshots__/test_glue: 55
unrelated snapshot paths: 0
```

A programmatic old/new SVG text-node comparison reported:

```text
checked=75 semantic_text_mismatches=0
```

Rendered PNG inspection covered EMR populated Carbon, Glue Catalog populated
Carbon, Glue Jobs populated GitHub Light, Glue forbidden Dracula, and compact
Glue Crawlers. Layout, source labels, data, empty/forbidden semantics, and
compact framing were unchanged. The intended differences restore each theme's
backgrounds, selections, accents, and semantic status/error colors instead of
the prior grayscale `NO_COLOR` output.

The complete normal-color tier then passed:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  uv run pytest tests/snapshot -q
329 snapshots passed.
624 passed in 89.78s (0:01:29)
```

### Final verification

```text
uv run pytest tests/unit tests/integration tests/e2e -q
1466 passed, 9 deselected in 257.00s (0:04:16)

uv run pytest tests/docs -q
55 passed, 2 skipped in 0.29s

make docs-check
check_docs: clean
Documentation built in 0.40 seconds

uv run ruff check .
All checks passed!

uv run ruff format --check .
342 files already formatted

uv run mypy src
Success: no issues found in 129 source files

./scripts/check-layers.sh
layer rules clean

uv build
Successfully built dist/aws_tui-0.8.0.tar.gz
Successfully built dist/aws_tui-0.8.0-py3-none-any.whl

git diff --check
(no output; exit 0)
```

The two docs skips are the existing optional direct-`uv run` Cairo renderer
skips. `make docs-check` supplies Homebrew's Cairo path and completed the
renderer/check/strict-build path. Its Material for MkDocs 2.0 notice is an
upstream warning; the command exited 0. The generated
`docs/diagrams/img/architecture.png` was restored after the check because no
architecture source changed.

### Follow-up self-review

- Mount failure leaves RootVM content, selected nav service, and active
  connection mutually consistent at S3 even if the Textual host cannot mount
  the widget. Bind/navigation failures leave one mounted S3 tree with matching
  root identity.
- `asyncio.CancelledError` is not caught by the advisory `Exception` handlers,
  so Textual cancellation and app disposal continue to propagate normally.
- No exception text, request URI, query token, bucket, or credential is logged
  by the new handoff failure path.
- Current docs consistently describe runtime `BindingResolver` overlays,
  shipped Quick Look and command palette handlers, dynamic palette items that
  remain deferred, and Glue-backed demo mode.
- No unrelated snapshots or generated docs artifacts remain in the diff.

## Second Important Review Follow-up

### Status and implementation

Resolved the remaining exact-profile, mount-result, transactional rollback,
and README findings.

- `_mount_service_view()` now accepts an optional `required_connection`.
  Exact-profile handoffs pass the resolved connection, verify that it is still
  RootVM's active connection, and bypass the session-wide local-fallback retry
  branch. Ordinary S3 nav selections retain the existing retry behavior.
- `_mount_local_only_dual_pane()` now returns `True` only after the local
  `DualPane` widget has mounted. Missing-journal, content-adoption, and widget
  mount failures return `False`; `_mount_service_view()` propagates that result
  instead of returning unconditional success.
- The Glue-to-S3 handler captures the prior connection, auth state, and service
  before mutating RootVM. An S3 switch or mount failure rebuilds and remounts
  that prior state before raising the redacted advisory.
- Bind and navigation failures retain the already-mounted S3 page because its
  root, nav, content VM, mounted widget, and remote pane remain coherent and
  usable.
- README current-behavior sections now distinguish the deferred
  `auth.authenticate` handler from the shipped `BindingResolver` and
  `[keybindings]` overlay behavior.

### RED evidence

Tests were added or strengthened before production and README edits:

```text
uv run pytest \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_ignores_stale_fallback_retry_and_keeps_exact_profile \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_mount_failure_is_reported_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_bind_failure_is_contained_and_redacted \
  tests/integration/test_glue_s3_handoff.py::test_s3_handoff_navigation_failure_is_contained_and_redacted \
  tests/integration/test_settings_flow.py::test_s3_selection_propagates_failed_local_fallback_mount \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_true_only_with_mounted_view \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_false_without_transfer_journal \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_false_when_content_adoption_fails \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_false_when_widget_mount_fails \
  tests/docs/test_shipped_behavior.py::test_readme_describes_shipped_runtime_bindings_quick_look_and_palette -q
```

Captured result:

```text
8 failed, 2 passed, 14 rerun in 23.91s
```

The failures showed:

- the stale fallback retry ran for `demo-prod` after an exact `demo-dev`
  handoff;
- the fallback caller returned `True` after its local mount helper returned
  `False`;
- every local-helper result assertion received `None`;
- the mount path had neither the required-connection contract nor Glue
  rollback;
- README still called `BindingResolver` wiring deferred and the overlay
  contract pending.

The strengthened bind and navigation checks already passed in the RED run,
confirming that those stages retained a visible S3 view; they now also pin
root/nav/content/widget/pane coherence.

### Focused GREEN evidence

The same ten-test command after implementation:

```text
10 passed in 3.61s
```

The formatted focused regression set:

```text
uv run pytest tests/integration/test_glue_s3_handoff.py \
  tests/integration/test_settings_flow.py::test_s3_selection_propagates_failed_local_fallback_mount \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_true_only_with_mounted_view \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_false_without_transfer_journal \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_false_when_content_adoption_fails \
  tests/integration/test_settings_flow.py::test_local_only_mount_returns_false_when_widget_mount_fails \
  tests/docs/test_shipped_behavior.py -q
19 passed in 7.01s
```

The first complete affected-file run exposed one existing test wrapper that
still implemented the old `_mount_service_view(service_id)` signature:

```text
1 failed, 34 passed, 2 rerun in 157.42s
TypeError: observed_mount() got an unexpected keyword argument
'required_connection'
```

After forwarding the optional connection pin, the full handoff file passed:

```text
uv run pytest tests/integration/test_glue_s3_handoff.py -q
10 passed in 5.60s
```

### Final verification

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  uv run pytest tests/snapshot -q
329 snapshots passed.
624 passed in 88.89s (0:01:28)

uv run pytest tests/unit tests/integration tests/e2e -q
1472 passed, 9 deselected in 272.84s (0:04:32)

uv run pytest tests/docs -q
55 passed, 2 skipped in 0.26s

make docs-check
check_docs: clean
Documentation built in 0.39 seconds

uv run ruff check .
All checks passed!

uv run ruff format --check .
342 files already formatted

uv run mypy src
Success: no issues found in 129 source files

./scripts/check-layers.sh
layer rules clean

uv build
Successfully built dist/aws_tui-0.8.0.tar.gz
Successfully built dist/aws_tui-0.8.0-py3-none-any.whl

git diff --check
(no output; exit 0)
```

The direct docs run retains the two existing optional Cairo renderer skips.
`make docs-check` supplied Homebrew Cairo and completed rendering, repository
checks, and strict MkDocs. The generated architecture PNG was restored because
no diagram source changed. The Material for MkDocs 2.0 notice and the shell's
missing `/tmp/vmx-cargo-182/env` warning are pre-existing warnings; every gate
exited zero.

### Self-review

- The deterministic identity regression forces a stale `demo-prod` fallback
  while requesting `demo-dev`; it proves no retry occurs and RootVM, selected
  service, ContentHostVM, mounted `DualPane`, target pane, and final path all
  remain on `demo-dev`.
- The mount-failure regression proves rollback produces one visible
  `GluePage` whose VM is exactly `ContentHostVM.current`, with Glue selected,
  the original profile active, and no residual `DualPane`.
- Bind failure leaves the requested-profile S3 left pane usable while the
  requested right pane remains local. Navigation failure leaves the
  requested-profile S3 pane at root. Both retain a nonempty host and one
  mounted widget bound to the current VM.
- The local-only helper covers success, missing journal, failed content
  adoption, and failed widget mount. Its caller now propagates `False`.
  Ordinary `Exception` handling is unchanged and `asyncio.CancelledError`
  remains uncaught, so worker cancellation still propagates.
- Handoff failure logs retain only connection name, stage, and exception type.
  Request URI, query token, exception text, bucket, and credentials remain
  absent from advisories and durable logs.
- All 75 previously approved EMR/Glue color baselines and every other snapshot
  file are unchanged.
- A current-README scan finds only shipped `BindingResolver`/overlay claims;
  the remaining deferred wording applies to handlerless action IDs, not the
  resolver or overlay.

### Remaining concern

Every shell still emits the pre-existing `.zshenv` warning for missing
`/tmp/vmx-cargo-182/env`; all commands above exited with the recorded status.
