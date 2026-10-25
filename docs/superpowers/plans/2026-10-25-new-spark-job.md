# New Spark Job Implementation Plan

> Execute inline with executing-plans; use independent review before integration.

**Goal:** Deliver #260 with reviewed blank submission for an application with no runs.

**Architecture:** Add explicit blank mode to the existing Spark form and bind it
to the current EMR page/application. Reuse validation, client admission and retry
semantics. Preserve the clone constructor, review and lifecycle contracts.

**Tech Stack:** Python 3.11–3.13, Textual, VMx, pytest and controlled providers.

## 1. Global constraints

Preserve #237 protection and #238 clone fidelity. No synthetic source run.
Use `emr.new_job` with `N`; do not introduce an alias with file-manager `n`.
Local checks only. No AWS, Actions, IAM or shared infrastructure changes.
Unavailable live/native validation is explicitly deferred and nonblocking.

## 2. Tasks

- [x] VM: add failing `tests/unit/vm/emr_serverless/test_new_job_vm.py` for blank mode, literal arguments, complete review, required values, retry/edit tokens, duplicate submits and stale ownership. Extend `src/aws_tui/vm/emr_serverless/clone_vm.py` with optional detail and captured application ID; retain clone behavior. Snapshot requests before await and protect newer edited tokens from older completions. Run new tests RED then GREEN and existing clone VM regressions.
- [x] Modal: extend `src/aws_tui/ui/widgets/emr_serverless/clone_modal.py` mode-dependent title, review and recovery wording. Add `tests/unit/ui/emr_serverless/test_new_job_modal.py` for blank edit/review, inline invalid fields, JSON/Unicode, all cancel stages, duplicate clicks and value retention. Reuse existing layout and footer ownership.
- [x] Page/actions: add a selected-Spark readiness predicate in `src/aws_tui/vm/emr_serverless/page_vm.py`; bind current page/client/application/source in `src/aws_tui/ui/widgets/emr_serverless/page.py`. Register/contextualize `emr.new_job` in app, keymap, action catalog and hints. Add real app-flow tests in `tests/integration/test_emr_new_job.py` for zero runs, default/remapped/palette, Hive/missing refusal, protected dispatch, source changes and late completion. Refresh/select created run only while the captured application remains current.
- [x] Docs: update canonical EMR guide, keybindings and affected entry summaries without worklog prose. Describe blank/clone choices, JSON argument boundaries, unknown application defaults, review, token retry and Close semantics. Regenerate owned outputs and verify local docs.
- [x] Review/verification: review ACs and complete diff independently. Run affected clone/modal/page/cancel/VM/SDK/UI and available Python checks, meaningful snapshots and applicable hooks. Resolve findings with regressions. Record limits honestly.

## 3. Integration

Commit/push after local verification. Merge feature PR into develop, then a
separate promotion PR into main. Verify exact reviewed contents and local/remote
parity, delete only the merged task branch/owned worktrees, and close #260 with
evidence before starting #257. Preserve unrelated active Dependabot work.
