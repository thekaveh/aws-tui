# Task 1 Report: Restore the Runtime Keymap Overlay

## Status

Implemented on `codex/post-merge-audit-remediation`. A valid
`Config.keybindings.bindings` overlay now becomes the `AppContext` runtime
`KeymapStore`; collision and unknown-action failures still atomically replace
the entire runtime map with defaults.

## Baseline

```console
$ git branch --show-current
codex/post-merge-audit-remediation

$ git status --short

$ git merge-base HEAD develop
a14bc98fce5847f31199d9d44cc2ff255448e09f

$ git rev-parse main origin/main
0b63c4a73f29a7fa58671163492fd3d0d17b2348
0b63c4a73f29a7fa58671163492fd3d0d17b2348
```

The starting worktree was clean and the requested branch and ancestry values
matched the task brief exactly.

## Implementation

- `src/aws_tui/composition.py`
  - Retains a successfully validated `KeymapStore(overlay=keybindings_overlay)`
    as the context's runtime store.
  - Retains the existing `KeybindingCollision` and `UnknownAction` warning and
    uses `KeymapStore()` only as the complete atomic fallback.
  - Removes the obsolete deferred-overlay comment and unconditional default
    store creation.
- `tests/unit/test_composition_initial_theme.py`
  - Replaces the former deferred-overlay regression with runtime remap and
    empty-overlay disable coverage.
  - Verifies the legend shares the same runtime store.
  - Strengthens collision fallback to compare the complete map, and adds the
    equivalent unknown-action atomic-fallback assertion.
- `tests/integration/test_keybinding_wiring.py`
  - Verifies config overlay values become installed Textual bindings with the
    expected `ctrl+y` priority, remove `c`, and omit disabled delete dispatch.
  - Verifies one `ctrl+y` key press dispatches the registered action exactly
    once through the live application.

No hot reload, global binding expansion, or unrelated refactor was added.

## TDD Evidence

The local shell emitted an unrelated `.zshenv` warning about a missing
`/tmp/vmx-cargo-182/env` before commands. It did not affect any exit status.

### RED

Tests were changed before production code.

```console
$ uv run pytest tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py -q
..FF.........RRFRRF.
4 failed, 12 passed, 4 rerun in 5.82s
```

The four expected failures were:

- runtime `pane.delete` resolved to `('d',)` rather than configured `('x',)`;
- an empty `pane.delete` overlay resolved to `('d',)` rather than `()`;
- the configured `ctrl+y` Textual `pane.copy` binding was absent;
- pressing `ctrl+y` did not dispatch `pane.copy`.

These failures showed that `build_app_context()` was still returning the
default runtime `KeymapStore` after only validating the overlay.

### GREEN

After the minimal composition-root change:

```console
$ uv run pytest tests/unit/test_composition_initial_theme.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_bindings.py tests/integration/test_keybinding_wiring.py -q
81 passed in 1.01s
```

After applying repository formatting to the changed integration test, the
focused suite was rerun:

```console
$ uv run pytest tests/unit/test_composition_initial_theme.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_bindings.py tests/integration/test_keybinding_wiring.py -q
81 passed in 1.33s
```

## Static Verification

```console
$ uv run ruff check src/aws_tui/composition.py tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py
All checks passed!

$ uv run ruff format --check src/aws_tui/composition.py tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py
3 files already formatted

$ uv run mypy src/aws_tui/composition.py
Success: no issues found in 1 source file

$ git diff --check
```

All final verification commands exited zero. Before formatting,
`ruff format --check` correctly identified the newly added integration-test
line wrapping; `uv run ruff format tests/integration/test_keybinding_wiring.py`
corrected it before the final GREEN run.

## Self-Review

- Valid overlays are retained as the runtime `KeymapStore`, so the app,
  resolver, and hint legend share the configured map.
- The exception handler catches exactly `KeybindingCollision` and
  `UnknownAction`, logs the existing event and fields, then creates a fresh
  default store. Tests compare `all()` to `KeymapStore().all()` for both
  collision and unknown-action cases, proving fallback cannot retain partial
  overlay state.
- The integration regression confirms the old `c` binding is absent, the
  disabled delete binding is absent, `ctrl+y` has priority `True`, and the
  configured action dispatches once at runtime.
- No production binding definitions, refresh mechanisms, or non-keymap
  composition behavior changed.

## Concerns

None for this task. The recurring `.zshenv` startup warning is external to the
repository and did not affect command results.
