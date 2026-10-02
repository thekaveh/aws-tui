# In-session Credential Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task, superpowers:test-driven-development for every behavior change, and superpowers:verification-before-completion before each commit and delivery claim.

**Goal:** Make the existing `auth.authenticate` action a durable, safe in-session retry for the active source after credentials are repaired externally.

**Architecture:** `AwsTuiApp` owns one coalesced recovery transaction guarded by source revision, connection identity, service identity, and hosted-VM identity. A pure classifier emits fixed credential guidance. S3 panes stage provider reads without touching live state, then commit synchronously only after every pane and global guard passes. Other services expose deterministic read-only active-surface refreshes. `RootVM` publishes refreshed connection/auth state only after an observable read succeeds.

**Tech Stack:** Python 3.11+, asyncio, Textual, MVVM, pytest/pytest-asyncio, Ruff, mypy, pre-commit.

**Spec:** `docs/superpowers/specs/2026-10-02-credential-recovery-design.md`

## Global constraints

- Keep action id `auth.authenticate` and default key `a`; expose the same action in the command palette as **Retry active source credentials**.
- Re-resolve the captured connection name, but accept it only when kind, name, and region still match. Never select another account.
- Never shell out, open a browser, write credentials, or retry a mutation.
- Publish connected auth only after the current read surface succeeds.
- Preserve current content, service, connection, auth, path, and selection on failure, cancellation, or stale completion.
- Stage all matching S3 panes before committing any pane. A failure in either pane prevents both commits.
- Preserve the S3 path when valid; use remote root only when the captured non-root path no longer exists.
- Two callers share one attempt. Cancelling one waiter must not cancel the shared attempt.
- Treat source changes away and back as stale through a monotonic revision.
- Sanitize guidance: fixed messages may include the profile name but never exception text, credential material, subprocess output, or full endpoint URLs.

## Review focus

Every task review must prove these cases rather than infer them from happy paths:

1. A connection removed and recreated under the same name with a different region is rejected.
2. When two panes target the same source and one read fails, neither pane commits.
3. A source change away and back still invalidates the original attempt.
4. Cancellation after a provider read completes but before commit leaves live state unchanged.
5. A connected token probe followed by a denied provider read reports access-denied guidance and does not publish connected auth.
6. An unexpected secret-bearing exception produces only fixed generic guidance.

## Task 1: Add fixed recovery guidance and non-destructive root publication

**Files:**

- Create: `src/aws_tui/vm/credential_recovery.py`
- Create: `tests/unit/vm/test_credential_recovery.py`
- Modify: `src/aws_tui/vm/root_vm.py`
- Modify: `tests/unit/vm/test_root_vm.py`

### Step 1: Write failing classifier tests

Parameterize expired SSO, missing non-SSO credentials, `AuthRequiredError`, `PermissionDeniedError`, `ProviderUnreachableError`, and an unexpected exception. Assert the exact `RecoveryFailureKind`, retryable flag, and fixed guidance. Include a secret-bearing exception and endpoint with userinfo/query data, then assert none of those strings reach the result.

Run:

```sh
.venv/bin/python -m pytest tests/unit/vm/test_credential_recovery.py -q
```

Expected: FAIL because the module and classifier do not exist.

### Step 2: Implement the smallest pure classifier

Add immutable `RecoveryGuidance`, `RecoveryFailureKind`, and `classify_recovery_failure(connection, *, token_state=None, error=None)`. Prefer typed token/error signals in a documented order and return fixed copy. Include only the sanitized connection profile/name needed for the external sign-in command.

### Step 3: Verify the classifier

Run the classifier test file and confirm all cases pass.

### Step 4: Write failing root-publication tests

In `tests/unit/vm/test_root_vm.py`, construct hosted content with a disposal spy, call `refresh_connection_state`, and assert that connection/auth projections plus `ConnectionChangedMessage` update while hosted content, service, pane objects, and disposal count remain unchanged.

Run:

```sh
.venv/bin/python -m pytest tests/unit/vm/test_root_vm.py -q
```

Expected: FAIL because `refresh_connection_state` does not exist.

### Step 5: Implement and verify root publication

Add a synchronous `RootVM.refresh_connection_state(connection, auth_state)` that updates only the source projection and publishes the existing message. Run both Task 1 test files plus mypy for the touched modules.

### Step 6: Commit Task 1

```sh
git add src/aws_tui/vm/credential_recovery.py src/aws_tui/vm/root_vm.py tests/unit/vm/test_credential_recovery.py tests/unit/vm/test_root_vm.py
git commit -m "feat: classify credential recovery failures"
```

## Task 2: Stage and atomically commit S3 provider recovery

**Files:**

- Modify: `src/aws_tui/vm/file_manager/pane_vm.py`
- Modify: `tests/unit/vm/file_manager/test_pane_vm.py`
- Modify: `tests/unit/vm/file_manager/test_pane_vm_contracts.py`

### Step 1: Write failing stage/commit tests

Add tests proving:

- successful staging performs exactly one `list(path)` and leaves the live provider, identity, entries, state, path, selection, and generation unchanged;
- committing a valid stage swaps metadata and materializes the already-read entries without another provider call;
- missing non-root paths retry root once and mark a root fallback;
- a missing root is a successful empty listing;
- auth, denied, unreachable, unexpected failure, and caller cancellation leave the complete live projection unchanged;
- a provider/path/reload-generation change makes `can_commit_provider_recovery` false and `commit_provider_recovery` refuses the stage;
- cancellation after staging but before commit leaves live state unchanged.

Run:

```sh
.venv/bin/python -m pytest tests/unit/vm/file_manager/test_pane_vm.py tests/unit/vm/file_manager/test_pane_vm_contracts.py -q
```

Expected: FAIL because the staged recovery API does not exist.

### Step 2: Implement immutable staged recovery

Add a private immutable staged value and public `stage_provider_recovery`, `can_commit_provider_recovery`, and `commit_provider_recovery`. Reuse the normal listing-to-entry materialization rules, including typed provider errors. Capture original provider object, path, and reload generation. Do not mutate observables or dispose existing entries while staging.

When a non-root `NotFoundError` occurs, read root with the same provider. Treat root `NotFoundError` as an empty result. The synchronous commit must first revalidate and then replace metadata and entries from the staged value without awaiting or reading again.

### Step 3: Add an atomic two-pane contract test

Build two panes for the same connection, stage the first successfully, fail the second, and prove neither live pane changed. Then stage both successfully, prevalidate both, commit them without an await, and prove each provider was read once.

### Step 4: Verify and commit Task 2

Run the pane VM unit suite, Ruff, and mypy for the touched module. Then commit:

```sh
git add src/aws_tui/vm/file_manager/pane_vm.py tests/unit/vm/file_manager/test_pane_vm.py tests/unit/vm/file_manager/test_pane_vm_contracts.py
git commit -m "feat: stage atomic pane credential recovery"
```

## Task 3: Wire the durable action and guarded app transaction

**Files:**

- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/vm/athena/page_vm.py`
- Modify: `src/aws_tui/vm/emr_serverless/page_vm.py`
- Modify: `src/aws_tui/vm/glue/page_vm.py`
- Modify: `tests/integration/test_keybinding_wiring.py`
- Create: `tests/integration/test_credential_recovery.py`
- Modify: `tests/unit/vm/athena/test_page_vm.py`
- Modify: `tests/unit/vm/emr_serverless/test_page_vm.py`
- Modify: `tests/unit/vm/glue/test_page_vm.py`

### Step 1: Write failing key and palette tests

Update keybinding wiring expectations so `a` dispatches `auth.authenticate`. Assert `_PALETTE_COMMANDS` contains the same action id and the visible label **Retry active source credentials**. Verify the action remains available after a toast expires and in local-only fallback mode.

Run:

```sh
.venv/bin/python -m pytest tests/integration/test_keybinding_wiring.py -q
```

Expected: FAIL because the action is handlerless and absent from the palette.

### Step 2: Add deterministic read-only service refresh contracts

Write unit tests first, then expose a small deterministic recovery refresh in each service VM:

- Athena refreshes the currently visible workgroups/query/history/results/saved read surface and returns its terminal pane state or typed error. It must never execute or cancel a query.
- EMR delegates to the current focused applications/job-runs/detail/logs read and reports the resulting child state. It must never clone or submit a job.
- Glue awaits `refresh_active`, identifies the active child state, and never invokes a catalog/job/crawler/Iceberg mutation.

Use service request logs that distinguish reads from mutations. Run the three page-VM unit files and retain the initial failures as reproduction evidence.

### Step 3: Write the expired-to-valid S3 pilot

In `tests/integration/test_credential_recovery.py`, use a resolver that returns the same immutable `Connection`, a token probe that changes from expired to connected, and a provider with a held listing. Start from AUTH_REQUIRED or the boot local-only fallback, repair the token, invoke `a`, and assert:

- the same account/kind/name/region is used;
- the prior service stays active;
- the source/banner auth state becomes connected only after the held read succeeds;
- valid paths and entries return; missing paths use remote root;
- the right local pane remains local in fallback recovery;
- the provider request log contains only the expected `list` operation.

Expected first run: FAIL because no app recovery coordinator exists.

### Step 4: Write failure, cancellation, coalescing, and stale pilots

Cover all of these before implementation:

- expired SSO, missing credentials, access denied, endpoint/network failure, and unexpected error surface distinct fixed guidance;
- a connected probe followed by denied listing leaves auth unconnected;
- failure and cancellation preserve the complete before/after app, root, hosted VM, pane, path, selection, and content projections;
- two simultaneous action invocations share one task and one provider call per target pane; cancelling one waiter does not cancel the shared attempt;
- switching source during the held read discards the result;
- switching away and back discards the result through the revision guard;
- changing service or hosted VM discards the result;
- removing/recreating the same connection name with a different region is rejected;
- one of two same-source panes failing produces no partial commit;
- cancellation after all stage reads and before commit produces no change;
- S3 copy/delete/move/mkdir/rename, EMR submission/cancel, Athena execute/cancel, and Glue mutations never appear in request logs.

### Step 5: Implement app ownership and coalescing

In `AwsTuiApp`:

- register `auth.authenticate` and add the palette entry;
- subscribe to `ConnectionChangedMessage` and increment `_source_revision` for every publication;
- own `_auth_recovery_task` and have later callers await `asyncio.shield` on the same task;
- capture the exact connection identity, revision, service id, hosted VM identity, S3 paths, and fallback status before the first await;
- re-resolve only the captured name and reject kind/name/region differences;
- probe credentials again and classify non-connected results;
- for S3, build providers through the registered S3 service factory, stage every captured matching pane, validate every stage plus global guards, then commit synchronously;
- for Athena, EMR, and Glue, invoke only the new deterministic read-only active-surface refresh and inspect its result;
- call `RootVM.refresh_connection_state` only after success and a final guard;
- clear only the relevant fallback/unreachable memo and show fixed success or guidance text;
- log unexpected error types without stringifying potentially sensitive values;
- dispose the new hub subscription and task through existing app shutdown paths.

Do not replay actions or command history. Do not call any mutation-capable method.

### Step 6: Verify Task 3 behavior

Run:

```sh
.venv/bin/python -m pytest tests/integration/test_keybinding_wiring.py tests/integration/test_credential_recovery.py tests/unit/vm/athena/test_page_vm.py tests/unit/vm/emr_serverless/test_page_vm.py tests/unit/vm/glue/test_page_vm.py -q
```

Then run affected source-switch, navigation, S3, Athena, EMR, and Glue integration suites. Run Ruff and mypy for every touched Python file.

### Step 7: Commit Task 3

```sh
git add src/aws_tui/app.py src/aws_tui/vm/athena/page_vm.py src/aws_tui/vm/emr_serverless/page_vm.py src/aws_tui/vm/glue/page_vm.py tests/integration/test_keybinding_wiring.py tests/integration/test_credential_recovery.py tests/unit/vm/athena/test_page_vm.py tests/unit/vm/emr_serverless/test_page_vm.py tests/unit/vm/glue/test_page_vm.py
git commit -m "feat: retry active source credentials in session"
```

## Task 4: Update the shipped contract and complete delivery verification

**Files:**

- Modify: `README.md`
- Modify: `docs/keybindings.md`
- Modify: the existing credential/S3 troubleshooting document found by `rg -l 'credential|AUTH_REQUIRED|auth.authenticate' docs`
- Modify: `tests/docs/test_shipped_behavior.py`

### Step 1: Write failing documentation contract assertions

Replace the shipped-behavior expectation that auth is handlerless or requires relaunch. Assert docs expose `a`, the palette action, external credential repair, same-source retry, distinct guidance, and the boundary that aws-tui never runs login or writes credentials.

Run:

```sh
.venv/bin/python -m pytest tests/docs/test_shipped_behavior.py -q
```

Expected: FAIL against the old documentation.

### Step 2: Update user documentation

Document the recovery workflow with concrete expired-SSO and missing-credential examples, plus preserved path/root-fallback behavior. Remove deferred/unbound/relaunch-only claims. Keep troubleshooting aligned with the fixed classifier text and mutation boundary.

### Step 3: Review the full diff against all acceptance criteria

Create an AC-to-test ledger for #244. Inspect the entire branch diff from `origin/develop`. Search for placeholders and unsafe calls:

```sh
rg -n 'TODO|TBD|implement later|auth\.authenticate|sso login|subprocess|StartJobRun|StartQueryExecution|StopQueryExecution' src tests README.md docs
git diff --check origin/develop...HEAD
```

Confirm every review-focus case has a named test and every user-visible claim is implemented.

### Step 4: Run local release gates

Use the repository's isolated AWS environment:

```sh
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true .venv/bin/python -m pytest
.venv/bin/ruff check src tests
.venv/bin/mypy src
./scripts/check-layers.sh
make docs-check
uv run pre-commit run --all-files --show-diff-on-failure
```

Diagnose every failure; do not weaken tests, refresh snapshots blindly, or rerun hosted failures as a substitute for a fix.

### Step 5: Commit docs and final corrections

Commit the documentation contract separately. Keep any review correction in a focused commit and rerun its affected tests plus the full required gates.

```sh
git add README.md docs tests/docs/test_shipped_behavior.py
git commit -m "docs: explain in-session credential recovery"
```

### Step 6: Execute the protected delivery cycle

- Push `codex/issue-244-credential-recovery` and create a PR to `develop` with the AC ledger, local verification, limitations, and `Refs #244` so the issue stays open.
- Attach the PR to this chat, obtain whole-branch code review, resolve all findings, and verify required checks on the final head before merging.
- Create and attach the `develop` to `main` promotion PR, verify final-head checks, and merge through protection.
- If merge history requires it, create and verify a `main` to `develop` back-merge PR.
- Verify post-merge CI and source parity between `origin/main` and `origin/develop`.
- Delete only this issue's local/remote feature branch and remove only its unused workspace artifacts.
- Post a substantive issue conclusion linking implementation and promotion PRs plus verification evidence, close #244 as completed, and mark the project item Done.
- Confirm no open #244 PR, branch, worktree, dirty file, or unrecorded limitation remains. Record completion in the goal ledger before starting #236.
