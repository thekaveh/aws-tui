# EMR job cancellation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel one selected EMR Serverless run through exact-target confirmation, a single service attempt, and truthful later polling.

**Architecture:** Extend the existing domain/client/demo boundary and EmrServerlessPageVM operation owner. Reuse the danger confirmation and deferred Textual workers; all UI entry points converge on one registry action. Preserve existing selection, clone, authentication and polling contracts.

**Tech Stack:** Python, aioboto3/botocore, VMx, Textual, pytest/asyncio/Pilot, existing docs tooling.

**Spec:** `docs/superpowers/specs/2026-10-03-emr-cancellation-design.md`.

## 1. Global Constraints

- Python remains >=3.11,<3.14; dependencies and lockfile remain unchanged.
- Coverage floor remains 70%.
- Only applicable local checks run; GitHub Actions remain disabled.
- Do not invoke live AWS mutations during implementation or verification.
- Preserve VMx lifecycle/observable/modal ownership, Textual focus containment, existing clone fidelity/idempotency, source recovery, pagination, and existing log/crash formats.
- Application stop/delete and bulk cancellation remain outside scope.
- Dependency #237 is still open; recheck before final integration and use its actual policy if it lands first. Do not invent its protection field or block current work on it.
- Exact API: cancel_job_run(application_id, job_run_id) -> None; wire applicationId/jobRunId only. Cancellation-specific total_max_attempts=1, standard mode, existing connect/read timeouts10/60. Existing shared retries remain unchanged.
- No worker pushes, PRs, issue/board edits, child agents, remote publication, broad unrequested refactor, hook bypass or blind golden replacement. Controller owns those integration/delivery steps.
- Use `.venv/bin/python` with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib`. Normal hooks use `UV_CACHE_DIR=/tmp/aws-tui-239-uv-cache UV_NO_SYNC=1 UV_OFFLINE=1`. Controller owns the complete final suite; workers run the full appropriate covering scope once after their focused RED/GREEN work, not repeated repository-wide runs.

## 2. Review Focus

1. A matching detail and summary disagree, including equal timestamps or unavailable detail: honor the latest observation, prefer non-cancellable state on a tie, and allow a valid selected summary without a detail permission prerequisite. Task2 pins these cases.
2. A demo clone state walk is sleeping when cancelled: it must never resurrect the cancelled run or stop a different run. Task1 uses barriers at state transitions.
3. Source/application/run ownership changes during confirmation or a sent request, including shutdown: no wrong-target request, stale feedback/state, leaked modal or undrained client cleanup. Tasks2/3 pin before/after-dispatch and queued-worker cases.
4. A retryable transport/service failure or lost response: exactly one cancellation service attempt, fixed distinct safe feedback, no automatic replay, and caller cancellation preserved. Tasks1/2 pin wire attempts/error categories.
5. The key is remapped, a text input/modal has focus, the palette is still dismissing, or several keys arrive before workers start: honor the configured action, preserve typing/containment, defer palette dispatch, and make one client call. Task3 uses real Pilot entry points.

## 3. Tasks

### 3.1. Task 1: Domain cancellation and demo integrity

**Files:** Modify `src/aws_tui/domain/emr_serverless.py`, `src/aws_tui/demo/in_memory_emr.py`, `src/aws_tui/services/emr_serverless/service.py` (failed adapter only), `tests/unit/domain/test_emr_serverless.py`, and `tests/unit/services/test_emr_serverless_service.py`. Demo regression assertions may live in the existing domain test module; no unrelated fake/provider rewrite.

**Interfaces:** Produces `EmrServerlessClientProtocol.cancel_job_run(self, application_id: str, job_run_id: str) -> None`, the identical real/demo/failed-adapter methods, `CANCELLABLE_JOB_RUN_STATES: frozenset[JobRunState]`, and `EMR_CANCEL_BOTO_CONFIG: BotoConfig`. Consumes the existing `_map_boto_error`, `EMR_BOTO_CONFIG`, immutable run records, demo clock/state walk, and failed-client error factory. Later tasks consume the method and state set, not the private transport implementation.

- [ ] **Step 1: Write exact wire/error/lifecycle and demo RED tests.** Use existing fake session patterns or this complete mock boundary:

```python
wire = SimpleNamespace(cancel_job_run=AsyncMock(return_value={
    "applicationId": "a1", "jobRunId": "r1",
}))
manager = AsyncMock()
manager.__aenter__.return_value = wire
session = Mock()
session.client.return_value = manager
client = EmrServerlessClient(session=session, region_name="us-east-1")
assert await client.cancel_job_run("a1", "r1") is None
wire.cancel_job_run.assert_awaited_once_with(applicationId="a1", jobRunId="r1")
config = session.client.call_args.kwargs["config"]
assert config.retries == {"total_max_attempts": 1, "mode": "standard"}
assert (config.connect_timeout, config.read_timeout) == (10, 60)
assert EMR_BOTO_CONFIG.retries["total_max_attempts"] == 6
manager.__aexit__.assert_awaited_once()
```

Import SimpleNamespace and unittest.mock helpers in the test. Parameterize SDK AccessDeniedException/ResourceNotFoundException/ThrottlingException/ValidationException, credential errors, transport exceptions, and an unrelated exception; assert the mapped class, one await, cleanup and preserved unrelated exception/caller CancelledError. Add a synthetic SDK HTTP boundary with complete fake credentials and no real sockets: retryable500/throttling/transport failures must record one service attempt with this config, not merely one mocked wrapper call.

Seed two demo applications/runs and details. Cancel a RUNNING run and assert both read surfaces become CANCELLED, its immutable old records retain RUNNING, payload fields are preserved, and the other app/run is unchanged. Assert NotFoundError on a missing application/run, no rewrite of SUCCESS/FAILED, and idempotent already-CANCELLED acknowledgement. The failed adapter must raise its original typed error for the new method.

Pin the state-walk race with an Event barrier at the existing `_advance_state` sleep/transition boundary; call public cancel while the walk is paused, release and await it, then assert:

```python
await fake.cancel_job_run("a1", run_id)
release.set()
await asyncio.gather(*walk_tasks)
assert (await fake.get_job_run("a1", run_id)).state is JobRunState.CANCELLED
assert (await fake.list_job_runs("a1"))[0].state is JobRunState.CANCELLED
assert (await fake.get_job_run("a2", other_run_id)).state is JobRunState.SUCCESS
await fake.aclose()
```

The test fixture supplies run_id/other_run_id/release and captures both actual walk_tasks from public start calls and controlled transition barriers; do not use guessed elapsed sleeps or a saved expected constant in place of reading the other run. Exercise cancellation before each scheduled transition and cleanup without pending-task warnings.

- [ ] **Step 2: Run RED.** `.venv/bin/python -m pytest tests/unit/domain/test_emr_serverless.py tests/unit/services/test_emr_serverless_service.py -q --tb=short`. Expected initial failure: missing cancel_job_run/config/state-set method contract. Record separate meaningful RED for state-walk resurrection if the first new method alone exposes it.
- [ ] **Step 3: Implement the narrow boundary.** Add the protocol/real/failed methods. Build a cancellation-specific config by merging the shared config with total_max_attempts1/standard mode; preserve async-context mapping and cancellation. Demo cancellation records exact ids, finds only their records, uses `_set_run_state`/replace with the deterministic clock, and guards every later automatic transition against an already cancelled run. Do not stop all demo tasks or change another application's data.
- [ ] **Step 4: Verify GREEN and covering regressions once.** Run the two files plus `tests/unit/demo`, `tests/unit/vm/emr_serverless/test_clone_vm.py`, and focused Ruff/format/mypy on changed source. Confirm exact attempt/error/cleanup assertions, old clone/dedup behavior and demo acclose/dispose still pass. No repository-wide suite or snapshots needed for this API-only task.
- [ ] **Step 5: Self-review, commit and report.** Review complete task diff, stage only owned files, use normal hooks, commit `feat: add single-attempt EMR job cancellation`. Report BASE/HEAD, concrete RED/GREEN and covering commands/counts, synthetic transport scope, demo race evidence and any concerns to `task-1-report.md` in the assigned workspace. Stop for controller review before Task2.

### 3.2. Task 2: Exact-target cancellation in the page viewmodel

**Files:** Modify `src/aws_tui/vm/emr_serverless/page_vm.py`; create `tests/unit/vm/emr_serverless/test_cancel_job_run.py`; modify existing page-VM tests only where a genuine shared contract assertion requires it.

**Interfaces:** Consumes Task1's cancel_job_run method and CANCELLABLE_JOB_RUN_STATES, existing selected summary/detail/source and OperationOwner, and existing ConfirmRequest/ConfirmPath. Produces `CancelJobRunResult(status: Literal["requested", "dismissed", "busy", "inert", "stale", "superseded", "denied", "not_found", "throttled", "unreachable", "auth_required", "invalid", "error"], message: str | None)`, a read-only `cancel_busy: bool` property, `can_cancel_selected_run(self) -> bool`, and `async cancel_selected_run(self, ask: Callable[[ConfirmRequest], Awaitable[bool]], *, source_is_current: Callable[[], bool] | None = None) -> CancelJobRunResult`. Export the result class for typing in Task3. Preserve existing PageVM construction and selection/recovery APIs.

- [ ] **Step 1: Add VM RED cases using a real page and recording demo client.** Build EmrServerlessPageVM with InMemoryEmr, MessageHub, NULL_DISPATCHER, and an explicit Connection(name="active",profile="profile",region="us-east-1",kind="aws",source="config"). Construct, seed application a1/run r1 and optional detail, then await setup. Keep fake provider records separate from cached VM snapshots. Dispose/drain the page and fake in fixture teardown.

```python
@pytest.fixture
async def page_with_state():
    owners = []
    async def make(state):
        fake = InMemoryEmr()
        fake.add_application(app_id="a1", name="etl")
        fake.add_job_run(application_id="a1", job_run_id="r1", state=state)
        fake.add_job_run_detail(application_id="a1", job_run_id="r1")
        page = EmrServerlessPageVM(
            client=fake, logs_client=fake.make_logs_client(),
            hub=MessageHub(), dispatcher=NULL_DISPATCHER,
            connection=Connection(name="active", profile="profile",
                region="us-east-1", kind="aws", source="config"),
        )
        page.construct()
        await page.setup()
        fake.calls.clear()
        owners.append((page, fake))
        return page, fake
    yield make
    for page, fake in owners:
        await page.shutdown()
        page.dispose()
        await fake.aclose()

@pytest.mark.parametrize("state", list(JobRunState))
async def test_cancel_eligibility_for_every_state(page_with_state, state):
    page, fake = await page_with_state(state)
    ask = AsyncMock(return_value=True)
    result = await page.cancel_selected_run(ask)
    calls = [args for name, args in fake.calls if name == "cancel_job_run"]
    if state in CANCELLABLE_JOB_RUN_STATES:
        assert result.status == "requested"
        assert result.message == "cancellation requested"
        assert calls == [("a1", "r1")]
    else:
        assert result.status == "inert" and result.message
        assert calls == []
        ask.assert_not_awaited()
```

Define page_with_state in this test file, not through an undefined shared fixture. Pin named confirmation paths/source label/profile/region with an ask callback returning False, zero calls and untouched cached/backend records. Use Events in ask to swap application, selected run, source guard, disappearance and terminal state before acceptance; assert no substitute call. Cover latest summary/detail disagreement in both timestamp directions, equal-time non-cancellable precedence, unrelated detail, missing detail, missing selection and disposed/shutdown owner.

Hold a recording client's cancel await behind an Event and make several concurrent cancel_selected_run calls. Assert one confirmation/one exact provider call, cancel_busy stays true through the wait and then releases. Change selection/source during this request, return success and each mapped error, and assert superseded silent result plus no change to the new target. Pin caller CancelledError and shutdown's durable cleanup with barriers, not timed sleeps.

```python
result = await page.cancel_selected_run(AsyncMock(return_value=True))
assert result.status == "requested"
assert page.job_runs.runs[0].state is JobRunState.RUNNING
assert page.job_run_detail.detail.state is JobRunState.RUNNING
await page.refresh_job_runs()
await page.refresh_job_run_detail()
assert page.job_run_detail.detail.state is JobRunState.CANCELLED
```

Use a recording provider variant whose subsequent reads first return CANCELLING and then CANCELLED; both stages must come from refresh, not acknowledgement. Parameterize PermissionDeniedError→denied, NotFoundError→not_found, ThrottledError→throttled, ProviderUnreachableError→unreachable, AuthRequiredError→auth_required, ValidationError→invalid, and unexpected/other ProviderError→error; assert distinct fixed guidance, no planted secret/error payload, one call, and no retry/pane reset.

- [ ] **Step 2: Run RED.** `.venv/bin/python -m pytest tests/unit/vm/emr_serverless/test_cancel_job_run.py -q --tb=short`. Expected missing VM contract; after the basic flow, observe specific stale/duplicate/poll/error RED before the corresponding fixes. Preserve all assertions.
- [ ] **Step 3: Implement within the existing owner.** Add the immutable result/internal target. Derive exact target from the visible selected summary and matching fresh detail under spec§3; missing detail is not a blanket refusal. Reserve cancel_busy before first await, notify safely through the hub, and put confirmation/provider work into `_operations.run`. Construct danger ConfirmRequest with literal source/application/run paths, Request cancellation/Keep running labels. After ask, revalidate owner/source/ids and current eligibility; send captured ids once. Map safe statuses, ignore stale after-await outcomes, catch OperationSuperseded silently, propagate caller cancellation and release reservation in finally. Do not fabricate backend states or add polling/retry infrastructure.
- [ ] **Step 4: Verify GREEN and existing lifecycle/clone/read coverage once.** Run the new file and `tests/unit/vm/emr_serverless`, `tests/unit/services/test_emr_serverless_service.py`, `tests/unit/vm/test_operation_owner.py`, and focused lint/type checks. Required evidence includes every enum member, before/after-dispatch races, no-detail permission independence, actual polling and all mapped categories; do not run the complete repository suite here.
- [ ] **Step 5: Self-review, commit and report.** Commit only owned changes with normal hooks as `feat: coordinate confirmed EMR run cancellation`. Report actual interfaces/BASE/HEAD, RED/GREEN/covering evidence and lifecycle/error limits in `task-2-report.md`. Stop for the independent task review before Task3.

### 3.3. Task 3: Real UI action, danger confirmation and docs

**Files:** Modify `src/aws_tui/app.py`, `src/aws_tui/ui/widgets/emr_serverless/page.py`, `src/aws_tui/infra/keymap_store.py`, `src/aws_tui/ui/bindings.py`, `src/aws_tui/vm/chrome/hint_legend_vm.py`, `src/aws_tui/services/emr_serverless/service.py` and domain docstrings (stale deferred-cancellation statements only). Create `tests/integration/test_emr_cancellation.py`; extend existing keymap/binding/hint/palette/poller/docs contract tests as required. Canonical docs: `docs/services/emr-serverless.md`, `docs/keybindings.md`, `docs/contract-ledger.md`, `CHANGELOG.md`. Snapshot goldens only after an observed rendered change, scoped and reported.

**Interfaces:** Consumes Task2's can_cancel_selected_run/cancel_busy/cancel_selected_run/CancelJobRunResult and existing TextualDialogService(app, app.app_ctx.confirm_vm, hub=hub), registry/palette register/unregister, notifications and source/page ownership. Produces `emr.cancel` with default x and `EmrServerlessPage.action_cancel_selected_run(self) -> None`, routed by `AwsTuiApp.action_cancel_emr_run(self) -> None`. It must use Task2's workflow; no second dispatch/error/selection engine.

- [ ] **Step 1: Write real Pilot RED tests with synthetic providers and actual app.** Use app_context_factory or existing build_app_context/test service factory patterns with private temp config/cache and complete fake sources. Mount the real EMR page with a RUNNING row and pollers explicitly controlled at their public read boundary. Press x and assert:

```python
await pilot.press("x")
await wait_until(lambda: isinstance(app.screen, ConfirmModal), what="EMR cancel confirmation")
assert app.screen.request.title == "Cancel EMR job run?"
paths = {path.label: path.path for path in app.screen.request.paths}
assert paths["Application"] == "a1" and paths["Run"] == "r1"
assert "active" in paths["Source"] and "us-east-1" in paths["Source"]
await pilot.press("escape")
assert cancel_calls == []
```

The fixture supplies cancel_calls from the actual recording client's call log, never a saved empty expectation. Reopen, move to the positive button and confirm: exactly one captured call, visible cancellation requested, unchanged cached RUNNING before a controlled detail poll, then actual CANCELLING/CANCELLED rendering after provider transitions. Repeat x before workers begin and while the accepted request waits; assert one request, no replaced worker or coroutine warning. Terminal/missing states show reason and have no callable hint/palette offer.

Test active connection/application/selection replacement and page unmount while confirmation/client await is held; no wrong-source call, stale toast or surviving confirmation, caller/client cleanup settles. Exercise denied/not-found/throttled/network feedback through the real UI with fixed safe text. Remap emr.cancel to another printable key, verify x stops invoking and the overlay key works; typing x into Settings or a focused modal input remains typing, and other service contexts never cancel a run.

Open the actual palette, choose Cancel selected EMR job run and assert it dismisses before confirmation opens. A terminal/busy transition while the palette is open removes this one entry through existing registry methods; no new general palette API. Hint click and keyboard use the same registered command. Keep existing clone/modal/focus/poller tests.

- [ ] **Step 2: Run RED.** `.venv/bin/python -m pytest tests/integration/test_emr_cancellation.py -q --tb=short`. Expected missing action/confirmation/VM routing. Record actual UI failures and fix fixture errors before counting them as product RED.
- [ ] **Step 3: Wire the existing workflow and availability.** Register emr.cancel/default x/description/effect/precondition and EMR hint only. App handler forwards to the mounted page. Page starts deferred work in distinct non-exclusive emr-cancel, uses shared TextualDialogService as ask callback and current mounted-owner guard, and projects result messages only for the same live owner. Defer palette invocation after dismissal, conditionally register/unregister just this entry, and update hint disables from relevant EMR selection/detail/busy hub events. Retain standard binding/input/modal handling and existing pollers; never set a run state on acknowledgement.
- [ ] **Step 4: Update canonical docs and verify covering scopes once.** Document x/emr.cancel, exact-target danger confirmation, eligible states, CancelJobRun IAM permission, one-attempt/ambiguous-outcome guidance, polled states, demo cancellation and state-walk integrity. Add the consumed request/config contract without rewriting unrelated ledger rows. Run the new integration file; existing EMR integration/UI/poller/clone tests, confirmation/modal containment, keybinding/palette wiring, unit keymap/bindings/hint/palette and docs tests. Run local site/wiki generation, `make docs-check` with Cairo DOCS_PY, and normal applicable lint/type/architecture checks. If changed Commands chrome causes a real golden difference, show its content/layout evidence before replacing only the affected files; never normalize failures away.
- [ ] **Step 5: Self-review, commit and report.** Commit coherent owned code/docs/tests with normal hooks as `feat: expose confirmed EMR cancellation in the app`. Report exact source/target/poll/error/entry-point evidence, RED/GREEN/covering counts, docs generation/strict results and every golden change in `task-3-report.md`. No full-suite repetition or GitHub delivery by the worker; controller reviews the task and then whole branch.

## 4. Controller gates and delivery

After each task, independent spec/quality review checks its complete BASE..HEAD package before the next task. Adjudicate/fix real findings without weakening requirements; use scoped re-reviews and the whole-branch final review per SDD. Reread current #239 AC/comments/dependency237 before final integration.

On the final clean reviewed head, run all-file hooks, locked dependency audit, full default suite with coverage, canonical site/wiki generation and strict docs, local build/dist/twine and actual-wheel imports/synthetic command smoke. Use existing unchanged-source prior gate as baseline, never as evidence for new product changes. Controller owns final gate evidence and no hosted checks.

Push matching branch, create/attach protected develop PR without premature issue auto-closure; verify exact PR head/check state and merge normally. Create/attach protected develop-to-main promotion, merge normally, verify identical whole source/local-origin sync/main ancestry and applicable post-promotion checks. Safely clean only own feature branch/workspace after preserving all evidence/rulings, post the substantive conclusion, check all nine ACs, close completed and mark Done. Only then start #250. Releases, remote docs publication, live AWS mutations and branch-protection bypass remain prohibited.
