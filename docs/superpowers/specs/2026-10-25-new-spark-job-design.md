# New Spark job submission

Issue: #260. Baseline: `e870e1d4f4fa60f6623b48e3241036652b72a3c0`.
The owner authorizes routine design and execution without another approval pause.

## 1. User flow

Add `emr.new_job`, default `Shift+N` (`N`), to the EMR action registry,
palette, help and hints. It requires a current selected Spark application,
not a run or loaded run detail. Hive, unknown application types and missing
application context cannot open this form. Keep file-manager `n` independent.

Open the existing bounded Spark edit/review modal in explicit blank mode.
Role and entry point start empty; name, argument array, Spark parameters and
advanced settings remain optional. Use the existing JSON editors, preserving
argument boundaries, literal whitespace, quotes, multiline values and Unicode.

Require review before Submit. Identify connection, profile, region, application,
role and the full effective Spark request. Describe omitted application defaults
as unknown/inherited. Do not invent a source run or claim settings were copied.
The app-owned request token can be described without exposing its opaque value.
Cancel makes no submit call. Close during submission cannot undo an accepted job.
Typed errors retain form values and give value-free recovery guidance.

## 2. Reuse and alternatives

Extend the existing form VM with `detail=None, application_id=<captured id>`.
Retain its public clone constructor and clone review behavior. `source_detail`
is optional in blank mode. The modal derives its title and wording from this
explicit mode. Reuse existing request validation and idempotency, not a fake
`JobRunDetail`. The historical class name remains for caller compatibility.

A separate duplicate form would split validation and retry guarantees. A broad
submission-framework extraction would change more contracts than this feature
requires. An explicit mode keeps the existing tested form as the common boundary.

## 3. Ownership and submission

Bind the page VM, client, source and application ID at form entry. Check current
ownership before review/dispatch and after awaited completion. A new selection
cannot retarget a pending request or refresh another application. Preserve the
fresh #237 mutation policy at the client admission boundary.

The modal and VM reject concurrent submissions. Reuse the token for an unchanged
retry after an ambiguous failure. Request-affecting edits rotate it; equal edits
do not. Snapshot submitted values and token before awaiting. A completed older
request must not overwrite the token of a newer edited intent.

On success, show the returned run ID, refresh the captured application's runs,
and select that ID when it is listed. If eventual consistency or filtering hides
it, retain truthful success feedback instead of selecting a different resource
as the submitted run. Retain existing clone fidelity and cancellation behavior.

## 4. Verification and limits

Use TDD for blank defaults, review/request fidelity, validation, concurrency,
retry tokens, source invalidation, late completion and zero initiating calls.
Exercise actual keyboard/remapped/palette routes with zero runs, Hive refusal,
protection, demo submission, error retention, close/cancel and disposal.
Run existing clone VM/modal/page/cancellation suites, relevant snapshots, local
hooks, SDK contracts and canonical documentation checks. Obtain independent review.

Local controlled providers are acceptance evidence. Live AWS and unavailable
native-platform checks are explicitly deferred, nonblocking follow-up. Do not
claim those checks passed. No account/resource scope, hosted CI, protection
changes, IAM changes or shared infrastructure changes are authorized.
