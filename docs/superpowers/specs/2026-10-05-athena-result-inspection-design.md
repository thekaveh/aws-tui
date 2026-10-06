# Athena loaded-result inspection design — #253

## 1. Authority and intake

The owner authorizes the complete sequential ticket cycle and routine decisions unsupervised, with applicable local checks only. This supersedes fresh design/worktree approval menus. #256 is CLOSED/Done and fully archived before this intake. Current issue #253 is OPEN/Todo, has eleven ACs and no comments or linked implementation PR. Its only cross-reference is #254 (complete-result export), which is out of scope and is not a dependency. Branch starts at develop `a1195a5cd838d0951ed44416ace26c4301bc32b4`; source tree `c2a21b3689d76618cf6da5ada24f56da7f8b84a4`. Clean primary checkout is reused, preserving unrelated dependency PRs. Fresh local baseline evidence is the unchanged-source 7,271-test run and 464-test post-promotion run archived with #256.

## 2. Approaches and decision

Use a pure index projection owned by AthenaResultsVM, with cell selection anchored to original loaded-row ordinal and numeric column ordinal. This keeps paging authoritative and makes stale/context reset and later page behavior directly testable. A UI-only projection would scatter selection reset and lifecycle rules across widgets. Mutating pager rows would compromise snapshot/paging semantics. Choose the VM projection; no dependency or SQL/provider change.

## 3. Binding behavior

- Local operations use fetched rows only; no SQL rewrite/rerun, start_query_execution, automatic page fetch, AWS mutation or full-result export.
- Rows remain `tuple[str | None, ...]`. The existing pager, token and 10,000-row ceiling stay authoritative. Projection state is transient and is not added to AthenaResultsSnapshot.
- Filtering is a case-insensitive literal substring over every cell. None is searchable as `null`; strings retain their exact content. Empty filter shows all loaded rows. A filter matches only loaded rows, even if more results exist.
- Sorting uses the selected column ordinal, ascending or descending Unicode string order, with null always last in both directions, no numeric conversion, and stable ties in loaded order. Reset returns loaded order. Duplicate labels have independent indexed columns.
- Clipboard serialization is JSON of original values: cell None → `null`, empty string → `""`, literal `NULL` → `"NULL"`; copy-row is a JSON array in column order, preserving duplicate labels, newlines and original strings. UTF-8/non-ASCII values remain readable (`ensure_ascii=False`). This interprets the string-or-null AC through serialization required by the string-only App.copy_value seam; rendering never supplies copy data.
- Cell selection anchors an original loaded-row ordinal and a column ordinal. Filter hiding that row clears selection. Sorting and explicit Load more preserve a surviving original cell. No selection means inspection/copy/sort do nothing and never select stale data. Table empty-state coordinates cannot fabricate a selected VM cell.
- Execution reload/replacement, changed QueryContext, clear, snapshot install, shutdown and disposal reset selection/filter/sort and retire old projection generation. Existing generation guards drop a cancellation-resistant late result page. An unchanged context does not reset a valid selection.
- The cell inspector displays the complete original string in a read-only scrollable TextArea; null and empty have explicit status. It preserves multiline/long/markup-like data without Rich interpolation. Escape/Close restores the same original row and column if generation and current page still match. Changed generation closes/invalidates stale content; dismissal never restores selection into a different execution or dead page. No query runs on Enter in the inspector.
- Filter uses a modal Input with Apply, Clear and Cancel. Apply changes only the local filter; Cancel leaves it unchanged; Clear restores the loaded projection. Closing restores a valid current table focus. Deferred table projections preflight live widgets and guard stale highlight events using the table generation.
- Footer always states visible and loaded counts and loaded-only scope, plus `more available` or `safety limit` when relevant, including zero matches. Existing Load more availability/error/loading behavior is retained.
- Keyboard actions integrate with the existing configured keymap and shared clipboard behavior: `athena.inspect_cell` Alt+Enter, `athena.copy_cell` Alt+C, `athena.copy_row` Alt+Shift+C, `athena.filter_results` Alt+F, `athena.sort_results` Alt+S (ascending → descending → reset), and `athena.reset_results` Alt+R (clear local filter and sort). Actions are scoped to current Athena results, protect modal/input typing, and appear with actual configured keys in Help and relevant palette entries. Existing editor/service/global actions remain intact.
- Retain summary/table/footer vertical footprint and the existing page focus ring; no new persistent toolbar row. The inspector/filter dialog and all actions must remain usable at 80×24 and 120×40. Arrow keys address cells. No new focus enum member is needed for a modal-only filter trigger.
- Results values, filter text and copy payloads are not logged, added to value-bearing notifications, crash dumps or repr-visible snapshot/projection objects. Errors use fixed messages. Original result/Snapshot privacy tests remain.
- All necessary checks run locally. Actions stays disabled; normal protected push/PR/merge only. No force-push, admin merge, shared reset, release/package publication, remote docs publication or live AWS mutation.

## 4. Components and interfaces

`vm/athena/result_projection.py`: pure projection and JSON serializers; imports no UI/infra/provider.
`AthenaResultsVM`: transient filter/sort/selection and generation; original rows/token/Snapshot fields untouched. Internal QueryVM-to-ResultsVM snapshot installation passes prepared context atomically, without public context-reset publication; coherent snapshot notification adds only visible_rows and selection, retaining the original bounded message contract.
`AthenaResultsView`: indexed cell cursor, projected rendering, guarded highlights, modal actions and footer.
`result_cell_modal.py` and `result_filter_modal.py`: small read-only/explicit-apply overlays; existing modal/focus/lifecycle patterns.
`App`, KeymapStore and HelpModal: scoped registered commands, configured keys, common clipboard worker/reporting.
Documentation: Athena service and keybindings/cookbook updates; local generated site/wiki verification.

## 5. Acceptance evidence plan

1. Extend tests/unit/ui/athena/test_page.py with cell cursor and inspector close exact-coordinate regression; sorted/filter/duplicate rows and stale execution included.
2. Pilot copy-cell and copy-row intercept App.copy_value and compare exact JSON serialization of original string/null cells, including two same-label columns, not rendered text.
3. Pure serializer unit table: None, empty, literal NULL/null, newlines, Unicode and duplicate-column row arrays.
4. Pilot zero-match filter with has_more true asserts `0 visible`, loaded count and `more available`; ceiling case asserts safety limit.
5. Pure projection tests: ascending/descending/reset, fixed null-last, stable ties, integers above 2^53 stay strings, duplicate columns and no mutation.
6. VM tests assert original rows and exported pager token/has_more unchanged across local operations.
7. Actual pilot explicit second-page load while filter/sort active; new rows obey both, fake records exactly requested page and no extra fetch.
8. Pilot copy both columns with identical labels and distinct values.
9. tests/unit/vm/athena/test_results_vm.py cancellation-resistant old-page barrier, changed execution and changed context reset all transient state; unchanged context preservation.
10. Pilot local actions with fake provider rejecting start_query_execution and any unrequested get_results_page.
11. tests/snapshot/test_athena.py compact/wide full-app result/inspector/filter states with content guards and reviewable SVGs; interactive demo TTY pass with captured keyboard steps; help/remapped-key coverage. Feature-caused golden changes individually reviewed; unrelated goldens byte-preserved.

## 6. Verification and delivery

Task-scoped genuine RED/GREEN and covering tests, independent task reviews, one whole-branch review, final full applicable local suite with coverage plus all-file hooks, syntax, docs generation/strict checks, dependency audit if inputs change, package checks/smoke. Do not rerun unchanged failures until green or weaken assertions/goldens/timeouts. Reproduce failures, preserve evidence, repair and review before rerunning a changed-source gate.

Push normal feature PR into develop, protected merge, protected promotion to main, exact source parity and selected postchecks, then owned branch cleanup, substantive issue closure/board Done and verified archive before #245. #283 remains open without actual Windows evidence.
