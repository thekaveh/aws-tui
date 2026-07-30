# Task 7 Report: Glue and Athena Interaction Polish

## Scope

- Branch: `codex/glue-athena-interaction-polish`
- Exact branch point: `b92ad89f68dd19ca61cd567ce0f82b5379fb0499`
- Integration target: `develop` only; `main` remains out of scope.
- Final production/test state and CI remediation are summarized below.

## Verification

| Gate | Result |
|---|---|
| VMx smoke | 5 passed |
| Glue, Athena, and chrome VM suites | 547 passed |
| Glue/Athena/nav UI suites | 82 passed |
| Shared context picker, source header, and service tab strip | 19 passed |
| Focused lifecycle, navigation, clipboard, source-swap, routing, hints, keybinding, and S3 handoff integration matrix | 153 passed |
| Documentation tests | 73 passed |
| End-to-end user journeys | 9 passed |
| Mypy | Clean across 161 source files |
| Ruff check | Clean |
| Ruff format | 407 files already formatted |
| Architecture layers | Clean |
| Full unit and integration suite | 2979 passed, 9 deselected |
| Full-suite coverage | 85.76% |
| Full snapshot suite | 806 passed; 481 snapshot comparisons |
| Hostile snapshot environment probe | 7 passed; 4 snapshot comparisons |
| Diff whitespace | `git diff --check` clean |

The exact detached baseline is 2789 passed, 9 deselected at 85.67%. The final
delta is +190 passing tests and +0.09 percentage points. The final full run
needed no reruns.

## LOC

Generated `.raw` snapshots are excluded from authored LOC. `src/aws_tui/app.py`
is counted as UI/view production.

| Category | Additions | Deletions | Net |
|---|---:|---:|---:|
| VM production | 253 | 1 | +252 |
| UI/view production | 2097 | 379 | +1718 |
| Other production | 102 | 7 | +95 |
| Non-generated tests | 2906 | 78 | +2828 |
| Generated snapshots, excluded (237 changed files) | 24265 | 22489 | +1776 |

No file-level rename or move is detected from the exact branch point. Internal
replacement churn is treated as neutral, and measured deletion savings are
**zero**. This is net-new feature work.

VMx composition avoided a second focus discriminator/widget index, bespoke
observable clipboard storage, an untyped copy callback, duplicate Athena
quoting, and custom command lifecycle/disposal. These are avoided mechanisms,
not deleted LOC.

Task 7's initial snapshot refresh is commit `2f42a6e`: 282 generated files with
26062 additions and 25936 deletions, plus one authored snapshot harness file
with 12 additions and 5 deletions. Review correction `177e47f` canonicalizes
color rendering and reactive capture; that commit changes 371 generated files
with 34528 additions and 34073 deletions and five authored test/harness files
with 116 additions and 18 deletions. Because later regeneration can reverse
earlier branch-local churn, the final branch-point table above remains the
authoritative LOC result.

The final snapshot suite owns 481 comparisons. Of those, 237 generated files
differ from the branch point; comparison count and changed-file count are not
interchangeable.

## Review Remediation

- `67ffc54` adds an immediate rapid Glue view-switch regression. It failed RED
  when synchronous focus projection was replaced with
  `call_after_refresh(...)`, then passed with the production synchronous path;
  the full Glue page unit module passed 24 tests at that review point.
- `7ff0b1b` adds an empty teardown-ring regression after the final coverage
  gate exposed a deferred Glue focus callback observing no mounted targets.
  The view now returns without violating the VM selector's intentional
  non-empty precondition; the final Glue page module passes 25 tests.
- `3691ec0` restores a source picker to its page's authoritative source when a
  profile disappears before the asynchronous app transaction accepts it. The
  integration regression covers the real picker event and confirms that the
  picker label, active connection, mounted page, and service calls remain
  unchanged. Independent re-review approved the remediation with no findings.
- The PR's first Linux E2E run exposed a stale journey assertion that rendered
  the `ServiceSourceHeader` container directly after `9277a6a` changed EMR to
  the compact passive header. The journey now queries the visible
  `.service-source-value` child; all nine E2E journeys pass locally.
- The refreshed cross-platform matrix exposed that routing `Shift+S` through
  the exact-picker transaction had lost the established one-profile remount
  behavior. A shared validated rebuild transaction now keeps direct selection
  idempotent while `Shift+S` still refreshes the current service. The regression
  now fixes its candidate set, activates `dev` explicitly, and awaits the app
  action, so it is independent of caller AWS profiles and event-loop timing.
- Windows/Python 3.11 exposed that a unit test combined the shared picker's
  asynchronous Textual message delivery with Athena's handler-to-VM routing.
  Picker emission remains covered by its keyboard and click tests; the Athena
  test now invokes `ContextPicker.Changed` at the page-handler boundary and
  drains the lifecycle worker it creates, removing scheduler dependence without
  weakening either contract.
- `177e47f` introduces a shared autouse snapshot environment fixture using
  pytest `monkeypatch`. It removes `NO_COLOR`, `CLICOLOR`, and
  `CLICOLOR_FORCE`, sets `TERM=xterm-256color`, and removes Athena-only setup.
  Three hostile caller environments failed before that fixture and now produce
  byte-identical representative output.
- The demo Iceberg harness no longer calls `GlueIcebergView._refresh()`.
  It selects through the public VM action and waits for public VM state, table
  row count, and active-tab rendering. A policy test prevents a direct private
  refresh call from returning.
- A canonical update regenerated all affected goldens. The canonical no-update
  run passed all 806 snapshot tests and all 481 comparisons. A separate
  no-update probe under `NO_COLOR=1 CLICOLOR=0 CLICOLOR_FORCE=0 TERM=dumb`
  passed 7 tests and 4 comparisons.
- Rendered PNGs were visually inspected for all ten Athena themes, Athena and
  Glue narrow layouts, both open context pickers, and the EMR page. Theme color,
  borders, picker bounds, text legibility, and compact source-header layout
  were intact without clipping or overlap.

## Defects Found

Verification exposed five production defects and one stale E2E assertion, all
fixed before merge:

- `5a89d14 fix(ui): settle Glue focus during view switches`
- `9277a6a fix(ui): preserve compact EMR source identity`
- `7ff0b1b fix(ui): ignore empty teardown focus rings`
- `3691ec0 fix(ui): restore rejected source selections`
- `fix(ui): preserve one-profile source rebuilds`
- `test(e2e): read compact EMR source identity`

The first full coverage attempt exposed the EMR regression and ended with 1
failed, 2974 passed, 9 deselected, 2 rerun at 85.70%. After the narrow fixes,
the branch reached a clean intermediate full run. The pre-PR gate then exposed
the empty teardown ring, and independent review found the rejected-source
projection issue. Both failed focused RED tests before remediation. The final
exact-head full run reached the result above without reruns.

The initial app-wide snapshot run had 282 expected mismatches because the wider
service rail changed shared chrome and the EMR source header had inherited an
unintended tall picker. After the EMR correction and explicit regeneration,
the first no-update full snapshot suite passed. Review then exposed inherited
terminal-color sensitivity and a private refresh call in the harness; the
canonical regeneration and public reactive wait above supersede that first
refresh.

## Documentation

- `docs/keybindings.md` documents bordered selectors, named commands,
  `Shift+S`, complete forward/reverse rings, copy/direct-query/insert paths,
  source mismatch refusal, and editor-safe printable binding priority.
- The approved design contains the final VMx responsibility ledger, public API
  choices/rejections, retained Textual concerns, exact metrics, and zero-savings
  statement.
- The approved plan has all completed steps checked and its stale nonexistent
  clipboard test path replaced with the actual focused integration matrix.
- `CHANGELOG.md` records the interaction polish under Unreleased and now states
  that entries may reside on `develop` before promotion to `main`.

## Concerns

- Snapshot churn is intentionally large because the service rail width is
  app-wide and color output is now canonical. It is generated evidence, not
  authored implementation LOC.
- OS clipboard delivery remains terminal-dependent and best effort by design;
  the VMx-backed typed in-app clipboard is authoritative.
