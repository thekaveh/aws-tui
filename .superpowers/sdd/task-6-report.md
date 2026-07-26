# Athena Task 6 Report

## Status

Implemented multi-profile Athena demo data, deterministic in-memory query
behavior, exact-profile S3 result handoff, stale-row protection during real
profile switches, and the ten normal-color demo snapshot updates. The review
follow-up now also gives the demo client production-equivalent read-only,
idempotency, and workgroup-output behavior; reveals concrete Athena result
objects in S3; binds Results handoff to its owning query context; and makes
every handoff failure and internal cancellation transactional.

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

### Review Follow-up RED

The reviewer regressions failed for the intended missing behavior before the
follow-up implementation:

```text
uv run pytest tests/unit/demo/test_in_memory_athena.py -q
7 failed, 10 passed

- changed request parameters reused the original execution
- DROP TABLE was accepted
- caller output overrode enforced workgroup output

uv run pytest \
  tests/unit/vm/test_messages.py::test_open_s3_request_carries_source_identity -q
1 failed: unexpected keyword argument 'reveal_object'

uv run pytest \
  tests/integration/test_athena_s3_handoff.py::test_history_result_location_opens_same_profile_in_s3 \
  tests/integration/test_athena_s3_handoff.py::test_result_handoff_rejects_execution_identity_mismatch \
  'tests/integration/test_athena_s3_handoff.py::test_result_handoff_rolls_back_every_post_mount_failure[bind]' \
  -q
3 failed

- the S3 cursor remained on ".." instead of the result object
- a coherent foreign profile/context detail was accepted
- bind failure left S3 active instead of restoring Athena

uv run pytest \
  tests/unit/demo/test_in_memory_athena.py::test_non_enforced_workgroup_accepts_caller_output_configuration -q
1 failed: add_workgroup() did not expose enforcement configuration
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

The reviewer follow-up matrix is green:

```text
uv run pytest tests/unit/demo/test_in_memory_athena.py -q
18 passed

env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest \
  tests/integration/test_athena_s3_handoff.py \
  tests/integration/test_glue_s3_handoff.py -q
24 passed

env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest \
  tests/unit/demo/test_in_memory_athena.py \
  tests/unit/vm/test_messages.py \
  tests/unit/vm/athena/test_results_vm.py \
  tests/unit/vm/athena/test_query_vm.py \
  tests/unit/vm/athena/test_history_vm.py \
  tests/unit/vm/athena/test_page_vm.py \
  tests/integration/test_athena_s3_handoff.py \
  tests/integration/test_glue_s3_handoff.py \
  tests/integration/test_demo_mode.py \
  tests/e2e/test_journeys.py -q
175 passed
```

## Implementation

- Added a fresh, instance-owned `InMemoryAthena` per demo AWS profile. It
  implements the Task 2 client surface, records repr-safe calls, paginates
  deterministically, keeps only SHA-256 token/request fingerprints for
  idempotency, rejects changed-parameter token reuse, validates every query
  with `ReadOnlySqlPolicy`, honors enforced and non-enforced workgroup output
  configuration, and creates no background tasks.
- Seeded disjoint development and production profile workgroups, catalogs,
  databases, tables, query history, saved queries, prepared statements,
  outcomes, result pages, and S3 objects. `demo-shared` intentionally exposes
  only a typed access-denied scenario and has no Athena resources. Scenarios
  cover running, succeeded, failed, cancelled, empty, access denied, and
  missing output.
- Limited state progression to app-started queries:
  `QUEUED -> RUNNING -> SUCCEEDED`. Historical rows remain static, and stop
  only affects active app-started executions.
- Wired demo Athena clients through `build_app_context()` without module-global
  fake state.
- Added `athena.open_result_location` to the command palette. History uses its
  hydrated execution detail; Results reloads authoritative execution metadata
  before publishing the service-neutral `OpenS3LocationRequest`. Results also
  compares connection, region, workgroup, catalog, and database against its
  owning context before publishing.
- Extended the app-owned S3 transaction with explicit object-reveal intent.
  Athena handoff navigates to the object's parent directory and moves the
  requested pane cursor onto the exact result file, including a bucket-root
  object. Glue locations retain directory semantics.
- Unified rollback for switch, mount, bind, navigation exceptions, terminal
  pane errors, missing result objects, focus failures, and internal
  cancellation. Rollback unmounts failed S3 widgets before disposing their
  VMs, restores the exact profile/service and persisted page context, restores
  an active Athena Results execution, preserves query history, and still lets
  explicit user navigation supersede the handoff.
- Added teardown guards for queued Athena page refresh/focus callbacks after
  the service view is removed.
- Added integration and E2E coverage for exact-profile handoff, authoritative
  metadata, malformed/missing locations, no automatic handoff, identity
  mismatch rejection, coherent foreign-context rejection, concrete object
  selection, every rollback phase, cancellation races, disjoint profile data,
  and empty new rows while a real profile switch is still loading.
- Updated all ten demo snapshot themes under normal color. They now include
  the Athena service row and `athena-results/` bucket.

## Full Verification

### Functional suite

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/unit tests/integration tests/e2e -q

1912 passed, 9 deselected in 318.83s (0:05:18)
```

That run reported two warnings caused by the test forcing a private view
refresh and swapping profiles before the resulting selection worker drained.
The test now drains those workers; the warning-as-error demo run above verifies
the correction.

### Normal-color snapshots

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE TERM=xterm-256color \
  uv run pytest tests/snapshot -q

744 passed in 125.99s
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
- Handoff requires a succeeded execution whose complete context belongs to the
  Results VM and a valid concrete S3 object URI. Results metadata is fetched
  again at action time.
- Existing app orchestration resolves the exact profile, rejects region drift,
  preserves requested pane/path/object selection, rolls back all failed or
  internally cancelled phases, and redacts advisories.
- Demo fake mutable state and app-started lifecycle state are instance-local;
  no fake, task, or query state is shared globally.

## Concerns

- Every shell command prints a pre-existing `.zshenv` warning for missing
  `/tmp/vmx-cargo-182/env`; it does not affect command exit status.

## Changed Files

- `.superpowers/sdd/task-6-report.md`
- `src/aws_tui/app.py`
- `src/aws_tui/demo/in_memory_athena.py`
- `src/aws_tui/demo/seeds.py`
- `src/aws_tui/vm/athena/history_vm.py`
- `src/aws_tui/vm/athena/query_vm.py`
- `src/aws_tui/vm/athena/results_vm.py`
- `src/aws_tui/vm/messages.py`
- `tests/integration/test_athena_s3_handoff.py`
- `tests/integration/test_glue_s3_handoff.py`
- `tests/snapshot/test_demo_mode.py`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[amber].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[carbon].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[dracula].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[github-light].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[gruvbox-dark].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[lattice].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[nord].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[one-light].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[solarized-light].raw`
- `tests/snapshot/__snapshots__/test_demo_mode/test_demo_mode_snapshot[voidline].raw`
- `tests/unit/demo/test_in_memory_athena.py`
- `tests/unit/vm/athena/test_results_vm.py`
- `tests/unit/vm/test_messages.py`

## Remaining Review Findings Follow-up

### RED

The seven focused regressions failed for the intended missing behavior:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest \
  tests/unit/vm/athena/test_history_vm.py::test_history_rejects_coherent_detail_owned_by_another_profile \
  'tests/integration/test_athena_s3_handoff.py::test_missing_or_malformed_result_location_stays_in_athena_and_advises[q-dev-hostile-output-s3://[RESULT_URI_SECRET]' \
  tests/integration/test_athena_s3_handoff.py::test_history_handoff_rejects_coherent_foreign_profile_detail \
  tests/integration/test_athena_s3_handoff.py::test_results_hostile_s3_uri_stays_in_athena_and_advises_redacted \
  tests/integration/test_athena_s3_handoff.py::test_app_validation_rejects_hostile_s3_uri_without_navigation \
  tests/integration/test_athena_s3_handoff.py::test_demo_query_artifacts_are_profile_local_replay_safe_and_distinct \
  tests/integration/test_athena_s3_handoff.py::test_app_started_demo_query_opens_its_exact_result_object -q

7 failed, 12 rerun in 24.58s
```

- History published a coherent detail owned by another profile.
- History, Results, and app validation raised `ValueError: Invalid IPv6 URL`
  for hostile `s3://[` locations.
- Successful app-started queries had no object in the profile's S3 fake, so
  both direct profile-isolation checks and the explicit handoff failed.

The first full functional run also exposed a compatibility regression in the
initial eager demo S3 map:

```text
1 failed, 1918 passed, 9 deselected, 4 rerun in 302.06s

tests/integration/test_service_source_swap.py::
  test_shift_s_rebuilds_emr_under_next_profile
```

That existing test replaces the demo resolver after composition. The eager
map narrowed the prior factory contract, so it was replaced with a lazy,
context-local cache and the isolated regression returned to green.

### GREEN

The exact seven reviewer regressions passed after the ownership, parser, and
demo-store changes:

```text
7 passed in 4.58s
```

The focused Athena/demo/lifecycle matrix passed:

```text
181 passed in 35.70s
```

The final full functional run was clean:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest tests/unit tests/integration tests/e2e -q

1919 passed, 9 deselected in 313.43s (0:05:13)
```

Normal-color snapshots were unchanged:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest tests/snapshot -q

744 passed in 126.67s (0:02:06)
437 snapshots passed
```

Static and architecture gates passed:

```text
uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
371 files already formatted

uv run mypy src
Success: no issues found in 150 source files

bash scripts/check-layers.sh
layer rules clean

git diff --check
(no output; exit 0)
```

### Design Evidence

- `AthenaHistoryVM` now receives and privately retains its owning
  `QueryContext`. Before publishing, it compares the selected execution ID
  and the complete connection, region, workgroup, catalog, and database
  context. `AthenaPageVM` replaces that context on every existing context
  transition; no client or ownership state was made public.
- Added one domain-owned `S3Uri` parser whose bucket and path are excluded
  from repr. It catches parser/netloc `ValueError` and returns advisory
  invalidity. History, Results, and app transaction validation use the same
  parser, so hostile locations cannot start navigation or escape into raw
  errors.
- Demo composition now owns a lazy `connection.name -> InMemoryFS` cache per
  `build_app_context()` call. The S3 and Athena factories receive the same
  exact profile instance, while separate profiles and app contexts remain
  disjoint. There are no module globals or background tasks.
- `InMemoryAthena` writes deterministic `_col0\n1\n` content to the
  execution-specific output path before the first `SUCCEEDED` detail becomes
  authoritative. Distinct execution IDs produce distinct objects, and
  idempotent request-token replay reuses the existing execution and object.
- New tests cover direct and integrated coherent foreign History ownership,
  hostile URI handling at all three required boundaries with redacted
  advisories, exact app-started result reveal, repeated-query separation,
  idempotent replay, and cross-profile S3 isolation.

### Follow-up Changed Files

- `.superpowers/sdd/task-6-report.md`
- `src/aws_tui/app.py`
- `src/aws_tui/composition.py`
- `src/aws_tui/demo/in_memory_athena.py`
- `src/aws_tui/demo/seeds.py`
- `src/aws_tui/domain/s3_uri.py`
- `src/aws_tui/vm/athena/history_vm.py`
- `src/aws_tui/vm/athena/page_vm.py`
- `src/aws_tui/vm/athena/results_vm.py`
- `tests/integration/test_athena_s3_handoff.py`
- `tests/unit/vm/athena/test_history_vm.py`

### Residual Concerns

- No code or test concern remains. Commands continue to print the pre-existing
  `.zshenv` warning for missing `/tmp/vmx-cargo-182/env`; exit status and
  verification results are unaffected.

## Final Two Findings Follow-up

### Root Cause and RED Evidence

The existing parser accepted hostile URI components because it validated only
the scheme, nonempty netloc, and parsed userinfo. A direct reproduction showed
ports, queries, fragments, percent-encoded newlines, and IPv4-shaped
authorities all returning a valid `S3Uri`. The demo composition issue had a
separate cause: Athena clients were eagerly built from the resolver's startup
list while S3 filesystems were lazy, so an injected runtime alias failed with
`KeyError`.

The table-driven parser tests failed before implementation:

```text
uv run pytest tests/unit/domain/test_s3_uri.py -q

30 failed, 10 passed
```

The lazy-cache and one representative three-boundary integration regression
also failed for the intended reasons:

```text
uv run pytest \
  tests/integration/test_athena_s3_handoff.py::test_runtime_alias_uses_lazy_cached_athena_and_exact_s3_result_store \
  tests/integration/test_athena_s3_handoff.py::test_demo_athena_and_s3_caches_are_isolated_between_app_contexts -q

2 failed, 4 rerun
- runtime-dev raised KeyError in the eager Athena map

uv run pytest \
  'tests/integration/test_athena_s3_handoff.py::test_hostile_s3_uri_is_advisory_redacted_and_non_navigating_at_every_boundary[port]' -q

1 failed, 2 rerun
- the port-bearing authority started handoff instead of issuing the stable
  invalid-location advisory
```

### Implementation

- `parse_s3_uri()` is now a total, non-throwing parser that rejects malformed
  percent escapes, raw or decoded controls and whitespace, query strings,
  fragments, IPv6 literals, ports, userinfo, empty authorities, and invalid
  bucket names without returning or logging the URI, bucket, or path.
- Bucket validation applies current AWS general-purpose naming constraints:
  3-63 lowercase alphanumeric/period/hyphen characters, alphanumeric ends, no
  adjacent periods, no IPv4-shaped names, and no reserved S3 prefixes or
  suffixes. Valid bucket-root objects and ordinary dotted/hyphenated buckets
  remain accepted.
- History, Results, the app transaction, and demo result publication continue
  to use the one domain parser. Thirteen hostile families now traverse
  History, Results, and direct app validation with advisory-only behavior, no
  navigation, no exception, and redacted diagnostics.
- Demo composition now owns lazy S3 and Athena caches keyed by connection name.
  Each Athena cache miss receives the exact object returned by
  `demo_s3_fs(connection)`. Seed profile is separate from runtime connection
  identity, so aliases inherit profile data while query refs, contexts, and
  handoffs retain the alias name and region.
- Same-name calls reuse both clients; different profiles and app contexts
  remain disjoint; runtime `demo-shared` aliases retain typed access denial.
  No module globals or background tasks were added.
- The first full gate exposed seven existing Glue rollback fixtures whose
  nominal request included a query string. Because query strings are now
  intentionally invalid, that shared nominal URI was changed to a valid S3
  location. Secret-bearing injected exceptions still exercise the original
  failure redaction checks.

### GREEN Evidence

```text
uv run pytest tests/unit/domain/test_s3_uri.py -q
40 passed

uv run pytest tests/integration/test_athena_s3_handoff.py \
  -k hostile_s3_uri_is_advisory_redacted_and_non_navigating_at_every_boundary -q
13 passed, 20 deselected

uv run pytest \
  tests/integration/test_athena_s3_handoff.py::test_runtime_alias_uses_lazy_cached_athena_and_exact_s3_result_store \
  tests/integration/test_athena_s3_handoff.py::test_demo_athena_and_s3_caches_are_isolated_between_app_contexts -q
2 passed

uv run pytest tests/unit/demo/test_in_memory_athena.py -q
18 passed

uv run pytest \
  tests/unit/domain/test_s3_uri.py \
  tests/unit/demo/test_in_memory_athena.py \
  tests/unit/vm/athena/test_history_vm.py \
  tests/unit/vm/athena/test_results_vm.py \
  tests/unit/vm/athena/test_page_vm.py \
  tests/integration/test_athena_s3_handoff.py \
  tests/integration/test_demo_mode.py \
  tests/integration/test_service_source_swap.py \
  tests/e2e/test_journeys.py -q
174 passed in 30.36s

uv run pytest tests/integration/test_glue_s3_handoff.py -q
12 passed in 6.97s
```

The authoritative full functional gate passed after the intentional Glue
fixture correction:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest tests/unit tests/integration tests/e2e -q

1974 passed, 9 deselected in 307.43s (0:05:07)
```

Static and architecture gates:

```text
uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
372 files already formatted

uv run mypy src
Success: no issues found in 150 source files

bash scripts/check-layers.sh
layer rules clean

git diff --check
(no output; exit 0)
```

### Final Follow-up Changed Files

- `.superpowers/sdd/task-6-report.md`
- `src/aws_tui/composition.py`
- `src/aws_tui/demo/seeds.py`
- `src/aws_tui/domain/s3_uri.py`
- `tests/integration/test_athena_s3_handoff.py`
- `tests/integration/test_glue_s3_handoff.py`
- `tests/unit/domain/test_s3_uri.py`

### Final Follow-up Concerns

- No code or test concern remains. The pre-existing `.zshenv` warning for the
  missing `/tmp/vmx-cargo-182/env` still appears on every shell command but
  does not affect exit status or verification.

## Final Approval Findings

### Root Cause and RED Evidence

Athena page refresh guarded only the final query-view lookup. During partial
service teardown, `_sync_context()` found the surviving selects and then
raised while querying a removed load-more button. Broad `Exception` catches
around other widget queries also hid live-page errors instead of limiting the
teardown exception to `NoMatches`.

The direct partial-removal regression failed at the reported boundary:

```text
uv run pytest \
  tests/unit/ui/athena/test_page.py::test_page_refresh_is_safe_during_partial_descendant_teardown \
  -q --tb=short -p no:rerunfailures

1 failed
NoMatches: No nodes match '#athena-more-workgroups' on AthenaPage()
```

The live-error regression proved the old broad catch masked an unexpected
widget query failure:

```text
uv run pytest \
  tests/unit/ui/athena/test_page.py::test_live_page_context_query_errors_are_not_masked \
  -q --tb=short -p no:rerunfailures

1 failed
Failed: DID NOT RAISE RuntimeError
```

Glue Catalog independently parsed table locations with `urlparse()`. The VM
raised on a malformed bracket authority and published requests for ports,
queries, raw controls, and encoded controls:

```text
uv run pytest \
  tests/unit/vm/glue/test_catalog_vm.py::test_catalog_rejects_invalid_s3_locations_without_publishing \
  -q --tb=short -p no:rerunfailures

5 failed
- malformed authority raised ValueError: Invalid IPv6 URL
- port, query, raw-control, and encoded-control locations returned True
```

### Implementation

- Athena refresh now preflights the complete mounted context-control and
  view-control sets before mutating any widget. Missing or unmounted required
  descendants produce a teardown-only no-op; only `NoMatches` is caught, so
  normal live-page failures still surface.
- Context load-more synchronization uses the already validated controls, and
  view refresh reuses the preflighted query view. No worker or task lifecycle
  was added.
- Glue Catalog removed `urlparse` and validates every non-null table location
  with the shared total `parse_s3_uri()` before publishing.
- Invalid Glue locations return `False`, publish nothing, retain Glue as the
  active service, and use the existing stable redacted
  `glue-s3-location-invalid` advisory.
- Valid dotted/hyphenated bucket prefixes still publish the original URI with
  `reveal_object=False`, navigate to the prefix directory, and leave the
  directory cursor semantics unchanged.

### GREEN Evidence

Focused Athena page, Glue Catalog, and Athena/Glue handoff matrix:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest \
  tests/unit/ui/athena/test_page.py \
  tests/unit/vm/glue/test_catalog_vm.py \
  tests/integration/test_athena_s3_handoff.py \
  tests/integration/test_glue_s3_handoff.py -q

87 passed in 49.26s
```

The app-started demo query handoff runs eight isolated app contexts through
query success, Athena teardown, S3 mount, and exact result-object selection.
The warning-as-error stress run was clean:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest \
  tests/integration/test_athena_s3_handoff.py::test_app_started_demo_query_opens_its_exact_result_object \
  -W error::RuntimeWarning -q

8 passed in 14.11s
```

Authoritative functional gate:

```text
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR \
  TERM=xterm-256color uv run pytest tests/unit tests/integration tests/e2e -q

1995 passed, 9 deselected in 325.70s (0:05:25)
```

Static and architecture gates:

```text
uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
372 files already formatted

uv run mypy src
Success: no issues found in 150 source files

bash scripts/check-layers.sh
layer rules clean

git diff --check
(no output; exit 0)
```

### Final Approval Changed Files

- `.superpowers/sdd/task-6-report.md`
- `src/aws_tui/ui/widgets/athena/page.py`
- `src/aws_tui/vm/glue/catalog_vm.py`
- `tests/integration/test_athena_s3_handoff.py`
- `tests/integration/test_glue_s3_handoff.py`
- `tests/unit/ui/athena/test_page.py`
- `tests/unit/vm/glue/test_catalog_vm.py`

### Final Approval Concerns

- No code, lifecycle, navigation, or security concern remains. The pre-existing
  `.zshenv` warning for missing `/tmp/vmx-cargo-182/env` appears on shell
  commands but does not affect exit status or verification.
