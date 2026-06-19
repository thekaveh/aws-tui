# Modal + Toast Polish — Design Spec

**Date:** 2026-06-19
**Author:** Kaveh (with Claude pair-design)
**Status:** Approved for implementation
**Tracks:** `ConfirmModal`, `ThemePickerModal`, `ServicesMenu` rail + `ServicesHamburger`, `TransfersOverlay` + `TransferRowWidget`, `ToastStack` + `Toast`

## 1. Motivation

The dialogs the user actually sees in v0.7.x (copy/delete confirm modal, theme picker, services rail, transfers overlay, toast stack) read as half-baked: button labels can spill past button borders, the theme picker has zero per-theme CSS and falls back to a bare-terminal frame, and the transfers overlay's progress bars look "awful." This spec defines a focused polish pass that fixes the bugs, introduces a shared modal-frame convention across all 10 themes, and redesigns the transfers row to a card-style layout with full per-transfer information.

The pass is deliberately **conservative on shape, expressive on color**: every theme gets the same modal silhouette (rounded border, consistent padding, fixed button geometry); the theme's `$accent` / `$bg-elev` / `$rule-dim` / `$text` tokens do the personality work.

## 2. Scope

### 2.1. In scope

- `ConfirmModal` (copy + delete; danger + non-danger variants)
- `ThemePickerModal` (full CSS for all 10 themes + preview-on-cursor + Esc-rollback behavior change)
- `ServicesMenu` rail + `ServicesHamburger` (cross-theme audit + selected-row visual-language unification with file panes)
- `TransfersOverlay` + `TransferRowWidget` (card redesign per Option C from brainstorm: left-bar state color, custom Static-based progress bar, bytes + speed + eta)
- `ToastStack` + `Toast` (non-progress: theme-change toast, auth toast, generic notifications)
- Snapshot-test coverage for the surfaces that don't have it today

### 2.2. Out of scope

The following modals are **built but never `push_screen`-ed at runtime** per `CHANGELOG.md [Unreleased] Deferred / v0.8 roadmap` and are therefore not user-visible in v0.7.x. They are not polished in this pass:

- `CommandPalette`, `CrashModal`, `ResumeModal`, `FirstRunModal`, `S3CompatFormModal`, `QuickLook`, `HelpModal`

They keep their current CSS (which has snapshot coverage already). When the M6-deferred wiring lands, a follow-up polish pass should re-evaluate them against the conventions established here.

### 2.3. Decisions already locked in (from the brainstorm)

| Decision | Choice |
|---|---|
| Modal design language | **Conservative** — same rounded shape across all themes; colors do the talking |
| Theme-picker preview | Live preview on cursor move; **Esc rolls back** to the originally-active theme |
| Transfers-row layout | **Card-style** with colored left accent rule (blue=running / green=done / red=failed) |
| Theme-family coverage | All 10 themes (carbon, voidline, lattice, amber, solarized-light, github-light, one-light, nord, dracula, gruvbox-dark) |

## 3. Modal frame primitive (shared across all 10 themes)

A single visual convention every confirm/picker modal honors. Only color tokens vary across themes.

### 3.1. Frame

- Outer container: `border: round $rule-dim`, `background: $bg-elev`, `padding: 1 2`.
- Width: `70` for confirm modals, `44` for the theme picker. `max-height: 80%` to stay usable on small terminals.
- Centered horizontally and vertically (`align: center middle` on the screen).

### 3.2. Title

- One row at the top of the container.
- `color: $accent`, `text-style: bold`.
- No underline rule beneath the title (Option B from the brainstorm — rejected for simplicity).

### 3.3. Body

- Sibling rows below the title; per-row padding `0 1`.
- Field labels in `$accent` (e.g., the "From" / "To" / "Target" labels on confirm-modal path entries).
- Field values inside a small rounded chip: `border: round $rule-dim`, `background: $bg`, `color: $text`.

### 3.4. Footer

- `height: 5`, `align: right middle`, `padding: 1 0 0 0`.
- These figures preserve the post-pass-13 fix in CHANGELOG that resolved the "buttons clipped" bug. Do not regress.

### 3.5. ModalButton

The root cause of the spill bug: `width: 18` fixed + `padding: 0 3` leaves 12 chars for the label, which fits "Confirm" / "Cancel" but clips longer strings ("Authenticate", "Delete all marked"). Fix:

- **`width: auto`** with **`min-width: 14`** — guarantees no clipping while preventing a one-char button.
- `height: 3`, `padding: 0 2` (was `0 3` — three chars of side-padding pushes labels off-center on narrow buttons).
- Margin between buttons: `0 1` (unchanged).
- `content-align: center middle`, `text-style: bold` (unchanged).
- Focus + hover treatments unchanged — they work today.

`ModalButton.DEFAULT_CSS` is updated to reflect this; theme `.tcss` files that previously hard-coded `width: 18` get the corresponding update.

## 4. ConfirmModal (copy + delete)

### 4.1. Fine-tuning

- Apply the §3 frame.
- **Path-value chips:** add `text-wrap: nowrap` so long paths never push the modal wider than 70 cols. Truncate with `…` if the path exceeds the chip's inner width. For multi-path lists, cap at 5 rows and add a `+N more` tail line.
- **Body lines:** keep `color: $text`, padding `0 1 1 1`.
- **Danger variant** (delete): keep the existing `solid $danger` border and `$danger`-colored title. Add one polish: the right-aligned footer shifts the Confirm button right by +2 cols for danger modals (a small UX guardrail so a reflex Enter doesn't land where the cursor was before the modal opened). Implementation: `.modal-footer.-danger > ModalButton.-danger { margin-right: 2; }`.
- **Hover transition:** add `transition: background 80ms` on buttons if Textual's stylesheet supports it for the property (verify during implementation; drop the line if it errors).

### 4.2. No behavior change

Keyboard wiring (Enter commits focused, Esc cancels, Tab/Shift+Tab swap focus) stays as-is. This is purely a CSS pass for this modal.

## 5. ThemePickerModal

### 5.1. Add full CSS (10 themes)

The widget currently ships with inline `DEFAULT_CSS` only — no `.tcss` file references it. **Every theme gains a `ThemePickerModal` block** matching the §3 frame primitive plus per-row treatment:

- `_ThemeRow` default: `color: $text` for the theme name; `color: $accent` for the active-marker glyph (●).
- `_ThemeRow.-cursor`: `background: $bg-sel`, `color: $accent-soft`, `text-style: bold`. Matches the file-pane cursor pattern so the visual language is consistent.

### 5.2. Preview-on-cursor

New behavior the user explicitly requested: as the user moves the cursor through the theme list, the active theme switches live so they see the candidate before committing.

- `ThemePickerVM` gains `preview_command: RelayCommandOf[str]` (separate from `pick_theme_command`).
- `preview_command.execute(theme_name)` calls the existing `RootVM.switch_theme(theme_name)` pipeline. The entire app repaints — brand banner, pane chrome, modal itself.
- `ThemePickerModal._move_by(delta)` calls `self._picker.preview_command.execute(new_theme_name)` after updating the cursor index.
- **Semantic distinction:** `preview_command` is for the in-modal candidate; `pick_theme_command` is for the final commit. Future config-persistence work hangs off `pick_theme_command` only.

### 5.3. Esc rollback

- `ThemePickerModal.__init__` captures `self._original_theme = picker.active_theme`.
- `action_close` becomes:

  ```
  if current_previewed != self._original_theme:
      self._picker.preview_command.execute(self._original_theme)
  self.dismiss(None)
  ```

- Enter still commits via `pick_theme_command` (unchanged).

### 5.4. Performance flag

Cursoring fast through 10 themes will repaint 10 times. Textual's `refresh_css(animate=False)` is sub-frame, so this should be smooth in practice. If implementation reveals flicker, add a 60 ms debounce on `_move_by` before the preview fires. Decide after seeing it; don't pre-optimize.

## 6. ServicesMenu rail + ServicesHamburger

This is largely an audit + tightening pass — the rail already has theme rules.

### 6.1. Cross-theme audit

For each of the 10 themes, confirm these selectors exist with consistent token usage:

- `ServicesMenu`
- `ServicesMenu > .service-item`
- `ServicesMenu > .service-item.-selected`
- `ServicesMenu > .service-item.-focused`
- `ServicesMenu > .service-item.-dimmed`
- `ServicesMenu > .title`
- `ServicesHamburger`
- `ServicesHamburger:hover`

Fix any drift — e.g., a theme using a literal hex where it should reference `$accent`.

### 6.2. Selected-row visual language

Standardize on the file-pane cursor pattern: `background: $bg-sel`, `color: $accent-soft`, `text-style: bold`, leading `▌` accent-color bar. The rail and the file panes then read as siblings rather than as parallel-but-different surfaces.

### 6.3. Hamburger

Standard rounded chip: `border: round $rule-dim`, `background: $bg-elev`, `color: $accent`. Hover swaps to `background: $accent`, `color: $bg`. Carbon already does this — propagate to the other nine themes.

### 6.4. No layout changes

Collapsed-state width stays at `3` (per the post-pass-13 fix in CHANGELOG). Expanded width stays at `16`. No regression desired.

## 7. TransfersOverlay + TransferRowWidget (Option C: card redesign)

### 7.1. Overlay frame

- Width unchanged: `44`. Top-right dock unchanged.
- Title becomes `▌ TRANSFERS` (was plain `Transfers`). Leading `▌` in `$accent` matches the visual-language used on modal titles and the file-pane selected-row marker.

### 7.2. Row layout (5 lines per row)

```
▌ <name>                                 ↑ 62%
  → s3://bucket/dst/
  ▰▰▰▰▰▰▱▱▱▱                          [✕]
  1.2 GB / 1.9 GB        12.5 MB/s · 0:55
```

- **Leading `▌` accent bar:** color reflects state (`$accent` running, `$success` done, `$danger` failed, `$warning` paused, `$text-muted` cancelled).
- **State word** ("running" / "done" / "failed" / etc.) in the top-right of line 1, same state color.
- **Bar (line 3):** drop Textual's built-in `ProgressBar` and render a 10-cell Static using `▰` (filled, state color) + `▱` (empty, `$rule-dim`). Recomputed on every `model` property change.
- **Cancel chip (line 3, right):** `[✕]` — `background: $bg-elev`, `border: round $rule-dim`, `color: $text`; hover swaps to `background: $danger`, `color: $bg`. When the row is finished, opacity drops to 30 % and clicks are no-ops.
- **Meta line (line 4):** `humanize_bytes(done) / humanize_bytes(total)` on the left; `<speed>/s · <eta>` on the right, both in `$text-muted`. If `bytes_total` is `None` (indeterminate stream), the line reads `<done> · streaming…`.
- **Inter-row separator:** none — the colored left bar is the visual separator.

### 7.3. State machine and visual mapping

| State | Left bar | State word | Bar | Cancel chip |
|---|---|---|---|---|
| `RUNNING` | `$accent` | `↑ N%` (`$accent`) | `▰`s in `$accent` | enabled |
| `PAUSED` | `$warning` | `⏸ N%` (`$warning`) | `▰`s in `$warning` | enabled |
| `COMPLETED` | `$success` | `✓ done` (`$success`) | all `▰` in `$success` | dim 30 % |
| `FAILED` | `$danger` | `✗ failed` (`$danger`) | partial bar in `$danger` | dim 30 % |
| `CANCELLED` | `$text-muted` | `⊘ cancelled` (`$text-muted`) | partial bar in `$text-muted` | hidden |

### 7.4. Speed + eta

- New private `TransferVM._speed_window: collections.deque[tuple[float, int]]` capped at 5 seconds of `(timestamp, bytes_done)` samples (deque maxlen plus timestamp prune on each insert).
- `apply_update` appends a sample.
- `current_speed` property: `(last_bytes - first_bytes) / (last_ts - first_ts)` if the window has ≥ 2 samples and the span ≥ 250 ms; else `None`.
- `current_eta` property: `(bytes_total - bytes_done) / current_speed` if `bytes_total` and `current_speed`; else `None`.
- Both rendered by `TransferRowWidget` via `humanize_bytes` and an `m:ss` / `mm:ss` / `h:mm:ss` formatter.
- Tests use a fixture clock (monotonic time injected via constructor) so speed assertions are deterministic.

### 7.5. Empty state

`TransfersOverlay` already hides itself via `.-hidden` when there are no visible rows. Unchanged.

## 8. ToastStack + Toast (non-progress toasts)

These are theme-change / auth / generic notifications — distinct from the transfers overlay's progress toasts.

### 8.1. Per-toast frame

- `border: round $rule-dim`, `background: $bg-elev`, `padding: 0 1`. The current widget has padding but no border, so toasts look like floating text rather than discrete units.
- `height: auto` (unchanged — toasts grow to fit their text).

### 8.2. Level colors

| Level | Border | Title color |
|---|---|---|
| `.-info` | `$rule-dim` | `$text` |
| `.-success` | `$success` | `$success` |
| `.-warning` | `$warning` | `$warning` |
| `.-error` | `$danger` | `$danger` |

### 8.3. Action chip

When a toast carries an `action_label` (e.g., `[authenticate]`), it renders as a small inline chip: `color: $accent`, `text-style: bold`. The existing `Toast.render()` already emits this as Rich markup — we keep it there; the chip styling is purely a `color` override in the per-theme `.tcss`.

### 8.4. Stack ordering and lifecycle

Unchanged. Newest on top, auto-dismiss timers handled by `ToastStackVM` (the fix for which already landed in Pass 2 of the overnight-maintenance loop).

## 9. Snapshot test coverage

Three new snapshot apps + tests to cover the surfaces that have zero coverage today, plus refresh of the existing confirm-modal snapshots:

### 9.1. New snapshot apps

- `tests/snapshot/apps/theme_picker.py` — composes a real `ThemePickerVM` with cursor on row 4 (`amber`). One test parametrized over all 10 themes → 10 new goldens.
- `tests/snapshot/apps/transfers.py` — composes a `TransfersVM` with three transfers: one `RUNNING` at 62 %, one `COMPLETED` in linger, one `FAILED`. Parametrized over all 10 themes → 10 new goldens.
- `tests/snapshot/apps/toast.py` — composes a `ToastStackVM` with one INFO toast (theme-change) plus one ERROR toast (with action chip). Parametrized over all 10 themes → 10 new goldens.

### 9.2. New test files

- `tests/snapshot/test_theme_picker.py`
- `tests/snapshot/test_transfers.py`
- `tests/snapshot/test_toast.py`

### 9.3. Refresh existing snapshots

Three existing snapshot groups will need a bulk refresh after the polish lands:

- `tests/snapshot/test_modals/test_confirm_modal_copy_paths[*]` — 10 themes.
- `tests/snapshot/test_modals/test_confirm_modal_danger[*]` — 10 themes.
- `tests/snapshot/test_main_screen[*]` — 10 themes (the services rail + hamburger polish is rendered as part of the main-screen composition).

That's 30 refreshed goldens. Five other modal snapshot groups (`command_palette`, `crash_modal`, `first_run_modal`, `quick_look`, `resume_modal`) and the `test_pane_states` suite are explicitly out-of-scope and should **not** change. The visual diff is the gate — any out-of-scope drift is a regression introduced by the polish.

Run: `uv run pytest tests/snapshot --snapshot-update`, then visually inspect the diff in `snapshot_report.html` against the design described in this spec.

### 9.4. Net snapshot delta

- **New** goldens: **30** (theme picker × 10 + transfers × 10 + toast × 10).
- **Refreshed** in-scope goldens: **30** (confirm-copy × 10 + confirm-danger × 10 + main-screen × 10).
- **Out-of-scope, must not change:** **74** (5 deferred modals × 10 = 50, plus `test_pane_states` = 24).

## 10. Implementation outline (for the implementation plan)

Suggested commit chunking. Each chunk is independently green (ruff + mypy + pytest + snapshots).

1. **`ModalButton` width fix + `ConfirmModal` CSS sweep.** Widget DEFAULT_CSS change + per-theme `.tcss` updates across all 10 themes. Refresh 20 confirm-modal snapshots.
2. **`ThemePickerModal` CSS in all 10 themes** (no behavior change yet). New snapshot app + 10 goldens.
3. **`ThemePickerVM.preview_command` + Esc-rollback wiring.** Test the live preview without the CSS (validates behavior independent of paint). Refresh theme-picker snapshots if the cursor visual changes.
4. **`ServicesMenu` + `ServicesHamburger` cross-theme audit.** No new snapshots; existing main-screen snapshots regenerate as part of the bulk refresh in step 6.
5. **`TransfersOverlay` card redesign + `TransferVM` speed/eta.** New widget code + new VM properties + new snapshot app + 10 goldens. Bulk of the work.
6. **`ToastStack` polish.** Per-theme borders + level colors + action-chip styling. New snapshot app + 10 goldens. Final main-screen snapshot refresh if hover styles changed.

A separate **chunk 0** establishes the snapshot harness scaffolding (or refactors the existing harness to support the three new apps) — the harness pattern is well established under `tests/snapshot/apps/`, so this should be a small step.

## 11. Acceptance criteria

The PR is acceptable when **all** of the following are true:

1. Every modal listed in §2.1 In Scope renders without label spill on any theme at any reasonable terminal width (≥ 80 cols).
2. Every theme has a `ThemePickerModal` block in its `.tcss` file referencing `$bg-elev`, `$rule-dim`, `$accent`, `$text`, `$bg-sel`, `$accent-soft` tokens.
3. Cursoring through the theme picker live-previews the candidate theme; Esc restores the originally-active theme; Enter commits the cursored theme.
4. `TransfersOverlay` rows render the card layout described in §7.2 with state-colored left bar, custom-styled progress bar, bytes/speed/eta meta row, and `[✕]` cancel chip.
5. `ToastStack` toasts have visible per-theme borders with level-appropriate accent color.
6. All 10 themes pass the new and existing snapshot tests.
7. Out-of-scope snapshots (74 goldens listed in §9.4) are unchanged.
8. `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`, `bash scripts/check-layers.sh`, and `uv run pre-commit run --all-files` all pass.
9. The PR's `CHANGELOG.md [Unreleased] ### Changed` section documents the modal-frame convention and the theme-picker preview behavior; `### Fixed` lists the button-spill, ThemePicker-missing-CSS, and TransfersOverlay-bar-styling resolutions.

## 12. Risks

- **Per-theme drift:** 10 themes × ~50 CSS rules touched is real risk surface. Mitigation: every theme has at least one snapshot exercising every new selector; bulk visual diff is the gate.
- **Custom progress bar:** dropping `textual.widgets.ProgressBar` for a Static-rendered cell sequence means we own the rendering. Cost: ~30 LOC + tests. Benefit: full theme control. Net positive given the "awful" critique today.
- **Speed/eta complexity:** the rolling window is a small new state machine on `TransferVM`. If implementation reveals subtle flake (clock skew under load), the fallback is to defer speed/eta and ship bytes-counter-only as a v1 with `current_speed` / `current_eta` returning `None`; the row layout already handles the `None` case gracefully.
- **Live preview flicker:** unlikely (Textual `refresh_css(animate=False)` is sub-frame), but the §5.4 debounce is the bail-out.
- **Snapshot churn:** every theme gets new + refreshed goldens. Reviewer needs to visually diff. Mitigation: PR description includes before/after composites for one representative theme per family (carbon, amber, voidline, solarized-light) so the reviewer can ratify the design once and accept the rest as derivative.

## 13. References

- Brainstorm session: this spec is the output of the 2026-06-19 brainstorming run; the Option A modal direction, Option C transfers-row layout, and Esc-rollback behavior were each user-chosen in the visual companion.
- `CHANGELOG.md [Unreleased]` — the source-of-truth for what's already deferred and what's already shipped in v0.7.x.
- `docs/architecture.md` §2 — layer rules; the polish stays inside the View layer (`ui/widgets/` + `ui/themes/`) and the chrome VM layer (`vm/chrome/theme_picker_vm.py` gets the new `preview_command`, `vm/file_manager/transfer_vm.py` gets the speed-window). No domain / infra changes.
- `docs/keybindings.md` — no key remapping; existing `t` opens picker, `Shift+T` cycles. No new bindings required.
