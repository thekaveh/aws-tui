# Faithful EMR Serverless cloning

## Goal and verified baseline

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

## Chosen design

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

## Alternatives considered

Automatically preserving extra fields without a review would still hide changes
and unknown defaults. Passing raw response JSON into StartJobRun would include
read-only fields and make unsupported additions unsafe. A separate control for
every nested monitoring/application property would greatly expand this focused
workflow. The chosen advanced object plus explicit review preserves arbitrary
supported nested values while keeping a bounded UI and strict request contract.

## Acceptance-to-evidence mapping

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

## Contract references and scope

- https://docs.aws.amazon.com/emr-serverless/latest/APIReference/API_StartJobRun.html
- https://docs.aws.amazon.com/emr-serverless/latest/APIReference/API_JobRun.html

The installed SDK supports mode and executionIamPolicy. Its configuration model
lacks newer diskEncryptionConfiguration visible in current AWS documentation;
unsupported values must block with a reason, not disappear. Hidden defaults cannot
be inferred from GetJobRun. No dependency upgrade, live AWS mutation, generic submit
form, Hive authoring or cancellation API is part of this ticket.

The user authorized routine design decisions and unsupervised execution of these
acceptance criteria. This design therefore proceeds without a separate approval.
