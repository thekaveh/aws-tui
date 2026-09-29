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
- [ ] Commit and push; open a develop PR referencing #282 without auto-closing it.
- [ ] Verify and merge the implementation, promotion and required back-merge PRs with protections intact.
- [ ] Verify post-merge CI and source parity; clean this ticket's branches/worktrees; publish conclusions, close the issue and mark its board entry Done.

## 7. Local review evidence

The reviewed test sweep reduces bare pauses from 1,059 to 667 and immediate sibling pause/assert sites from 411 to 87. Retained sites have inline explanations for event delivery, unchanged geometry, stale-callback rejection or exact callback counts. An AST comparison preserves all 2,190 original assertions across the 48 edited test files.

Independent review found and corrected three sequencing mistakes: the Quick Look no-op check needed an event-delivery barrier; the navigation cascade guard needed to drain queued projections; and navigation rebuilding needed to observe mounted replacement rows rather than merely the rebuild method call. The two affected modules pass all 11 tests after these corrections.

Focused service UI verification passed 219 tests. Shared UI verification passed 146 tests, with another 50 after final barrier corrections; snapshot hook verification passed 25 unchanged goldens. The initial integration run passed 353 tests and exposed one scrollbar capture precondition failure. Waiting for the scrollbar's grab bookkeeping corrected it; the affected app-blur/settings group passed 33 tests and the other refined integration modules passed 78 tests.

All pre-commit hooks, documentation contracts (177 tests), the strict documentation build, local wheel/sdist contents and Twine validation passed. The complete local unit/in-process integration run passed 4,255 tests with four platform skips, but 38 Moto fixture setups were blocked by sandbox socket permissions. All 117 tests in those three modules pass with permission to bind the local Moto servers. Hosted cross-platform acceptance checks remain outstanding.

The baseline CI rerun completed with two Windows Python 3.11 demo-source failures. Diagnosis is required before relying on the branch's cross-platform gate; these failures must not be concealed by repeated reruns or increased timeouts.

## 8. Diagnosed Glue projection blocker

A deterministic probe captured a real `OptionHighlighted` emitted by rendering the old `dev_events` selection. Delivering it after `open_table(dev_events_iceberg)` reverted the selected table and detail to `dev_events`, disabled Iceberg, and made `select_view("snapshots")` return `False` without reaching the provider. A subsequent user highlight still selected the Iceberg table correctly. This reproduces a failure mechanism consistent with the Windows logs; those logs alone do not prove their exact interleaving.

The chosen fix suppresses `OptionHighlighted` only while `ResourceListPane.replace` projects view-model state. Genuine keyboard and mouse events retain their normal path. Guarding each consumer against old option identities would duplicate logic and still treat rendering as user intent; additional test waits cannot repair a selection that has actually been overwritten.

New regression tests require initial/repeated/loading-to-ready projections to emit no selection intent, preserve the selected row, and allow keyboard navigation to dispatch exactly once. Before the fix, both projection cases fail with the rendered row delivered as an event; the keyboard case passes. With suppression, all three pass. The production change is limited to this projection boundary; no timeout or assertion was weakened. Existing misleading demo-test comments about awaited methods returning before their loads are corrected.

After the projection fix, 191 Glue/demo tests and 109 Athena tests passed. Glue/demo snapshot verification passed 160 tests with 118 unchanged goldens. The new regression module also passes strict mypy, and all pre-commit hooks, package validation and the strict documentation build passed again after the production change. Independent follow-up review verified genuine mouse selection still dispatches exactly once and found no substantive issues.
