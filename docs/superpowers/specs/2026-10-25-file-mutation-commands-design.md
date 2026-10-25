# File-manager mutation commands

Issue: #242. Baseline: `5b403ec1bf24b48bcadf5068bd27c8e06cdbb435`.
The owner authorizes routine design and execution without another approval pause.

## 1. User flow

Register `pane.move` (`m`) and `pane.new` (`n`) in the application registry,
action catalog, contextual help, palette and hints. Keep these actions scoped to
ready file-manager panes. Editable inputs and other services retain their keys.

Move uses marked entries first, then a non-parent cursor fallback. A review
shows both connection identities, original source paths, destination directory,
item count and conflict policy. The policy is ERROR: stop on conflict without
replacement. The review offers Move, single-entry Rename, and Cancel. Rename
remains available for one marked entry even if another entry has the cursor.
Cancel performs no mutation. A scrollable review retains access to all paths.

Rename collects one new basename and then reviews the original and new paths.
New folder collects one basename and reviews its destination before creation.
Reject blank names, dot segments, slash, backslash, NUL and control characters
with inline feedback. Preserve names instead of silently trimming valid spaces.
Do not expose bucket creation or relax provider-specific refusals.

## 2. Ownership and execution

Capture providers, paths, target objects and listing revisions when an action
starts. Reuse `_file_confirmation_guard` before and after every input/review
modal, and immediately before the mutation worker starts. Expire the request
when its source, selection, provider, directory or listing changes. Never
retarget a pending review to the current cursor or pane.

Reuse `DualPaneVM.move_across` and its durable transfer runtime. Preserve cursor
fallback marking and cleanup, endpoint aliases, no-replace publication,
versioned-S3 refusals, cancellation and partial outcomes. Providers and paths
are bound before the VM's first await. The move refreshes both affected panes.

Add an explicit-entry rename operation to PaneVM and retain `rename_cursor` as
a compatible facade. Bind provider/path/target before awaiting. Refresh after
success or failure only while the pane still shows that bound location. New
folder uses the same captured-location rule. Do not disturb unrelated selection
or focus. Protection from #237 remains at provider admission after confirmation.

## 3. Approach

Reuse the existing operation runtime and guard instead of adding a second
transfer executor. Keep the current copy entry points compatible while sharing
worker error/history/mark cleanup where that removes duplicate orchestration.
A generic shell-like destination parser would add path ambiguity; this ticket
uses the opposite pane for move and a single basename for in-place rename.

Use the project's ModalButton and modal/focus conventions. View widgets collect
intent and display validation. Provider calls remain in existing VM operations
and application workers. No direct filesystem writes occur in the view layer.

## 4. Verification and limits

Write failing tests before production changes. Cover default/remapped keys,
palette/help/hints, parent and marked targets, name validation, cancel, stale
confirmation, post-confirmation protection changes, partial failure and focus.
Use temporary files, recording providers and isolated loopback S3 fixtures.
Run existing copy/delete/selection/ownership/provider tests and applicable local
hooks, type/lint, architecture, docs and UI checks. Obtain independent review.

Live S3 and unavailable native-platform validation are explicitly deferred,
nonblocking follow-up checks under the owner's acceptance policy. Preserve
#234's native Windows directory limitations. No hosted CI or live AWS is used.
