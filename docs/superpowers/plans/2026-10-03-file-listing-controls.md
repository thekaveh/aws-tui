# File Listing Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete all nine acceptance criteria of #240 with loaded-only filter, find, and sort controls.

**Architecture:** PaneVM retains loaded entry identities and derives visibility and order. Textual modal forms route to VM methods and the persistent pane status reflects active filters/sorts. No filesystem operation is required to change presentation.

**Tech Stack:** Python 3.11–3.13, VMx, Textual, pytest/Pilot, pytest-textual-snapshot.

**Spec:** `docs/superpowers/specs/2026-10-03-file-listing-controls-design.md`

## 1. Global Constraints

- All nine issue acceptance criteria are required. Search never traverses another prefix, reads content, or lists the provider again. EMR and Glue filters keep their own behavior.
- No provider protocol, transfer engine, persisted configuration, dependencies, package version, or unrelated service contracts change.
- Missing metadata is last in both directions. Parent stays first regardless of sort or filter.
- Filter changes retain underlying marks; only visible real marks contribute to selection_count and transfer targets.
- Reset filter on directory navigation, source replacement, and committed credential recovery. Refresh of the same directory preserves filter.
- Use isolated local providers/demo fixtures and applicable local checks only. GitHub Actions remain disabled under the later user instruction.

## 2. Review Focus

- Mixed naive/aware modification times and equal metadata must sort deterministically without host-timezone dependence (Task 1).
- Empty/nonmatching filters must retain parent navigation while reporting zero real matches (Task 1 and Task 2).
- Refresh/source replacement while a find or filter form is open must not affect the replacement listing (Task 1 and Task 2).
- Literal markup-like entry names/queries must render safely, and editor shortcuts must remain input (Task 2).
- Clearing a filter can reactivate stored marks; find must describe its hidden-result clear behavior and copy/delete must still use visible targets (Task 1 and Task 2).

## 3. Task 1: Loaded listing semantics and VM regressions

**Files:** Modify `src/aws_tui/vm/file_manager/pane_vm.py` and `tests/unit/vm/file_manager/test_pane_vm.py`; create `tests/unit/vm/file_manager/test_pane_listing_controls.py` if keeping new cases focused improves readability.

**Interfaces:** Consumes existing EntryVM identities, VMx visibility, `_reload_generation`, and `_cursor_index` bridge. Produces PaneSortField(name, size, modified); `PaneVM.set_sort(field: PaneSortField, *, descending: bool = False) -> None`; `find_entries(query: str) -> tuple[EntryVM, ...]`; `select_found_entry(entry: EntryVM, *, revision: int) -> bool`; read-only `listing_revision: int`; and PaneViewModel `filter_status_text: str`, `sort_status_text: str`. Use these exact names in Task 2.

- [ ] **Step 1: Write failing unit tests before product changes.** Construct real PaneVM against InMemoryFS or a counting fake implementing the provider protocol. The explicit metadata fixture and expected size ordering are:

```python
from datetime import UTC, datetime, timedelta, timezone
from aws_tui.domain.filesystem import EntryKind, FileEntry

rows = [
    FileEntry('zeta', EntryKind.FILE, 20, datetime(2026, 1, 2, tzinfo=UTC)),
    FileEntry('alpha', EntryKind.FILE, 10, datetime(2026, 1, 1)),
    FileEntry('Beta', EntryKind.FILE, 10, datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1)))),
    FileEntry('unknown', EntryKind.FILE, None, None),
    FileEntry('directory', EntryKind.DIRECTORY, None, None),
]
size_ascending = ['alpha', 'Beta', 'zeta', 'directory', 'unknown']
size_descending = ['zeta', 'alpha', 'Beta', 'directory', 'unknown']
```

Assert name both directions, size and modified tie-name ordering/None-last, parent first, and cursor identity/marks unchanged across all sorts. Add fuzzy matching tests (prefix, substring, subsequence, no match, empty input) over loaded real rows including hidden rows. `select_found_entry` selects only, refuses stale revision/identity and unavailable state, clears filter only for a hidden valid match, and does not touch provider data.

- [ ] **Step 2: Record RED.** Run the exact new unit node IDs with `.venv/bin/python -m pytest ... -q`, capture missing-interface/parent-filter failures to `/tmp/aws-tui-240-vm-red.log`, and explain each expected failure in the task report.

- [ ] **Step 3: Implement VM ordering and find methods.** Keep `_entries` in provider order and sort indices in `_sync_filtered_from_composite`; keep parent first and unknown metadata last independently of descending. Stable sort by `(name.casefold(), name)` before metadata sorting so reverse primary order never reverses ties. Treat naive datetime as UTC, convert aware datetime to UTC. Default no explicit sort preserves provider order. Set-sort must recompute filtered indices and notify a body-order property plus viewmodel while preserving cursor identity.

```python
# Preserve the composite current EntryVM, rather than resetting to row zero.
def set_sort(self, field: PaneSortField, *, descending: bool = False) -> None:
    self._sort_field = field
    self._sort_descending = descending
    self._sync_filtered_from_composite()
    self._sync_cursor_selection()
    self._notify('filtered_entries')
    self._notify('viewmodel')
```

Implement find ranking in VM with a small dependency-free casefold prefix/substring/subsequence scorer, name ties, exclude parent, return empty in unavailable state. Selection validates revision and exact EntryVM identity before clearing a filter or moving the cursor. Expose existing reload generation as revision, including invalidation on disposal. Fix parent predicate to always accept synthetic parent. Preserve existing reset/keep lifecycle rule and publish filter reset notifications coherently.

- [ ] **Step 4: Complete observable projection tests.** Assert filter_status_text includes exact query and real-entry counts, empty state excludes parent, and explicit sort status is present. Extend `test_marked_entries_are_scoped_to_the_visible_filtered_rows` with sorting and hidden-find/clear behavior. Add named tests `test_filter_resets_on_directory_and_source_change_but_refresh_keeps_it` and a credential-recovery reset test. Counting provider assertions compare call lists before and after filter/find/sort, including no-match and invalid stale selection. Ensure retained hidden marks, visible selection_count, and cursor-actionable ordered rows agree.

- [ ] **Step 5: Run GREEN and commit.** Run new controls units plus pane VM/contracts, dual-pane and transfer units; no live providers. Record exact output/logs. Run relevant lint/type hooks; commit coherent task changes without bypassing hooks. Do not run the repository full suite in the worker; controller runs it once after source reviews are stable.

## 4. Task 2: Mounted controls, shortcuts, discovery, docs, and snapshots

**Files:** Create `src/aws_tui/ui/widgets/pane_listing_controls.py`; modify `src/aws_tui/app.py`, `src/aws_tui/ui/widgets/pane.py`, `src/aws_tui/ui/widgets/help_modal.py`, `src/aws_tui/ui/bindings.py` where needed; modify `tests/integration/test_keybinding_wiring.py`; create `tests/integration/test_pane_listing_controls.py`, `tests/snapshot/test_pane_listing_controls.py` and focused snapshot app/goldens; modify canonical `docs/keybindings.md`, `docs/services/s3.md`, README if relevant, and regenerate site/wiki through `scripts.docs.build_docs`. Inspect relevant existing tests; update only assertions/goldens whose intended visible behavior changed, never blanket regenerate.

**Interfaces:** Consumes exact Task 1 PaneVM/PaneViewModel interfaces. Produces registered pane.filter, pane.fuzzy_find, pane.sort and pane.clear_filter handlers; FilterPaneModal, FindPaneModal, SortPaneModal bound to a ready focused PaneVM. Modal controls and persisted status render VM-owned dynamic copy literally. No default sort shortcut; sort discoverable through palette/help.

- [ ] **Step 1: Write failing running-app tests.** Use RecordingFS/seeded fixture and `_use_injected_s3_connection` pattern from `tests/integration/test_keyboard_selection.py`, and `drain_workers`/observable waits. Update expected default binding map for slash and Ctrl+P dispatch; drop slash from the handlerless assertion. Pin registered actions and file-manager-only palette entries.

```python
# In an injected app.run_test context, with a ready pane:
before_calls = list(fs.calls)
await pilot.press('slash')
assert isinstance(app.focused, Input)
await pilot.press(*'ALPHA')
await pilot.pause()
assert [e.name for e in pane.filtered_entries] == ['alpha.txt']
assert [row.entry_vm.name for row in pane_widget.query(EntryRow)] == ['alpha.txt']
await pilot.press('escape')
assert pane.filter_text == 'ALPHA'
assert '1 / 3 matches' in str(pane_widget.query_one('.pane-filter-status', Static).render())
assert fs.calls == before_calls
```

Add physical Clear restoration, find result selection/no-match/cancel, loaded hidden result with clear explanation, directory finding without open, right-pane focus, six sort choices retaining cursor identity, and actual Help/palette filter visibility/execution. Add literal bracket queries/names, stale source/refresh while forms are open, modal containment, slash typed into editors, and zero-match parent reachability cases. Extend visible-only copy/delete confirmations through physical filter UI; existing `marked_entries` is also move engine's target contract, do not wire deferred move.

- [ ] **Step 2: Record RED.** Run focused new Pilot node IDs and binding contract before UI implementation, capture expected missing bindings/forms/status failures in `/tmp/aws-tui-240-ui-red.log` and task report.

- [ ] **Step 3: Implement controls and wiring.** Use small themed ModalScreen forms matching existing app conventions: focused filter Input with Clear/Done and live set_filter_command; find Input/results plus explicit no-match, Escape and selected-result confirmation; sort six named choices plus cancel/apply. Use ModalButton where appropriate; support keyboard and clicks. All forms capture listing_revision, observe/validate stale VM state and release subscriptions on unmount. Find lists loaded entries, not filtered_entries; selecting valid hidden result clears only on commit, with explanation in form. App registry handlers reject unavailable/non-file-manager panes and active modal stack/coordinator state. Palette callbacks defer until dismissal and return None like existing selection scheduling. Ctrl+P now dispatches pane.fuzzy_find; colon/Ctrl+K unchanged. Follow modal action forwarding (`action_apply`/`action_execute`, `action_move_up/down`) and editable navigation behavior already in AwsTuiApp; avoid broad routing changes.

```python
# Curated entries remain service scoped, with callbacks scheduled after dismiss.
PaletteEntry('pane.filter', 'Filter loaded entries', 'pane', service_ids=_PANE_SERVICE_IDS)
PaletteEntry('pane.fuzzy_find', 'Find loaded entry', 'pane', service_ids=_PANE_SERVICE_IDS)
PaletteEntry('pane.sort', 'Sort loaded entries', 'pane', service_ids=_PANE_SERVICE_IDS)
PaletteEntry('pane.clear_filter', 'Clear pane filter', 'pane', service_ids=_PANE_SERVICE_IDS)
```

Add persistent filter status and Clear control while query active, explicit sort status only when sorting chosen; hidden inactive widgets consume no default layout space. Pane body refresh responds to ordered filtered_entries changes as well as filter_text. Render query and entry text with markup=False or Text. Status includes query plus counts, and zero-match text visible even with parent. Keep default unfiltered/unsorted rendering unchanged.

- [ ] **Step 4: Add focused Carbon snapshots and visual evidence.** Use a deterministic app fixture with filtering after editor closure. Snapshot query/counts, matching rows, Clear, parent/zero matches and sort label as useful. Content guards must assert required text and real rows in generated SVG. Generate only these new goldens, inspect SVG/PNG rendering at 120x40 and Pilot checks at 80x24; do not accept blank parity. Existing changed Help/palette goldens must be reviewed individually, preserving unrelated demo hero.

- [ ] **Step 5: Update docs and verify GREEN.** Describe slash/Ctrl+P, Clear/escape semantics, loaded-only scope, six sort choices/ties/None-last, reset-on-navigation/source versus keep-on-refresh, and hidden-find clearing/reactivated marks. Remove deferred filter/find claims from canonical keybindings; regenerate site/wiki from canonical sources. Follow three-surface-docs references. Run new Pilot/snapshot suites plus existing keybinding, keyboard-selection, modal containment, palette, pane widget and docs tests. Record RED/GREEN and AC1-9 evidence with exact commands/output. Run relevant hooks and commit without bypass.

## 5. Controller delivery gates

- [ ] Perform independent per-task review and one whole-branch review against all nine ACs and spec, resolve material findings with scoped tested fixes.
- [ ] Run applicable full local pytest with coverage >=70%, docs generation/tests/check, all-file hooks, and package build/check_dist/twine. Do not run or dispatch GitHub Actions.
- [ ] Push feature, create/attach protected develop PR without premature issue-closing keywords; merge exact reviewed head, then create/attach/merge develop-to-main promotion.
- [ ] Verify source-tree parity and ancestry, scoped post-promotion tests, clean working tree; safely remove only completed feature branch locally/remotely.
- [ ] Close issue with both PRs/all-AC evidence/local verification/limitations, mark board Done, export ordered rulings before own workspace cleanup, update goal ledger, then begin #243.
