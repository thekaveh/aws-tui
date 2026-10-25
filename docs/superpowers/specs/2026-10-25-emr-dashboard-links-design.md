# EMR Serverless dashboard links

## 1. Objective and acceptance policy

Complete #262 from developc0d1089b after #254 is closed. The issue explicitly
records owner-authorized local controlled-provider acceptance and nonblocking
live AWS/native-platform deferrals before implementation. No real AWS scope,
Actions, protection changes, releases or shared infrastructure changes.

## 2. Selected design

Use a source/run-specific dialog with Open, Copy and Close. The dialog shows
source/region, application and run, optional attempt (blank means latest), an
explicit system-profile-log switch defaulting to false, and one-hour link
validity. Open/Copy each fetch a fresh provider URL after that review. Default
Open shortcut U; Copy Alt+U. Both are remappable and in Help/palette; they open
the same review with the requested action focused, rather than bypassing it.
Only a matching selected run of a SPARK or HIVE application is eligible.

A direct browser command was rejected because it hides the reviewed target.
Caching a fetched URL for later copy was rejected because repeated activations
must regenerate links. Copy fallback uses the same freshly fetched link within
one admitted Open activation, not a persistent cache.

Browser dispatch uses an injected native port off the event loop. A separate
disabled implementation is mandatory in demo; no test may invoke a real browser.
If dispatch reports unavailable/failure, request clipboard delivery through the
existing app clipboard flow, after a fresh ownership check. Its existing native
and OSC52 feedback remains authoritative; do not claim a confirmed copy merely
because a callback returned. Opening reports a dispatch request, not page load.

## 3. Provider and URL boundary

Add get_dashboard_for_job_run(application_id,job_run_id,*,attempt=None,
access_system_profile_logs=False)->DashboardLink to the current real/protocol,
failed-client and demo boundaries. It performs one GetDashboardForJobRun with
exact IDs, optional attempt only when provided, false-by-default explicit
accessSystemProfileLogs, captured region and existing EMR SDK config.
Validate exact request types, AWS identifier constraints for the real client,
positive exact-int attempts (not bool), and exact-bool log access before SDK I/O.
Demo preserves its existing synthetic identifiers and never contacts AWS.

DashboardLink is immutable with url excluded from repr. Validate URL at provider,
coordinator and native-browser admission boundaries. Require an exact string,
1..2048 characters, absolute HTTPS, DNS hostname under amazonaws.com or
amazonaws.com.cn, no credentials, no whitespace/control characters/backslashes,
no invalid percent escapes, and no port except absent/443. Preserve the exact
query/fragment/path; do not assemble, fetch, rewrite or infer dashboard URLs.
The trusted AWS DNS suffix check accepts service subdomains without inventing a
narrow dashboard-host naming convention absent from the API contract.

Translate dashboard failures into fixed value-free AuthRequired, PermissionDenied,
NotFound, Validation, Throttled, Unreachable or generic Provider errors. Suppress
raw provider exception chains from rendered diagnostics. No generic raw exception
logging or URL-valued event payloads. Existing unrelated error mappers stay intact.

## 4. Ownership, cancellation and privacy

PageVM owns a dashboard coordinator and a monotonically increasing selection
revision derived from actual application/run selection changes, including ABA.
Capture source, region, application/run, application type and revision in a frozen
DashboardTarget. No signed URL belongs in targets, results, notifications, history,
workspace/recovery snapshots, logs or crash dumps. Only short-lived local variables
carry it between the provider and explicitly chosen delivery ports; clear references
on completion. Demo returns synthetic links and records no URL in its call log.

Coordinator single-flight admission rejects duplicates; it rechecks exact target
and live page ownership before/after provider read and immediately before external
handoff. Selection changes cancel pending retrieval and invalidate old reviews.
Page shutdown fences admission and drains owned provider/browser/clipboard work.
Providers that ignore cancellation still cannot cause late or retargeted delivery.
Browser dispatch admission is linearized with a thread-safe permit. Cancel before
admission prevents launch; an already admitted OS request cannot be recalled and
must be reported truthfully for its reviewed target. No late fallback copy after
selection/ownership loss. Clipboard admission follows the same captured-target
check and delegates delivery/acknowledgment to the existing pipeline.

URL privacy means no original URL/host/path/query/fragment appears in public repr,
provider/VM errors, logs or crash dumps in controlled failure tests. No global
sensitive-URL registry, persisted URL or broad redaction refactor is needed.

## 5. Component ownership and verification

- domain/emr_dashboard.py: immutable link and pure safe URL validation.
- domain/emr_serverless.py + demo/in_memory_emr.py: captured provider retrieval.
- infra/browser.py: injectable native/disabled browser dispatch; no logging URLs.
- vm/emr_serverless/dashboard_vm.py: target, outcome, single-flight lifecycle and
  privacy-safe read/delivery orchestration.
- PageVM: selection revision, coordinator ownership and shutdown integration.
- AppContext: native browser in production, disabled in demo; injectable for tests.
- Dashboard modal/page/App/actions/keymap: review, busy/cancel/error/retry and routes.

TDD provider payloads/SDK model, valid/invalid URLs and safe error taxonomy; native
browser monkeypatches only; coordinator pending/cancel/ABA/source/dispatch races;
actual-app Open/Copy/fallback, normal/remap/palette, Spark/Hive, protected source,
explicit log opt-in and optional attempt, safe diagnostics and 80x24/120x40 geometry.
Update canonical EMR/keybindings/IAM/contract documentation, applicable local checks,
independent review and available native macOS Python versions. Unavailable live
Spark/Tez/browser expiry/native Windows/Linux checks are not represented as passed.

## 6. Integration

Feature PR to develop then separate develop-to-main promotion, exact reviewed tree
parity, owned branch/worktree cleanup, substantive issue conclusion/readback/closure.
Only after #262 completion audit may the full goal be marked complete.
