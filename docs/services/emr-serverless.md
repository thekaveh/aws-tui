# EMR Serverless

The EMR Serverless service is an AWS-only operational view for applications,
job runs, details, and S3 or CloudWatch logs. It is read-mostly: browsing and log
inspection are read-only. Focused workflows clone an existing run or request
cancellation of one selected active run.

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

Applications refresh every 60 seconds. Runs refresh every 60 seconds while
active, or every sixth tick when none are active. Selected nonterminal run
detail refreshes every 30 seconds. Demo intervals are 30, 30 and 5 seconds,
respectively.

The logs pane loads only on demand. It discovers Spark or Hive log objects
under the selected run's complete prefix. It streams all gzip members in bounded
chunks and applies the configured regular-expression filter. Discovery fails closed if a
provider exceeds 100 listing pages or 200 classified log files, preventing an
unbounded object list from becoming VM state.

Classification uses only the run-relative key suffix. Thus, a configured S3 prefix containing a reserved worker marker cannot change a log's role. Labels use the last retry/worker marker. Retry attempts and worker identity remain visible in the file choices. The pane
updates one reusable text widget for the streamed body and updates progress
separately, keeping mounted widget count bounded as logs grow.

### 2.1. CloudWatch source and loaded data

The logs pane shows separate S3 and CloudWatch destinations for the selected
run. Each source is **configured**, **unavailable**, **unknown**, or **access
denied**. Pending or failed detail reads remain unknown; they do not prove
logging is absent. Enabled CloudWatch is selected when S3 is not configured; when
both are configured S3 remains the default. `Ctrl+S` cycles configured log
sources without changing the AWS connection. There is no automatic failover.

Logging must already be configured on AWS. This read-only example describes a
run's monitoring configuration; aws-tui does not enable or modify it:

```json
{
  "monitoringConfiguration": {
    "cloudWatchLoggingConfiguration": {
      "enabled": true,
      "logGroupName": "/example/emr",
      "logStreamNamePrefix": "example"
    }
  }
}
```

CloudWatch requires `enabled: true`. An omitted group uses
`/aws/emr-serverless`. Default streams start with
`/applications/{applicationId}/jobs/{jobRunId}/`; driver, worker suffixes and
`attempts/{attempt}/` remain distinct choices.

For a custom prefix, discovery
conservatively accepts only names starting with that literal prefix followed
by the same complete application/run path. It does not guess an undocumented
AWS prefix join convention or search unrelated runs. Missing groups/streams
show **not created yet** and can be retried. Left/Right selects an exact stream.

`f` edits the regex filter and `Shift+F` restores its defaults.

Filters permit at most 64 patterns, with 4,096 characters per pattern. A match
has a shared 50 ms deadline across patterns. CloudWatch matching also has a
100 ms deadline across loaded lines. A limit failure preserves the previous
CloudWatch display and stops follow. Simplify the patterns, reset the filter,
or choose **Show all**.

Matching uses the timeout-capable `regex` engine in VERSION0 mode. Unicode case
folding can differ from Python's standard regex engine, including scoped ASCII
flags. Brace expressions can also use engine-specific syntax, such as fuzzy
matching. Both engines must accept a pattern before it can be applied.

Enable
**Match case** when exact character case matters. For CloudWatch,
`filter: loaded data only` means edits immediately reproject retained events
without an AWS request; they are not account-wide searches. Bodies, source
names and regex text render literally, including Rich-style brackets.

### 2.2. Read bounds, follow and credentials

CloudWatch discovery permits 100 pages and 200 returned records, including
rejected/duplicate names. One event read permits 100 pages, 10,000 returned
events, 8 MiB of UTF-8 message bytes, and 1 MiB per event. Each discovery/read
has a 30-second timeout. Continuing beyond a cap or repeating a token fails
closed with fixed guidance.

The pane retains the newest 5,000 whole events /
4 MiB of message bytes, then at most 5,000 display lines / 4 MiB including
newlines. **buffer capped** identifies discarded loaded data. Existing S3
limits remain 100 MiB compressed, 5,000 matched lines, and five cached reads.

`Ctrl+Alt+L` or the pane's **Start follow** control explicitly starts follow for
CloudWatch. **Stop follow** remains available during a pending read. Follow
waits two seconds after each completed read, with no overlapping polls, and
rereads a 60-second timestamp overlap. It deduplicates event IDs, retains
separate IDs with identical text, and stops if 20,000 overlap IDs would be
exceeded.

Events arriving with timestamps older than the overlap may be missed
until an explicit reload. A fixed end bounds eligibility, but pagination does
not provide an atomic AWS snapshot. The status shows Following/Stopped, last
successful check time and latest event time; an empty successful poll advances
the check time. Failures retain the last complete body and stop follow.

Changing application, run, source, stream or monitoring identity immediately
invalidates old read/follow results and requests cancellation. Late results
cannot update the new target; owned work is durably drained at lifecycle teardown
and connection replacement. Credential
recovery restores the exact source/group/stream and filter with one fresh
uncached read. Missing streams or changed monitoring identity reject the
candidate; no default stream is substituted and following stays stopped.

Authentication, denial, throttling and unavailable-service failures have fixed
safe categories; no log body or original exception text enters diagnostics.

The reader needs existing EMR read permissions plus `logs:DescribeLogStreams`
and `logs:FilterLogEvents` on the configured group. It does not require
`logs:GetLogEvents`, `logs:Unmask` or log creation/write permissions. The EMR
job's writer permissions are separate; see
[AWS CloudWatch logging configuration](https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/logging.html).

## 3. Clone workflow

Press `c` on a selected Spark run to open a prefilled form. Hive, missing or
unsupported job drivers are refused before the Spark form opens. The form
preserves the source name, execution role, entry point, arguments and Spark
parameters. Arguments use a JSON array of strings, preserving empty strings, embedded newlines and whitespace. `null` omits the argument field and `[]` sends an explicit empty array.

Spark parameters use a JSON string or `null` to omit
them. JSON escapes preserve mixed line endings and Unicode separators exactly
through editing and review.

The advanced JSON object carries the source's `configurationOverrides`
(application and monitoring configuration), `executionTimeoutMinutes`,
`retryPolicy`, `mode`, `executionIamPolicy` and `tags`. Both `BATCH` and
`STREAMING` are supported. Remove a key to omit it; explicit empty objects and
zero timeouts remain distinct from omission. Unsupported keys or values are
refused with a reason rather than silently dropped. The installed SDK model
defines supported nested settings.

Choose **Review** (or press Enter in a single-line field) before **Submit**. Review identifies the source run, connection, profile, region, application and
execution role. It lists preserved settings and every changed source/proposed
value. Application release, network, image and worker settings are inherited
from the current application; equality with the source run is unknown. Hidden
application defaults are also unknown.

Run identifiers, status, timestamps,
attempts and resource usage are read-only outputs and are not copied. Scroll
through the review; Tab reaches the fixed Back, Cancel and Submit buttons even
at 80×24. Back preserves the form; further edits require a fresh review.

Submit calls the public EMR Serverless `StartJobRun` API. Validation and provider
errors keep the modal open with category-specific recovery guidance. Submission
errors and observable diagnostics exclude argument, policy and credential
values. A source change invalidates the open clone. The service does not expose
a blank submit form. During submission,
**Close** dismisses the form; it does not cancel a job AWS may have accepted.

Submission carries an app-owned `clientToken`. The token is minted when the
clone form opens and reused after an ambiguous failure if the request remains
unchanged. Retry that unchanged review after a lost response. This lets AWS resolve the same request instead of creating a second job. Every request-affecting edit,
including advanced nested settings, rotates the token; reapplying equal values
does not.

Successful submission or closing and reopening the form starts a new
intent. Before reopening after an uncertain outcome, inspect the application's
runs because the earlier request may already have succeeded.

## 4. Cancel selected run

Press `x` (`emr.cancel`), click the **cancel** Commands hint, or choose
**Cancel selected EMR job run** in the command palette. Cancellation is
available for `SUBMITTED`, `PENDING`, `SCHEDULED`, `QUEUED`, and `RUNNING`.
`CANCELLING` and terminal `SUCCESS`, `FAILED`, or `CANCELLED` runs cannot
receive another request. The hint is disabled and the palette entry is removed
when the selected run is ineligible or a request is pending.

If your custom keymap already uses `x` for another action, explicitly remap `emr.cancel` to a distinct unused key. For example, use `z`. See
[Migrating custom x bindings](../keybindings.md#21-migrating-custom-x-bindings)
for the configuration example and the entire-overlay fallback on collision.

The danger confirmation **Cancel EMR job run?** names the exact source
(connection/profile/region), application id, and run id. It starts on
**Keep running**; choose **Request cancellation** to proceed. Escape sends no
request. Changing source, application, or run before acceptance prevents a
request to the old or replacement target. Leaving the page drains its owned
work and confirmation. Results from a superseded target do not notify the new
selection.

The AWS identity needs `emr-serverless:CancelJobRun` permission. The API has
one attempt per deliberate request, with no automatic retry. A denied or
missing target is reported with fixed recovery guidance. After an unconfirmed
network outcome, refresh job state before a deliberate retry: AWS may already
have accepted the earlier request.

An acknowledgement displays **cancellation requested** and leaves cached
state untouched. Existing reads and polling show actual `CANCELLING` (still
active), then `CANCELLED` (terminal); an observed `SUCCESS` or `FAILED` remains
truthful. Key repeats and polls never submit an automatic cancellation.

## 5. Architecture

`EmrServerlessService` composes `EmrServerlessPageVM`, which owns
`ApplicationsVM`, `JobRunsVM`, `JobRunDetailVM`, and `JobRunLogsVM`.
`EmrServerlessClient` maps botocore responses into typed domain records.
`EmrServerlessLogsClient` reads the selected run's monitoring objects through
S3 and CloudWatch. VMx owns lifecycle, commands, observable state, paging, and modal results;
Textual owns focus and rendering.

The exact AWS operations and pinned SDK model are recorded in the
[Consumed Contract Ledger](../contract-ledger.md). The complete keyboard
surface is in [Keybindings](../keybindings.md).

## 6. Verification and demo

Demo mode provides profile-isolated applications, terminal and active runs, and clone transitions. It includes CloudWatch-only and both-source runs with retry/worker streams, and streamable success and failure logs without network access. Demo cancellation changes only the selected backend run to `CANCELLED`,
which existing reads then reveal. A cancelled clone stops its state walk and
cannot resume or affect another run.

Unit tests cover poller cadence, stale-target rejection, clone
validation, provider errors, bounded discovery, and bounded log streaming. Snapshot and end-to-end tests cover selectors, focus order, master-detail
behavior, modal submission, and log filtering.
