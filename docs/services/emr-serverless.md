# EMR Serverless

The EMR Serverless service is an AWS-only operational view for applications,
job runs, details, and S3-backed logs. It is read-mostly: browsing and log
inspection are read-only, while cloning an existing run is the one focused
submission workflow.

## 1. Source and application context

The page uses one exact AWS connection and region at a time. The source and
application selectors open as overlays, so expanding a selector does not
resize the run or detail panes. `Shift+S` rebuilds the service under the next
supported AWS source; `Shift+A` selects the next application without opening
the picker.

Source changes dispose the prior page and its pollers before the replacement
VM publishes state. Selections and cached detail remain scoped to connection,
region, and application identity.

## 2. Runs, details, and logs

The runs pane provides state filters and drives the selected-run detail.
Independent pollers refresh applications, runs, and active-run detail, with a
slower cadence when no run is active and terminal-state suppression for
detail. Application discovery requests at most 50 records per page and fails
closed above 100 pages or 1,000 applications. The retained bulk job-run helper
also requests at most 50 rows per page and stops above 100 pages. `r` refreshes
the focused surface.

The logs pane loads only on demand. It discovers the exact Spark or Hive log
objects for the selected run, streams gzip content in bounded chunks, and
applies the configured regular-expression filter. Discovery fails closed if a
provider exceeds 100 listing pages or 200 classified log files, preventing an
unbounded object list from becoming VM state. Classification uses only the
run-relative key suffix, so a configured S3 prefix containing a reserved worker
marker cannot change a log's role; labels use the last retry/worker marker.
Retry attempts and worker identity remain visible in the file choices. The pane
updates one reusable text widget for the streamed body and updates progress
separately, keeping mounted widget count bounded as logs grow.

## 3. Clone workflow

Press `c` on a selected Spark run to open a prefilled form. Hive, missing or
unsupported job drivers are refused before the Spark form opens. The form
preserves the source name, execution role, entry point, arguments and Spark
parameters. Arguments use a JSON array of strings, preserving empty strings,
embedded newlines and whitespace; `null` omits the argument field and `[]` sends
an explicit empty array. Spark parameters use a JSON string or `null` to omit
them. JSON escapes preserve mixed line endings and Unicode separators exactly
through editing and review.

The advanced JSON object carries the source's `configurationOverrides`
(application and monitoring configuration), `executionTimeoutMinutes`,
`retryPolicy`, `mode`, `executionIamPolicy` and `tags`. Both `BATCH` and
`STREAMING` are supported. Remove a key to omit it; explicit empty objects and
zero timeouts remain distinct from omission. Unsupported keys or values are
refused with a reason rather than silently dropped. The installed SDK model
defines supported nested settings.

Choose **Review** (or press Enter in a single-line field) before **Submit**.
Review identifies the source run, connection, profile, region, application and
execution role. It lists preserved settings and every changed source/proposed
value. Application release, network, image and worker settings are inherited
from the current application; equality with the source run is unknown. Hidden
application defaults are also unknown. Run identifiers, status, timestamps,
attempts and resource usage are read-only outputs and are not copied. Scroll
through the review; Tab reaches the fixed Back, Cancel and Submit buttons even
at 80×24. Back preserves the form; further edits require a fresh review.

Submit calls the public EMR Serverless `StartJobRun` API. Validation and provider
errors keep the modal open with category-specific recovery guidance. Submission
errors and observable diagnostics exclude argument, policy and credential
values. A source change invalidates the open clone. The service does not expose
a blank submit form or an AWS job cancellation command. During submission,
**Close** dismisses the form; it does not cancel a job AWS may have accepted.

Submission carries an app-owned `clientToken`. The token is minted when the
clone form opens and reused after an ambiguous failure if the request remains
unchanged. Retry that unchanged review after a lost response to let AWS resolve
the same request instead of creating a second job. Every request-affecting edit,
including advanced nested settings, rotates the token; reapplying equal values
does not. Successful submission or closing and reopening the form starts a new
intent. Before reopening after an uncertain outcome, inspect the application's
runs because the earlier request may already have succeeded.

## 4. Architecture

`EmrServerlessService` composes `EmrServerlessPageVM`, which owns
`ApplicationsVM`, `JobRunsVM`, `JobRunDetailVM`, and `JobRunLogsVM`.
`EmrServerlessClient` maps botocore responses into typed domain records.
`EmrServerlessLogsClient` reads the selected run's monitoring objects through
S3. VMx owns lifecycle, commands, observable state, paging, and modal results;
Textual owns focus and rendering.

The exact AWS operations and pinned SDK model are recorded in the
[Consumed Contract Ledger](../contract-ledger.md). The complete keyboard
surface is in [Keybindings](../keybindings.md).

## 5. Verification and demo

Demo mode provides profile-isolated applications, terminal and active runs,
clone transitions, and streamable success and failure logs without network
access. Unit tests cover poller cadence, stale-target rejection, clone
validation, provider errors, bounded discovery, and bounded log streaming.
Snapshot and end-to-end tests cover selectors, focus order, master-detail
behavior, modal submission, and log filtering.
