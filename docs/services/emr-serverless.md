# 1. EMR Serverless

The EMR Serverless service is an AWS-only operational view for applications,
job runs, details, and S3-backed logs. It is read-mostly: browsing and log
inspection are read-only, while cloning an existing run is the one focused
submission workflow.

## 1.1. Source and application context

The page uses one exact AWS connection and region at a time. The source and
application selectors open as overlays, so expanding a selector does not
resize the run or detail panes. `Shift+S` rebuilds the service under the next
supported AWS source; `Shift+A` selects the next application without opening
the picker.

Source changes dispose the prior page and its pollers before the replacement
VM publishes state. Selections and cached detail remain scoped to connection,
region, and application identity.

## 1.2. Runs, details, and logs

The runs pane provides state filters and drives the selected-run detail.
Independent pollers refresh applications, runs, and active-run detail, with a
slower cadence when no run is active and terminal-state suppression for
detail. `r` refreshes the focused surface.

The logs pane loads only on demand. It discovers the exact Spark or Hive log
objects for the selected run, streams gzip content in bounded chunks, and
applies the configured regular-expression filter. Discovery fails closed if a
provider exceeds 100 listing pages or 200 classified log files, preventing an
unbounded object list from becoming VM state or mounted widgets. Retry attempts
and worker identity remain visible in the file choices.

## 1.3. Clone workflow

Pressing `c` on a selected run opens a form prefilled with the run name, role,
entry point, arguments, and Spark parameters. Save calls the public EMR
Serverless `StartJobRun` API through `EmrServerlessClient`; validation and
provider errors keep the modal open with actionable feedback. The service does
not currently expose a blank submit form or cancellation command.

## 1.4. Architecture

`EmrServerlessService` composes `EmrServerlessPageVM`, which owns
`ApplicationsVM`, `JobRunsVM`, `JobRunDetailVM`, and `JobRunLogsVM`.
`EmrServerlessClient` maps botocore responses into typed domain records.
`EmrServerlessLogsClient` reads the selected run's monitoring objects through
S3. VMx owns lifecycle, commands, observable state, paging, and modal results;
Textual owns focus and rendering.

The exact AWS operations and pinned SDK model are recorded in the
[Consumed Contract Ledger](../contract-ledger.md). The complete keyboard
surface is in [Keybindings](../keybindings.md).

## 1.5. Verification and demo

Demo mode provides profile-isolated applications, terminal and active runs,
clone transitions, and streamable success and failure logs without network
access. Unit tests cover poller cadence, stale-target rejection, clone
validation, provider errors, bounded discovery, and bounded log streaming.
Snapshot and end-to-end tests cover selectors, focus order, master-detail
behavior, modal submission, and log filtering.
