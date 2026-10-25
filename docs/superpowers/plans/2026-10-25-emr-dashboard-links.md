# EMR Dashboard Links Implementation Plan

> Use subagent-driven-development for bounded provider and coordinator tasks,
> with task reviews and whole-branch review. Routine design choices are authorized.

**Goal:** Complete #262 with reviewed, fresh, source-bound Open/Copy actions.

**Architecture:** Validated private link at provider boundary, page-owned lifecycle
coordinator, injected browser port and existing clipboard pipeline, one review modal.

**Tech Stack:** Python3.11–3.13, Textual8.2.8, VMx, asyncio/to_thread, Botocore stubs.

## 1. Global constraints

No AWS/Actions/protection/release/shared-infra changes. Link max2048chars;
HTTPS AWS DNS suffix only, no credentials/control/whitespace/backslash/non443port.
No persisted/logged/snapshot URL, including host/path/query/fragment. Fresh provider
read each activation; explicit log access false by default; optional attempt>=1.
Keep existing logs and protected reads. Native demo browser always disabled.
Deferred live/native checks are nonblocking, never passed. Follow binding spec.

## 2. Ordered tasks

- [x] Provider/link TDD: new domain/emr_dashboard.py; real/protocol client method and synthetic demo; tests/unit/domain/test_emr_dashboard.py and tests/unit/demo/test_emr_dashboard.py. Exact requests/region/config/defaultfalse/attempt; input/URL strict validation, service error taxonomy and full-link privacy; no start/cancel side effects. Run affected current EMR/demo/SDK checks; task review.
- [x] Browser/coordinator TDD: new infra/browser.py and vm/emr_serverless/dashboard_vm.py with their unit tests. No native browser launch in tests. Inject ports, single-flight, captured revision, cancel/drain, strict pre-handoff ownership, explicit real delivery outcomes, no URL state/diagnostics. Exercise blocking/failed browser, fallback, changing source/app/run/ABA, and cancellation-ignoring providers. Task review.
- [x] Actual application TDD: page selection-revision/coordinator lifecycle, AppContext browser injection/disabled demo, failed-client method, modal/page/App/actions/keymap U/AltU. Add real composition fixtures for Spark/Hive, protected source, unavailable browser and clipboard feedback, optional attempt/profilelogs, routes/remaps, stale review/pending/teardown, privacy, repeat fresh links and small-terminal layout. Preserve clone/new job/cancel/log flows.
- [x] Canonical docs/contracts, checks and independent whole review. Update EMR/keybindings/permissions/SDK/action inventory; run affected local tests, hooks/type/lint/layers, docs parity/strict build, representative snapshots and available native Python checks. Fix findings with regressions and re-review before delivery.

## 3. Delivery

Commit/push, feature PR develop, promotion PR main. Verify exact reviewed contents,
local/origin parity and merged PR states, remove owned merged branch/worktrees,
then comment/check applicable ACs/close/read back #262. Audit original full goal.
