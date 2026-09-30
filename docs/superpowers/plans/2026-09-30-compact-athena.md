# Compact Athena implementation plan

> **For agentic workers:** Execute inline with the executing-plans workflow;
> review the completed diff before publishing through the protected lifecycle.

**Goal:** Complete all six acceptance criteria of issue #233 in the full app.

**Architecture:** Keep the existing view models and mounted widgets. A compact
screen class reduces decorative chrome; Athena adapts its own constrained query
surface. Integrate status into the existing focus coordinator.

**Tech stack:** Python, Textual, VMx, pytest pilot, existing demo providers and
SVG snapshots. Design: `../specs/2026-09-30-compact-athena-design.md`.

## 1. Constraints

- Preserve 120×40 presentation, typed SQL, context identities and focus.
- Minimum supported terminal remains 80×24. Do not enable disabled commands.
- Use named waits on actual rendered/model readiness, not repeated pauses.
- Use only isolated demo providers and local tests; no live AWS mutation.
- Review intentional snapshot changes; never replace goldens blindly.
- Finish develop/main promotion, postmerge checks, cleanup and issue closure
  before beginning #238.

## 2. Reproduce and preserve editor space

Files: `tests/integration/test_compact_layout.py`, `src/aws_tui/app.py`,
`src/aws_tui/ui/widgets/brand_banner.py`,
`src/aws_tui/ui/widgets/athena/query_view.py`.

- [x] Add the exact full-app editor-height regression at both requested sizes.
- [x] Run `.venv/bin/pytest tests/integration/test_compact_layout.py -q`:
  baseline is one failure (`Region(... height=0)`) and one pass.
- [x] Add compact chrome for terminal heights below 34, retaining the demo label.
- [x] Adapt constrained query tracks, preserving a visible editor and scrollable
  detail. Use `grid-rows: 3 1fr 3` only where the roomy tracks cannot fit; ensure
  button and status content fit the reduced controls track.
- [x] Verify both geometry and rendered SQL, then resize back to the roomy view.

## 3. Keyboard access and context width

Files: `src/aws_tui/ui/widgets/athena/page.py`, query view, shared theme rules,
`src/aws_tui/vm/chrome/focus_coordinator_vm.py`, compact integration tests.

- [x] Add failing real-Tab tests for status and selectors, and viewport bounds
  checks for all context controls. Cover pagination buttons when visible.
- [x] Fit source/workgroup/catalog/database controls into the actual available
  width, retaining stable widget identity and overlay option access.
- [x] Add a dedicated status focus slot, include it in page/query Tab routing,
  and provide visible focus styling.
- [x] Reach Execute with valid SQL and Cancel during a gated demo operation;
  verify cancellation still works and unavailable commands remain disabled.
- [x] Verify forward and reverse focus traversal after resize and with an open
  picker. Use Escape to close the overlay and restore focus to its trigger.

## 4. Full acceptance journeys and snapshots

Files: compact integration tests, `tests/snapshot/test_athena.py`, relevant
snapshot host helpers and `tests/snapshot/__snapshots__/test_athena/`.

- [x] Drive 120×40 → 80×24 → 120×40 after typing SQL and selecting non-default
  context values; assert SQL, workgroup/catalog/database and focus survive.
- [x] Parameterize service/Settings coverage from the registry. Require nonzero
  content regions, visible service/source/demo identity and picker Escape.
- [x] Add four full-app Athena goldens (two sizes × carbon/github-light), each
  with a guard for actual rendered editor text. Exercise running/error detail
  access and resize with a live query using controlled in-memory fixtures.
- [ ] Generate only intended goldens, inspect their rendered images and run
  `.venv/bin/pytest tests/snapshot/test_athena.py` without update mode.

## 5. Review, verify and promote

- [x] Update relevant user documentation through the repository docs pipeline.
- [ ] Run affected integration/unit modules, snapshot tier, repository hooks,
  architecture/type checks, documentation gate and package checks.
- [x] Obtain independent diff review; resolve findings and verify affected tests.
- [ ] Commit coherent changes and push `codex/issue-233-compact-athena`.
- [ ] Create/attach the develop PR, pass final checks and merge normally.
- [ ] Promote through a checked develop-to-main PR and required back-merge.
- [ ] Verify final postmerge CI and source parity, delete only the merged ticket
  branch, close #233 with AC evidence and PR links, and mark its board card Done.

## 6. Verification notes

The original full-app editor-height regression failed at 80×24 with zero rows.
Compact chrome now leaves seven editor rows there. Real keyboard tests cover
both traversal directions, gated submission/cancellation, non-default context
preservation through resizing, open-picker Escape, and scrollable error detail.
The error-detail regression initially proved that the app consumed arrow keys
without forwarding them to the scroll container; the page now routes those keys.

Selector sizing uses the actual minimum space needed by the source and three
context controls, plus visible pagination buttons. The existing source-name
snapshot guard caught an overly broad compact breakpoint; its assertion remains
unchanged. Final actual renders match 91 original narrow goldens after removing
report-only SVG identifier suffixes. The remaining picker-open golden intentionally
reclaims editor rows and makes secondary detail scrollable. Four new full-app
goldens cover Carbon and GitHub Light at 80×24 and 120×40, with visible SQL guards.

Independent report-only review found no actionable defects. Demo probes verified
editor height at every terminal height from 24–36 and at 40, and selector layout
at both pagination-dependent width boundaries. Documentation contracts passed
177 tests; the strict site build, 10 end-to-end tests, and local wheel/sdist
content and metadata checks passed. Hosted CI and the protected promotion cycle
remain required before issue closure.
