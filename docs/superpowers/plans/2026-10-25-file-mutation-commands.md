# File Mutation Commands Implementation Plan

> Execute inline using executing-plans; the active goal authorizes routine design and Gitflow integration.

**Goal:** Deliver #242 without weakening transfer or connection protection.

**Architecture:** Register contextual commands and collect bounded intent in existing modal conventions. Reuse captured confirmation ownership and durable move execution.

**Stack:** Python 3.11–3.13, Textual, VMx, pytest.

## 1. Constraints

Local verification only. Preserve #237 admission, #403 aliases and #234 Windows limitations.
No live AWS, hosted CI, unsafe overwrite, automatic replay or shared infrastructure changes.

## 2. Implementation tasks

- [x] Registry/discovery: write `tests/unit/test_file_mutation_commands.py` asserting both actions are registered and cataloged for S3 only. Run it RED before touching `src/aws_tui/app.py`, `src/aws_tui/vm/chrome/action_catalog.py` or `src/aws_tui/vm/chrome/hint_legend_vm.py`. Add contextual readiness and remap coverage in `tests/integration/test_file_action_ownership.py` and the new application-flow suite.
- [x] Name/target behavior: add tests for explicit marked-entry rename, parent refusal, invalid basenames and location changes in `tests/unit/vm/file_manager/test_pane_vm.py`. Add `rename_entry(target, new_name)` to `src/aws_tui/vm/file_manager/pane_vm.py`; keep `rename_cursor(new_name)` compatible. Bind provider/location before I/O and condition refresh on that binding.
- [x] Modal intents: create `src/aws_tui/ui/widgets/file_operation_modals.py` and a VM-owned basename validator. Test Move/Rename/Cancel, inline errors, keyboard/mouse and long-path review scrolling with pure recording callbacks before wiring mutations.
- [x] App orchestration: implement `action_move`/`action_new_folder`, captured confirmation and worker execution in `src/aws_tui/app.py`. Preserve `_confirm_copy`/`_run_copy` behavior and signature. Reuse `DualPaneVM.move_across`; review ERROR conflict policy explicitly. Extend `tests/integration/test_file_mutation_commands.py` with actual temporary-file effects and zero-call refusals.
- [x] Safeguards: exercise context changes during input/review, protection changes after confirmation, alias/self moves, S3 bucket/directory/cross-bucket/versioned rename refusals, cancellation and partial-failure refresh. Keep target identity fixed even when the cursor or other pane changes.
- [x] Canonical docs: update `docs/keybindings.md` and `docs/services/s3.md`; remove only completed move/rename/new-folder deferrals. Describe the exact prompts, default conflict refusal and native Windows/live-validation limits. Check affected anchors and numbering.
- [x] Verification/review: run affected VM/provider/ownership/copy/delete/selection suites, new full-app flows, Python compatibility checks and applicable local hooks/docs validation. Independent review must clear material findings; rerun affected tests after repairs.

For each task, record the failing behavior, implement it, then record the passing command and result in the external ledger.

Integration: commit/push the reviewed change; feature PR into develop; separate develop→main PR; verify reviewed trees; remove the merged task branch locally/remotely; close #242 with evidence before starting #260.
