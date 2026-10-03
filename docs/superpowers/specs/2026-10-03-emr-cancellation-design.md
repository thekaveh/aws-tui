# Cancel selected EMR Serverless job runs

Issue: https://github.com/thekaveh/aws-tui/issues/239

## 1. Authority and scope

Implement all nine current acceptance criteria, one selected run at a time. The standing goal authorizes routine design decisions, sequential implementation/review, and protected develop/main delivery without another approval menu. Only applicable local checks run; GitHub Actions remain disabled. Do not invoke live AWS mutations during implementation or verification. Application stop/delete and bulk cancellation remain outside scope.

Python remains >=3.11,<3.14; dependencies and lockfile remain unchanged. Coverage floor remains 70%. Preserve VMx lifecycle/observable/modal ownership, Textual focus containment, existing clone fidelity/idempotency, source recovery, pagination, and existing log/crash formats.

Dependency #237 is still open and the current Connection/ConnectionEntry have no protection flag. Do not invent that feature here. Recheck the dependency before final integration; if its implementation lands first, use its actual policy at cancellation dispatch after confirmation. Its present absence does not block this ticket.

## 2. Client and demo contract

Add `async cancel_job_run(self, application_id: str, job_run_id: str) -> None` to EmrServerlessClientProtocol, EmrServerlessClient, InMemoryEmr, and the service's failed-client adapter. Call the public SDK operation with exactly `applicationId` and `jobRunId`; omit the optional shutdown grace parameter and discard the acknowledgement payload. The installed botocore model and the public CancelJobRun API return identifiers, not a new lifecycle state.

Use a cancellation-specific BotoConfig with the existing 10-second connect and 60-second read timeouts, standard mode, and `total_max_attempts: 1`. Do not change the shared six-attempt configuration for existing reads/submission. Route botocore failures through `_map_boto_error`; retain typed categories and async-context cleanup, with no wrapper retry and no swallowed asyncio cancellation. Synthetic SDK transport tests must establish that retryable errors do not cause a second cancellation request.

Expose `CANCELLABLE_JOB_RUN_STATES` as the frozenset of SUBMITTED, PENDING, SCHEDULED, QUEUED, and RUNNING. CANCELLING is transitional and already being cancelled; SUCCESS, FAILED, and CANCELLED are terminal. Neither category permits a new UI request.

InMemoryEmr records the same call and changes only that application's selected run summary/detail to CANCELLED, with coherent timestamps/duration and other fields preserved. Missing application/run yields NotFoundError, terminal SUCCESS/FAILED is not rewritten, and an already CANCELLED run can be acknowledged without resurrection. A pending clone state walk must stop advancing a cancelled run at every transition; it must not affect another run. Use existing frozen-record replacement and deterministic demo clock, not wall-clock state. Its backend mutation becomes visible to the page only when existing reads/polling retrieve new records. Keep dispose/aclose ownership and state-walk cleanup intact.

## 3. Target identity and lifecycle

Implement cancellation coordination in EmrServerlessPageVM, which already owns the exact client, immutable ServiceSourceContext, selected application/run, and OperationOwner. Avoid a second selection store or a new modal/credential subsystem.

Its public surface is a read-only `cancel_busy: bool` property, `can_cancel_selected_run(self) -> bool`, and `async cancel_selected_run(self, ask: Callable[[ConfirmRequest], Awaitable[bool]], *, source_is_current: Callable[[], bool] | None = None) -> CancelJobRunResult`. Keep the existing constructor and other selection/recovery methods compatible. The UI supplies the confirmation callback and mounted-owner guard; the VM owns eligibility, capture, dispatch and result classification.

An immutable internal target captures application id, run id, source context and observed state. Obtain it from the selected visible JobRunSummary whose application matches both the application picker and runs VM. Matching detail may refine state when its updated_at is newer than the summary's timestamp. At equal timestamps, a non-cancellable observation wins over a cancellable one. An unrelated or older detail must never redirect the target or override fresher summary state. A selected summary remains cancellable when detail could not load: do not add an unnecessary GetJobRun permission prerequisite.

Before opening confirmation, reject unavailable/closed owners, missing targets and non-cancellable states with a concrete reason. A single `cancel_busy` reservation is acquired synchronously before the first await and remains held through confirmation and the request; concurrent calls return a silent busy outcome. Publish its change through the existing hub so action hints can update.

Confirmation snapshots the exact connection/profile/region, application id and run id. After acceptance, revalidate source ownership, lifecycle, all captured identifiers and latest eligibility. An application/run/source change, disappearance or transition out of the allowed states prevents dispatch; never substitute the new selection. An optional `source_is_current` callback supplied by the mounted widget validates its owner at the UI boundary. No provider call occurs on dismissal.

Run confirmation and the provider request through the existing OperationOwner. Shutdown/disposal must cancel and drain their tasks, including modal waits and client cleanup. Propagate caller CancelledError, release busy in finally, and silently drop superseded outcomes. Once sent, the only request uses the captured ids/client; a later selection/source change cannot redirect it. Its eventual response or error must not replace the new target's state or show an old-target success/error there.

## 4. Acknowledgement and feedback

Return an immutable CancelJobRunResult with stable status and fixed safe message. Statuses are requested, dismissed, busy, inert, stale, superseded, denied, not_found, throttled, unreachable, auth_required, invalid, and error. Dismissed, busy and superseded are silent. Stale-before-dispatch explains that no cancellation was sent; stale-after-dispatch is superseded/silent because the remote outcome belongs to the earlier target.

An accepted API acknowledgement reports exactly `cancellation requested`; it does not mutate a cached run/detail to CANCELLING or CANCELLED. Existing list/detail polling observes the real state. CANCELLING means cancellation is in progress, never a terminal success; only a later observed CANCELLED is a cancelled terminal result. An independently observed SUCCESS/FAILED is shown truthfully. Preserve active polling for CANCELLING and terminal suppression for CANCELLED. No poll or key-repeat automatically submits another cancel request.

PermissionDeniedError explains denied CancelJobRun permission; NotFoundError explains missing application/run; ThrottledError explains service throttling; ProviderUnreachableError explains an unconfirmed network outcome and recommends refreshing state before a deliberate retry. AuthRequiredError, ValidationError and unexpected failures also get fixed category-specific guidance. Never display raw SDK exception/response/argument/credential contents. Cancellation errors do not become connection-reachability observations. Do not clear or overwrite unrelated pane data/errors.

## 5. UI and documentation

Register one `emr.cancel` action with default key `x`, a service-scoped command-palette entry, and the EMR Commands hint. `x` is not a deliberate alias of an existing action; retain ordinary printable-key handling so editors can consume it. Keyboard, palette and hint clicks dispatch through the same registry handler. Outside EMR the action is inert. Existing modal precedence prevents a shortcut from acting on an obscured page.

Use the existing ConfirmationVM/ConfirmRequest/ConfirmModal/TextualDialogService. The danger request is titled `Cancel EMR job run?`, names the active source, application and run in literal path blocks, starts on the safe dismissal side, and labels the positive choice `Request cancellation`. The negative choice is `Keep running`. It is the app-owned cancellation command, not closing a clone form or stopping a local worker.

EmrServerlessPage schedules deferred lifecycle-owned work in a distinct non-exclusive `emr-cancel` worker group. The page VM's reservation deduplicates scheduled/in-flight calls; do not use an exclusive group that cancels and replaces the first request. Notify only while the same mounted page owns the operation. Hints/palette eligibility reflect the VM predicate and busy/selected-state changes; directly invoked ineligible actions still explain why without a client call. Use the palette's existing register_entry/unregister_entry API for this one conditional entry; do not extend the general palette model. Defer its registry invocation until the palette screen has dismissed, as existing guarded pane commands do. Preserve clone workers, modal key containment, existing source/app selection and focus restoration.

Update canonical docs/services/emr-serverless.md, docs/keybindings.md, docs/contract-ledger.md, and changelog. Record the key, confirmation identity, eligible states, acknowledgement/poll distinction, no automatic retry, ambiguous transport guidance, demo behavior, and `emr-serverless:CancelJobRun` IAM permission. Remove stale statements that cancellation is deferred; generic blank submission remains deferred. Generate/check site and wiki locally without publication. If the added command visibly changes existing snapshots, inspect the exact rendered difference and update only demonstrated affected goldens.

## 6. AC evidence map

| AC | Required evidence | Owner |
|---|---|---|
| 1 | exact wire args, mapped error matrix, context closure and synthetic single-attempt transport | Task 1 |
| 2 | demo selected run reaches CANCELLED, records preserved, cancelled clone cannot resume, other run unaffected | Task 1 |
| 3 | every JobRunState, missing target, newer detail/summary disagreement, terminal/in-progress reasons and zero calls | Task 2; UI eligibility Task 3 |
| 4 | confirmation captures source/application/run; dismissal has zero calls and leaves records untouched | Task 2; real danger modal Task 3 |
| 5 | change application/run/source, remove target, or complete it while confirmation waits; zero wrong-target calls; stale in-flight outcomes dropped | Tasks 2–3 |
| 6 | accepted result requested with unchanged cached state; later poll observes CANCELLING/CANCELLED; real Pilot verifies visible acknowledgement and polled rendering | Tasks 2–3 |
| 7 | concurrent VM calls and rapid real keys before/during dispatch make exactly one call, no replacement/coroutine leak | Tasks 2–3 |
| 8 | exact denied/not_found/throttled/unreachable guidance, fixed safe output, one attempt, caller cancellation/owner drain | Tasks 1–3 |
| 9 | key/IAM permission and consumed operation documented, canonical docs/site/wiki contracts and strict docs pass | Task 3/controller gates |

## 7. Alternatives and self-review

A widget-only cancellation was rejected because it would duplicate VM identity/error/lifecycle logic and could pass UI tests while failing the required VM tests. A new bespoke cancel modal/VM was rejected because the existing danger confirmation and page owner already provide the needed boundaries. Direct state mutation on API acknowledgement was rejected because the response returns ids, not lifecycle status. Shared adaptive retries were rejected because this mutation must have one service attempt.

Self-review: all nine criteria mapped; acknowledgement versus CANCELLING/CANCELLED is explicit; missing detail does not block a valid selected summary; latest matching state and exact source identity are distinct checks; demo state-walk resurrection, queued/active work cancellation, raw-error privacy and UI entry-point convergence have tests. No new dependency, unrelated refactor, live AWS action, hosted check, release or remote docs publication is required.
