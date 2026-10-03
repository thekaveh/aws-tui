# File Listing Controls Design

## 1. Intent and scope

Implement issue #240 for people browsing a large current directory or S3 prefix.
Expose the existing case-insensitive name filter, find a loaded entry, and sort
the loaded listing by name, size, or modification time. All nine issue acceptance
criteria are required. Search never traverses another prefix, reads content, or
lists the provider again. EMR and Glue filters keep their own behavior.

## 2. Architecture and alternatives

Keep loaded EntryVM identities, marks, and provider order in PaneVM.entries.
Derive the visible ordered projection in PaneVM.filtered_entries, preserving
the VMx filtered composite as the visibility authority. This avoids rebuilding
entries or moving provider sorting into UI widgets. A provider-side approach
would add network calls and couple local and S3 implementations. Sorting or
filtering only widgets would make the displayed and actionable rows disagree.

Use small Textual modal forms for filter/find/sort. Existing app modal forwarding
contains navigation and protects the page underneath. An inline editor would
require additional priority-key routing through every file-manager navigation
handler. The underlying pane updates live during filter editing; persistent pane
status shows the query and real-entry match count after the modal closes.

## 3. Filter interaction and lifecycle

Register pane.filter at `/`. It opens a focused input initialized from the focused
pane. Typing executes set_filter_command immediately. Escape or Done closes the
editor and retains the query. Clear empties it and restores every loaded row.
Provide Clear in the editor and a persistent pane clear control while filtering.
The persistent status renders literal query text and `N / M matches`, excluding
the synthetic parent from both counts. Zero matches has an explicit visible
message; `..` remains the first reachable row even with zero real matches.

Reset filter on directory navigation, source replacement, and committed credential
recovery. Refresh of the same directory preserves filter. This is the existing
reset rule, made observable and tested. Filter changes retain underlying marks;
only visible real marks contribute to selection_count and transfer targets.
Clear filter makes those retained marks active again; clear selection still
clears all marks. No selection safety from #241 is weakened.

## 4. Find interaction

Register pane.fuzzy_find at Ctrl+P, replacing Textual's incidental built-in
command-palette binding; `:` and Ctrl+K continue to open the app palette.
Find searches every loaded real entry, including entries hidden by the active
filter. Case-insensitive prefix/substring matches precede subsequence matches;
stable name order breaks equal scores. Empty input lists loaded real entries.
Render matching names literally, permit selecting a result, and display an
explicit `No matches in loaded entries` state when none match. Escape changes
nothing. Enter on a result moves the pane cursor only: it never activates an
entry, opens a directory/preview, reads content, or mutates provider data.

If the chosen result is hidden, clear the filter before moving the cursor. State
this behavior in the find form and docs because retained hidden marks become
visible and active when clearing the filter. Find never changes stored marks.
Capture a listing revision and EntryVM identity; selection from an obsolete
listing after refresh, navigation, source swap, recovery, or disposal is refused.

## 5. Sort interaction

Expose pane.sort through the file-manager palette as `Sort loaded entries`;
the sort form offers all six name/size/modified ascending/descending choices.
There is no new default physical shortcut. Name uses casefold then original name.
Size and modified compare metadata, with ascending name tie breakers in both
directions. Missing metadata is last in both directions, including directories
whose size is None. Normalize naive times as UTC and aware times to UTC to permit
mixed metadata. Parent stays first regardless of sort or filter. Explicit sorts
do not force directories ahead of files. Until explicitly selected, retain the
existing provider order. Preserve the current entry identity and marks when
changing order; pane rendering, cursor movement, clicks, and transfer targets
must address the same visible ordered projection.

Sort choice lasts for the pane lifetime, including navigation, refresh, and
source changes. Display the active explicit sort without changing default
unfiltered chrome. Apply operates only on the listing revision the form opened.

## 6. Interfaces and boundaries

PaneSortField is a StrEnum with name, size, modified. PaneVM exposes
set_sort(field: PaneSortField, *, descending: bool = False) -> None,
find_entries(query: str) -> tuple[EntryVM, ...],
select_found_entry(entry: EntryVM, *, revision: int) -> bool, and read-only
listing_revision: int. PaneViewModel adds filter_status_text and sort_status_text.
No provider protocol, transfer engine, persisted configuration, dependencies,
package version, or unrelated service contracts change.

Modal forms bind PaneVM, release any subscriptions on unmount, and reject stale
listings. App handlers require a ready focused file pane and no active modal;
palette actions are scheduled after palette dismissal, like selection actions.
Default and remapped bindings respect editable inputs. Untrusted names and query
strings render without markup interpretation.

## 7. Acceptance evidence

| AC | Required evidence |
|---|---|
| 1 | Registered filter handler; default binding contract includes slash; remove slash from handlerless assertion |
| 2 | Pilot presses slash, checks focused input, types mixed-case query, checks mounted rows and filtered_entries |
| 3 | Pilot Escape retention/Clear restoration plus Carbon snapshot with query, counts, and nonblank matching rows |
| 4 | Pilot Ctrl+P finds loaded hidden/file/directory results without activation, verifies no-match state and cancellation |
| 5 | Unit tests for six orders, duplicate size/date values, None-last both directions, case ties and mixed UTC/offset times |
| 6 | Extend named visible-mark regression; copy/delete Pilot confirms only visible marks; move target contract uses marked_entries |
| 7 | Unit test explicitly names reset-on-directory/source and keep-on-refresh rule; parent remains first/reachable under nonmatching filter |
| 8 | Counting fake provider unit and mounted integration test assert no additional list/read/stat/write/delete calls |
| 9 | Pilot opens Help and command palette and observes filter label; palette dispatch opens focused filter form |

Additional focused regressions cover cursor identity after sort, stale find/form
results, literal brackets in names/queries, empty listings, focused right pane,
editable shortcuts, modal containment, and observable filter reset on recovery.

## 8. Validation and delivery

Use isolated local providers/demo fixtures and applicable local checks only.
GitHub Actions remain disabled under the later user instruction. Run focused
RED/GREEN tests, snapshot content/visual checks, independent task and whole-branch
reviews, full applicable pytest with coverage >=70%, all-file hooks, docs generation
and checks, build/distribution checks, and scoped post-promotion verification.
Keep canonical docs and generated site/wiki copies consistent; no separate docs
publication or package/release publication. Deliver protected PR into develop,
then promotion PR into main, verify source parity, clean only this feature branch,
and close/mark Done after post-promotion evidence exists.
