# In-session Credential Recovery Design

**Issue:** #244

**Status:** Approved for autonomous implementation by the standing goal directive.

## 1. Problem and outcome

When the active AWS or S3-compatible source cannot read because credentials are expired or missing, aws-tui currently leaves the user with transient guidance and no durable in-session recovery command. A boot failure can replace both panes with local storage; a later credential failure leaves the affected pane in an error state. The shipped `auth.authenticate` keymap entry is intentionally handlerless, even though pane copy says to press `a`.

The finished behavior gives `auth.authenticate` a real handler, the default `a` key, and a command-palette entry. It never signs in or shells out. The user repairs credentials externally, then invokes retry. Retry re-resolves the same named connection, re-reads the shared AWS configuration/token cache, repeats the failed read, and publishes the refreshed connection only after that read succeeds.

## 2. Options considered

### 2.1. Replay source selection

Invoke the existing source-selection or Settings-to-S3 path. This is small, but it resets pane paths, can temporarily select another resolver entry, publishes state before the confirming read finishes, and cannot reliably discard a stale result.

### 2.2. Add a new recovery service and VM

Introduce a separate orchestration layer for all services. This gives strong isolation, but duplicates the existing connection resolver, service registry, pane lifecycle, and app-level navigation transaction machinery.

### 2.3. Add a narrow app coordinator with staged recovery

Keep orchestration in `AwsTuiApp`, add a pure failure/guidance classifier, and stage the confirming read away from live content. S3 uses a two-phase read/commit operation on `PaneVM`; Athena, Glue, and EMR Serverless build and load a fresh off-screen service tree. The coordinator captures source identity, coalesces requests, re-resolves only that identity, and adopts staged state only after freshness checks.

**Decision:** C. It reuses existing ownership boundaries and provides the atomicity the acceptance criteria require without a parallel service architecture.

## 3. User contract

- `a` and the command palette expose **Retry active source credentials** through action id `auth.authenticate`.
- The action remains available after any toast expires.
- aws-tui never runs `aws sso login`, opens a browser, writes a token, or chooses a different account.
- For expired SSO, guidance tells the user to run `aws sso login --profile <profile>` and press `a` again.
- For missing non-SSO credentials, guidance names shared credentials, `credential_process`, environment/role credentials, and `aws sts get-caller-identity --profile <profile>`.
- Access denied is described as valid identity without permission. Endpoint/network failure names network, VPN, DNS, TLS, or configured endpoint checks.
- Success preserves the active service. For an S3 pane, it preserves the previous path when it still exists and falls back to the remote root only when that path no longer exists.
- Failure or caller cancellation leaves the prior pane content, active connection, auth state, service, and path unchanged.
- If the user switches source or service while retry is running, the result is discarded.
- Repeated requests share one in-flight attempt and cause one provider read per target pane.

## 4. Components and boundaries

### 4.1. Recovery classifier

Create `src/aws_tui/vm/credential_recovery.py` with:

- `RecoveryFailureKind`: `SSO_EXPIRED`, `CREDENTIALS_MISSING`, `ACCESS_DENIED`, `NETWORK`, `OTHER`.
- `RecoveryGuidance`: immutable `kind`, `message`, and `retryable` projection.
- `classify_recovery_failure(connection, *, token_state=None, error=None) -> RecoveryGuidance`.

The classifier consumes `Connection`, `TokenState`, and the existing provider error taxonomy. It emits fixed guidance without including exception text, credential values, endpoints with userinfo/query data, or subprocess output.

### 4.2. Staged pane read

Extend `PaneVM` with a private immutable staged result and three public operations:

- `stage_provider_recovery(provider, *, path, identity_label, path_protocol, connection_key)` performs `provider.list(path)` while the current provider, entries, state, path, selection, and identity remain untouched. If a non-root path is gone, it retries remote root and marks the staged result as a root fallback. Other failures propagate through the existing typed provider errors.
- `can_commit_provider_recovery(staged)` checks the original provider identity, path, and reload generation.
- `commit_provider_recovery(staged)` synchronously swaps provider metadata and materializes the already-read entries. It performs no second provider call.

The app stages every affected pane first, verifies every stage plus the global source generation, then commits them without an `await` between checks and commits. This prevents partial recovery when one of two panes still fails.

### 4.3. App recovery coordinator

`AwsTuiApp` registers `auth.authenticate` and adds `_auth_recovery_task` plus a monotonic `_source_revision`. A hub subscription increments the revision for every `ConnectionChangedMessage`; navigation/source actions also make a changed service or hosted VM fail the captured-context comparison.

`action_authenticate()` records the action and coalesces with the existing task. The first caller creates `_recover_active_source()`; later callers await the same shielded task. Cancelling one caller does not start a second attempt or cancel the shared read.

The coordinator captures:

- the connection's non-secret binding identity (kind, name, region, source,
  profile, endpoint, path-style routing, and TLS verification) plus the exact
  `Connection` value;
- auth state;
- active service id and hosted VM identity;
- source revision;
- each matching remote pane's path; and
- whether the S3 view is the local-only boot fallback.

It re-resolves the captured name. A missing connection or binding-identity mismatch fails without selection changes. Credential values are excluded from that identity so an expected key or session-token rotation can recover, while profile, endpoint, and routing changes cannot silently redirect the retry. It calls `AwsSession.probe_token` again. `EXPIRED` and `MISSING` return classifier guidance without a provider call. `CONNECTED` proceeds to the confirming read.

For S3, each pane already bound to the captured connection is staged at its current path. In a boot local-only fallback, the left pane is staged as the recovered remote pane at root while the right local pane stays untouched. On successful commit, `RootVM.refresh_connection_state(connection, CONNECTED)` publishes the refreshed source without disposing content, the unreachable/fallback memo is cleared, and a success toast reports the retained path or root fallback.

For EMR Serverless, Glue, and Athena, recovery builds a new VM tree against the re-resolved connection and runs its existing read-only setup/refresh path off-screen. Candidate diagnostics are suppressed while staged so an unexpected provider exception cannot publish candidate-only text through the live root. Failure, cancellation, or staleness shuts down and disposes the candidate; the current VM and widget remain authoritative. Success atomically adopts the prepared VM with the refreshed connection/auth projection, then mounts its matching widget. Recovery never calls clone submission, Glue mutations, Athena execute/cancel, or any historical action.

### 4.4. Root connection publication

Add `RootVM.refresh_connection_state(connection, auth_state)` for successful S3 pane commits. Add prepared-service build/adoption boundaries for AWS services so an off-screen VM can be loaded first and adopted without running setup twice. Prepared adoption updates the hosted VM and connection/auth projection in one publication boundary. The coordinator calls either path only after a successful confirming read and freshness checks.

## 5. Failure and cancellation rules

1. Probe failure is `OTHER` guidance and does not alter content.
2. `TokenState.EXPIRED` is expired SSO guidance.
3. `TokenState.MISSING` or `AuthRequiredError` is missing-credentials guidance.
4. `PermissionDeniedError` is access-denied guidance.
5. `ProviderUnreachableError` is endpoint/network guidance.
6. `NotFoundError` at a non-root S3 path stages the same connection at root; `NotFoundError` at root produces an empty successful listing.
7. `CancelledError` propagates without an error toast and without state changes.
8. A stale source revision, connection, service, hosted VM, pane provider/path, or reload generation discards the result without a success toast.
9. Unexpected errors are logged by type only and produce fixed generic guidance.

## 6. Mutation isolation

Recovery reaches only these operations:

- connection resolver `resolve`;
- `AwsSession.probe_token`;
- filesystem provider `list`;
- current-service read-only refresh methods; and
- in-memory publication of source/auth/pane state.

It does not call `copy`, `move`, `delete`, `mkdir`, `rename`, EMR `StartJobRun`, Athena `StartQueryExecution`/`StopQueryExecution`, or any command/action history replay. Tests use a request log containing read and mutation names and assert that recovery adds only the expected read.

## 7. Acceptance evidence plan

| Acceptance criterion | Evidence |
|---|---|
| Durable key and palette action | `tests/integration/test_keybinding_wiring.py`, palette registry test, docs contract |
| Re-read same connection after token repair | running Textual pilot with a fake expired-to-valid token probe and provider list log |
| Distinct guidance | parameterized unit tests for expired SSO, missing non-SSO credentials, denied, and unreachable |
| Restore source/auth/service/path | pilot asserts banner identity, `RootVM` connection/auth, service id, pane path, and entries |
| Failure/cancellation preserves prior content/account | pilot and `PaneVM` staging tests compare complete before/after projections |
| Coalescing and stale rejection | two concurrent actions share one call; source/service switch before release prevents commit |
| No mutation replay | fake provider/client request log contains no copy/delete/EMR/Athena mutation names |

## 8. Documentation

Update `README.md`, `docs/keybindings.md`, the S3/credential troubleshooting text, and shipped-behavior documentation tests. Remove statements that `auth.authenticate` is deferred or requires relaunch. Keep the boundary explicit: aws-tui observes credentials after the user signs in externally.

## 9. Out of scope

- Launching AWS CLI, device authorization, or a browser.
- Persisting or editing AWS credentials.
- Automatic background retry after a credential file changes.
- Retrying mutations or restoring an in-flight mutation.
- Selecting a fallback account on behalf of the user.
