# 1. Post-Merge Audit Remediation Design

**Date:** 2026-07-30
**Status:** Implemented and merged to `develop`; retained as the historical design record.
**Branch:** `codex/post-merge-audit-remediation`
**Base:** `develop` at `a14bc98fce5847f31199d9d44cc2ff255448e09f`

## 1.1. Purpose

This remediation closes the functional, usability, documentation, and
maintenance gaps found by the post-merge audit of the Glue and Athena
interaction-polish work. It restores the documented runtime keymap contract,
removes command-palette dead ends, eliminates newly introduced theme
duplication, updates every affected canonical document and architecture
artifact, and strengthens tests so the same drift cannot pass again.

The work was completed on a fresh branch from `develop` and merged back into
`develop`; it did not promote `develop` to `main`.

## 1.2. Outcomes

The completed remediation must provide all of the following:

1. A valid `[keybindings]` overlay from `config.toml` changes the live Textual
   bindings and hint legend on the next launch.
2. An empty overlay value disables that action's live binding.
3. An unknown action or unapproved key collision rejects the overlay
   atomically, logs one redacted diagnostic, and retains the complete default
   map.
4. The command palette shows global commands everywhere and service commands
   only where they can execute.
5. Glue and Athena structural pane styling has one owner per rule instead of
   ten identical copies in built-in theme files.
6. Every affected canonical document, published surface, release checklist,
   action/message ledger, and architecture artifact describes the current
   implementation.
7. Documentation checks derive contract inventories and dependency versions
   from source-of-truth files instead of fixed hand-maintained subsets.
8. Full verification passes without relying on automatic test reruns.

## 1.3. Non-Goals

- No new AWS service or write-capable Glue/Athena operation.
- No automatic execution of generated Athena SQL.
- No keymap file watcher or hot reload after startup.
- No redesign of `TableClipboardVM`, Glue/Athena navigation transactions, or
  the existing same-source copy/insert policy.
- No broad theme-system rewrite or conversion of all historical duplicated
  theme rules.
- No merge to `main` and no Pages deployment from this branch.
- No speculative change for the one observed Athena-to-S3 rerun unless a
  retry-disabled reproduction identifies a root cause.

## 1.4. Runtime Keymap Contract

### 1.4.1. Composition

`build_app_context()` will load and copy the configured keybinding overlay,
then construct the runtime-visible `KeymapStore` with that overlay. The same
store must flow into `RootVM`, `HintLegendVM`, `AwsTuiApp`, and
`BindingResolver`.

Overlay construction remains an all-or-nothing startup transaction:

- valid overlay: retain the overlaid store;
- `UnknownAction` or `KeybindingCollision`: log the type and safe diagnostic,
  then construct one default store;
- malformed configuration: retain the existing configuration-load fallback.

There must be no temporary validation store followed by an unconditional
default store.

### 1.4.2. Collision Policy

`KeymapStore` continues to reject duplicate keys unless the pair is explicitly
approved because the actions cannot share the same resolver scope. The new
`glue.copy_table_ref = "y"` default means `pane.copy = "y"` is invalid and
must not be added to the alias allowlist: both actions have registered
app-level handlers.

All current documentation examples will use a collision-free mapping such as
`pane.copy = "ctrl+y"`. Tests must calculate collisions after the complete
overlay is merged, including empty-list disables and Textual key-name
normalization.

### 1.4.3. Binding Installation

`AwsTuiApp` continues to install `BindingResolver.to_textual_bindings()` during
construction. Existing Textual base bindings are preserved according to the
current explicit merge behavior. Bare printable service bindings remain
non-priority so the Athena editor receives text input.

The authoritative integration test must start from a real temporary
`config.toml`, build `AppContext`, construct the app, and prove that:

- the configured key replaces the default;
- the old key no longer dispatches that action;
- an empty list emits no binding;
- invalid overlays restore all defaults;
- the hint legend resolves the same overlaid store;
- a configured key dispatches the registered action exactly once.

## 1.5. Contextual Command Palette

### 1.5.1. Entry Contract

`PaletteEntry` will gain immutable service-availability metadata. An empty
service set means global; a non-empty set contains service IDs such as
`"glue"`, `"athena"`, or `"s3"`. Categories will describe the actual command
family instead of assigning every entry to `"app"`.

The app remains responsible for declaring command metadata and handlers.
`CommandPaletteVM` remains responsible for the visible projection.

### 1.5.2. VMx Selection

The existing VMx `CompositeVM` registry and
`ScoredFilteredCompositeVM` projection are the best-fitting abstractions.
Service availability will become an additional eligibility condition in the
scorer before fuzzy scoring. The app will set the active service context
before opening the palette, and a context change will refresh scores and reset
selection through the existing VM projection.

This design rejects two alternatives:

- register/unregister on every open, which would churn VM children and action
  ownership;
- view-side filtering, which would create a second visible-entry authority
  outside `CommandPaletteVM`.

Global entries remain visible in Settings and service pages. Glue-only and
Athena-only entries disappear outside their matching service. No visible
palette entry may silently no-op because the corresponding page is absent.
Action-level guards remain as defensive runtime checks.

## 1.6. Styling Ownership

The identical 33-line Glue/Athena structural block added to all ten built-in
themes will be removed. Theme-independent layout, border topology, focus
selectors, and token references will move into the `DEFAULT_CSS` of the
widgets that own those elements:

- Glue context and Iceberg framing: Glue widget family;
- Athena context, editor, controls, detail, summary, and table framing:
  Athena widget family.

No Glue widget stylesheet may own Athena selectors, and vice versa. Built-in
themes continue to define color tokens. User replacement themes and
`theme.tcss` overlays continue to layer above widget defaults.

Snapshot coverage must verify all ten built-in themes, narrow layouts, open
pickers, focused borders, and representative custom-theme layering.

## 1.7. Documentation and Three-Surface Parity

### 1.7.1. Canonical Documents

The following sources must be reviewed and updated where applicable:

- `README.md`
- `CHANGELOG.md`
- `docs/index.md`
- `docs/keybindings.md`
- `docs/cookbook.md`
- `docs/connections.md`
- `docs/architecture.md`
- `docs/adding-a-service.md`
- `docs/contract-ledger.md`
- `docs/RELEASING.md`
- the Glue/Athena polish design and implementation records when they make a
  current-state claim rather than preserving historical instructions.

Agent execution reports under `.superpowers/` are transient working state,
not canonical documentation. Git history preserves reports that accompanied
older implementation commits; durable conclusions belong in this design, its
implementation plan, or another indexed document under `docs/`.

The documentation must cover the exact source picker, selector commands,
complete focus order, `ServiceTabStrip`, `TableClipboardVM`,
`CopyTableReferenceRequest`, copy/insert source guards, palette scoping,
startup-only keymap overlays, and valid customization examples.

The action-ID table must include every `KeymapStore.DEFAULT_BINDINGS` key,
including `app.open_settings`, `pane.modal_left`, and `pane.modal_right`, while
clearly separating deferred, widget-local, app-resolved, and palette-only
actions.

The contract ledger must match `uv.lock` and `pyproject.toml`, including
Textual 8.2.8 and Hatchling 1.31.0 with the current build-system lower bound.
Its service-identity, public action, and public message ledgers must include
Athena and `CopyTableReferenceRequest`.

The task report's defect list must restore the missing commit IDs
`3660c39` and `5dffc81`.

### 1.7.2. Architecture Diagram

The landscape architecture master will be revised through the
`architecture-diagram` workflow. The VMx layer must show
`TableClipboardVM`; the message/handoff lane must show
`CopyTableReferenceRequest` and the Glue copy to Athena insert flow.

The diagram must preserve:

- landscape orientation;
- readable labels at the existing output dimensions;
- no box overlap;
- no arrow crossing a box unless that box is the endpoint;
- orthogonal or perpendicularly broken routing where it improves clarity;
- generated SVG and PNG parity with the HTML master.

### 1.7.3. Publication Semantics

`docs/manifest.yaml` remains the single published-page inventory.
`generated/site`, `generated/wiki`, and `mkdocs.yml` remain ignored outputs.
The branch must prove that current canonical documents generate both surfaces
cleanly and that MkDocs builds in strict mode.

The live site and wiki are expected to remain at `main` until normal Gitflow
promotion. This branch must not claim that `develop` content is already live.

## 1.8. Semantic Documentation Tests

Tests will stop treating fixed tuples as complete source inventories.

1. Parse `KeymapStore.DEFAULT_BINDINGS` and require every action ID in the
   documented action ledger.
2. Parse registered public app actions and the explicit palette declaration,
   then require all intended public Glue/Athena actions in the contract ledger.
3. Parse public `*Request` message classes or their exported ledger and require
   exact parity with the documented cross-service message block.
4. Compare dependency versions and lower bounds named in the contract ledger
   with `uv.lock` and `pyproject.toml`.
5. Require `TableClipboardVM`, `CopyTableReferenceRequest`,
   `ContextPicker`, and `ServiceTabStrip` in the appropriate architecture
   prose and diagram groups.
6. Keep existing numbering, link, placeholder, forbidden-link, generated
   parity, and strict-build checks.

These checks validate semantic completeness while the three-surface generator
continues to validate mechanical parity.

## 1.9. Test and Verification Strategy

Implementation follows focused test-driven increments:

1. RED integration tests for config-to-runtime overlay propagation.
2. Keymap implementation and collision-example corrections.
3. RED VM tests for global versus service-scoped palette projections.
4. Palette metadata and active-context implementation.
5. CSS ownership move with snapshot no-update failures before regeneration.
6. Source-derived documentation tests, canonical updates, and diagram
   regeneration.
7. Retry-disabled Athena-to-S3 focused repetitions. If a failure reproduces,
   use systematic debugging to isolate and fix the race before continuing.

The final branch gate is:

- focused keymap, palette, Glue, Athena, and docs tests;
- Athena-to-S3 integration with `--reruns 0`, repeated;
- full non-MinIO suite with reruns disabled;
- full snapshot suite without update mode after canonical regeneration;
- coverage at or above the branch baseline, with no material regression in
  changed modules;
- Ruff check and format check;
- strict mypy;
- architecture layer rules;
- documentation checks and strict MkDocs build;
- diagram render, geometry checks, and visual inspection;
- `git diff --check`;
- clean worktree.

## 1.10. Delivery Structure

The implementation plan will use atomic, reviewable commits in this order:

1. restore runtime keybinding overlays and integration coverage;
2. scope command-palette entries through the existing VMx projection;
3. consolidate Glue/Athena structural CSS and refresh snapshots;
4. strengthen source-derived documentation contract tests;
5. update canonical docs and all publication surfaces;
6. regenerate and verify the architecture diagram;
7. close retry-disabled verification and record final metrics.

No task may mark documentation complete until all manifest pages and all three
generated surfaces have been checked. No branch-completion claim may rely on a
test that passed only after an automatic rerun.

## 1.11. Acceptance Criteria

The design is satisfied only when:

- a real config overlay changes a real app binding and hint;
- the documented remap examples pass collision validation;
- wrong-service palette commands are absent rather than inert;
- the ten themes no longer contain the duplicated operational-pane block;
- public actions, messages, dependency pins, and architecture entities are
  source-derived and documented exactly;
- all canonical manifest pages generate clean site and wiki outputs;
- the updated landscape diagram is readable and geometrically sound;
- full verification passes without reruns;
- the branch contains no unrelated production refactor.

## 1.12. Implementation Record

Task 7 established pre-report head
`35b419259d4978964f3fa1d7ce1c7c963cf4f5e5` on
`codex/post-merge-audit-remediation`, based on
`a14bc98fce5847f31199d9d44cc2ff255448e09f`.

The delivered task commits are:

- planning: `aae1e60`, `f4ce676`;
- Task 1, runtime keymap overlay: `c5663c0`;
- Task 2, contextual palette projection: `cbd5fce`;
- Task 3, operational CSS ownership and focus-selector correction:
  `e3d4867`, `7f80476`;
- Task 4, source-derived documentation contracts and changelog guard:
  `8c3dbb8`, `bada240`;
- Task 5, canonical interaction documentation and corrected smoke guidance:
  `d53eaa4`, `20aa86a`, `f0ec671`;
- Task 6, architecture diagram parity: `7564bbc`;
- Task 7, reproduced Athena teardown-race fix: `35b4192`.

Task 3 used a framework correction: Textual custom variables are source-scoped,
so `ThemeStore` concatenates one packaged `operational-panes.tcss` between
built-in CSS and the user overlay, while replacement themes bypass it. Actual
TCSS metrics are +10/-370 in the ten raw themes and +33/-0 in the shared
operational stylesheet; the original 330-deletion estimate was not forced.

Verification at the pre-report head passed 205 Athena-to-S3 stress tests across
five independent processes with no `R` markers, 315 focused tests, 2,980
unit/integration tests with 9 external-service tests deselected and 85.76%
coverage, 806 snapshot tests with 481 comparisons, and all 9 E2E journeys.
Layers, Ruff, formatting, mypy, strict docs, wiki parity, and diff checks passed.
No snapshot golden changed.

Authored pre-report metrics are 48 files, +3,119/-1,163: runtime Python
+138/-46, tests +511/-71, canonical docs +137/-32, architecture HTML +84/-61,
generated SVG +84/-61 plus one changed PNG, planning/design +2,034/-6,
tracked SDD records +87/-515, and CI +1/-1.

Local `main` and `origin/main` remained
`0b63c4a73f29a7fa58671163492fd3d0d17b2348`. Generated site, wiki, coverage,
and snapshot outputs remained ignored and were not staged. Live publication
still waits for normal promotion to `main`.
