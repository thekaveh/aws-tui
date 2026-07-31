# Post-Merge Audit Remediation Report

## Scope

- Branch: `codex/post-merge-audit-remediation`
- Base: `develop` at `a14bc98fce5847f31199d9d44cc2ff255448e09f`
- Exact pre-report head: `35b419259d4978964f3fa1d7ce1c7c963cf4f5e5`
- Implemented: runtime keymap overlays, contextual command-palette projection,
  operational CSS ownership, source-derived documentation contracts, canonical
  documentation parity, architecture diagram parity, and the reproduced
  Athena-to-S3 teardown race fix.
- Main comparison: local `main` and `origin/main` were both
  `0b63c4a73f29a7fa58671163492fd3d0d17b2348` before Task 7. This branch did not
  switch, update, merge, or push either ref.

## Runtime Corrections

- `build_app_context()` now carries the configured keybinding overlay into the
  live `KeymapStore`, Textual bindings, resolver, and hint legend. Unknown
  actions and unapproved collisions reject the overlay atomically, emit one
  redacted diagnostic, and retain the complete default map. Empty values
  disable their actions.
- Palette entries carry immutable service availability. The existing VM
  projection keeps global commands visible everywhere and excludes Glue,
  Athena, or S3 commands outside their executable service context.
- Textual custom variables are source-scoped. The implemented framework
  correction therefore uses one packaged `operational-panes.tcss`, which
  `ThemeStore` concatenates after built-in theme CSS and before the user
  overlay. Replacement themes bypass that packaged layer. The ten raw themes
  retain color tokens and no longer duplicate operational pane selectors.
- The retry-disabled stress run reproduced a queued
  `AthenaHistoryView._refresh` during Textual subtree pruning. Descendants had
  already detached, so `ResourceListPane.replace` raised `NoMatches` and the
  handoff worker was cancelled. A deterministic RED lifecycle regression now
  proves pruning views ignore queued refreshes; `_refresh` returns while the
  view is `_pruning`.

## Documentation and Three-Surface Parity

Canonical changes cover `README.md`, `CHANGELOG.md`, release guidance,
service-extension guidance, architecture, connections, contract ledger,
cookbook, index, and keybindings. Contract tests derive keymap actions, public
messages, app/palette actions, and dependency versions from source instead of
fixed subsets.

The architecture HTML master, generated SVG, and generated PNG include
`TableClipboardVM`, `CopyTableReferenceRequest`, and the typed Glue-to-Athena
clipboard flow. `make docs-check` passed the source checks and strict MkDocs
build; `make docs-wiki` regenerated and checked wiki parity. Generated site and
wiki outputs remain ignored. Live site/wiki publication still waits for normal
promotion to `main`.

## VMx Decisions

`ScoredFilteredCompositeVM` remains the command-palette projection. Service
availability is an eligibility condition before fuzzy scoring, and active
service changes refresh the existing projection and selection. No parallel
visible-entry state authority was added in the view, and palette children are
not churned through register/unregister cycles on each open.

## Verification

The initial exact stress command exposed one automatic rerun on process 2:
`40 passed, 1 rerun in 42.53s`. Execution stopped during process 3, systematic
debugging captured the first unmasked traceback with `--force-reruns 0`, and the
new deterministic regression was observed RED before the source fix and GREEN
after it.

Final required results at `35b419259d4978964f3fa1d7ce1c7c963cf4f5e5`:

| Command | Result |
| --- | --- |
| `for run in 1 2 3 4 5; do uv run pytest --reruns 0 tests/integration/test_athena_s3_handoff.py -q; done` | Five independent processes, each `41 passed`; 205 total passes, no `R` markers |
| `uv run pytest --reruns 0 tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py tests/unit/vm/chrome/test_command_palette.py tests/integration/test_command_palette_wiring.py tests/unit/ui/test_themes.py tests/docs -q` | `315 passed` |
| `uv run pytest --reruns 0 tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml` | `2980 passed, 9 deselected`; 85.76% coverage |
| `uv run pytest --reruns 0 tests/snapshot -v` | `806 passed`; 481 snapshot comparisons; no update mode |
| `uv run pytest --reruns 0 tests/e2e -v` | `9 passed` |
| `scripts/check-layers.sh` | `layer rules clean` |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 408 files already formatted |
| `uv run mypy src` | No issues in 161 source files |
| `make docs-check` | Source checks clean; strict MkDocs build passed |
| `make docs-wiki` | Wiki generation and parity check passed |
| `uv run pytest tests/docs -q` | `77 passed` |
| `git diff --check` | Clean |

Coverage lines for the requested sensitive surfaces were
`composition.py` 92%, `command_palette_vm.py` 82%,
`athena/history_view.py` 84%, `athena/page.py` 78%,
`glue/detail_rows.py` 97%, and `glue/page.py` 90%. Documentation helpers were
validated by the focused docs tests plus both three-surface build gates.

## Metrics

Authored numstat from `a14bc98fce5847f31199d9d44cc2ff255448e09f`
through pre-report head `35b419259d4978964f3fa1d7ce1c7c963cf4f5e5`:

| Category | Additions | Deletions | Notes |
| --- | ---: | ---: | --- |
| Runtime Python | 138 | 46 | Includes the Task 7 lifecycle guard |
| Ten raw built-in theme files | 10 | 370 | Actual raw-theme count; not the original 330-line estimate |
| Packaged `operational-panes.tcss` | 33 | 0 | Shared operational rules |
| Tests | 511 | 71 | Includes the deterministic teardown regression |
| Canonical docs | 137 | 32 | README, changelog, and canonical `docs/` pages |
| Architecture HTML source | 84 | 61 | Landscape master |
| Generated architecture SVG | 84 | 61 | Source-parity artifact |
| Generated architecture PNG | binary | binary | 125,122 to 148,500 bytes |
| Planning and design records | 2,034 | 6 | Tracked `docs/superpowers` records |
| Tracked SDD records | 87 | 515 | Existing audit task records |
| CI workflow | 1 | 1 | Docs contract adjustment |
| Snapshot goldens | 0 | 0 | No files changed |

Overall pre-report shortstat: `48 files changed, 3119 insertions(+), 1163 deletions(-)`.

## Residual Risk

No reproducible product issue remained after the lifecycle fix and all final
required gates passed without an `R` marker. Harness note: in
`pytest-rerunfailures` 16.4, an item-level `flaky(reruns=2)` marker takes
precedence over global `--reruns 0`; `--force-reruns 0` was therefore used for
the unmasked diagnostic and RED/GREEN proof. The final required commands still
showed first-attempt passes only.
