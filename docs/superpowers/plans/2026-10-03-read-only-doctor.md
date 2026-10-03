# Read-only doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement every AC of #246 with safe offline diagnostics, explicit bounded read-only probes, and useful in-app help.

**Architecture:** Immutable checks and reports are shared by a pure local collector and an explicitly invoked probe adapter. CLI dispatch occurs before app composition. Help receives actual context paths without reaching AWS layers.

**Tech Stack:** Python 3.11+, existing ConfigStore/ConnectionResolver/AwsSession/KeymapStore/redaction, botocore 1.40.61+, Textual and pytest/Pilot.

**Spec:** docs/superpowers/specs/2026-10-03-read-only-doctor-design.md

## 1. Global Constraints

- Python remains >=3.11,<3.14; dependencies and lockfile remain unchanged.
- JSON has integer `schema_version: 1` and each check has `name`, `result`, `context`, `next_step`.
- Default doctor opens no socket, performs no keyring/provider/process call, and writes no file or permission.
- Optional probe requires one exact source name; no SSO OIDC refresh/rotation or durable writes.
- Probe connect/read timeout 5 seconds and total_max_attempts 1, including credential-provider clients.
- Preserve architecture rules, log/crash formats, existing CLI behavior and actual running help paths.
- Only applicable local checks run; GitHub Actions remain disabled.
- Use synthetic fixtures only; do not invoke live AWS resources or publish packages/docs.

## 2. Review Focus

- Corrupt UTF-8/JSON or non-object SSO cache: safe unreadable result and no traceback/secrets (Task 1).
- A keychain source beside the probed source: only the named source may request its own secrets (Task 2).
- A near-expiry modern SSO token used through a nested assume-role source: no refresh or disk save (Task 2).
- Terminal controls/Rich markup/secret-bearing endpoint in metadata: both reports and help render safely (Tasks 1 and 3).
- Doctor under AWS_TUI_DEMO=1 or combined launch flags: real offline diagnostics or clear usage error, no UI (Task 3).

---

## 3. Implementation tasks

### 3.1. Task 1: Immutable reports and offline local diagnostics

**Files:**
- Create: `src/aws_tui/infra/doctor.py`
- Test: `tests/unit/infra/test_doctor.py`
- Read/reuse: config_store.py, connection_resolver.py, aws_session.py, keymap_store.py, paths.py, redaction.py

**Interfaces:**
- Produces frozen `DoctorPaths(config_file: Path, cache_dir: Path, aws_config_file: Path, aws_credentials_file: Path, sso_cache_dir: Path)` and `doctor_paths() -> DoctorPaths` with runtime defaults.
- Produces frozen `DoctorCheck(name: str, result: str, context: Mapping[str, str], next_step: str, actionable: bool = False)`.
- Produces frozen `DoctorReport(checks: tuple[DoctorCheck, ...])`, `.exit_code: int`, `.render_json() -> str`, `.render_text() -> str`. Public report serializers never expose internal auth data.
- Produces `collect_local_diagnostics(paths: DoctorPaths | None = None) -> DoctorReport`.
- Later tasks compose checks using `dataclasses.replace(report, checks=(*report.checks, check))`; for an explicit probe, first omit the default skipped probe row. No duplicate schema or mutable auth report payload.

- [ ] **Step 1: Write failing tests for the immutable schema and actual fixture-driven collector.** Fixture creates a private tmp home with explicit paths and no real credentials. Write tests equivalent to:

```python
def test_report_schema_and_exit():
    report = DoctorReport((DoctorCheck('config', 'ok', {'path': '/tmp/config.toml'}, 'No action needed.'),))
    payload = json.loads(report.render_json())
    assert payload['schema_version'] == 1
    assert set(payload['checks'][0]) >= {'name', 'result', 'context', 'next_step'}
    assert report.exit_code == 0
    assert 'config' in report.render_text()
    assert 'No action needed.' in report.render_text()

def test_invalid_config_is_actionable(paths):
    paths.config_file.write_text('not = [valid', encoding='utf-8')
    report = collect_local_diagnostics(paths)
    assert any(c.result == 'invalid_config' and c.actionable for c in report.checks)
    assert report.exit_code == 1
```

Include distinct fixture cases for missing config (with/without usable auto profile), keymap token/collision/unknown action, missing S3 env/static/shared keys, valid static and modern/legacy SSO, expired/missing/unreadable SSO, missing accessToken, malformed cache shape/encoding, AWS INI discovery failure, non-SSO offline limitations, role-source SSO/cycle, dynamic providers unverified, keychain unverified and no keychain calls. Block socket creation, keyring, credential_process and provider clients; compare file bytes/modes/inventory. Plant all privacy sentinels in both reports, metadata, invalid inputs, env and pre-existing logs/cache; no raw content may escape.

- [ ] **Step 2: Run RED before implementation.** `.venv/bin/python -m pytest tests/unit/infra/test_doctor.py --no-cov -q` must fail because the doctor module/behavior is absent; record the output in the task report.
- [ ] **Step 3: Implement the smallest collector using read-only existing infrastructure.** Use fixed names/results/guidance and whitelist context. Keep selected secret values internal; no raw exceptions. Avoid keychain/backend/provider instantiation. Discriminate SSO using local metadata and reuse probe_token while containing malformed shapes/encodings. Do not alter AwsSession's app semantics or existing log/crash code.

```python
@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def exit_code(self) -> int:
        return int(any(check.actionable for check in self.checks))
```

- [ ] **Step 4: Verify GREEN and affected existing components.** Run doctor, config_store, connection_resolver, aws_session, keymap_store and redaction unit files with `--no-cov -q`; run Ruff and mypy on changed production files. Record command/count/output and self-review full task diff. Do not run the full repository suite; the controller performs that once on final branch.
- [ ] **Step 5: Commit coherent Task 1 files.** `git add src/aws_tui/infra/doctor.py tests/unit/infra/test_doctor.py` then `git commit -m "feat: add read-only local doctor diagnostics"`. Hooks remain enabled; request justified escalation if sandbox OS hooks fail.

### 3.2. Task 2: Bounded explicitly named read-only probes

**Files:**
- Create: `src/aws_tui/infra/doctor_probe.py`
- Modify if needed: `src/aws_tui/infra/connection_resolver.py` (selected-source resolution only; preserve old API behavior)
- Test: `tests/unit/infra/test_doctor_probe.py`, selected-source cases in existing resolver tests

**Interfaces:**
- Consumes `DoctorPaths`, `DoctorCheck` and `doctor_paths()` from Task 1.
- Produces `probe_source(name: str, paths: DoctorPaths | None = None) -> DoctorCheck`.
- Task 3 calls this function only for explicit --probe NAME. Source identity must remain exact internally; projected display is safe.

- [ ] **Step 1: Write failing fake-client probes and real-SDK local SSO integrity tests.** Classify both service families with complete fake context/lifecycle, not mocked result strings:

```python
@pytest.mark.parametrize(('condition', 'expected'), [('denied', 'denied'), ('unreachable', 'unreachable'), ('timeout', 'timed_out'), ('success', 'ok')])
def test_probe_classifies_client_outcomes(paths, client_factory, condition, expected):
    client_factory.condition = condition
    result = probe_source('chosen', paths)
    assert result.result == expected
    assert client_factory.closed
    assert client_factory.calls == ['chosen']
```

Test no fallback for unknown names, keychain selected only, missing/expired/unreadable prerequisite, process/MFA skipped including nested profiles, secret-rich service errors never printed, TLS/endpoint/path-style preserved, retries/timeouts on nested auth clients, success payload discarded, and cleanup on every error. Build valid near-refresh modern, legacy and nested assume-role SSO fixtures with fake get_role_credentials/AssumeRole/STS clients; forbid OIDC create_token, token cache writes and interactive prompts; verify exact cache bytes/modes/inventory. These tests use actual SDK credential provider loading at botocore 1.40.61, not an invented substitute resolver.

- [ ] **Step 2: Verify RED.** `.venv/bin/python -m pytest tests/unit/infra/test_doctor_probe.py --no-cov -q` must fail before probe implementation; save evidence.
- [ ] **Step 3: Implement confined SDK adaptation and named resolution.** Use standard botocore providers; a read-only subclass of SSOTokenProvider overrides `_refresher` to load and return FrozenAuthToken without refresh/save. A small ProfileProviderBuilder override creates SSOProvider with that token provider; use it also inside AssumeRoleProvider. Replace credential resolver providers through its methods, not global monkeypatches. Pass bounded default client config, memory-only role credential cache and runtime SSO path. Skip unknown external credential processes/interactive MFA before resolving, retaining safe guidance. Resolve only named S3 credentials. Main probe operations are GetCallerIdentity or ListBuckets, response discarded.

```python
probe_config = BotoConfig(connect_timeout=5, read_timeout=5,
                          retries={'total_max_attempts': 1, 'mode': 'standard'})
# All client creation, including provider clients, inherits probe_config.
```

- [ ] **Step 4: Verify GREEN and unchanged auth components.** Run doctor_probe, doctor, resolver and aws_session unit files, Ruff and mypy changed files. Read full diff and record test counts and bounded/read-only evidence. No real AWS/keychain/network use in tests.
- [ ] **Step 5: Commit coherent Task 2.** Stage only its production/test files and `git commit -m "feat: add explicit read-only doctor probes"`.

### 3.3. Task 3: Early CLI dispatch, actual-path help and canonical docs

**Files:**
- Modify: `src/aws_tui/app.py` main/action_help
- Modify: `src/aws_tui/ui/widgets/help_modal.py`
- Create: `tests/unit/test_doctor_cli.py`, `tests/integration/test_doctor_help.py`
- Modify: `README.md`, `docs/cookbook.md`, `CHANGELOG.md`; affected existing docs tests only when genuine shipped behavior changed
- Existing regression tests: test_app_sanity.py, infra/test_log_sink.py, infra/test_crash_dump.py, ui/test_overlay_widgets.py, integration/test_modal_key_containment.py

**Interfaces:**
- Consumes Task 1 report/collector and Task 2 probe_source.
- App parser adds doctor subparser and --json/--probe; raises SystemExit(report.exit_code) after rendering before build_app_context. Explicit --probe replaces the default skipped probe row with the actual result; retain every local diagnostic row.
- HelpModal adds optional `log_path: Path | None = None`, `crash_path: Path | None = None`; app supplies ctx.log_sink.path and ctx.log_sink.path.parent.parent / "crash" (the actual _build_crash_report calculation). Defaults preserve existing callers using pure paths. Dynamic values use literal Static markup=False.

- [ ] **Step 1: Write failing real CLI and running-app help tests.** Block socket/keyring/process/build_app_context/resize/AwsTuiApp.run; use actual local collector fixtures for healthy/failing results and SystemExit. Both formats and explicit fakeprobe invocation receive tests, including malformed flags and demoenv:

```python
def test_doctor_main_never_composes(monkeypatch, capsys, healthy_home):
    monkeypatch.setattr(sys, 'argv', ['aws-tui', 'doctor', '--json'])
    monkeypatch.setattr(app_module, 'build_app_context', forbidden)
    with pytest.raises(SystemExit) as exit_info:
        app_module.main()
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out)['schema_version'] == 1
```

Running AwsTuiApp with app_context_factory and run_test, open help through action/registered key, await mounted HelpModal, assert doctor command and exact ctx log/crash paths in rendered Static text. Include bracket/control-bearing synthetic paths and keyboard-scroll reachability. Preserve existing help/keymap/theme tests.
- [ ] **Step 2: Verify RED.** Run new CLI/help tests before modifying app/help; save argparse missing-subcommand/content failures.
- [ ] **Step 3: Wire early CLI and safe help.** Reuse one report schema. For an explicit name, replace the default skipped probe row via `dataclasses.replace(report, checks=(*tuple(check for check in report.checks if check.name != 'probe'), probe_source(name)))`. With no explicit name, retain the collector's skipped check and do not call probe_source. Add a CLI test asserting exactly one probe row for an explicit request. Existing root flags/version/help/demo behavior remain compatible. Provide error usage for doctor+launch flags. No app composition on doctor, including malformed config. Update main docstring and actual HelpModal construction, preserving layer boundaries.
- [ ] **Step 4: Update canonical docs and verify.** Document schema_version=1, 0/1/2 exits, default offline/no socket/no writes, namedprobe usage/operations/timeouts and SSO/process/MFA limits; help locations derive effective runtime paths. Add a concise changelog entry. Inspect actual docs renderer/generator and run existing strict docs/parity tests locally, no publication. Do not update unrelated snapshots; if actual help snapshots exist and change, inspect product rendering/content first and report exact changed golden list.
- [ ] **Step 5: Run new tests plus affected CLI/help/log/crash/docs tests, Ruff and mypy.** Existing log/crash tests remain unmodified. Report RED/GREEN commands and counts, self-review full diff and all AC mappings.
- [ ] **Step 6: Commit coherent integration/docs.** Stage exact Task 3 files and `git commit -m "feat: expose doctor CLI and diagnostic help"`.

## 4. Controller verification and delivery

After each task, review its complete BASE..HEAD diff with fresh spec/quality reviewer. Do not start the next task with unresolved material findings. After all tasks, request whole-branch review, run all applicable all-file hooks, complete default full suite with coverage and all snapshots, strict docs/site/wiki parity, package/build/twine/import checks. Verify no hosted Actions run. Complete exact-head protected develop PR, attached promotion PR, post-merge local checks/tree parity, safe own feature cleanup, substantive issue closure with all eight ACs and board Done. Preserve unrelated Dependabot PRs. Only then start #239.

## 5. Plan self-review

All ACs map to explicit tasks and evidence; interfaces and path names match across tasks. The five review-focus cases are each required in their owning task. Default offline validation and explicit SDK networking are separate; task constraints agree. Worker suites are bounded to affected files; final full local gate belongs to the controller. The goal's standing autonomous authorization supplies execution-method and routine plan approval, so implementation proceeds without a redundant approval menu.
