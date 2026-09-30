# Reliable test waits implementation plan

> **For agentic workers:** Use subagent-driven-development for the disjoint test groups below; review the complete branch before promotion.

**Goal:** Complete issue #282 by replacing scheduler-dependent waits for deferred assertions with bounded, named observations while preserving the assertions.

**Architecture:** Keep the existing `tests.helpers.wait_until` contract. Each test observes the state its assertion actually depends on; focus acquisition uses `focus_and_settle` where a dropped request needs retry. Keep intentional event-delivery barriers for negative assertions and synchronous behavior, explaining why they are needed.

**Tech Stack:** Python 3.11–3.13, pytest, Textual 8.2.8, existing Textual pilot and fake providers.

## 1. Global constraints

- No assertion is weakened or removed; preserve expected values and failure behavior.
- Every new wait has a descriptive `what=` and a bounded timeout.
- Do not poll a captured scalar or a condition true before the action under test.
- Negative assertions need a positive completion signal or a justified event-delivery barrier; waiting for an already-true negative is not verification.
- No arbitrary sleeps, larger blanket timeouts or snapshot re-recording. Any production blocker requires a separate root-cause reproduction and regression test; see section 8.
- Run unit and in-process integration tests across Ubuntu, macOS and Windows in CI without rerunning failed jobs as a substitute for diagnosis.
- Maintain source parity after develop/main promotion and close #282 only after the whole promotion cycle is verified.

## 2. Evidence and acceptance mapping

The initial revision is `e08dfcb4`. The textual census is 1,059 bare `await pilot.pause()` calls. An AST census finds 411 immediate sibling pause/assert sites because it includes comments and multiline assertions omitted by the ticket's grep. The baseline census is retained in `/tmp/aws-tui-282-census.json` during implementation.

Textual's pause drains screen callbacks, waits for CPU idle and refreshes timers. It is useful as an event barrier but does not prove completion of a particular worker, deferred projection or delayed operation. Review intent rather than mechanically deleting every pause.

| Acceptance criterion | Verification |
| --- | --- |
| Deferred assertions use condition waits | Review every AST candidate and nearby setup; repeat textual and AST census and classify retained barriers |
| Conditions name failures | Review `what=` strings and existing helper timeout tests |
| Assertions preserved | Compare per-test assertion ASTs before/after; review any structural movement explicitly |
| Cross-platform green | Required unit matrix and integration checks on the final PR and promoted commits |

## 3. Task 1: Service UI tests

**Files:** `tests/unit/ui/glue/*.py`, `tests/unit/ui/athena/*.py`, `tests/unit/ui/emr_serverless/*.py`.

- [x] Read each pause/assert candidate and its producing operation.
- [x] Replace deferred waits with live predicates, retaining original assertions:

```python
await wait_until(lambda: widget.has_focus, what="the requested widget took focus")
assert widget.has_focus
```

- [x] For projections, observe rendered rows/text rather than only the already-updated view model.
- [x] Run the changed service UI modules and review retained barriers.

## 4. Task 2: Application integration tests

**Files:** `tests/integration/*.py`.

- [x] Observe service adoption, provider state, mounted content, selected identity and worker completion at their actual boundaries.
- [x] Preserve key/modal containment tests: deliver the event before asserting absence of an effect.
- [x] Run changed integration files with the default non-Docker marker filter.

## 5. Task 3: Shared UI, snapshot helpers and journeys

**Files:** remaining `tests/unit/ui/*.py`, `tests/snapshot/`, `tests/e2e/`.

- [x] Audit focus, pane rendering, picker visibility and deferred chrome assertions.
- [x] Preserve synchronous assertions and intentional scheduler barriers with concise explanations.
- [x] Run affected UI, snapshot and journey modules.

## 6. Task 4: Combined review and publication

- [x] Reconcile the census, retained-site explanations and assertion comparison.
- [x] Run lint, formatting, typing, layer checks, documentation checks and the full unit/in-process integration suite.
- [x] Obtain independent diff review; correct substantive findings and verify the affected scope.
- [x] Commit and push; open a develop PR referencing #282 without auto-closing it.
- [ ] Verify and merge the implementation, promotion and required back-merge PRs with protections intact.
- [ ] Verify post-merge CI and source parity; clean this ticket's branches/worktrees; publish conclusions, close the issue and mark its board entry Done.

## 7. Local review evidence

The reviewed test sweep reduces bare pauses from 1,059 to 666 and immediate sibling pause/assert sites from 411 to 88. Retained sites have inline explanations for event delivery, unchanged geometry, stale-callback rejection or exact callback counts. An AST comparison preserves all 2,292 original assertions across the 49 edited existing test files (final comparison against the develop base).

Independent review found and corrected three sequencing mistakes: the Quick Look no-op check needed an event-delivery barrier; the navigation cascade guard needed to drain queued projections; and navigation rebuilding needed to observe mounted replacement rows rather than merely the rebuild method call. The two affected modules pass all 11 tests after these corrections.

Focused service UI verification passed 219 tests. Shared UI verification passed 146 tests, with another 50 after final barrier corrections; snapshot hook verification passed 25 unchanged goldens. The initial integration run passed 353 tests and exposed one scrollbar capture precondition failure. Waiting for the scrollbar's grab bookkeeping corrected it; the affected app-blur/settings group passed 33 tests and the other refined integration modules passed 78 tests.

All pre-commit hooks, documentation contracts (177 tests), the strict documentation build, local wheel/sdist contents and Twine validation passed. The complete local unit/in-process integration run passed 4,255 tests with four platform skips, but 38 Moto fixture setups were blocked by sandbox socket permissions. All 117 tests in those three modules pass with permission to bind the local Moto servers. Hosted cross-platform acceptance checks remain outstanding.

The baseline CI rerun completed with two Windows Python 3.11 demo-source failures. Diagnosis is required before relying on the branch's cross-platform gate; these failures must not be concealed by repeated reruns or increased timeouts.

## 8. Diagnosed Glue projection blocker

A deterministic probe captured a real `OptionHighlighted` emitted by rendering the old `dev_events` selection. Delivering it after `open_table(dev_events_iceberg)` reverted the selected table and detail to `dev_events`, disabled Iceberg, and made `select_view("snapshots")` return `False` without reaching the provider. A subsequent user highlight still selected the Iceberg table correctly. This reproduces a failure mechanism consistent with the Windows logs; those logs alone do not prove their exact interleaving.

The chosen fix suppresses `OptionHighlighted` only while `ResourceListPane.replace` projects view-model state. Genuine keyboard and mouse events retain their normal path. Guarding each consumer against old option identities would duplicate logic and still treat rendering as user intent; additional test waits cannot repair a selection that has actually been overwritten.

New regression tests require initial/repeated/loading-to-ready projections to emit no selection intent, preserve the selected row, and allow keyboard navigation to dispatch exactly once. Before the fix, both projection cases fail with the rendered row delivered as an event; the keyboard case passes. With suppression, all three pass. The production change is limited to this projection boundary; no timeout or assertion was weakened. Existing misleading demo-test comments about awaited methods returning before their loads are corrected.

After the projection fix, 191 Glue/demo tests and 109 Athena tests passed. Glue/demo snapshot verification passed 160 tests with 118 unchanged goldens. The new regression module also passes strict mypy, and all pre-commit hooks, package validation and the strict documentation build passed again after the production change. Independent follow-up review verified genuine mouse selection still dispatches exactly once and found no substantive issues.

## 9. First hosted run: remaining test preconditions

Run `36644202395` on PR #295 exposed two remaining sequencing assumptions. Ubuntu Python 3.12/3.13, Windows Python 3.13 and coverage failed the Athena pager-recovery check. A gated provider reproduced the failure locally: projecting the busy state disables the pager button and moves focus to the catalog; the next context-routed load-more action consequently never retries workgroups. The test now holds the first provider call until that state is visible, restores pager focus before retrying, and establishes the same focus context before asserting the hint. The gated case failed with the original retry sequencing; all 13 Athena integration module tests pass after correction. All 79 original assertions remain unchanged.

Windows Python 3.11/3.12 failed the Settings-during-boot check with `WorkerCancelled`. Settings navigation intentionally cancels the content-mount worker in `AwsTuiApp`; requiring that worker to finish successfully is the wrong wait contract. The test now uses the existing `drain_workers` helper and a named condition for the final mounted Settings content, retaining every original assertion. All 28 Settings module tests pass after the correction. Independent review found no substantive issues in either fix, and all 199 original assertions across both modules are unchanged.

These are fixes before a new commit/run, not reruns of unchanged failing jobs. The first run's coverage percentage was 86.09%, above the required 70%; its failure was the pager test rather than a coverage shortfall. Both hosted snapshot jobs passed without golden changes.


The delayed macOS Python 3.12 result also exposed a teardown measurement boundary: the pane's render counter recorded a callback queued before removal after the counter had been reset. The subscription observer count and post-removal notification count were already correct. A documented event-delivery barrier after removal, before resetting counters, separates that earlier work from the new detached navigation. All 25 pane-widget tests, strict mypy, Ruff and independent review pass, with all 123 original assertions unchanged. The final census includes 88 retained original barriers plus two in the new projection regression tests.

## 10. Corrected hosted run: persistence observation

Run `36646660991` on head `522cf540` passed all Ubuntu legs, Windows Python 3.11/3.12, both snapshot jobs and coverage. Windows Python 3.13 exposed the existing add-connection file-polling failure described in #274: the test timed out waiting for the saved entry. The handler awaits an AnyIO thread rather than a Textual worker, so its `drain_workers` does not establish persistence completion. Reopening the target file in the wait predicate can overlap the writer's atomic replace on Windows.

A diagnostic scheduling probe held the add operation pending and rejected event-loop config reads during that interval. The unchanged test failed at its file-polling predicate. The corrected wait observes the form losing its open class, which the handler does only after successful persistence; errors retain the open form. An explicit open-state assertion prevents an already-satisfied predicate. The 120 original assertions and the existing 30-second budget are unchanged. Independent review found no substantive issues. The probe proves removal of concurrent test reads; it does not establish the exact scheduling of the hosted failure. Final hosted acceptance remains outstanding.

All 28 Settings integration tests pass with the same overlap-detecting probe enabled (74.20 seconds), including the previously failing add test. Ruff, formatting and the assertion-preservation check pass.

## 11. Exact grep acceptance audit

The ticket's exact grep count falls from 225 at the develop base to 36. Inspection of its cross-block matches identified a remaining copy-confirmation loop: forty event-delivery yields were an iteration budget for deferred transfer completion. A diagnostic delayed the real copy worker by five seconds, within the existing worker-drain timeout; the original test failed after 1.70 seconds while the copy was still pending. The test now awaits the existing confirmation/copy worker chain with `drain_workers`, then reads the destination once and retains its original file and crash assertions. All 36 tests in the three reviewed modules pass with the same delayed-copy probe (13.15 seconds). Independent review found no substantive issues.

Other cross-block matches are intentional: navigation selection changes synchronously before refocus/row delivery; keybinding spies append synchronously and the retained yields precede exact-count checks after teardown; SQL seeding reissues the source-of-truth value and verifies stability across delivery, rather than assuming one yield finishes a deferred change. The navigation and keybinding barriers now document those semantics. The final full-tree census is 669 bare pauses, of which three belong to the new projection regression module; 666 remain in existing tests. All 2,292 original assertions in the 49 edited existing test modules remain intact.

## 12. Postmerge snapshot readiness follow-up

Implementation PR #295 merged after run `36650833569` passed all 22 jobs without reruns (coverage 86.08%; 4,296 tests passed). Develop postmerge run `36652777255` subsequently failed the macOS `test_demo_iceberg_snapshot[voidline]` comparison: 646 snapshots passed and one differed. Promotion PR #296 remains on hold. The workflow did not upload the failed SVG/report, so the mismatch's exact content cannot be established from its log. A corrected local capture of the demo module passed all 56 tests and 28 unchanged goldens; that passing diagnostic does not identify the hosted failure's cause.

A gated regression demonstrates a separate, concrete readiness gap in the capture helper. Dismissing the startup advisory empties its model synchronously, but its widget rebuild is queued after refresh and removal is asynchronous. Holding that rebuild lets the original helper return with the advisory widget still mounted. The new test fails at that precise condition on the old helper. The corrected helper preserves the model assertion and adds a named bounded wait for the matching widget to leave the tree; releasing the gate lets capture complete. The regression and demo module pass all 57 tests with 28 unchanged goldens. No timeout or golden changed.

The snapshot workflow now retains expected/actual SVGs and test identity directly from the snapshot plugin's comparison records after report generation. It never parses or uploads the HTML report, which embeds the runner environment. Four regression cases cover missing expected images, exact image/test-identity export, opt-in behavior, absent failures, and exclusion of SVG-shaped environment values. This supplies inspectable evidence for any future mismatch instead of assuming that the demonstrated toast gap explains the missing hosted artifact.

The complete local snapshot tier with the readiness correction passed 1,032 tests and all 647 unchanged goldens. The artifact regressions passed separately after the export hook was added. A separate intentionally failing snapshot process confirms the hook runs after report generation and emits images plus test identity while excluding an SVG-shaped environment sentinel present in the HTML report. Repository hooks and 207 documentation/workflow contracts passed. Hosted follow-up verification remains required.

Independent review approved the readiness correction and final structured-field artifact export. Review also identified the plugin's string placeholder for a missing golden; the export now uses its `snapshot_exists` flag and the four artifact regression cases include that real representation. All four pass.

## 13. Retained promotion mismatch: slow-boot success narration

Follow-up PR #297 merged as `4443909a` after all 22 jobs passed without reruns (coverage 86.10%). Its develop postmerge snapshots passed. The updated promotion run `36656799239` then failed the macOS `demo-prod` Iceberg files snapshot. Artifact `11072533171` retained the expected/actual SVGs and test identity. Their text/style comparison identifies a green “Connection: demo-dev connected” toast covering the header; the selected `demo-prod` source and Iceberg data agree. Unlike the earlier unavailable SVG, this mismatch is directly observable.

The boot worker emits that success only when its 500 ms narration grace period elapses. Its three-second expiry is a raw asyncio task, so worker draining does not prove the toast has disappeared. A regression forces the production narration path and holds expiry; the previous capture helper returns while the success remains. The corrected shared demo readiness path dismisses only the known `demo-dev` boot-outcome ID at success severity and waits for absence from both the model and rendered widgets. Checking widgets directly also covers a model that already expired before readiness began. This applies to ordinary demo captures while preserving their intentional advisory.

The regression and demo module pass 58 tests with 28 unchanged goldens. Additional cases exercise warnings/errors using the same boot-outcome ID, unrelated success notifications, and another profile's outcome; these notices must remain visible. No production notification behavior, timeout, original assertion, or golden is changed. Independent review found no substantive issues, and its separate readiness run passed all four tests. The final readiness run with named mounted-notice preconditions also passes all four cases; all repository hooks and 177 documentation contracts pass. The full snapshot suite passes 1,039 tests and all 647 unchanged goldens (237.19 seconds). All 31 existing assertions in the two changed test modules remain intact. Hosted verification remains required.

## 14. Back-merge failure: scrollbar layout before repaint counting

Follow-up PR #298, develop postmerge run `36662258573`, and final promotion run `36662263880` each passed all 22 jobs without reruns. Promotion #296 merged as `c7760853`. Back-merge #299's run `36665582316` then failed Windows Python 3.12 in `test_cursor_move_repaints_only_the_two_affected_rows`: the two expected cursor refreshes were followed by all 60 rows in reverse order. The job otherwise passed 4,236 tests, with its existing 62 platform skips and nine deselections.

Local stack instrumentation identifies that reverse-order refresh mechanism: Textual's layout updates each row's reactive `virtual_size` when the vertical scrollbar reduces its width from 120 to 118. Mount completion and a single event-delivery pause do not establish that the second layout pass has finished. A diagnostic gate holding that real follow-up layout across the initial pause reproduces the same two-plus-60 failure on the unchanged test.

The test now waits for the visible vertical scrollbar, overflowing body, and every row's actual and virtual width to match the scrollbar-adjusted content region before clearing its refresh counter. Its original exact two-row assertion and subsequent event-delivery barrier remain unchanged. A second parameterized case deliberately defers the real follow-up layout across the mount barrier, asserts that the row geometry is still pending, then releases layout through a normal refresh request. Both cases pass; omitting the new geometry wait makes the delayed case fail with the original 60 extra refreshes. This adds no production changes, sleeps, timeout increases, or snapshot updates.

All 26 pane-widget tests pass (30.53 seconds), all repository hooks and 177 documentation tests pass, and all 123 original assertions remain intact. Independent review found no substantive issues and separately passed both regression variants, Ruff, and module mypy. Main's original postmerge run `36665526068` passed all 22 jobs, but the back-merge failure still requires this correction. Hosted verification remains required; the issue stays open until the correction is promoted and final branch validation completes.

## 15. Late back-merge result: independent-process watchdog

The same back-merge run's later macOS Python 3.13 result failed `test_transaction_lock_excludes_an_independent_process`. The subprocess watchdog expired after two seconds even though captured stderr already contained the expected “timed out after 0.05s” lock error and retry advice. The log establishes successful lock contention followed by failure to complete the whole child lifecycle within that watchdog; it does not identify how much time imports, scheduling, and shutdown each consumed.

The test's whole-process watchdog now allows 15 seconds, still below pytest's 60-second test ceiling. The child's 50 ms lock timeout and all three original result assertions are unchanged. This is a process-harness budget correction, not a change to lock behavior or its required result. A diagnostic child-exit delay of 2.5 seconds reproduces the original `TimeoutExpired` with the expected error already captured; all 64 config-store tests pass with that same probe after correction. Ruff and formatting pass. No production change or unchanged hosted rerun is involved.

Independent review found no substantive issues and separately passed all 64 config-store tests without the diagnostic delay. All 64 original module assertions are preserved, and changed-file hooks pass. The old back-merge run finished with 19 passing jobs and these two test failures plus its aggregate gate; its remaining jobs introduced no further failures. PR #300 includes both corrections and still requires hosted confirmation on its final commit.
