# First-run setup implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Complete all ten #243 criteria with actionable setup and rediscovery in the same session.

**Architecture:** Resolver snapshots own read-only discovery diagnostics. A small setup view and optional existing-rail section own presentation and physical focus. App integrates existing persistence and serialized transactional service mounting.

**Tech Stack:** Python >=3.11,<3.14; Textual; vmx; anyio; pytest and existing snapshot tooling.

**Spec:** docs/superpowers/specs/2026-10-03-first-run-setup-design.md

## 1. Global Constraints

- Preserve Python >=3.11,<3.14. No new product dependencies.
- Reuse ConnectionResolver, ConnectionFormInline, its validators, and S3ConnectionsVM.add_async.
- Do not execute AWS CLI commands, launch browser login, generate IAM policies, create cloud resources, rewrite AWS CLI files, or add a second credential subsystem.
- AWS config and credentials bytes stay unchanged.
- Only applicable local checks; do not dispatch or rerun GitHub Actions.
- All ten original #243 acceptance criteria remain binding, including nav-rail rediscovery, literal config/auto-aws-profile/demo origins, and keyboard-only 120x40.
- No probe or provider work starts until explicit row selection or Save and open. Normal configured/demo startup stays intact.
- Use existing theme palette tokens and shared ModalButton; normal rail width remains 12, first-run width is 28.
- Preserve list/resolve behavior: app-config errors still raise, malformed AWS INI remains tolerated, explicit names win.
- Product tests start RED before implementation. Do not fabricate historical RED, weaken assertions, replace unrelated snapshots, or broaden live AWS access.
- No child agents. Workers run bounded affected suites; controller performs full applicable gates after final review.

## 2. Review Focus

- A malformed AWS INI still yields usable partial/profile sources with a diagnostic and can recover after external repair; Task 1 pins this.
- User-controlled name/source containing markup cannot render instructions or lose selection identity; Task 2 pins this.
- More discovered profiles than fit the rail remain reachable with the keyboard; Task 2 pins this.
- A submitted atomic save cannot be presented as cancelled or written twice; Tasks 2 and 3 pin controls and end-to-end duplicate/cancel behavior.
- Slow probe/discovery followed by Settings, modal focus, identity change, or shutdown cannot adopt stale content or steal focus; Task 3 pins deterministic barriers and original app regressions.

## 3. File responsibilities

| File | Responsibility |
|---|---|
| src/aws_tui/infra/connection_resolver.py | Immutable discovery diagnostic snapshot using existing parser/merge logic. |
| src/aws_tui/demo/connections.py | Same snapshot API over real demo connections. |
| src/aws_tui/vm/connection_discovery.py | Read-only structural presentation contracts; no resolver re-exports. |
| src/aws_tui/ui/widgets/first_run.py | Focusable ConnectionChoice, optional rail list, setup view, typed UI intents. |
| src/aws_tui/ui/widgets/nav_menu.py | Public first-run section update/activation; regular service rail unaffected. |
| src/aws_tui/ui/widgets/settings/connection_form.py | Optional public submit label, has_errors, safe pending-submit controls. |
| src/aws_tui/app.py | Existing startup, focus, save/discovery/selection workers and lifecycle integration. |
| tests/unit/infra/test_connection_resolver.py | Discovery and unchanged list/file contracts. |
| tests/unit/ui/test_first_run.py | Setup/form/row/focus/rail rendering contracts. |
| tests/unit/ui/test_connection_form_inline.py | Shared pending-submit/validation contracts. |
| tests/unit/ui/test_nav_menu.py | Optional rail and regular navigation isolation. |
| tests/unit/test_app_sanity.py | Awaited first-run mount and original public routing regression scope. |
| tests/integration/test_first_run_setup.py | All full-App ACs, real persistence, no premature requests, stale results. |
| tests/snapshot/test_first_run.py | Shipped themes at 120x40 and visible-content guards. |
| tests/snapshot/apps/first_run.py | Isolated production-shaped first-run snapshot harness if needed. |
| README.md; docs/connections.md; CHANGELOG.md | Accurate shipped first-run behavior and limits. |
| tests/docs/test_shipped_behavior.py; tests/docs/test_contract_parity.py | Replace now-obsolete absence claims with truthful guards. |

## 4. Task 1: Read-only connection discovery snapshots

**Files:** Modify resolver and demo files; test existing resolver suite and create tests/unit/demo/test_connection_discovery.py if no suitable existing demo connection suite exists (use inventory first).

**Interfaces:**
- Consume ConfigStore.load, existing _read_ini and credential dispatch; no service/provider dependency.
- Produce frozen ConnectionDiscovery with connections: tuple[Connection, ...] and invalid_sources: tuple[str, ...].
- Produce ConnectionResolver.discover() -> ConnectionDiscovery and DemoConnectionResolver.discover() -> ConnectionDiscovery.
- Invalid tokens exactly app-config, aws-config, aws-credentials; no raw error or secret text.
- Existing list() -> list[Connection] and resolve(name) unchanged for all callers.

- [ ] Step 1: Add tests first for empty snapshot, malformed app TOML plus usable AWS profile, malformed AWS INI diagnostic with surviving profiles, explicit-name precedence, external file repair, immutability, demo sources, and AWS bytes/absence unchanged. A representative assertion is:

```python
def test_discovery_reports_bad_app_config_without_hiding_profiles(store, tmp_path):
    store.path.write_text("[broken", encoding="utf-8")
    aws_config, aws_credentials = _write_aws_files(
        tmp_path, config_body="[profile added]\nregion = eu-west-1\n"
    )
    resolver = ConnectionResolver(config_store=store,
        aws_config_path=aws_config, aws_credentials_path=aws_credentials)
    before = aws_config.read_bytes()
    result = resolver.discover()
    assert result.invalid_sources == ("app-config",)
    assert [(c.name, c.source) for c in result.connections] == [
        ("added", "auto-aws-profile")]
    assert aws_config.read_bytes() == before
    assert not aws_credentials.exists()
```

- [ ] Step 2: Run the new tests before modifying product source; retain RED output in /tmp/aws-tui-243-discovery-red.log. Confirm missing snapshot/API is the cause, not fixture/setup failure.
- [ ] Step 3: Implement snapshot and diagnostics through shared existing helpers. The required shape is:

```python
@dataclass(frozen=True)
class ConnectionDiscovery:
    connections: tuple[Connection, ...]
    invalid_sources: tuple[str, ...] = ()
```

`discover` catches ConfigStore parsing/read failures in its own surface; list
continues to raise those. Pass a local diagnostic collector through the existing
AWS discovery helpers and inspect _read_ini's boolean result. Do not use mutable
resolver-global diagnostics, a new parser, raw error strings, probes or writes.
Keep merge order/credential behavior consistent with list. Demo returns its actual
list converted to a tuple without invented empty behavior.

- [ ] Step 4: Run complete resolver/demo affected suites, Ruff and mypy affected files; record GREEN exact commands/results. Inspect AWS-byte assertions and no-provider imports.
- [ ] Step 5: Self-review and commit feat: expose read-only connection discovery diagnostics. Write full task-1-report.md with RED/GREEN, interfaces, files, commit, concerns. Independent task review is the controller's next gate.

## 5. Task 2: First-run widgets and optional navigation section

**Files:** Create vm/connection_discovery.py, first_run.py and test_first_run.py; modify nav_menu.py, connection_form.py and their UI unit suites.

**Interfaces:**
- Consume existing real ConnectionDiscovery/Connection structurally through readonly VM-facing protocols, existing form/messages and shared ModalButton.
- Produce ConnectionDisplay(Protocol) with readonly name: str and source: str; ConnectionDiscoveryDisplay(Protocol) with readonly connections: tuple[ConnectionDisplay, ...] and invalid_sources: tuple[str, ...]. UI imports these from vm/connection_discovery.py; no infra imports, re-exports, casts or type ignores.
- Produce ConnectionChoice(connection: ConnectionDisplay), retaining exact connection name and rendering literal source as plain text.
- Produce FirstRunConnectionList with setup and connection rows; nested typed Textual messages SetupRequested() and ConnectionSelected(name: str).
- Produce FirstRunView(config_path: Path, hub: MessageHub[Message], id: str = "content-first-run").
- Public view methods: show_discovery(snapshot: ConnectionDiscoveryDisplay) -> None; show_error(text: str) -> None; set_busy(busy: bool) -> None; focus_default() -> None; cycle_focus(*, reverse: bool = False) -> bool; activate_focused() -> bool.
- View emits nested RetryRequested(). Add and setup guidance are view-local. Existing ConnectionFormSubmitted and ConnectionFormCancelled keep existing signatures and bubble/cancel appropriately.
- NavMenu public async show_first_run_connections(snapshot: ConnectionDiscoveryDisplay | None) -> None; None hides/removes section and restores normal width. Public activate_first_run_focused() -> bool routes the setup/choice controls before regular service commit.
- Form public optional keyword submit_label: str = "save" and readonly has_errors: bool. Existing Settings default unchanged.

- [ ] Step 1: Write UI tests before product changes. Mount real widgets under a Textual harness with real hub and resolver snapshots. Assert three literal separately focusable action labels, actual ConnectionFormInline, invalid Save disabled, setup instructions without CLI execution, cancel prior status, public Enter activation, all origin tokens, escaped markup, busy/duplicate/cancel semantics, 28/12 actual rail widths and >20 rows keyboard scrolling. Representative row test:

```python
@pytest.mark.parametrize("source", ["config", "auto-aws-profile", "demo"])
async def test_row_renders_literal_origin(source):
    connection = Connection(name="[bold]literal[/]", kind="aws",
        region="us-east-1", source=source, profile="literal")
    row = ConnectionChoice(connection=connection)
    assert source in str(row.render())
    assert row.connection_name == connection.name
```

The assertion must inspect rendered plain text including source, not only the
stored object. Add mounted pixel/content checks for the long origin token.
Pending submission must disable Save/Cancel/edit inputs and reject direct cancel;
close/clear_submitting/mark_name_invalid must restore controls and edit-name lock.
Existing form tests must still pass. Show status-copy tests for invalid and empty
snapshots; failure copy is provided by Task 3. Use focus assertions and physical
Pilot keys; no sleep-based focus guesses.

- [ ] Step 2: Execute new UI tests against pre-widget source; retain /tmp/aws-tui-243-widgets-red.log and verify meaningful RED.
- [ ] Step 3: Implement the bounded public widget interfaces through the readonly presentation protocols (existing concrete frozen snapshots satisfy them). Compose three ModalButtons, status/path guidance, AWS setup instructions, and the actual form with submit_label="Save and open". Canonical status strings:

```python
INVALID_CONFIGURATION = "Invalid configuration. Fix the configuration file, then Retry discovery."
NO_CONNECTIONS = "No AWS profiles or S3-compatible connections found. Add a connection or set up an AWS profile, then Retry discovery."
PROBE_FAILED = "Credential probe failed. Refresh credentials outside aws-tui, then select the connection again."
```

Invalid snapshot status takes precedence over empty. Nonempty valid snapshot says
select a connection to open it. Preserve preceding status while the form is open,
so Cancel restores it. Once submitting, reject direct Cancel and duplicate Save;
refresh disabled state with the existing _submitting flag. The first-run view
routes form-button Enter through ModalButton.press and Input Enter through the
existing form focus ring, never reading/mutating a private VM. Only plain labels
are rendered; no endpoints/secrets. Use a scrollable rail section for choices and
Connection setup. ConnectionChoice keyboard focus/arrow navigation never starts a
service; explicit activation posts only the exact name. Choice arrows must not
bubble into regular service selection. App will handle the intents in Task 3.

- [ ] Step 4: Run complete affected unit UI suites, including existing form and nav tests, and Ruff/mypy. Record GREEN and actual 120x40 rendering/focus evidence. Do not refresh normal nav/demo snapshots.
- [ ] Step 5: Self-review and commit feat: add actionable first-run setup widgets. Write full task-2-report.md with evidence and final public signatures. Independent task review is required before App integration.

## 6. Task 3: App lifecycle, acceptance tests, snapshots and docs

**Files:** Modify App and app sanity; create full-App integration and themed snapshot coverage; update README, connections, changelog and truthful documentation guards. Generate existing site/wiki outputs through current docs tooling.

**Interfaces:**
- Consume discover snapshots and all public UI APIs from Tasks 1/2.
- Consume S3ConnectionsVM.entry_from_form/add_async, existing public form failure methods, AwsSession.probe_token, RootVM.switch_connection_and_service, App service navigation lock/ownership and existing lifecycle content-mount worker.
- Produce working full-App setup flow; no new resolver/credential/persistence abstractions.
- Use typed UI message handlers, scoped to first-run sender so Settings submissions remain owned by its panel. Do not double-handle bubbled Settings messages.

- [ ] Step 1: Write full-App Pilot tests before modifying App. Use app_context_factory, overwrite only each new test's seeded app config with empty/malformed bytes, add in-memory keychain to the same real resolver/persistence owner when saving, and inject recording InMemoryFS plus a probe stub. Do not weaken/change default factory behavior. Representative physical rediscovery test structure:

```python
async with app.run_test(size=(120, 40)) as pilot:
    await wait_until(lambda: len(app.query(FirstRunView)) == 1,
                     what="empty setup mounted")
    assert recording_fs.calls == []
    aws_config.write_text("[profile added]\nregion = eu-west-1\n", encoding="utf-8")
    await pilot.press("tab", "tab", "enter")
    await wait_until(lambda: any(r.connection_name == "added"
        for r in app.query(ConnectionChoice)), what="rediscovered rail row")
    assert [c.name for c in ctx.connection_resolver.list()] == ["added"]
    assert recording_fs.calls == []
    assert probe_calls == []
```

Use the actual settled initial Add focus; if a harness has a different initial
focus, drive it explicitly and assert that focus before pressing keys. Cover all
ACs with real mounted production App: valid keyboard save/open; AWS bytes unchanged
for every path; invalid form; exact Cancel app bytes/absence; invalid app and AWS
config repair; zero sources; failed explicit probe distinct text; provider starts
only after selection; removed/changed identity; duplicate save; save failure with
preserved fields; provider/mount failure recovery; Settings -> setup navigation;
slow discovery/probe superseded by Settings/shutdown and modal focus. Use event
barriers, wait_until and drain_workers. Pin startup normal/demo behavior with
existing regressions, not fixture simplification. Update bare awaited-mount sanity
test to assert FirstRunView/config_path while preserving awaited replacement proof.

- [ ] Step 2: Run the new integration/sanity tests before App implementation; retain /tmp/aws-tui-243-flow-red.log and explain each expected failure. New snapshot content guards also start before implementation; record actual chronology honestly.
- [ ] Step 3: Mount FirstRunView on the existing no-connection branch. Run discovery in local worker without provider/probe calls; await rail refresh. Give first-run Add deferred initial focus rather than executing normal _drop_initial_focus; keep normal/demo focus behavior unchanged. App priority Enter and Tab handle first-run public APIs before existing nav/service handlers and preserve modal routing. Add SetupRequested, RetryRequested, ConnectionSelected, and scoped form-submit handlers. Save delegates exactly:

```python
entry = ctx.s3_connections_vm.entry_from_form(event.form)
await ctx.s3_connections_vm.add_async(entry)
```

On success close the form and refresh discovery; on failure clear/rearm using the
form's existing public methods and safe status text, preserving values. Then
activate the freshly resolved saved name only if Save and open still owns the
current generation. Retry never selects. Explicit selection re-discovers identity,
probes off-loop, treats thrown/MISSING/EXPIRED as PROBE_FAILED without provider
creation, and uses CONNECTED plus serialized existing transactional S3 adoption
and service mounting. Suppress duplicate service menu mount callbacks only through
existing ownership conventions; never bypass lifecycle. Check generation after
every await and before VM or widget adoption, restore actionable setup on build/
mount failure, and do not let stale work overwrite Settings or shutdown. Navigation
away during a committed save never undoes durable config; stale visual work must
not autoactivate. App source stays on public page/form interfaces.

- [ ] Step 4: Implement snapshot harness at 120x40 using actual setup/nav widgets and existing theme loader. Use shipped theme parameterization, snapshot cases empty actions and open form; inspect representative dark/light SVGs visually. Content guards must read generated SVG and assert action/form labels, status and visible source tokens, so blank parity cannot pass. Only create new first-run goldens; diagnose any existing changed golden before editing it.
- [ ] Step 5: Update shipped guidance: add-in-session, Save and open, external AWS setup then Retry, origins, read-only AWS files and busy Cancel boundary. Remove obsolete absence/relaunch statements without claiming a retired FirstRunModal/ResumeModal exists. Update old absence-test guards to positive behavior and preserve unrelated checks. Canonical markdown uses numbered headings; generate site/wiki via existing build_docs and check parity. No separate wiki/site publication.
- [ ] Step 6: Run full affected App sanity, integration setup/Settings/navigation/focus/modal/keybindings/recovery, unit resolver/UI suites, first-run snapshots/content guards and docs tests once stable. Record command/result evidence in /tmp/aws-tui-243-flow-green.log and task-3-report.md; run hooks/Ruff/mypy affected source. Controller alone runs complete local gate once after independent whole-branch review, including coverage >=70%, all-file hooks, strict docs and package build/check_dist/twine. No Actions dispatch, release, live AWS or publication.
- [ ] Step 7: Self-review every AC against concrete tests and commit feat: integrate first-run setup and connection rediscovery. Report exact commits, RED/GREEN chronology, snapshot inspection artifacts, all ten AC mappings, and remaining concerns.

## 7. Plan self-review and execution

Task 1 provides exactly the snapshot types Task 2 consumes; Task 2 provides exact
view, row, rail, form and message interfaces Task 3 consumes. Shared form pending
controls are tested in Task 2 and through real persistence in Task 3. All ten ACs
and five Review Focus cases have test owners. Invalid snapshot does not auto-open
usable profiles, and failed probe does not accidentally fall through to provider
construction. Public form label defaults preserve Settings. Use the clean current
codex/issue-243-first-run-setup branch, no additional worktree needed. Fresh workers
and fresh task reviews are sequential, then one whole-branch review and complete
authorized ticket delivery before #246. Standing unsupervised user authorization
supersedes repeated stage approval prompts.
