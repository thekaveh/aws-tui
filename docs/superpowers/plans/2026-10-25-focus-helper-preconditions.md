# Focus helper preconditions implementation plan

**Goal:** Complete #276 without changing production focus behavior.

**Architecture:** Keep deferred retry by default and expose an explicit
ready-target contract. Reject detached targets in either mode and include
current precondition state in failure messages.

**Constraints:** Textual remains pinned at 8.2.8. Local checks and isolated
fixtures only. No dependencies, hosted workflows or shared infrastructure
changes. The owner authorized autonomous design and execution.

## 1. Regression tests

- [ ] Extend `tests/unit/test_helpers.py` with real-widget cases for hidden,
  disabled, inherited display/disabled, loading, non-focusable, detached,
  removed-during-wait and successful ready-target states.
- [ ] Use `asyncio.wait_for(..., timeout=0.5)` to bound prompt-failure cases
  independently of the helper's default timeout.
- [ ] Preserve delayed availability and add a dropped-first-request case.
- [ ] Run the new tests against the unchanged helper and inspect the failures.

## 2. Repair and caller adoption

- [ ] Add `require_focusable: bool = False` to `focus_and_settle`.
- [ ] Compute an assertion diagnostic from public widget and ancestor state.
  Reject absent attachment/mount, or strict-mode failed focusability/display,
  before issuing focus; repeat attachment checks while waiting.
- [ ] Keep focus requests retriable and append current state on timeout.
- [ ] Opt the three known-ready Iceberg action buttons into strict checking.
- [ ] In `tests/unit/ui/test_context_picker.py`, focus the enabled picker before
  disabling it, then verify that Enter cannot open it.
- [ ] Run helper regressions, all direct caller cases and repeated Glue cases.

## 3. Review and delivery

- [ ] Run applicable Python 3.11 compatibility, Ruff, local hooks and docs checks.
- [ ] Obtain independent review and resolve findings with covering regressions.
- [ ] Commit/push, merge the feature PR into develop, then its promotion into main.
- [ ] Verify identical reviewed trees, synchronize local refs, remove the merged
  task branch and close #276 with evidence before starting #237.
