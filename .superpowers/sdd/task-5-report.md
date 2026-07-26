# Task 5 Report: Register Glue and Add Multi-Profile Demo Data

## Status

`DONE_WITH_CONCERNS`

Glue is registered after EMR Serverless, production composition builds a real
`GlueClient`, demo composition uses one `InMemoryGlue` per AWS connection name,
and profile switching rebuilds the Glue page without retaining rows from the
previous profile.

## Changed Files

- `src/aws_tui/services/glue/__init__.py`
- `src/aws_tui/services/glue/service.py`
- `src/aws_tui/demo/in_memory_glue.py`
- `src/aws_tui/demo/seeds.py`
- `src/aws_tui/composition.py`
- `src/aws_tui/app.py`
- `tests/unit/services/glue/__init__.py`
- `tests/unit/services/glue/test_service.py`
- `tests/integration/test_glue_page.py`
- `tests/integration/test_demo_mode.py`
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
- `.superpowers/sdd/task-5-report.md`

The demo snapshots changed only because the required Glue navigation row is now
registered in the real demo application.

## Design Notes

- `GlueService` is AWS-only and exposes the required `Glue` / `🔗` descriptor.
- Production VMs receive `GlueClient(aws_session=..., connection=...)`.
- Tests and demo mode inject clients through `GlueClientFactory`.
- One `ServiceSelectionStore` belongs to the long-lived service, so rebuilt page
  VMs restore only selections scoped by service, connection name, and region.
- `seeded_demo_glue()` creates a fresh dictionary on every call. The composition
  closure keys clients by connection name; there is no module-global fake.
- `demo-dev` contains `dev_events`, successful/running runs, and ready/running
  crawlers.
- `demo-prod` contains `prod_sales`, successful/failed runs, and ready/failed
  crawlers.
- `demo-shared` has empty catalog/job states and a crawler access-denied hook.
- `InMemoryGlue` implements the complete read-only Task 2 client surface,
  including pagination, detail, statistics, runs, crawlers, and metrics.
- The existing resolver order is unchanged. Source switching still uses the
  explicit-then-discovered order supplied by the resolver.
- `app.py` now passes the Glue page class explicitly through the established
  service-view factory and focuses the catalog database list on explicit entry.
- The fake owns no background tasks, so no additional app shutdown lifecycle
  storage is needed.

## TDD Transcript

### RED

Command:

```bash
uv run pytest tests/unit/services/glue/test_service.py tests/integration/test_glue_page.py tests/integration/test_demo_mode.py -q
```

Output:

```text
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env

==================================== ERRORS ====================================
__________ ERROR collecting tests/unit/services/glue/test_service.py ___________
ImportError while importing test module '/Users/kaveh/repos/aws-tui/tests/unit/services/glue/test_service.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
../../.local/share/uv/python/cpython-3.12.9-macos-aarch64-none/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/unit/services/glue/test_service.py:9: in <module>
    from aws_tui.services.glue import GlueService
E   ModuleNotFoundError: No module named 'aws_tui.services.glue'
=========================== short test summary info ============================
ERROR tests/unit/services/glue/test_service.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.11s
```

This was the expected missing-service failure.

### GREEN

Command:

```bash
uv run pytest tests/unit/services/glue tests/integration/test_glue_page.py tests/integration/test_demo_mode.py -q
```

Output:

```text
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
..........                                                               [100%]
10 passed in 2.74s
```

## Additional Verification

```text
uv run pytest tests/unit/services/glue tests/unit/demo tests/unit/vm/glue tests/unit/ui/glue tests/unit/ui/test_service_view_factory.py tests/unit/test_composition_emr_registered.py tests/integration/test_glue_page.py tests/integration/test_demo_mode.py -q
102 passed in 7.15s

uv run pytest tests/snapshot/test_demo_mode.py tests/snapshot/test_glue.py -q
85 passed in 21.33s

uv run ruff check .
All checks passed!

uv run ruff format --check .
340 files already formatted

uv run mypy src
Success: no issues found in 129 source files

./scripts/check-layers.sh
layer rules clean

git diff --check
exit 0
```

A direct seed audit also reported:

```text
3 disjoint clients; ordinary/failed/empty/access-denied scenarios verified
```

## Self-Review

- Confirmed registry order is exactly S3, EMR Serverless, Glue.
- Confirmed Glue rejects S3-compatible connections.
- Confirmed real-client construction remains lazy until VM setup.
- Confirmed demo fakes are newly allocated per `build_app_context()` and keyed
  by connection name inside that context.
- Confirmed `demo-dev` and `demo-prod` table identifiers and backing fake
  instances are disjoint.
- Confirmed source switching removes `dev_events` before showing `prod_sales`.
- Confirmed selection memory belongs to the service and survives same-profile
  page replacement while retaining existing connection/region scoping.
- Confirmed Glue remains read-only and no Task 4 routing behavior was changed.
- Confirmed no layer violations, type errors, lint errors, or whitespace errors.

## Concerns

`tests/snapshot/test_nav_menu.py` has 10 pre-existing snapshot mismatches in
this checkout. Its fixture constructs a private registry containing only a
fake S3 service, so Task 5 cannot affect its output; none of its files or
baselines were changed. The Task 5 demo and Glue snapshot suites are green.

Every shell command also prints a pre-existing `.zshenv` warning for the
missing `/tmp/vmx-cargo-182/env`; it does not affect command exit status.

## Snapshot Blocker Resolution

The committed demo baselines in `6661d71` were regenerated while `NO_COLOR=1`
and `TERM=dumb` were present in the environment. Under an explicit color
environment, the focused Amber snapshot reproduced the mismatch:

Command:

```bash
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR TERM=xterm-256color uv run pytest 'tests/snapshot/test_demo_mode.py::test_demo_mode_snapshot[amber]' -q
```

Output:

```text
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
F                                                                        [100%]
=============================== warnings summary ===============================
.../site-packages/pytest_textual_snapshot.py:350
  DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version
[1mTextual Snapshot Report[0m
[1m1[0m[30;41m mismatched snapshots[0m
```

All ten demo baselines were then regenerated with color explicitly enabled:

Command:

```bash
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR TERM=xterm-256color uv run pytest tests/snapshot/test_demo_mode.py --snapshot-update -q
```

Output:

```text
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
...................                                                     [100%]
--------------------------- snapshot report summary ----------------------------
10 snapshots updated.
20 passed in 4.49s
```

Verification with the same color-enabled environment, without snapshot
updates:

Command:

```bash
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR TERM=xterm-256color uv run pytest tests/snapshot/test_demo_mode.py -q
```

Output:

```text
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
...................                                                     [100%]
--------------------------- snapshot report summary ----------------------------
10 snapshots passed.
20 passed in 4.52s
```

Task 5 focused verification:

Command:

```bash
env -u NO_COLOR -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR TERM=xterm-256color uv run pytest tests/unit/services/glue tests/integration/test_glue_page.py tests/integration/test_demo_mode.py -q
```

Output:

```text
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
/Users/kaveh/.zshenv:.:3: no such file or directory: /tmp/vmx-cargo-182/env
.....                                                               [100%]
10 passed in 2.70s
```

Diff inspection against parent `400a668` normalized volatile Rich terminal
IDs before comparison. The stylesheet sections match the parent color
baselines for all ten themes, and the semantic comparison found only the
required Glue navigation text on the existing row:

Command:

```bash
for f in tests/snapshot/__snapshots__/test_demo_mode/*.raw; do
  base=$(basename "$f")
  if diff -q <(git show 400a668:"$f" | sed -n '/<style>/,/<\/style>/p' | sed -E 's/terminal-[0-9]+/terminal-ID/g') <(sed -n '/<style>/,/<\/style>/p' "$f" | sed -E 's/terminal-[0-9]+/terminal-ID/g') >/dev/null; then
    printf '%s: parent palette matches\n' "$base"
  fi
done
```

Output:

```text
test_demo_mode_snapshot[amber].raw: parent palette matches
test_demo_mode_snapshot[carbon].raw: parent palette matches
test_demo_mode_snapshot[dracula].raw: parent palette matches
test_demo_mode_snapshot[github-light].raw: parent palette matches
test_demo_mode_snapshot[gruvbox-dark].raw: parent palette matches
test_demo_mode_snapshot[lattice].raw: parent palette matches
test_demo_mode_snapshot[nord].raw: parent palette matches
test_demo_mode_snapshot[one-light].raw: parent palette matches
test_demo_mode_snapshot[solarized-light].raw: parent palette matches
test_demo_mode_snapshot[voidline].raw: parent palette matches
```

Semantic diff command:

```bash
for f in tests/snapshot/__snapshots__/test_demo_mode/*.raw; do
  base=$(basename "$f")
  parent_text=$(git show 400a668:"$f" | perl -0pe 's/<style>.*?<\/style>//s; s/<[^>]+>//g; s/terminal-[0-9]+/terminal-ID/g')
  current_text=$(perl -0pe 's/<style>.*?<\/style>//s; s/<[^>]+>//g; s/terminal-[0-9]+/terminal-ID/g' "$f")
  if diff -u <(printf '%s' "$parent_text") <(printf '%s' "$current_text") | rg -q '^\+.*Glue'; then
    changed=$(diff -u <(printf '%s' "$parent_text") <(printf '%s' "$current_text") | rg '^[+-][^+-]' | wc -l | tr -d ' ')
    printf '%s: only Glue text added (%s changed text lines)\n' "$base" "$changed"
  else
    printf '%s: unexpected semantic diff\n' "$base"
  fi
done
```

Output:

```text
test_demo_mode_snapshot[amber].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[carbon].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[dracula].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[github-light].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[gruvbox-dark].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[lattice].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[nord].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[one-light].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[solarized-light].raw: only Glue text added (2 changed text lines)
test_demo_mode_snapshot[voidline].raw: only Glue text added (2 changed text lines)
```

No private S3-only nav-menu baseline was changed.
