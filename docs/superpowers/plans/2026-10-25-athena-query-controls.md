# Athena Query Controls Implementation Plan

> Use subagent-driven-development for the isolated client task; execute VM/UI inline.

**Goal:** Deliver #257 with truthful scan metadata and explicit safe reuse.

**Architecture:** Extend existing client/runner/VM submission; transient reuse
settings modal and current query details. No second execution coordinator.

**Tech Stack:** Python 3.11–3.13, Textual, VMx, pytest controlled providers.

## 1. Constraints

Local only, no AWS/Actions/workgroup/protection/infra mutations. Default off,
strict age0–10080; unknown/invalid cutoff never unlimited. Preserve handoff and
protection. Nonblocking external deferrals recorded in issue before implementation.

## 2. Tasks

- [x] Client TDD: modify domain/athena.py start_query with optional result_reuse_max_age_minutes. Write tests/unit/domain/test_athena_reuse.py first; validate all boundaries, no member off, exact nested API member on, output-override refusal and fresh managed metadata refusal. Preserve other call shapes and error privacy.
- [x] Runner/demo TDD: extend AthenaRunnerClient/start and InMemoryAthena optional keyword. Forward enabled options without fallback; default-off calls unchanged. Include options in demo idempotency fingerprint; do not pretend AWS will reuse a result.
- [x] VM TDD: query_vm.py strict transient settings, availability callback, immutable request snapshot and failed-retry intent ownership. page_vm.py supplies current valid workgroup eligibility; metadata/context changes clear stale enabled state. Add tests/unit/vm/athena/test_query_reuse.py for failures, edits, stale dispatch and late completion.
- [x] UI TDD: add query_options_modal.py and query_view.py Reuse button, captured-context apply, inline validation and cancel. Show known/absent/unavailable/invalid cutoff and actual reuse statistics. Test actual application controls and 80x24 reachability in tests/integration/test_athena_query_options.py; preserve editor/document focus.
- [x] Docs/contracts: update canonical Athena guide and consumed SDK checks for optional member. Regenerate owned docs; verify relevant snapshots, handoff, protection, execution/cancel, lint/type/layers/docs and native available Python subsets. Review full AC diff independently.

## 3. Integration

After complete local checks and review: commit/push, feature PR develop, promotion
PR main, verify exact trees/local-origin parity, delete merged owned branch,
close/read back #257 with evidence, only then start #254.
