# Athena scan cutoff and result reuse

## 1. Objective and authority

Deliver #257 from baseline e1444474. The owner authorizes autonomous routine
choices, local fixtures and nonblocking deferral of unavailable live/native
checks. No AWS account scope, Actions, workgroup changes or shared infra changes.
Preserve Glue-to-Athena selection and the default read-only LIMIT 5 query.

## 2. Selected approach

Add explicit query options to the existing submission path. A compact Reuse
button opens a focused settings modal; the existing scrollable details area
shows scan cutoff, configured reuse and AWS-reported outcome. This keeps the SQL
editor usable at 80x24. Inline switches would consume fixed editor rows; a second
submission framework would duplicate ownership and cancellation. Neither is
needed.

Reuse defaults off; age defaults 60 minutes and accepts strict integers 0–10080.
Invalid input stays in the modal with a safe inline explanation. Changes are
applied explicitly; cancellation preserves existing settings. The modal captures
context and refuses applying to another source/workgroup. Reuse is unavailable
until valid current workgroup metadata exists, and for managed results. State
resets on context changes. Options are transient and are not persisted with SQL.

## 3. Domain and ownership

Add optional keyword result_reuse_max_age_minutes: int | None = None to client,
runner start and demo. None omits ResultReuseConfiguration entirely. Enabled
requests use Enabled=true plus the exact age. Validate type/range and refuse
caller output overrides before any initiating call. For enabled production
requests, reread workgroup metadata and refuse managed/disabled/mismatched
responses before StartQueryExecution. No compatibility fallback drops an enabled
option. Existing default-off callers retain their request shape.

VM submission snapshots normalized SQL, context and options before awaiting.
An unchanged failed submission reuses its token. SQL/editor/context/option edits
invalidate the intent; an accepted execution retires only its matching intent.
Late completion cannot overwrite newer settings/tokens. Actual dispatch checks
captured generation and metadata eligibility; stale source/workgroup responses
cannot enable reuse or redirect a request. Fresh #237 mutation admission stays
intact. Existing runner cleanup and bounded Glue inspections remain unchanged.

## 4. Presentation and limits

Show known cutoff as exact bytes plus a binary-readable size. Absent means no
per-query cutoff is reported; unavailable and invalid values are distinct.
Zero is invalid, never unlimited. Never infer cost estimates or edit workgroups.
Show AWS-reported reused yes/no alongside scanned bytes; an ordinary non-reused
success remains successful. Explain potentially stale results and AWS eligibility,
including that LIMIT without ORDER BY is not cached. LIMIT 5 limits returned
rows, not scan bytes or charges. Unknown metadata must not become a false zero.

## 5. Verification

TDD domain request shape/refusal, VM retry/edit/context races, actual modal/app
controls, cutoff states, true/false outcomes, controlled protected dispatch and
existing Glue handoff. Update SDK consumed-contract coverage and canonical guide.
Run affected local suites, responsive snapshots, lint/types/layers/docs and
available Python versions. Independent review precedes Gitflow integration.
