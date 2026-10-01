# Faithful EMR Serverless cloning

## 1. Goal and verified baseline

Issue #238 requires preserving the source run's supported submission settings and
making differences and unknown settings visible before a new run is submitted.
The baseline is develop `5c1ddc5c`, after the complete #233 lifecycle. The current
client extracts five Spark fields and an S3 log URI, drops the other configuration,
and reconstructs a five-field StartJobRun request. The modal has no source identity
or comparison. It filters blank arguments and strips Spark parameter whitespace.
The VM already reuses client tokens on ambiguous failure and rotates them on edits;
that behavior is a regression requirement, not new functionality.

Current issue description and its owner comment were fetched on 2026-09-30. There
are no declared prerequisite issues or linked implementation PRs. #260 references
#238 as its own dependency; new-job authoring remains outside this ticket.

## 2. Chosen design

Keep the existing domain/client/VM/modal boundaries. Extend JobRunDetail with the
source driver identity, configuration overrides, execution timeout, retry policy,
mode, execution IAM policy, tags, and source application-level settings needed for
honest comparison. Preserve absence separately from an explicit empty structure or
zero. Configuration overrides include all nested application and monitoring
settings returned by the SDK. Keep the existing S3 log URI projection for logs.
Copy mutable nested data at domain, VM and request boundaries; suppress sensitive
configuration and Spark values from generated representations.

Use a focused request helper in `domain/emr_job_request.py` to build an explicit
StartJobRun allowlist and validate it against the installed SDK model. Do not pass
a GetJobRun response through as request kwargs. Keep all supported nested values,
including tags, and reject unsupported keys/types rather than silently filtering
nested structures. Only BATCH and STREAMING modes are accepted when present.
Missing mode is disclosed as unknown/defaulted, never asserted equivalent.
Reject Hive, unknown, missing or mixed driver unions as unsupported Spark sources.
The VM must refuse submission even when called outside the modal; the page must
show the reason without opening a Spark form for a non-Spark source.

The five existing fields remain editable. Represent the arguments as a JSON array
of strings so empty strings, embedded newlines and whitespace round-trip exactly.
Use JSON `null` to distinguish omitted arguments from an explicit empty array.
Spark parameters use a JSON string or `null`; escape Unicode separators in every
JSON editor to preserve mixed line endings and values through TextArea editing.
Do not trim Spark parameters. An advanced JSON object edits the optional request
settings: configurationOverrides, executionTimeoutMinutes, retryPolicy, mode,
executionIamPolicy and tags. Parse it with explicit field/type validation and
value-free errors; do not log malformed text. Changes to any request-affecting
setting rotate the token, including nested values and omission versus zero/empty.
Reapplying equal settings does not rotate it. An ambiguous submit retry reuses the
same token and exact reviewed request. A successful submit rotates the token.

Use two stages within a bounded, scrollable modal: edit, then review. The edit
stage's primary action is Review; Enter in an Input advances to review and cannot
submit immediately. The review stage shows source run id, exact connection/profile
(or explicit default credential-chain wording), region, application id and source
execution role. List all editable/request-supported fields with source and proposed
values, identifying preserved, changed, omitted/unknown and inherited states.
Show the complete values without truncating the comparison data; scroll it with
the keyboard. The footer remains reachable at 80x24 and 120x40. Back returns to the
same form without losing edits; editing invalidates the prior review. Submit is
available only for a valid, reviewed intent and remains guarded against duplicate
in-flight activation. Escape/cancel never starts a run.

Application-level release, network, image and worker settings cannot be set through
StartJobRun. Display their source values when available, label the target inherited
from the application's current settings, and state that equality is unknown.
Absent optional request fields and any hidden/default application settings are
explicitly unknown, not matching. Source ids, status, times, attempts and resource
usage are read-only/new-run outputs and are never sent back; disclose this group
as new-run values unknown until AWS returns them. Preserve the source snapshot and
source identity for the modal lifetime. Abort a stale/disposed source workflow
rather than retargeting it to a new connection/application.

Do not emit request bodies, arguments, policy contents, credentials or tokens in
logs, observable notifications or errors. Keep notifications value-free. Submission
errors retain their category and useful recovery guidance, but never echo arbitrary
provider exception text or a chained exception containing form values. Test the
configured log and diagnostic paths with distinctive argument/credential sentinels,
including validation and provider errors. Rendered form/review values are user UI,
not diagnostic logging.

The demo client carries the same settings into the new JobRunDetail and preserves
its existing idempotency/state transitions. No live AWS calls are needed to verify
this feature.

## 3. Alternatives considered

Automatically preserving extra fields without a review would still hide changes
and unknown defaults. Passing raw response JSON into StartJobRun would include
read-only fields and make unsupported additions unsafe. A separate control for
every nested monitoring/application property would greatly expand this focused
workflow. The chosen advanced object plus explicit review preserves arbitrary
supported nested values while keeping a bounded UI and strict request contract.

## 4. Acceptance-to-evidence mapping

| AC | Required evidence |
| --- | --- |
| 1: details carry settings | Stub GetJobRun with nested application/monitoring configuration, timeout and retry policy; assert exact values, absence/zero distinction and no aliasing. |
| 2: mode and IAM policy | Exact StartJobRun kwargs for BATCH and STREAMING with executionIamPolicy; unsupported mode/settings raise before calling the client. |
| 3: source identity and every difference | Running Textual pilot plus review snapshots; source id/profile/region/application/role and changed old/new values are rendered and keyboard reachable. |
| 4: non-Spark refusal | VM validation and submit refusal for Hive/unknown/missing/mixed driver; page never opens a Spark form for these sources. |
| 5: honest inheritance/unknowns | Pilot and snapshots show inherited release/network/image/worker settings, missing optional settings and unknown future/default values without a matching label. |
| 6: request keys only | Exact kwargs comparison for complete settings with response-only data present; nested unsupported fields fail closed. |
| 7: idempotency | Extend ambiguous-retry and edit-rotation tests to every new field and nested changes; equal settings keep token; successful submit rotates. |
| 8: no sensitive diagnostics | Capture emitted records and application diagnostic formatting for success, malformed JSON, SDK validation and provider exceptions; assert all sentinels absent. |

Also cover empty/newline arguments, exact Spark whitespace, deep-copy isolation,
review invalidation, double submit, cancellation, stale source/disposal, demo
round-trip and real keyboard navigation at compact/spacious sizes. Update service
docs and contract ledger, review all changed goldens visually, and run required
lint/type/architecture/docs/build/full CI through protected develop/main promotion.

## 5. Contract references and scope

- https://docs.aws.amazon.com/emr-serverless/latest/APIReference/API_StartJobRun.html
- https://docs.aws.amazon.com/emr-serverless/latest/APIReference/API_JobRun.html

The installed SDK supports mode and executionIamPolicy. Its configuration model
lacks newer diskEncryptionConfiguration visible in current AWS documentation;
unsupported values must block with a reason, not disappear. Hidden defaults cannot
be inferred from GetJobRun. No dependency upgrade, live AWS mutation, generic submit
form, Hive authoring or cancellation API is part of this ticket.

The user authorized routine design decisions and unsupervised execution of these
acceptance criteria. This design therefore proceeds without a separate approval.

## 6. Delivery correction: deferred EMR mount focus

CI run 36795110567 exposed a source-picker Enter timeout. Holding the actual
EMR mount-focus callback until the picker has focus reproduces the same failure:
`_maybe_focus_left` moves focus back to the runs pane. The callback must provide
an initial default only when the active page's screen has no focused widget.
Existing focus in a picker, overlay, detail/log pane, or navigation rail wins;
use `screen.focused` so a loading widget still owns focus. Preserve the existing
no-focus default and guard against a callback targeting an inactive screen.
Waiting longer in the test would conceal this production race; removing default
focus entirely would break immediate arrow navigation. A narrow callback guard
preserves both contracts. Validate delayed callbacks before and after picker
opening, other focus targets, loading focus, and the existing default-navigation,
modal, and snapshot regressions. This is a routine authorized delivery repair.

## 7. Delivery correction: queued pane refresh after unmount

Windows Python 3.11 in CI36797833782 exposed a queued pane refresh running after
its subscription was correctly disposed. Textual's screen retains callbacks
independently of their sending widget. A held mounted filter refresh released
through the screen after removal reproduces the detached-pane render assertion.
Clear the refresh-pending latch, then return if the pane is no longer attached,
before reading its view model or invoking chrome/body rendering. Subscription
cleanup remains mandatory and unchanged. Waiting longer in the test would leave
stale work able to reach the detached pane. The regression retains the positive
mounted control, zero observer count, zero later notifications and zero renders.

## 8. Delivery correction: live focus before stale service slots

Windows Python 3.13 in CI36799600908 exposed a Glue source-picker Enter timeout;
Windows Python 3.11 exposed the corresponding source-switch picker timeout.
Holding Glue's mount focus callback until the source trigger has focus reproduces
the first timeout exactly: Textual already names the source picker while the
coordinator still names the navigation rail. Athena's matching callback has the
same independently reproduced defect. The page must use `screen.focused`, and
when a coordinator exists and no explicit fallback reference is supplied, preserve
a valid focus target and project its slot before consulting older VM state. This
also preserves an open overlay or loading trigger. Inactive screens remain
guarded. Explicit references used to reconcile unavailable controls retain their
existing nearest-slot behavior. Standalone pages without a coordinator retain
their existing default focus. A global focus rewrite or extra test delay would
change unrelated contracts or leave the race intact. Cover all three source
states for both services, existing fallback and modal tests, source swaps,
navigation, compact layout and unchanged snapshots.
