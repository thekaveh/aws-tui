# Session-scoped launch selectors implementation plan — #264

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement every #264 launch-selector acceptance criterion with
exact session identity, read-only locations and fail-fast account boundaries.

**Architecture:** A small launch module parses/validates explicit local
selection into immutable state before composition. The composition root
wires that state into a dedicated app startup path while retaining the
legacy no-selector boot chain. Service choices/support use actual service
definitions and are checked against the real registry.

**Tech stack:** Existing argparse, dataclasses, pathlib, ConfigStore,
ConnectionResolver, ServiceRegistry, VMx/Textual and pytest; no dependency.

## 1. Global constraints

- Preserve all nine #264 acceptance criteria; no new limitation waiver.
- Support Python >=3.11 and existing layer boundaries; add no dependency.
- GitHub Actions remain disabled; perform applicable verification locally.
- Preserve normal protected push/PR/merge workflow; no admin bypass or force push.
- Explicit selector failures never start a client for another account,
  enter the legacy connection retry chain, or silently become local fallback.
- Help, version and argument errors exit before `build_app_context`, client
  creation, terminal protocol negotiation or UI mounting.
- Launch arguments do not save, materialize or change configuration bytes.
- Startup location handling lists/reads only; it never copies, deletes,
  uploads, submits SQL or starts a job.
- Keep no-argument startup, demo behavior, doctor, existing assertions,
  platform skips, coverage floor, snapshot schema and golden bytes intact.
- Use exact connection/profile/region identity. Session overrides must be
  compatible with existing current-source checks and credential recovery;
  they must not make a valid launch look stale or reroute it to another source.
- Do not replace the navigation/focus architecture or add saved workspaces,
  credential flags, ARN routing, releases or remote publication.
- Preserve unrelated branches, PRs, worktrees, evidence and shared caches.

## 2. Ownership and review

Primary branch: `codex/issue-264-launch-selectors`, base
`2bf47d17b3d7acf86a6ce0d82ea556934d4fdf28`.
Owned scratch/evidence: `.superpowers/sdd/2026-10-06-launch-selectors`.
Read `issue-intake.json` there and the canonical design above before editing.
Task report is `task-1-report.md` in that workspace. Root owns GitHub delivery,
board changes, complete final gates, archive and cleanup. The implementer
owns feature changes, TDD, focused checks, docs, self-review and normal commits.
Do not start a parallel editor or the next issue.

One integrated task is intentional: exposing selectors without pinning the
actual startup and identity checks would violate AC5. The examples below
specify testable contracts, not an extra literal implementation inventory.
Choose narrow internal helper names after reading the current source; record
routine choices in the report rather than asking the owner again.

### 2.1. Task 1: Complete launch selectors and read-only explicit startup

**Files:**

- Create a focused launch module, preferably `src/aws_tui/launch.py`, for
  immutable request/resolution/location data and preflight validation.
- Modify `src/aws_tui/app.py`: CLI args and explicit mounted startup/error exit.
- Modify `src/aws_tui/composition.py`: side-effect-free service definitions,
  resolved session state and source-check wiring.
- Modify `src/aws_tui/infra/connection_resolver.py` only for exact profile/identity
  resolution needed by preflight and runtime; reuse `resolve_selected` where valid.
- Modify the four `src/aws_tui/services/{s3,athena,glue,emr_serverless}/service.py`
  files only for shared support metadata/predicates or initial file locations.
- Modify `src/aws_tui/vm/file_manager/pane_vm.py` only if a narrow public
  pre-setup path API is necessary; existing `initial_path` is preferred.
- Create `tests/unit/test_launch_selectors.py` and
  `tests/integration/test_launch_selectors.py` with isolated fixtures.
- Update `README.md`, `docs/cookbook.md` and focused `tests/docs` contracts for
  CLI precedence, value forms, early failures and session-only behavior.

**Interfaces and current seams:**

- `main()` currently parses demo/version/doctor before calling
  `build_app_context(demo=demo)`. Preserve the no-selector call contract.
- `ConfigStore(path=..., read_only=True).load()` does not harden/write paths.
- `ConnectionResolver.resolve_selected(name)` resolves an exact name without
  dereferencing unrelated secrets. AWS profiles require exact profile semantics,
  including a configured non-AWS name collision; do not call `materialize`.
- Each service has `descriptor.id` and `supports(connection)`; the current
  support predicates use only the supplied connection kind. Derive the CLI
  definitions from those IDs/predicates and compare to a built real registry.
- `RootVM.switch_connection_and_service` atomically adopts supported service
  content. Source-check factories compare captured/current source identity.
- `PaneVM(initial_path=PathRef(...))` sets state before setup/listing.
  `LocalFS.canonical_path(pane.path)` proves the selected native directory.
- `AwsTuiApp._initial_mount_worker` is the legacy multi-account fallback chain;
  an explicit launch must take a separate branch before invoking it.
- `_mount_local_only_dual_pane` already composes real local panes but currently
  requires/marks a failed connection. A successful explicit local-only launch
  must not invent/mark a failed AWS source; reuse only the appropriate narrow
  composition portion and preserve existing fallback behavior.
- Context `close_unstarted()` and app shutdown already own resource disposal.

- [ ] **Step 1: Write behavioral RED CLI/preflight tests.**

Use actual `main()` with `sys.argv` patched and forbidden spies installed on
context creation, resize negotiation and app run. Initial tests must fail
because selectors are absent, not because a fake/fixture cannot construct.

```python
@pytest.mark.parametrize("flag", ["--connection", "--profile", "--region",
                                 "--service", "--location"])
def test_help_lists_launch_selectors(monkeypatch, capsys, flag):
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--help"])
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 0
    assert flag in capsys.readouterr().out

def test_connection_and_profile_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aws-tui", "--connection", "named",
                                    "--profile", "profile"])
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    with pytest.raises(SystemExit) as result:
        app_module.main()
    assert result.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
```

Add concrete matrices for all registered service IDs plus unknown `settings`,
`bogus` and empty IDs; unknown connection/profile; unsupported S3-compatible/
Athena pair; invalid `s3:///prefix`, unsupported `https://...`/ARN, missing
directory, file path, location plus non-S3 service and control characters.
For the semantic error cases assert exactly one stderr line, nonzero, no
traceback and zero context/client/UI calls. Test demo flag and environment
against every selector, doctor conflicts, and help/version without discovery.
Use synthetic source files and barred network/keychain calls where unrelated.

Run and retain:

```bash
.venv/bin/python -m pytest tests/unit/test_launch_selectors.py -q -m ''
```

Expected initial result: real missing-selector/behavior failures. Save the
original exits/logs and report fixture mistakes separately if any occur.

- [ ] **Step 2: Implement parser and exact read-only preflight.**

Use a mutually exclusive connection/profile argparse group and registry-derived
service choices. A minimal immutable request shape can use:

```python
@dataclass(frozen=True, slots=True)
class LaunchRequest:
    connection: str | None = None
    profile: str | None = None
    region: str | None = None
    service: str | None = None
    location: str | None = None

    @property
    def explicit(self) -> bool:
        return any(value is not None for value in (
            self.connection, self.profile, self.region,
            self.service, self.location,
        ))
```

The resolved immutable state must retain selected underlying connection,
effective connection with session region, service ID and typed location
(scheme plus exact PathRef/native path). For `None` request leave legacy
composition/startup alone. For explicit selectors resolve only the intended
connection/profile; apply documented default precedence only if neither
identity flag was supplied. Validate support before constructing context.
Reject no-connection AWS launches; allow a valid local-only file launch.

Keep the class descriptor IDs/support methods as the service definition
source; expose pure predicates without instance/client creation if necessary.
Do not create another literal service-kind table. Unit tests compare every
definition/choice/support result with actual registered service instances.

Resolve local directories using native pathlib/CWD/expanduser; preserve the
provider's drive/root representation. Parse S3 bucket/prefix without URL
decoding or normalizing away literal key identity. Preflight never performs
remote listing, authenticates, saves config or imports a live client factory.
One-line semantic errors use safe strings and `SystemExit(2)`; no traceback.

Run Step 1's complete module after implementation and retain GREEN evidence.

- [ ] **Step 3: Write RED startup and identity regression journeys.**

Create isolated real AppContext/app tests with fake providers and actual
ContentHost/panes. Seed at least one selected and one other-account connection.
Record provider factory identities and calls. Use named bounded conditions,
not sleeps, to observe readiness, chosen content, path, focus and worker drain.

Required cases:

1. Selected S3 prefix opens left, focuses it and lists that exact initial
   prefix; no earlier unrelated root listing or mutation call.
2. Existing local directory (absolute, relative, space/unicode name) opens
   right and its provider canonical path equals the resolved directory;
   no mutation. With no AWS sources, local-only startup still succeeds.
3. Each supported explicit service is actually hosted with the chosen
   connection/region; no S3 boot request precedes an Athena/Glue/EMR launch.
4. Explicit region reaches the real fake factory, source header and retained
   connection; its source-validity guard accepts it. Underlying source edit
   or removal still invalidates the selected identity.
5. `--profile` uses the exact profile with a differently named AWS alias or
   configured non-AWS name collision; neither can silently select another
   account. Assert actual profile, name/kind, region and factory parameters.
6. A selected provider/adoption/readiness failure with another usable default
   exits nonzero and never calls another account factory, the legacy chain,
   or a local fallback mount. Test resolver failure separately from runtime
   failure so parser-only proof cannot satisfy this case.
7. Cancellation/shutdown during explicit startup drains owned tasks and
   disposes candidate resources, without a late replacement or failure leak.
8. Isolated configuration bytes are identical after startup and teardown;
   an absent config stays absent. No `save`, `materialize`, environment
   mutation or persistence path supplies the session override.

Run and preserve RED:

```bash
.venv/bin/python -m pytest tests/integration/test_launch_selectors.py -q -m ''
```

- [ ] **Step 4: Wire explicit startup and cleanup, then GREEN.**

Thread optional resolved launch state through `build_app_context`/AppContext.
Preserve underlying-vs-effective identity for profile/region overrides in
source checks and credential retry; avoid global/default/env mutation.
On mount, branch to the explicit worker before default resolution/probing/
boot-chain work. Adopt the chosen service directly and set initial file-pane
paths before construction/setup. Only the selected source may build a client.

Use actual VM/provider terminal readiness to decide explicit startup success.
On failure record a safe explicit-launch error and exit; `main()` prints one
line and returns a nonzero process exit. Do not raise an unhandled Textual
worker exception and then call it a selector error. Reuse lifecycle ownership
and context cleanup so shutdown and a failed constructor leave no work alive.
Keep no-selector branch and its existing fallback narration unchanged.

Run the complete startup module and all changed preflight tests. Read logs and
inspect configuration/factory/no-mutation assertions rather than relying on
the aggregate count alone. Test only isolated local/fake providers.

- [ ] **Step 5: Document shipped behavior and run covering checks.**

Document all five flags, exact value forms, mutually exclusive identity flags,
explicit-over-config/environment precedence, default S3, location-selected
pane, demo/doctor conflicts, no persistence and fail-fast errors. Include:

```bash
aws-tui --connection dev --region eu-west-1 --service athena
aws-tui --profile analytics --location 's3://reports-bucket/daily/'
aws-tui --location './downloads'
```

Do not imply a CLI credential argument, writable launch operation, saved
workspace, native Windows runtime proof or live AWS acceptance run.

Applicable covering commands (add any other actually changed contract module):

```bash
.venv/bin/python -m pytest tests/unit/test_launch_selectors.py tests/integration/test_launch_selectors.py tests/unit/test_doctor_cli.py tests/integration/test_doctor_help.py tests/unit/test_app_boot_chain.py tests/unit/test_app_boot_budget.py tests/unit/demo/test_public_api.py tests/unit/test_composition_emr_registered.py tests/unit/test_app_context_unreachable.py tests/integration/test_discovery_navigation.py tests/integration/test_modal_key_containment.py tests/docs -q -m ''
.venv/bin/python -m ruff check src/aws_tui tests/unit/test_launch_selectors.py tests/integration/test_launch_selectors.py
.venv/bin/python -m ruff format --check src/aws_tui tests/unit/test_launch_selectors.py tests/integration/test_launch_selectors.py
.venv/bin/python -m mypy src/aws_tui
PATH="$(pwd)/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin" UV_NO_SYNC=1 UV_OFFLINE=1 bash scripts/check-layers.sh
```

The controller runs complete final hooks/docs/build/default-suite gates after
review; do not independently duplicate that expensive full run. Any narrower
implementation hook/test no-files behavior must be accurately disclosed.

- [ ] **Step 6: Self-review, normal commit and report.**

Read the full scoped diff against all nine ACs and the design. Inspect existing
assertion migrations rather than weakening expectations to fit the code.
Preserve dependencies/skips/schema/goldens; no snapshot changes expected.
Run appropriate normal commit hooks, stage only owned feature files and commit
with a coherent subject such as `feat: support explicit session launch selectors`.
No pushing, GitHub actions or merges from the implementer.

Report DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED in `task-1-report.md`.
Include exact commit(s), changes, AC-to-test mapping, behavioral RED and GREEN
commands/exits/raw logs with tested input hashes, final covering checks,
diagnosed failures, self-review, limitations and exact owned scratch inventory.
All new evidence, scripts, cache/basetemp files belong under the owned workspace;
pre-existing shared UV/pre-commit caches remain unowned. Return only status,
commit(s), one-line test summary, report path and material concerns.

## 3. Controller gates after the task

The controller verifies the committed/tested bytes and report, generates a
unique full base-to-head review package and dispatches a fresh combined task
reviewer with this task brief, report, package and binding constraints. Resolve
Critical/Important findings through a tested fix and re-review; preserve Minor
findings for final triage. Then dispatch a most-capable whole-branch reviewer.
Only reviewed committed bytes proceed to the complete local gate and protected
develop/main delivery. Post-promotion, conclusion/Done, safe own branch cleanup
and verified archive precede #261. Do not claim the goal complete at #264.
