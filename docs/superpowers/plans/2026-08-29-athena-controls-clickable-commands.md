# Athena Controls and Clickable Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore fully visible Athena query controls, make Glue starter SQL visibly durable, and route command-hint clicks through the canonical action registry.

**Architecture:** Preserve VMx and MVVM ownership: the query VM owns SQL and command availability, the Athena page projects VM state, and the app owns action dispatch and cross-service transactions. Extend existing widgets and tests without introducing parallel command or query state.

**Tech Stack:** Python 3.11-3.13, Textual 8.2.8, VMx 3.23.0, pytest, pytest-asyncio, pytest-textual-snapshot, Ruff, mypy.

## Global Constraints

- Work on `codex/athena-controls-clickable-commands`, branched from synchronized `develop` commit `dc70d2e2`.
- Preserve query view order and the editor -> Run -> Stop -> execution detail Tab sequence.
- Preserve exact quoted `SELECT * ... LIMIT 5` starter SQL and never execute it automatically.
- Keep `AthenaPageVM.open_table()` as the starter SQL owner.
- Route mouse activation through `AwsTuiApp.action_dispatch()`; never synthesize shortcut keys.
- Keep hint chips out of the keyboard focus chain.
- Disabled hint chips must remain inert and retain their tooltips.
- Use test-driven development for each behavior change.

---

### Task 1: Prove and Repair Query-Control Geometry

**Files:**
- Modify: `tests/snapshot/apps/athena.py`
- Modify: `tests/unit/ui/athena/test_page.py`
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`
- Update: affected `tests/snapshot/__snapshots__/test_athena/*.raw`

**Interfaces:**
- Consumes: `AthenaQueryView` controls, `Button.content_region`, existing focus chain.
- Produces: positive Run/Stop content height and complete containment at supported sizes.

- [ ] Add assertions that both controls have `content_region.height >= 1`, remain inside the controls content area, and precede the editor.
- [ ] Run the focused test and confirm it fails with content height `0`.
- [ ] Increase the controls grid track and restore standard three-row button geometry without changing focus order.
- [ ] Run focused unit and snapshot tests and confirm they pass.

### Task 2: Make Starter SQL a Visible Handoff Completion Contract

**Files:**
- Modify: `tests/integration/test_glue_athena_navigation.py`
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py` only if a public projection method is needed.

**Interfaces:**
- Consumes: `AthenaPageVM.open_table(TableRef, snapshot_id)`, mounted `AthenaPage`, `AthenaQueryView.refresh_from_vm()`.
- Produces: app transaction completion after the mounted editor reflects `AthenaQueryVM.sql`.

- [ ] Add a failing acceptance test that revisits a mounted Athena page and requires explicit visible projection after Glue `Q`.
- [ ] Run the focused integration test and confirm the missing transaction-level projection fails.
- [ ] Add the smallest public Athena-page projection hook and call it after `open_table()` under the existing generation guard.
- [ ] Verify exact VM/editor SQL, active Query view, enabled Run command, and no execution.

### Task 3: Dispatch Clickable Command Hints

**Files:**
- Modify: `tests/unit/ui/test_chrome_widgets.py`
- Modify: `tests/integration/test_chrome_and_hint_legend.py` if full-app routing coverage belongs there.
- Modify: `src/aws_tui/ui/widgets/hint_legend.py`

**Interfaces:**
- Consumes: `HintAction.action_id`, `HintAction.enabled`, `ActionDispatcher.action_dispatch(str)`.
- Produces: primary-click action parity for enabled chips with no new Tab stops.

- [ ] Add failing tests that click an enabled chip, observe one registry dispatch, click a disabled chip, observe none, and assert chips remain non-focusable.
- [ ] Run the focused tests and confirm enabled clicks do not yet dispatch.
- [ ] Handle `_HintChip` primary clicks by dispatching the named action and scheduling awaitables through a bounded worker group.
- [ ] Run legend geometry, rebuild-race, tooltip, and full-app routing tests.

### Task 4: Verify the Complete Repair

**Files:**
- Modify: snapshots only when approved geometry changes require regeneration.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: merge-ready branch with no regressions.

- [ ] Run focused Athena, Glue navigation, legend, binding, and action-registry test modules.
- [ ] Regenerate affected snapshots and inspect wide, compact, and narrow output.
- [ ] Run `uv run ruff check .`, `uv run mypy src`, and the complete pytest suite.
- [ ] Review `git diff --check`, changed-file scope, and documentation consistency.
- [ ] Commit and push the verified repair branch.
