# Modal + Toast Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the user-visible dialogs (confirm modal, theme picker, services rail, transfers overlay, toast stack) so labels fit their buttons, the theme picker is themed across all 10 themes with live preview-on-cursor and Esc rollback, and the transfers overlay's progress rows redesigned as cards with state-colored left bars and bytes/speed/eta meta. The "design language" is Option A from the brainstorm — same rounded shape across all 10 themes; only color tokens swap.

**Architecture:** Pure View-layer + small VM additions. The shared modal frame primitive (rounded `$rule-dim` border, `$bg-elev` background, `1 2` padding, fixed footer geometry) is the contract every confirm/picker modal honors via per-theme `.tcss` rules. `ModalButton` becomes `width: auto; min-width: 14` instead of `width: 18` to fix label spill. `ThemePickerVM` gains a `preview_command` distinct from `pick_theme_command` so live preview and final commit are semantically separate. `TransfersOverlay` drops Textual's `ProgressBar` for a custom 10-cell Static-rendered bar so we own the per-state coloring; `TransferVM` gains a rolling 5-second speed window for the new bytes/speed/eta meta line. Snapshot harness gets three new apps (theme picker, transfers, toast) — each parametrized across all 10 themes via the existing `THEMES` constant in `tests/snapshot/conftest.py`.

**Tech Stack:** Textual `.tcss`, VMx VMs/RelayCommands, `pytest-textual-snapshot`, ruff + mypy + pre-commit gates per the existing CI matrix.

## Global Constraints

- **Branch:** `polish/modal-toast-2026-06-19` (already created and pushed; spec committed at `360399d`).
- **Themes covered:** all 10 — `carbon`, `voidline`, `lattice`, `amber`, `solarized-light`, `github-light`, `one-light`, `nord`, `dracula`, `gruvbox-dark` (verbatim from `tests/snapshot/conftest.py::THEMES`).
- **Tokens you may use:** `$bg`, `$bg-elev`, `$bg-sel`, `$text`, `$text-muted`, `$text-dim`, `$accent`, `$accent-soft`, `$accent-hot`, `$success`, `$warning`, `$danger`, `$rule-dim`, `$rule-accent`. Every theme defines all 14. Do not introduce new tokens; do not hard-code hex literals in widget Python code (the `.tcss` files are the only place hexes belong).
- **Snapshot terminal size:** `(120, 40)` (verbatim from `tests/snapshot/conftest.py::TERMINAL_SIZE`).
- **Out of scope:** `CommandPalette`, `CrashModal`, `ResumeModal`, `FirstRunModal`, `S3CompatFormModal`, `QuickLook`, `HelpModal` — these are M6-deferred at runtime per `CHANGELOG.md [Unreleased] Deferred`. Their existing snapshots (50 goldens) must NOT change.
- **Layer rules:** `scripts/check-layers.sh` must stay clean. No new imports of `aws_tui.ui.*` from `vm/` or `services/`. New widget code lives in `src/aws_tui/ui/widgets/`; new VM code lives in `src/aws_tui/vm/chrome/` (theme picker) or `src/aws_tui/vm/file_manager/` (transfer speed window).
- **Per-pass gate (every task ends green):** `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --tb=short -q`, `bash scripts/check-layers.sh`, `uv run pre-commit run --all-files`.
- **No emojis in code/docs unless the user explicitly asked for them** — the spec calls for `▌` (BLOCK), `▰`/`▱` (BLACK SQUARE), `↑`, `✓`, `✗`, `⏸`, `⊘`, `→`, `●`, `○`. These are typographic glyphs the rest of the codebase already uses and are not emojis.
- **Existing test count baseline:** 613 default-tier + 9 opt-in MinIO (snapshot tier currently 104 goldens). This plan adds 30 new goldens (3 new snapshot apps × 10 themes) and refreshes 30 existing goldens (`test_confirm_modal_copy_paths` × 10 + `test_confirm_modal_danger` × 10 + `test_main_screen` × 10).

---

## File Structure

### Files modified

- `src/aws_tui/ui/widgets/modal_button.py` — change `DEFAULT_CSS` from `width: 18` to `width: auto; min-width: 14`; reduce `padding: 0 3` to `padding: 0 2`.
- `src/aws_tui/ui/widgets/theme_picker_modal.py` — inline `DEFAULT_CSS` slimmed (most styling moves to per-theme `.tcss`); calls `preview_command` on every cursor move; remembers `_original_theme` and rolls back on `action_close`.
- `src/aws_tui/ui/widgets/transfers_overlay.py` — full row redesign (drop `ProgressBar`, render custom bar with `Static`, add meta line, restructure layout).
- `src/aws_tui/ui/widgets/toast.py` — no structural change; rendering stays the same; the visual change is per-theme `.tcss` adding borders/level colors.
- `src/aws_tui/ui/widgets/services_menu.py` — no structural change (it already exposes the classes); only CSS audit work.
- `src/aws_tui/vm/chrome/theme_picker_vm.py` — add `preview_command: RelayCommandOf[str]` and dispose it in `dispose()`; constructor takes `on_preview: Callable[[str], None]` alongside the existing `on_pick`.
- `src/aws_tui/vm/file_manager/transfer_vm.py` — add `_speed_window: deque[tuple[float, int]]` (5-second rolling cap), `current_speed` property, `current_eta` property; constructor takes optional `clock: Callable[[], float] = time.monotonic` for test injection.
- `src/aws_tui/app.py` — `action_themes` passes `on_preview=self.switch_theme` to `ThemePickerVM` (alongside the existing `on_pick=self.switch_theme`).
- `src/aws_tui/ui/themes/{10 themes}.tcss` — every theme gets: ModalButton width/padding refresh, ThemePickerModal full block, TransfersOverlay row redesign rules, Toast level rules, ServicesMenu selected-row pattern tightening.
- `CHANGELOG.md` — new `### Changed` and `### Fixed` bullets in `[Unreleased]`.
- `tests/snapshot/test_modals.py` — no test additions; just the bulk snapshot refresh covers it.

### Files created

- `tests/snapshot/apps/theme_picker.py` — `ThemePickerSnapshotApp` composing a real `ThemePickerVM` with 10 themes and cursor on row 4 (`amber`).
- `tests/snapshot/test_theme_picker.py` — single `@pytest.mark.parametrize("theme", THEMES)` test → 10 new goldens.
- `tests/snapshot/apps/transfers.py` — `TransfersSnapshotApp` composing a `TransfersVM` with 3 transfers (running at 62%, completed lingering, failed).
- `tests/snapshot/test_transfers.py` — 10 new goldens.
- `tests/snapshot/apps/toast.py` — `ToastSnapshotApp` composing a `ToastStackVM` with one INFO + one ERROR toast (the ERROR carries an `action_label` so the action chip is exercised).
- `tests/snapshot/test_toast.py` — 10 new goldens.
- `tests/unit/vm/chrome/test_theme_picker_preview.py` — covers the new `preview_command` and `on_preview` callback wiring (no Textual; pure VM test).
- `tests/unit/vm/file_manager/test_transfer_speed_window.py` — covers `_speed_window` insertion + prune + `current_speed` + `current_eta` with an injected fake clock.

### Files NOT touched

- `src/aws_tui/ui/widgets/{command_palette,crash_modal,first_run_modal,quick_look,resume_modal,help_modal}.py` — out of scope (deferred runtime).
- `src/aws_tui/vm/chrome/{crash_vm,resume_vm,first_run_vm,quick_look_vm,command_palette_vm}.py` — out of scope.
- `src/aws_tui/domain/*` — no domain change.
- `src/aws_tui/infra/*` — no infra change.
- `scripts/check-layers.sh` — already correct; no rule change needed.

---

## Task 0: Bootstrap — verify clean baseline before touching anything

**Files:** none (validation only).

**Interfaces:** none.

- [ ] **Step 1: Confirm working tree clean and on the right branch**

```bash
cd /Users/kaveh/repos/aws-tui
git status
git branch --show-current
```

Expected: `working tree clean`; branch `polish/modal-toast-2026-06-19`.

- [ ] **Step 2: Run the full default-tier gate to capture a baseline**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
bash scripts/check-layers.sh
uv run pytest --tb=short -q
uv run pre-commit run --all-files
```

Expected: every command exits 0. `pytest` reports `613 passed, 9 deselected`.

- [ ] **Step 3: Spot-check the existing in-scope snapshot directories so you have the file list memorized**

```bash
ls tests/snapshot/__snapshots__/test_modals/ | grep -E "(confirm_modal_copy_paths|confirm_modal_danger)" | wc -l
ls tests/snapshot/__snapshots__/test_main_screen/ | wc -l
```

Expected: `20` and `10` respectively (the to-be-refreshed goldens). If either number is different, stop and reconcile — the plan assumes these baselines.

- [ ] **Step 4: No commit needed** — Task 0 is verification only.

---

## Task 1: Fix `ModalButton` label spill (auto-width + min-width)

**Files:**
- Modify: `src/aws_tui/ui/widgets/modal_button.py` (DEFAULT_CSS block)
- Modify: 10× `src/aws_tui/ui/themes/{theme}.tcss` (ModalButton width override in the confirm-modal footer rules)

**Interfaces:**
- Consumes: nothing new.
- Produces: a `ModalButton` whose width grows to fit its label with a 14-cell floor. Every confirm-modal footer rule that hard-codes `width: 18` is updated.

- [ ] **Step 1: Read the current `ModalButton.DEFAULT_CSS`**

```bash
sed -n '26,42p' src/aws_tui/ui/widgets/modal_button.py
```

Expected: you see `ModalButton { width: 18; height: 3; padding: 0 3; … }`.

- [ ] **Step 2: Update the widget DEFAULT_CSS**

Replace the `ModalButton { … }` block in `src/aws_tui/ui/widgets/modal_button.py` with:

```python
    DEFAULT_CSS = """
    ModalButton {
        width: auto;
        min-width: 14;
        height: 3;
        padding: 0 2;
        content-align: center middle;
        text-style: bold;
        margin: 0 1;
    }
    ModalButton.-primary {
        text-style: bold;
    }
    ModalButton.-focused {
        text-style: bold reverse;
    }
    """
```

The change is: `width: 18` → `width: auto` + `min-width: 14`; `padding: 0 3` → `padding: 0 2`. Nothing else.

- [ ] **Step 3: Find every theme override of ModalButton width and update**

```bash
grep -nE "ModalButton.*width: 18|width: 18" src/aws_tui/ui/themes/*.tcss
```

For every match, change `width: 18;` to `width: auto; min-width: 14;` and (if also present in the same block) `padding: 0 3;` to `padding: 0 2;`. There is one occurrence per theme in the `ConfirmModal > Container > .modal-footer > ModalButton { … }` rule. Do all 10 themes. Do not change anything else inside those rule blocks.

- [ ] **Step 4: Refresh the in-scope confirm-modal snapshots**

```bash
uv run pytest tests/snapshot/test_modals.py::test_confirm_modal_copy_paths tests/snapshot/test_modals.py::test_confirm_modal_danger --snapshot-update -q
```

Expected: `20 passed`. Twenty `.raw` files under `tests/snapshot/__snapshots__/test_modals/` are updated.

- [ ] **Step 5: Visually diff one carbon snapshot to confirm the new button geometry looks right**

Open `tests/snapshot/__snapshots__/test_modals/test_confirm_modal_copy_paths[carbon].raw` in a browser (it's an SVG). Confirm: Cancel + Copy buttons are visible, label is centered, no clipping. If a button is now too narrow (label touching border), increase `min-width` in `modal_button.py` from 14 to 16 and re-snap.

- [ ] **Step 6: Confirm OUT-OF-SCOPE snapshots did NOT shift**

```bash
git status tests/snapshot/__snapshots__/test_modals/ | grep -E "(crash_modal|first_run|resume|command_palette|quick_look)"
```

Expected: no output. If any of those 50 goldens drifted, you've introduced a regression — investigate before continuing.

- [ ] **Step 7: Run the full gate**

```bash
uv run ruff check src tests && uv run mypy src && uv run pytest tests/snapshot/test_modals.py --tb=short -q && bash scripts/check-layers.sh
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/aws_tui/ui/widgets/modal_button.py src/aws_tui/ui/themes/*.tcss tests/snapshot/__snapshots__/test_modals/
git commit -m "fix(ui,themes): ModalButton width auto + min-width 14 to stop label spill

Root cause of the confirm-modal button-label spill: fixed
width: 18 + padding: 0 3 left only 12 cells for the label.
Labels like 'Authenticate' or 'Delete all marked' would clip
through the right border.

Changed:
- ModalButton.DEFAULT_CSS: width: auto + min-width: 14, padding 0 2.
- Every theme's ConfirmModal > Container > .modal-footer >
  ModalButton override: same change.

Confirm-modal snapshots refreshed in all 10 themes (20 goldens:
copy + danger variants). Out-of-scope modal snapshots unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: ConfirmModal — path-chip nowrap + danger right-shift

**Files:**
- Modify: 10× `src/aws_tui/ui/themes/{theme}.tcss` (ConfirmModal path-value chip rule + new `.modal-footer.-danger > ModalButton.-danger` margin rule)
- Modify: `src/aws_tui/ui/widgets/confirm_modal.py` (add `-danger` class to the footer Horizontal when request is a danger request)

**Interfaces:**
- Consumes: `ConfirmModal.compose()` already conditionally adds `-danger` to the modal screen; add the same class to the footer container so the new CSS selector matches.
- Produces: long path strings truncate with `…` instead of pushing the modal wider; the Delete button in danger modals sits 2 cells to the right of where Copy sits in non-danger modals.

- [ ] **Step 1: Update ConfirmModal compose to tag the footer**

In `src/aws_tui/ui/widgets/confirm_modal.py`, find the `compose` method and change:

```python
            with Horizontal(classes="modal-footer"):
```

to:

```python
            footer_classes = "modal-footer -danger" if self._request.danger else "modal-footer"
            with Horizontal(classes=footer_classes):
```

(Two changes in one block: introduce the local, swap the argument.) Nothing else moves.

- [ ] **Step 2: Add the per-theme CSS rules**

For each of the 10 themes, locate the existing `ConfirmModal > Container > .modal-path-value { … }` rule. Add `text-wrap: nowrap;` and `text-overflow: ellipsis;` to that rule. Example for `carbon.tcss`:

```
ConfirmModal > Container > .modal-path-value {
    color: $text;
    background: $bg;
    border: round $rule-dim;
    height: auto;
    padding: 0 1;
    margin: 0 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
```

For each of the 10 themes, also add a new rule immediately after the existing `.-danger` rules (search for `ConfirmModal.-danger > Container > .modal-title`):

```
ConfirmModal > Container > .modal-footer.-danger > ModalButton.-danger {
    margin-right: 2;
}
```

Both new rules use the same tokens in every theme (the second one has no token references at all). Apply identically to all 10 themes.

- [ ] **Step 3: Refresh confirm-modal snapshots**

```bash
uv run pytest tests/snapshot/test_modals.py::test_confirm_modal_copy_paths tests/snapshot/test_modals.py::test_confirm_modal_danger --snapshot-update -q
```

Expected: `20 passed`. The 10 danger goldens should show the Delete button shifted right by 2 cells.

- [ ] **Step 4: Confirm out-of-scope snapshots still unchanged**

```bash
git status tests/snapshot/__snapshots__/test_modals/ | grep -vE "(confirm_modal_copy|confirm_modal_danger)" | grep "\.raw"
```

Expected: empty.

- [ ] **Step 5: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/ui/widgets/confirm_modal.py src/aws_tui/ui/themes/*.tcss tests/snapshot/__snapshots__/test_modals/
git commit -m "feat(ui,themes): confirm-modal path-chip nowrap + danger right-shift

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §4.1:

- Path-value chips now text-wrap: nowrap with ellipsis truncation;
  long paths no longer push the modal wider than its 70-col bound.
- Danger modals (delete) shift the Confirm button +2 cells right
  via a new .modal-footer.-danger > ModalButton.-danger margin
  rule. Small UX guardrail so a reflex Enter doesn't land where
  the cursor was before the modal opened (borrowed from macOS
  NSAlert).

ConfirmModal.compose adds the '-danger' class to the footer
Horizontal so the new CSS selector matches.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: ThemePickerModal CSS in all 10 themes (no behavior change yet)

**Files:**
- Modify: `src/aws_tui/ui/widgets/theme_picker_modal.py` (slim the inline `DEFAULT_CSS` — keep only the structural rules that aren't theme-dependent)
- Modify: 10× `src/aws_tui/ui/themes/{theme}.tcss` (add a full `ThemePickerModal` block)
- Create: `tests/snapshot/apps/theme_picker.py` (snapshot harness)
- Create: `tests/snapshot/test_theme_picker.py` (10-golden test)

**Interfaces:**
- Consumes: the existing `ThemePickerVM(themes=..., active_theme=..., on_pick=..., hub=..., dispatcher=...)` constructor signature (do NOT change it in this task — that's Task 4).
- Produces: 10 new snapshot goldens at `tests/snapshot/__snapshots__/test_theme_picker/test_theme_picker[<theme>].raw`. Every theme renders the picker as a rounded `$bg-elev` box with `$accent` title, `$bg-sel`/`$accent-soft` cursor highlight matching the file-pane cursor pattern.

- [ ] **Step 1: Slim the inline DEFAULT_CSS in the widget**

In `src/aws_tui/ui/widgets/theme_picker_modal.py`, the `ThemePickerModal` class has a multi-rule `DEFAULT_CSS` that hardcodes width/padding. Reduce it to ONLY the layout rules — the visual rules move to `.tcss`. Replace the existing `DEFAULT_CSS` on `ThemePickerModal` (lines ~88–108) with:

```python
    DEFAULT_CSS = """
    ThemePickerModal {
        align: center middle;
    }
    """
```

Also slim `_ThemeRow.DEFAULT_CSS` (lines ~40–49) to ONLY layout. Replace with:

```python
    DEFAULT_CSS = """
    _ThemeRow {
        height: 1;
        padding: 0 2;
    }
    """
```

(Color comes from the theme rule we're about to add.)

- [ ] **Step 2: Add the per-theme block — write the carbon version first as the template**

Open `src/aws_tui/ui/themes/carbon.tcss` and append (right after the existing `ConfirmModal` block, around line 343):

```
/* ── Theme picker modal ──────────────────────────────────────────────── */
ThemePickerModal {
    background: $bg 60%;
}
ThemePickerModal > #picker-frame {
    background: $bg-elev;
    color: $text;
    border: round $rule-dim;
    padding: 1 0;
    width: 44;
    max-height: 20;
}
ThemePickerModal #picker-title {
    color: $accent;
    text-style: bold;
    text-align: center;
    width: 100%;
    padding: 0 2 1 2;
}
ThemePickerModal #picker-help {
    color: $text-muted;
    text-align: center;
    width: 100%;
    padding: 1 2 0 2;
}
ThemePickerModal _ThemeRow {
    color: $text;
}
ThemePickerModal _ThemeRow.-cursor {
    background: $bg-sel;
    color: $accent-soft;
    text-style: bold;
}
```

- [ ] **Step 3: Replicate the block to all 9 other themes**

The shape is identical for every theme — only the token references stay (no hex literals). Copy the carbon block verbatim into the same location (after the existing `ConfirmModal` block) in each of:

```
src/aws_tui/ui/themes/voidline.tcss
src/aws_tui/ui/themes/lattice.tcss
src/aws_tui/ui/themes/amber.tcss
src/aws_tui/ui/themes/solarized-light.tcss
src/aws_tui/ui/themes/github-light.tcss
src/aws_tui/ui/themes/one-light.tcss
src/aws_tui/ui/themes/nord.tcss
src/aws_tui/ui/themes/dracula.tcss
src/aws_tui/ui/themes/gruvbox-dark.tcss
```

After this step you should have 10 identical theme-picker CSS blocks. Each will render differently because the `$token` values differ.

- [ ] **Step 4: Create the snapshot harness app**

Create `tests/snapshot/apps/theme_picker.py`:

```python
"""ThemePickerModal snapshot harness.

Composes a real ``ThemePickerVM`` with all 10 themes registered and
cursor on row 4 (``amber``). Pushes the modal so the snapshot
captures the picker on top of an empty base.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.theme_picker_modal import ThemePickerModal
from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM
from tests.snapshot.conftest import THEMES


class ThemePickerSnapshotApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._theme = theme
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        # noop callback — snapshot captures static state, doesn't actually
        # switch themes during the screenshot
        self._picker = ThemePickerVM(
            themes=THEMES,
            active_theme=theme,
            on_pick=lambda _name: None,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )
        self._picker.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui (behind picker)", id="placeholder")

    async def on_mount(self) -> None:
        modal = ThemePickerModal(picker=self._picker, hub=self._hub)
        await self.push_screen(modal)
        await self.refresh_bindings()
        # Move cursor to row 3 (amber) so the snapshot exercises the
        # cursor highlight (different theme position per row keeps the
        # snapshot informative).
        modal.action_move_down()
        modal.action_move_down()
        modal.action_move_down()


__all__ = ["ThemePickerSnapshotApp"]
```

- [ ] **Step 5: Create the snapshot test file**

Create `tests/snapshot/test_theme_picker.py`:

```python
"""Snapshot tests for ThemePickerModal across all 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.theme_picker import ThemePickerSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_theme_picker(theme: str, snap_compare) -> None:
    assert snap_compare(ThemePickerSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)
```

- [ ] **Step 6: Generate the initial snapshots**

```bash
uv run pytest tests/snapshot/test_theme_picker.py --snapshot-update -q
```

Expected: `10 passed`. Ten new files appear under `tests/snapshot/__snapshots__/test_theme_picker/`.

- [ ] **Step 7: Visually inspect three goldens (carbon, voidline, amber)**

Open these three SVG files in a browser:

- `tests/snapshot/__snapshots__/test_theme_picker/test_theme_picker[carbon].raw`
- `tests/snapshot/__snapshots__/test_theme_picker/test_theme_picker[voidline].raw`
- `tests/snapshot/__snapshots__/test_theme_picker/test_theme_picker[amber].raw`

Confirm: rounded frame, themed background, themed accent title, cursor highlight on the 4th row, no terminal-default fallback colors anywhere.

- [ ] **Step 8: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 613 + 10 = `623 passed`. Layer + lint + format + mypy + pre-commit all clean.

- [ ] **Step 9: Commit**

```bash
git add src/aws_tui/ui/widgets/theme_picker_modal.py src/aws_tui/ui/themes/*.tcss tests/snapshot/apps/theme_picker.py tests/snapshot/test_theme_picker.py tests/snapshot/__snapshots__/test_theme_picker/
git commit -m "feat(ui,themes): full ThemePickerModal CSS across all 10 themes

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §5.1:
the theme picker had ZERO per-theme rules in v0.7.x — it fell back
to bare-terminal styling.

Added a ThemePickerModal block to every theme:
- rounded \$bg-elev frame with \$rule-dim border
- \$accent-colored bold centered title
- \$bg-sel + \$accent-soft cursor highlight (matches file-pane
  cursor pattern; rail and picker now read as siblings)
- \$text-muted help line at the bottom

Widget DEFAULT_CSS slimmed to layout-only (align: center middle on
the screen, height: 1 + padding on _ThemeRow). All color rules live
in .tcss now.

Snapshot coverage: new tests/snapshot/apps/theme_picker.py harness
+ tests/snapshot/test_theme_picker.py parametrized over all 10
themes (10 new goldens). Cursor on row 4 (amber) so every snapshot
exercises the cursor-highlight selector.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: ThemePickerVM `preview_command` + Esc rollback wiring

**Files:**
- Modify: `src/aws_tui/vm/chrome/theme_picker_vm.py` (add `on_preview` constructor arg + `preview_command`)
- Modify: `src/aws_tui/ui/widgets/theme_picker_modal.py` (capture `_original_theme`, call `preview_command` in `_move_by`, rollback in `action_close`)
- Modify: `src/aws_tui/app.py` (pass `on_preview=self.switch_theme` in `action_themes`)
- Create: `tests/unit/vm/chrome/test_theme_picker_preview.py`

**Interfaces:**
- Consumes: existing `RootVM.switch_theme(name: str) -> None` already wired through `AwsTuiApp.switch_theme`. No change to that pipeline.
- Produces:
  - `ThemePickerVM.__init__` adds `on_preview: Callable[[str], None]` keyword argument (default: `lambda _name: None` so existing callers compile).
  - `ThemePickerVM.preview_command: RelayCommandOf[str]` — invokes `on_preview(name)` then `self.set_active(name)` (mirroring `pick_theme_command` but without committing).
  - `ThemePickerModal.action_close` now rolls back to the originally-active theme via `preview_command.execute(self._original_theme)` before dismissing.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/vm/chrome/test_theme_picker_preview.py`:

```python
"""Tests for ThemePickerVM.preview_command and Esc-rollback semantics."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def test_preview_command_calls_on_preview_without_committing_pick() -> None:
    previewed: list[str] = []
    picked: list[str] = []
    picker = ThemePickerVM(
        themes=("carbon", "amber", "voidline"),
        active_theme="carbon",
        on_pick=picked.append,
        on_preview=previewed.append,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")
        assert previewed == ["amber"]
        assert picked == []  # preview did NOT call on_pick
        # The active-theme bookkeeping IS updated by preview so the
        # marker glyph in the modal follows the cursor.
        assert picker.active_theme == "amber"
    finally:
        picker.dispose()


def test_pick_command_still_calls_on_pick_after_preview() -> None:
    previewed: list[str] = []
    picked: list[str] = []
    picker = ThemePickerVM(
        themes=("carbon", "amber", "voidline"),
        active_theme="carbon",
        on_pick=picked.append,
        on_preview=previewed.append,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")
        picker.pick_theme_command.execute("amber")
        assert previewed == ["amber"]
        assert picked == ["amber"]
    finally:
        picker.dispose()


def test_on_preview_defaults_to_noop_when_omitted() -> None:
    """Backward-compat: existing callers that don't pass on_preview
    must still construct cleanly."""
    picker = ThemePickerVM(
        themes=("carbon", "amber"),
        active_theme="carbon",
        on_pick=lambda _n: None,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")  # must not raise
        assert picker.active_theme == "amber"
    finally:
        picker.dispose()
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest tests/unit/vm/chrome/test_theme_picker_preview.py -v
```

Expected: FAIL — `ThemePickerVM` has no `on_preview` parameter or `preview_command` attribute.

- [ ] **Step 3: Add `on_preview` + `preview_command` to `ThemePickerVM`**

In `src/aws_tui/vm/chrome/theme_picker_vm.py`:

In the constructor signature (around line 105–114), add `on_preview` after `on_pick`:

```python
    def __init__(
        self,
        *,
        themes: tuple[str, ...],
        active_theme: str,
        on_pick: Callable[[str], None],
        on_preview: Callable[[str], None] | None = None,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "theme_picker",
    ) -> None:
```

In the constructor body, after `self._on_pick = on_pick` (around line 116), add:

```python
        self._on_preview: Callable[[str], None] = on_preview if on_preview is not None else (lambda _name: None)
```

After the `self._pick_theme_command` builder block (around line 140-142), add:

```python
        self._preview_theme_command: RelayCommandOf[str] = (
            RelayCommandOf[str].builder().task(self._preview).build()
        )
```

In the Properties section (after `pick_theme_command` property around line 156), add:

```python
    @property
    def preview_command(self) -> RelayCommandOf[str]:
        return self._preview_theme_command
```

In `dispose` (around line 174-178), add the dispose call:

```python
    def dispose(self) -> None:
        self._pick_theme_command.dispose()
        self._preview_theme_command.dispose()
        for opt in self._options:
            opt.dispose()
        self._inner.dispose()
```

In the Internal section (after `_pick` method around line 210-215), add:

```python
    def _preview(self, name: str | None) -> None:
        """Live-preview ``name`` without committing the pick.

        Calls the injected ``on_preview`` (which the modal wires to
        ``AwsTuiApp.switch_theme`` so the live repaint cascade fires)
        then updates ``active_theme`` so the row marker follows the
        cursor.
        """
        if not name:
            return
        self._on_preview(name)
        self.set_active(name)
```

- [ ] **Step 4: Re-run the unit test — it should pass**

```bash
uv run pytest tests/unit/vm/chrome/test_theme_picker_preview.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Wire the modal to call `preview_command` and remember original theme**

In `src/aws_tui/ui/widgets/theme_picker_modal.py`, in `ThemePickerModal.__init__` (around line 120-128), after `self._cursor = 0` (or after the try/except setting cursor), add:

```python
        self._original_theme: str = picker.active_theme
```

Then in `_move_by` (around line 187-194), after `self._sync_cursor_class()`, add the preview call:

```python
    def _move_by(self, delta: int) -> None:
        if not self._rows:
            return
        new = max(0, min(self._cursor + delta, len(self._rows) - 1))
        if new == self._cursor:
            return
        self._cursor = new
        self._sync_cursor_class()
        # Live-preview the cursored theme so the user sees what they'd
        # commit. Esc (action_close) restores _original_theme.
        self._picker.preview_command.execute(self._rows[self._cursor].theme_name)
```

Update `action_close` (around line 182-184):

```python
    def action_close(self) -> None:
        # Esc: roll back to the theme that was active when the modal opened.
        if self._picker.active_theme != self._original_theme:
            self._picker.preview_command.execute(self._original_theme)
        self.dismiss(None)
```

- [ ] **Step 6: Wire the real app to pass `on_preview`**

In `src/aws_tui/app.py`, find `action_themes` (around line 704). The current call to `ThemePickerVM(...)` passes `on_pick=self.switch_theme` only. Update both call sites (there are two — `action_themes` and `action_cycle_theme` may share one, plus the constructor inside `action_themes`):

```bash
grep -n "ThemePickerVM(" src/aws_tui/app.py
```

For each call site, add `on_preview=self.switch_theme,` immediately below the existing `on_pick=self.switch_theme,` line. Both `on_pick` and `on_preview` route through the same `switch_theme` method — that's intentional. The semantic distinction lives in the VM: `pick` is for the final selection (where config-persistence will hook in later), `preview` is throwaway.

- [ ] **Step 7: Refresh ThemePickerModal snapshot (the cursor position now triggers preview which calls set_active, so the marker glyph may follow the cursor — verify)**

```bash
uv run pytest tests/snapshot/test_theme_picker.py --snapshot-update -q
```

Expected: `10 passed`. Inspect one golden — the ● marker should now be on the cursored row (since preview calls `set_active`), not on the originally-active row.

- [ ] **Step 8: Confirm out-of-scope snapshots still unchanged**

```bash
git status tests/snapshot/__snapshots__/ | grep "\.raw" | grep -v "test_theme_picker"
```

Expected: empty.

- [ ] **Step 9: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 613 + 10 + 3 = `626 passed`.

- [ ] **Step 10: Commit**

```bash
git add src/aws_tui/vm/chrome/theme_picker_vm.py src/aws_tui/ui/widgets/theme_picker_modal.py src/aws_tui/app.py tests/unit/vm/chrome/test_theme_picker_preview.py tests/snapshot/__snapshots__/test_theme_picker/
git commit -m "feat(vm,ui): theme picker live preview on cursor + Esc rollback

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §5.2-§5.3:
the user explicitly asked for the theme to switch live as the
cursor moves through the picker, with Esc restoring the
originally-active theme.

VM (ThemePickerVM):
- new on_preview keyword arg on __init__ (defaults to a noop for
  backward-compat with existing snapshot harnesses)
- new preview_command: RelayCommandOf[str] alongside the existing
  pick_theme_command. Semantically distinct: preview is throwaway,
  pick is the commit (future config-persistence hangs off pick).
- _preview internal handler calls on_preview then set_active so the
  marker glyph follows the cursor.

Widget (ThemePickerModal):
- _original_theme captured in __init__
- _move_by calls preview_command.execute(name) after moving cursor
- action_close (Esc) rolls back to _original_theme if a preview is
  in effect

App (AwsTuiApp.action_themes):
- passes on_preview=self.switch_theme so the same theme-switching
  pipeline that on_pick uses is reused for live preview

Tests: 3 new unit tests for preview/pick/default-noop. ThemePicker
snapshots refreshed (cursor row now carries the marker because
preview calls set_active).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: ServicesMenu + ServicesHamburger cross-theme audit

**Files:**
- Modify: 10× `src/aws_tui/ui/themes/{theme}.tcss` (audit and tighten ServicesMenu + ServicesHamburger rules)

**Interfaces:** none (CSS-only).

- [ ] **Step 1: Audit which themes have which selectors**

Run this to see which themes mention `ServicesMenu` and `ServicesHamburger`:

```bash
for theme in carbon voidline lattice amber solarized-light github-light one-light nord dracula gruvbox-dark; do
  echo "=== $theme ==="
  grep -nE "^(ServicesMenu|ServicesHamburger)" src/aws_tui/ui/themes/$theme.tcss
done
```

Expected: every theme has both selectors with similar rules. Note any theme missing a rule or using literal hex where `$accent` etc. should be used.

- [ ] **Step 2: Read the carbon ServicesMenu block as the reference**

```bash
sed -n '72,110p' src/aws_tui/ui/themes/carbon.tcss
sed -n '468,477p' src/aws_tui/ui/themes/carbon.tcss
```

These are the reference. The `.service-item.-selected` rule should follow the file-pane cursor pattern: `background: $bg-sel; color: $accent-soft; text-style: bold;` plus a leading `▌` accent bar via the `padding-left` + a `Static`-set border-left or text-prefix approach. Inspect how the existing carbon does this — if it uses `background: $accent-hot` (the older pattern from the M5-pass-30 fix per CHANGELOG), update it to match.

- [ ] **Step 3: For each of the 10 themes, ensure `.service-item.-selected` uses these tokens**

Open each theme file and update the `.service-item.-selected` block to read exactly:

```
ServicesMenu > .service-item.-selected {
    background: $bg-sel;
    color: $accent-soft;
    text-style: bold;
}
```

Do this for all 10 themes. If a theme uses additional rules (e.g., a left-bar via a separate widget), leave those alone; only the three token references above should be standardized.

- [ ] **Step 4: For each of the 10 themes, ensure `ServicesHamburger` uses the carbon pattern**

```
ServicesHamburger {
    background: $bg-elev;
    color: $accent;
}
ServicesHamburger:hover {
    background: $accent;
    color: $bg;
}
```

If a theme has a `border:` line in `ServicesHamburger`, keep it but ensure it uses `$rule-dim`.

- [ ] **Step 5: Refresh main-screen snapshots (services rail is part of main screen)**

```bash
uv run pytest tests/snapshot/test_main_screen.py --snapshot-update -q
```

Expected: `10 passed`. Visually inspect carbon and amber goldens — selected service item should have the new `$bg-sel` background.

- [ ] **Step 6: Confirm out-of-scope snapshots unchanged**

```bash
git status tests/snapshot/__snapshots__/ | grep "\.raw" | grep -v "test_main_screen\|test_theme_picker"
```

Expected: empty.

- [ ] **Step 7: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 626 passed (same count — no new tests).

- [ ] **Step 8: Commit**

```bash
git add src/aws_tui/ui/themes/*.tcss tests/snapshot/__snapshots__/test_main_screen/
git commit -m "fix(themes): services rail selected-row matches file-pane cursor pattern

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §6.2:
the services-rail selected-row treatment was inconsistent across
themes. Standardized on the file-pane cursor pattern:

  ServicesMenu > .service-item.-selected {
      background: \$bg-sel;
      color: \$accent-soft;
      text-style: bold;
  }

ServicesHamburger rules normalized across all 10 themes to use
\$bg-elev / \$accent / \$rule-dim instead of any literal hex carryover.
Hover still flips to \$accent background / \$bg foreground.

Snapshots: test_main_screen \xc3\x97 10 refreshed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: TransferVM rolling speed window

**Files:**
- Modify: `src/aws_tui/vm/file_manager/transfer_vm.py`
- Create: `tests/unit/vm/file_manager/test_transfer_speed_window.py`

**Interfaces:**
- Consumes: existing `TransferVM(model, *, hub, dispatcher)` constructor; `apply_update` already takes `bytes_done`, `bytes_total`, `state`, optional `error`.
- Produces:
  - `TransferVM.__init__` now accepts an optional `clock: Callable[[], float] = time.monotonic` keyword (default unchanged behavior for callers).
  - `TransferVM.current_speed: float | None` property — bytes per second over the last 5 seconds, or `None` if fewer than 2 samples or window span < 250 ms.
  - `TransferVM.current_eta: float | None` property — seconds remaining if `bytes_total` is set and `current_speed` is positive, else `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/vm/file_manager/test_transfer_speed_window.py`:

```python
"""Tests for TransferVM rolling speed window + ETA derivation."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _model(*, bytes_done: int = 0, bytes_total: int | None = 1_000_000) -> TransferModel:
    return TransferModel(
        id="t1",
        direction="upload",
        source_label="/src/file",
        destination_label="s3://bucket/file",
        bytes_done=bytes_done,
        bytes_total=bytes_total,
        state=TransferState.PENDING,
    )


def test_speed_is_none_with_fewer_than_two_samples() -> None:
    clock_values = iter([0.0])

    def fake_clock() -> float:
        return next(clock_values)

    vm = TransferVM(_model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=fake_clock)
    vm.construct()
    try:
        vm.apply_update(bytes_done=100, bytes_total=1_000_000, state=TransferState.RUNNING)
        assert vm.current_speed is None
    finally:
        vm.dispose()


def test_speed_computed_after_two_samples() -> None:
    ticks = [0.0, 1.0]  # 1 second elapsed between samples
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=lambda: next(clock_values)
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=100, bytes_total=1_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=100_100, bytes_total=1_000_000, state=TransferState.RUNNING)
        # 100_000 bytes over 1 second
        assert vm.current_speed == 100_000.0
    finally:
        vm.dispose()


def test_speed_window_prunes_samples_older_than_5_seconds() -> None:
    ticks = [0.0, 1.0, 7.0]  # third sample is 7 s after the first
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=lambda: next(clock_values)
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=10_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=1_000_000, bytes_total=10_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=2_000_000, bytes_total=10_000_000, state=TransferState.RUNNING)
        # First sample is 7 s old, so it's pruned. Remaining window:
        # (1.0, 1_000_000) and (7.0, 2_000_000) -> 1_000_000 bytes / 6 s
        # = 166_666.67 B/s
        assert vm.current_speed is not None
        assert abs(vm.current_speed - 166_666.666_666_667) < 0.01
    finally:
        vm.dispose()


def test_eta_computed_from_speed_and_remaining_bytes() -> None:
    ticks = [0.0, 1.0]
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(bytes_total=1_000_000),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        clock=lambda: next(clock_values),
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=1_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=100_000, bytes_total=1_000_000, state=TransferState.RUNNING)
        # 100_000 B/s, 900_000 B remaining -> 9 seconds
        assert vm.current_eta == 9.0
    finally:
        vm.dispose()


def test_eta_is_none_with_no_bytes_total() -> None:
    ticks = [0.0, 1.0]
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(bytes_total=None),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
        clock=lambda: next(clock_values),
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=None, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=100_000, bytes_total=None, state=TransferState.RUNNING)
        assert vm.current_speed == 100_000.0
        assert vm.current_eta is None
    finally:
        vm.dispose()


def test_speed_window_requires_minimum_250ms_span() -> None:
    ticks = [0.0, 0.1]  # 100 ms span — too short
    clock_values = iter(ticks)
    vm = TransferVM(
        _model(), hub=_hub(), dispatcher=NULL_DISPATCHER, clock=lambda: next(clock_values)
    )
    vm.construct()
    try:
        vm.apply_update(bytes_done=0, bytes_total=1_000_000, state=TransferState.RUNNING)
        vm.apply_update(bytes_done=10_000, bytes_total=1_000_000, state=TransferState.RUNNING)
        # 100 ms window is below the 250 ms minimum
        assert vm.current_speed is None
    finally:
        vm.dispose()
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest tests/unit/vm/file_manager/test_transfer_speed_window.py -v
```

Expected: 6 FAILs — `TransferVM.__init__` has no `clock` parameter; `current_speed` and `current_eta` don't exist.

- [ ] **Step 3: Add the speed window to TransferVM**

In `src/aws_tui/vm/file_manager/transfer_vm.py`:

At the top of the file, add the imports:

```python
import time
from collections import deque
from collections.abc import Callable
```

In the constructor (around line 41-49), add the `clock` keyword arg:

```python
    def __init__(
        self,
        model: TransferModel,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
```

In the constructor body, after the existing instance assignments and before the `self._inner` builder (around line 48-49), add:

```python
        self._clock: Callable[[], float] = clock
        self._speed_window: deque[tuple[float, int]] = deque()
```

In `apply_update` (around line 132-150), add a single line at the very start of the method (before the `replace(...)` call):

```python
    def apply_update(
        self,
        *,
        bytes_done: int,
        bytes_total: int | None,
        state: TransferState,
        error: str | None = None,
    ) -> None:
        self._record_sample(bytes_done)
        new = replace(
            self._inner.model,
            ...
```

(Keep the rest of `apply_update` exactly as it is.)

In the Properties section (after `is_finished` around line 91), add:

```python
    @property
    def current_speed(self) -> float | None:
        """Bytes-per-second over the last 5 seconds of samples.

        Returns ``None`` if fewer than 2 samples or the sample window
        spans less than 250 ms (too short for a stable speed estimate).
        """
        if len(self._speed_window) < 2:
            return None
        first_ts, first_bytes = self._speed_window[0]
        last_ts, last_bytes = self._speed_window[-1]
        span = last_ts - first_ts
        if span < 0.25:
            return None
        delta = last_bytes - first_bytes
        return delta / span

    @property
    def current_eta(self) -> float | None:
        """Seconds remaining at current speed, or ``None`` if unknowable."""
        speed = self.current_speed
        total = self._inner.model.bytes_total
        if speed is None or speed <= 0 or total is None:
            return None
        remaining = total - self._inner.model.bytes_done
        if remaining <= 0:
            return 0.0
        return remaining / speed
```

In the Internal section (after `_retry` around line 178), add:

```python
    def _record_sample(self, bytes_done: int) -> None:
        """Append a sample to the rolling 5-second speed window."""
        now = self._clock()
        self._speed_window.append((now, bytes_done))
        # Prune samples older than 5 s. Always keep at least the most
        # recent sample so the next call still has a window.
        cutoff = now - 5.0
        while len(self._speed_window) > 1 and self._speed_window[0][0] < cutoff:
            self._speed_window.popleft()
```

- [ ] **Step 4: Re-run the tests — should pass**

```bash
uv run pytest tests/unit/vm/file_manager/test_transfer_speed_window.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Full gate (no snapshots touched in this task — it's pure VM)**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 626 + 6 = `632 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/vm/file_manager/transfer_vm.py tests/unit/vm/file_manager/test_transfer_speed_window.py
git commit -m "feat(vm): TransferVM rolling 5s speed window + ETA derivation

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §7.4:
the new transfers-overlay card layout needs bytes-per-second and
ETA values to render in its meta row.

TransferVM now keeps a deque[tuple[float, int]] of (timestamp,
bytes_done) samples appended on every apply_update. Samples older
than 5 seconds are pruned. Two derived properties:

- current_speed: float | None
  = (last_bytes - first_bytes) / (last_ts - first_ts)
  Returns None if window has <2 samples OR span <250 ms (too short
  for a stable estimate).

- current_eta: float | None
  = (bytes_total - bytes_done) / current_speed
  Returns None if speed is None / <=0 OR bytes_total is None.

Constructor takes optional clock: Callable[[], float] = time.monotonic
so tests can inject a fake clock. Production behavior unchanged
for existing callers (default kwarg).

6 new unit tests cover: <2 samples, 2 samples baseline, window
prune, ETA computation, ETA-None when bytes_total=None, 250 ms
minimum-span guard.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6.5: `TransfersVM.register_vm` for pre-built TransferVMs

**Files:**
- Modify: `src/aws_tui/vm/file_manager/transfers_vm.py` (add `register_vm` public method)
- Modify: `tests/unit/vm/file_manager/test_transfers.py` (one new unit test)

**Interfaces:**
- Consumes: existing `TransfersVM._find`, `_transfers`, `_inner`, `_hub` instance state; `TransferVM.id` property.
- Produces: `TransfersVM.register_vm(vm: TransferVM) -> TransferVM` — accepts a pre-constructed TransferVM (e.g. one built with an injected fake clock), registers it in the composite, and emits `transfers` PropertyChanged. Idempotent on transfer-id collision (returns the existing VM, same shape as `register(model)`). The existing `register(model)` semantics stay unchanged — under the hood it now delegates to `register_vm` after constructing a default-clock TransferVM.

**Why:** Task 7's snapshot harness needs to populate `TransferVM._speed_window` deterministically (the speed/eta meta row depends on it). `register(model)` builds its own TransferVM with `time.monotonic`, discarding any pre-built fake-clock VM. `register_vm` exposes the lower-level primitive without touching production callers.

- [ ] **Step 1: Write the failing test**

Open `tests/unit/vm/file_manager/test_transfers.py`. Add this test at the end of the file:

```python
def test_transfers_register_vm_accepts_prebuilt_transfer_vm() -> None:
    """register_vm(vm) lets a caller (notably the snapshot harness)
    construct a TransferVM with a custom clock and register it directly,
    bypassing register(model) which builds its own production-clock VM.
    """
    hub = _hub()
    tvms = TransfersVM(hub=hub, dispatcher=NULL_DISPATCHER)
    tvms.construct()
    # Pre-build a TransferVM with a fake clock; populate the speed window.
    ticks = iter([0.0, 1.0])
    vm = TransferVM(
        _model(id="custom", state=TransferState.RUNNING, bytes_done=0, bytes_total=1_000_000),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        clock=lambda: next(ticks),
    )
    vm.construct()
    vm.apply_update(bytes_done=0, bytes_total=1_000_000, state=TransferState.RUNNING)
    vm.apply_update(bytes_done=500_000, bytes_total=1_000_000, state=TransferState.RUNNING)

    returned = tvms.register_vm(vm)
    assert returned is vm  # identity preserved
    assert any(t.id == "custom" for t in tvms.transfers)
    # Speed survived because the pre-built VM kept its clock + samples.
    assert returned.current_speed == 500_000.0

    # Idempotent on id collision — re-registering returns the same VM, doesn't dupe.
    duplicate = TransferVM(
        _model(id="custom", state=TransferState.RUNNING),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    duplicate.construct()
    assert tvms.register_vm(duplicate) is vm  # original wins
    assert sum(1 for t in tvms.transfers if t.id == "custom") == 1
    tvms.dispose()
```

- [ ] **Step 2: Run it — should fail**

```bash
uv run pytest tests/unit/vm/file_manager/test_transfers.py::test_transfers_register_vm_accepts_prebuilt_transfer_vm -v
```

Expected: FAIL — `TransfersVM` has no `register_vm` attribute.

- [ ] **Step 3: Implement `register_vm` and refactor `register` to delegate**

In `src/aws_tui/vm/file_manager/transfers_vm.py`, find the existing `register` method (around line 136-147). Replace it with these two methods:

```python
    def register(self, model: TransferModel) -> TransferVM:
        """Add a new transfer from a model; the caller-driven path."""
        existing = self._find(model.id)
        if existing is not None:
            return existing
        vm = TransferVM(model, hub=self._hub, dispatcher=self._dispatcher)
        return self.register_vm(vm)

    def register_vm(self, vm: TransferVM) -> TransferVM:
        """Register a pre-constructed :class:`TransferVM`.

        Lower-level primitive ``register(model)`` delegates to. Tests and
        snapshot harnesses use this directly to inject a TransferVM built
        with a custom clock (so ``current_speed`` / ``current_eta`` render
        deterministically). Idempotent on transfer-id collision — returns
        the existing VM and does NOT dispose the passed-in one (caller
        owns it in that case).
        """
        existing = self._find(vm.id)
        if existing is not None:
            return existing
        self._transfers.append(vm)
        if self._inner.is_constructed:
            vm.construct()
        self._inner.append(vm.inner)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "transfers"))
        return vm
```

(Net: `register` becomes 5 lines; the previous body moved into `register_vm` verbatim except for the `TransferVM(model, ...)` line which is now in `register`.)

- [ ] **Step 4: Re-run the test — should pass**

```bash
uv run pytest tests/unit/vm/file_manager/test_transfers.py -v
```

Expected: all transfers-VM tests pass, including the new one.

- [ ] **Step 5: Full gate (no snapshot or theme change in this task)**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 632 + 1 = `633 passed` (the +1 is the new test_register_vm; the +6 from Task 6 + +3 from Task 4 + +10 from Task 3 already in the baseline = 613 + 19 = 632 before this task).

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/vm/file_manager/transfers_vm.py tests/unit/vm/file_manager/test_transfers.py
git commit -m "feat(vm): TransfersVM.register_vm for pre-built TransferVM injection

register(model) constructs its own TransferVM with time.monotonic,
which silently discards any pre-built VM a caller may have prepared
with a custom clock. The snapshot harness for the upcoming
TransfersOverlay redesign needs deterministic speed/eta values that
depend on TransferVM._speed_window being populated under a fake
clock.

Added TransfersVM.register_vm(vm: TransferVM) -> TransferVM as the
lower-level primitive. register(model) now delegates to it after
constructing a default-clock VM, so existing callers are unaffected.

Idempotent on transfer-id collision (returns the existing VM, does
not dispose the passed-in one — caller owns it in that case).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: TransfersOverlay card redesign (custom bar, meta row, themed)

**Files:**
- Modify: `src/aws_tui/ui/widgets/transfers_overlay.py` (drop `ProgressBar`, render custom 10-cell bar, restructure to card layout with state-colored left bar, add meta row)
- Modify: 10× `src/aws_tui/ui/themes/{theme}.tcss` (replace existing `TransferRowWidget`/`TransfersOverlay` rules)
- Create: `tests/snapshot/apps/transfers.py`
- Create: `tests/snapshot/test_transfers.py`
- Modify: `src/aws_tui/vm/chrome/resume_vm.py` exports — re-export `humanize_bytes` if not already public (verify)

**Interfaces:**
- Consumes: `TransferVM.current_speed`, `TransferVM.current_eta` (added in Task 6), `TransferVM.model` (existing), `humanize_bytes` from `vm/chrome/resume_vm.py` (existing — verify it's in `__all__`), `TransfersVM.register_vm` (added in Task 6.5).
- Produces: redesigned overlay rows; 10 new snapshot goldens.

- [ ] **Step 1: Verify `humanize_bytes` is importable**

```bash
grep -n "humanize_bytes" src/aws_tui/vm/chrome/resume_vm.py
```

Expected: it's defined and in `__all__`. (Confirmed during plan-writing.)

- [ ] **Step 2: Replace the entire `TransfersOverlay` widget body**

Open `src/aws_tui/ui/widgets/transfers_overlay.py`. Replace the entire file with the version below. Read the existing file first so you know what's going away (especially the `_arm_linger` logic — it stays).

```python
"""TransfersOverlay — top-right floating box listing in-progress transfers.

Each :class:`TransferVM` in :class:`TransfersVM` gets one
:class:`TransferRowWidget`: a card with a state-colored left bar
(``\$accent`` running / ``\$success`` done / ``\$danger`` failed),
title row, destination row, custom 10-cell progress bar + cancel chip,
and a meta row (bytes done/total + speed + eta).

Wiring: the overlay docks on the ``notifications`` layer (same one
ToastStack uses) so it floats above the dual-pane without taking flow
space. It listens for ``transfers`` PropertyChanged on the hub and
re-mounts children on each batch update.
"""

from __future__ import annotations

import os

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.resume_vm import humanize_bytes
from aws_tui.vm.file_manager.transfer_vm import TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM

# Seconds a completed/failed/cancelled transfer stays visible before it
# fades out. Long enough that the user notices completion; short enough
# that the box doesn't accumulate cruft. Override with $AWS_TUI_TRANSFER_LINGER
# (used by tests so they don't have to sleep).
_LINGER_SECONDS: float = float(os.environ.get("AWS_TUI_TRANSFER_LINGER", "3.0"))

#: Width of the custom progress bar in cells.
_BAR_CELLS: int = 10
_BAR_FILLED: str = "▰"  # ▰
_BAR_EMPTY: str = "▱"   # ▱


def _last_segment(uri: str) -> str:
    """Shorten a label to just the trailing path segment for the overlay."""
    cleaned = uri.rstrip("/")
    if not cleaned or "/" not in cleaned:
        return cleaned or "?"
    return cleaned.rsplit("/", 1)[-1]


def _format_eta(seconds: float | None) -> str:
    """Human-readable mm:ss / h:mm:ss for the ETA cell."""
    if seconds is None:
        return "--:--"
    total = int(seconds)
    if total < 0:
        return "--:--"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _state_class(state: TransferState) -> str:
    """CSS modifier class corresponding to a transfer state."""
    return {
        TransferState.PENDING: "-pending",
        TransferState.RUNNING: "-running",
        TransferState.PAUSED: "-paused",
        TransferState.COMPLETED: "-done",
        TransferState.FAILED: "-failed",
        TransferState.CANCELLED: "-cancelled",
    }.get(state, "-pending")


class TransferRowWidget(HubSubscriberMixin, Widget):
    """One card-style row inside the overlay — bound to a :class:`TransferVM`.

    Subscribes to the transfer's own ``state`` PropertyChanged so the
    bar / meta line / state class refresh without rebuilding the row."""

    DEFAULT_CSS = """
    TransferRowWidget {
        height: 5;
        width: 100%;
        padding: 0 1;
        border-left: thick transparent;
    }
    TransferRowWidget > .transfer-title-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    TransferRowWidget > .transfer-dest-row {
        height: 1;
        width: 100%;
    }
    TransferRowWidget > .transfer-bar-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    TransferRowWidget > .transfer-meta-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    TransferRowWidget .transfer-name { width: 1fr; }
    TransferRowWidget .transfer-state-word { width: auto; text-align: right; }
    TransferRowWidget .transfer-bar { width: 1fr; }
    TransferRowWidget .transfer-cancel {
        width: 5;
        height: 1;
        text-align: center;
        margin: 0 0 0 1;
    }
    TransferRowWidget .transfer-bytes { width: 1fr; }
    TransferRowWidget .transfer-rate { width: auto; text-align: right; }
    """

    def __init__(self, transfer_vm: TransferVM, *, hub: MessageHub[Message]) -> None:
        super().__init__(classes=f"transfer-row {_state_class(transfer_vm.state)}")
        self._vm: TransferVM = transfer_vm
        self._hub: MessageHub[Message] = hub

    @property
    def transfer_vm(self) -> TransferVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Horizontal(classes="transfer-title-row"):
            yield Static(self._name_text(), classes="transfer-name", markup=False)
            yield Static(self._state_word(), classes="transfer-state-word", markup=False)
        yield Static(self._dest_text(), classes="transfer-dest-row", markup=False)
        with Horizontal(classes="transfer-bar-row"):
            yield Static(self._bar_text(), classes="transfer-bar", markup=False)
            yield Static("[✕]", id="cancel-btn", classes="transfer-cancel", markup=False)
        with Horizontal(classes="transfer-meta-row"):
            yield Static(self._bytes_text(), classes="transfer-bytes", markup=False)
            yield Static(self._rate_text(), classes="transfer-rate", markup=False)

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def on_click(self, event: Click) -> None:
        # Bubble: react when the click landed on our Cancel Static.
        # Cancelled / completed / failed rows ignore clicks (the chip is dim).
        if self._vm.is_finished:
            return
        target = event.widget if hasattr(event, "widget") else None
        node: object | None = target
        while node is not None:
            if isinstance(node, Static) and getattr(node, "id", None) == "cancel-btn":
                self._vm.cancel_command.execute()
                return
            node = getattr(node, "parent", None)

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name != "state":
            return
        # Refresh state-class modifier and the four lines.
        self._sync_state_class()
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        try:
            self.query_one(".transfer-name", Static).update(self._name_text())
            self.query_one(".transfer-state-word", Static).update(self._state_word())
            self.query_one(".transfer-dest-row", Static).update(self._dest_text())
            self.query_one(".transfer-bar", Static).update(self._bar_text())
            self.query_one(".transfer-bytes", Static).update(self._bytes_text())
            self.query_one(".transfer-rate", Static).update(self._rate_text())
        except NoMatches:
            return

    def _sync_state_class(self) -> None:
        for cls in ("-pending", "-running", "-paused", "-done", "-failed", "-cancelled"):
            self.remove_class(cls)
        self.add_class(_state_class(self._vm.state))

    def _name_text(self) -> str:
        return _last_segment(self._vm.model.source_label)

    def _dest_text(self) -> str:
        return f"→ {_last_segment(self._vm.model.destination_label)}"

    def _state_word(self) -> str:
        state = self._vm.state
        pct = self._percentage()
        if state is TransferState.RUNNING:
            return f"↑ {pct}%" if pct is not None else "↑ ..."
        if state is TransferState.PAUSED:
            return f"⏸ {pct}%" if pct is not None else "⏸ ..."
        if state is TransferState.COMPLETED:
            return "✓ done"
        if state is TransferState.FAILED:
            return "✗ failed"
        if state is TransferState.CANCELLED:
            return "⊘ cancelled"
        return "..."

    def _bar_text(self) -> str:
        pct = self._percentage()
        if pct is None:
            return _BAR_EMPTY * _BAR_CELLS
        filled = round(pct / 100.0 * _BAR_CELLS)
        filled = max(0, min(filled, _BAR_CELLS))
        return (_BAR_FILLED * filled) + (_BAR_EMPTY * (_BAR_CELLS - filled))

    def _bytes_text(self) -> str:
        done = self._vm.model.bytes_done
        total = self._vm.model.bytes_total
        if total is None or total <= 0:
            return f"{humanize_bytes(done)} · streaming…"
        return f"{humanize_bytes(done)} / {humanize_bytes(total)}"

    def _rate_text(self) -> str:
        if self._vm.is_finished:
            return ""
        speed = self._vm.current_speed
        eta = self._vm.current_eta
        if speed is None:
            return ""
        speed_str = f"{humanize_bytes(int(speed))}/s"
        eta_str = _format_eta(eta)
        return f"{speed_str} · {eta_str}"

    def _percentage(self) -> int | None:
        total = self._vm.model.bytes_total
        if total is None or total <= 0:
            return None
        return int(self._vm.model.bytes_done / total * 100)


class TransfersOverlay(Widget):
    """Top-right floating box that aggregates active + recently-finished
    transfers."""

    DEFAULT_CSS = """
    TransfersOverlay {
        layer: notifications;
        dock: right;
        offset: 0 2;
        width: 44;
        height: auto;
        max-height: 60%;
        padding: 1 0;
    }
    TransfersOverlay.-hidden { display: none; }
    TransfersOverlay #transfers-overlay-inner {
        width: 100%;
        height: auto;
    }
    TransfersOverlay #transfers-overlay-title {
        height: 1;
        width: 100%;
        padding: 0 2 1 2;
        text-style: bold;
    }
    """

    def __init__(
        self,
        vm: TransfersVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: TransfersVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None
        self._expired_ids: set[str] = set()

    @property
    def vm(self) -> TransfersVM:
        return self._vm

    def compose(self) -> ComposeResult:
        yield Static("▌ TRANSFERS", id="transfers-overlay-title", markup=False)
        yield Vertical(id="transfers-overlay-inner")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)
        self._rebuild()

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.sender_object is not self._vm:
            return
        if msg.property_name != "transfers":
            return
        self.call_after_refresh(self._rebuild)

    def _rebuild(self) -> None:
        try:
            container = self.query_one("#transfers-overlay-inner", Vertical)
        except NoMatches:
            return

        visible: list[TransferVM] = []
        for t in self._vm.transfers:
            if t.id in self._expired_ids:
                continue
            if t.is_active or t.state is TransferState.PENDING:
                visible.append(t)
            elif t.is_finished:
                visible.append(t)
                self._arm_linger(t.id)

        new_ids = {t.id for t in visible}
        for row in list(container.query(TransferRowWidget)):
            if row.transfer_vm.id not in new_ids:
                row.remove()

        currently_mounted = {row.transfer_vm.id for row in container.query(TransferRowWidget)}
        for t in visible:
            if t.id not in currently_mounted:
                container.mount(TransferRowWidget(t, hub=self._hub))

        if visible:
            self.remove_class("-hidden")
        else:
            self.add_class("-hidden")

    def _arm_linger(self, transfer_id: str) -> None:
        """Schedule ``transfer_id`` to expire from the overlay after the
        configured linger interval. Idempotent on repeat calls."""
        if transfer_id in self._expired_ids:
            return

        def _expire() -> None:
            self._expired_ids.add(transfer_id)
            self.call_after_refresh(self._rebuild)

        self.set_timer(_LINGER_SECONDS, _expire)


__all__ = ["TransferRowWidget", "TransfersOverlay"]
```

- [ ] **Step 3: Replace the per-theme TransfersOverlay/TransferRowWidget rules — carbon first**

In `src/aws_tui/ui/themes/carbon.tcss`, replace the entire block from `/* ── Transfers overlay … ──*/` through the end of the `TransferRowWidget ProgressBar` rules with:

```
/* ── Transfers overlay (top-right floating box) ──────────────────────── */
TransfersOverlay {
    background: $bg-elev 92%;
    border: round $rule-dim;
}
TransfersOverlay #transfers-overlay-title {
    color: $accent;
}

TransferRowWidget {
    border-left: thick $rule-dim;
    background: $bg;
}
TransferRowWidget.-running        { border-left: thick $accent; }
TransferRowWidget.-paused         { border-left: thick $warning; }
TransferRowWidget.-done           { border-left: thick $success; }
TransferRowWidget.-failed         { border-left: thick $danger; }
TransferRowWidget.-cancelled      { border-left: thick $text-muted; }

TransferRowWidget .transfer-name        { color: $text; text-style: bold; }
TransferRowWidget .transfer-state-word  { color: $text-muted; }
TransferRowWidget.-running    .transfer-state-word  { color: $accent; }
TransferRowWidget.-paused     .transfer-state-word  { color: $warning; }
TransferRowWidget.-done       .transfer-state-word  { color: $success; }
TransferRowWidget.-failed     .transfer-state-word  { color: $danger; }
TransferRowWidget.-cancelled  .transfer-state-word  { color: $text-muted; }

TransferRowWidget .transfer-dest-row { color: $text-muted; }

TransferRowWidget .transfer-bar         { color: $accent;     background: $bg; }
TransferRowWidget.-running    .transfer-bar { color: $accent; }
TransferRowWidget.-paused     .transfer-bar { color: $warning; }
TransferRowWidget.-done       .transfer-bar { color: $success; }
TransferRowWidget.-failed     .transfer-bar { color: $danger; }
TransferRowWidget.-cancelled  .transfer-bar { color: $text-muted; }

TransferRowWidget .transfer-cancel {
    color: $text;
    background: $bg-elev;
    border: round $rule-dim;
}
TransferRowWidget .transfer-cancel:hover {
    background: $danger;
    color: $bg;
}

TransferRowWidget .transfer-bytes { color: $text-muted; }
TransferRowWidget .transfer-rate  { color: $text-muted; }
```

- [ ] **Step 4: Copy the block to all 9 other themes**

Identical structure, only `$token` references — copy verbatim into the same location in each of the remaining 9 themes.

- [ ] **Step 5: Create the snapshot harness app**

Create `tests/snapshot/apps/transfers.py`:

```python
"""TransfersOverlay snapshot harness."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.transfers_overlay import TransfersOverlay
from aws_tui.vm.file_manager.transfer_vm import TransferModel, TransferState, TransferVM
from aws_tui.vm.file_manager.transfers_vm import TransfersVM


def _model(*, id: str, bytes_done: int, bytes_total: int | None,
           state: TransferState, src: str, dst: str,
           direction: str = "upload") -> TransferModel:
    return TransferModel(
        id=id,
        direction=direction,
        source_label=src,
        destination_label=dst,
        bytes_done=bytes_done,
        bytes_total=bytes_total,
        state=state,
    )


class TransfersSnapshotApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._theme = theme
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._transfers = TransfersVM(hub=self._hub, dispatcher=self._dispatcher)
        # Inject a fake clock so speed/eta render deterministically in the
        # snapshot. Two samples 1s apart with 700_000 vs 1_240_000 bytes ->
        # speed = 540_000 B/s ≈ 527.3 KB/s; ETA = (2_000_000-1_240_000)/540_000
        # ≈ 1.41 s
        clock_ticks = iter([0.0, 1.0])

        def fake_clock() -> float:
            return next(clock_ticks)

        running = TransferVM(
            _model(id="r", bytes_done=700_000, bytes_total=2_000_000,
                   state=TransferState.RUNNING,
                   src="/Users/kaveh/repo.tar.gz",
                   dst="s3://prod/repo.tar.gz"),
            hub=self._hub, dispatcher=self._dispatcher,
            clock=fake_clock,
        )
        # First sample at t=0 with bytes_done=700_000; second sample at t=1.0
        # with bytes_done=1_240_000. The second apply_update IS the visible
        # state in the snapshot. Use register_vm (Task 6.5) so the fake-clock
        # samples survive — register(model) would discard this VM and build
        # its own with time.monotonic.
        running.construct()
        running.apply_update(bytes_done=700_000, bytes_total=2_000_000,
                             state=TransferState.RUNNING)
        running.apply_update(bytes_done=1_240_000, bytes_total=2_000_000,
                             state=TransferState.RUNNING)
        self._transfers.register_vm(running)

        done = TransferVM(
            _model(id="d", bytes_done=458_000, bytes_total=458_000,
                   state=TransferState.COMPLETED,
                   src="/Users/kaveh/backup.zip",
                   dst="s3://archive/backup.zip"),
            hub=self._hub, dispatcher=self._dispatcher,
        )
        done.construct()
        self._transfers.register_vm(done)

        failed = TransferVM(
            _model(id="f", bytes_done=120_000, bytes_total=4_200_000,
                   state=TransferState.FAILED,
                   src="/Users/kaveh/2026-Q2.csv",
                   dst="s3://reports/2026-Q2.csv"),
            hub=self._hub, dispatcher=self._dispatcher,
        )
        failed.construct()
        self._transfers.register_vm(failed)
        self._transfers.construct()

    def compose(self) -> ComposeResult:
        yield Static("aws-tui (behind transfers overlay)", id="placeholder")
        yield TransfersOverlay(self._transfers, hub=self._hub)


__all__ = ["TransfersSnapshotApp"]
```

- [ ] **Step 6: Create the snapshot test file**

Create `tests/snapshot/test_transfers.py`:

```python
"""Snapshot tests for TransfersOverlay across all 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.transfers import TransfersSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_transfers(theme: str, snap_compare) -> None:
    assert snap_compare(TransfersSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)
```

- [ ] **Step 7: Generate the snapshots**

```bash
uv run pytest tests/snapshot/test_transfers.py --snapshot-update -q
```

Expected: `10 passed`. Ten new files under `tests/snapshot/__snapshots__/test_transfers/`.

- [ ] **Step 8: Visually inspect three goldens (carbon, voidline, amber)**

Open in browser:
- `tests/snapshot/__snapshots__/test_transfers/test_transfers[carbon].raw`
- `tests/snapshot/__snapshots__/test_transfers/test_transfers[voidline].raw`
- `tests/snapshot/__snapshots__/test_transfers/test_transfers[amber].raw`

Confirm: 3 cards stacked vertically; running row has accent-colored left bar; completed row has success-colored left bar; failed row has danger-colored left bar; bar fills (▰/▱) render; meta row shows bytes + speed + eta on the running row; cancel `[✕]` chip rendered.

- [ ] **Step 9: Confirm no other snapshots shifted**

```bash
git status tests/snapshot/__snapshots__/ | grep "\.raw" | grep -v "test_transfers"
```

Expected: empty.

- [ ] **Step 10: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 633 + 10 = `643 passed`.

- [ ] **Step 11: Commit**

```bash
git add src/aws_tui/ui/widgets/transfers_overlay.py src/aws_tui/ui/themes/*.tcss tests/snapshot/apps/transfers.py tests/snapshot/test_transfers.py tests/snapshot/__snapshots__/test_transfers/
git commit -m "feat(ui,themes): TransfersOverlay card redesign with custom bar + meta

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §7:

- Dropped textual.widgets.ProgressBar (fights theme tokens) and
  replaced with a custom 10-cell Static-rendered bar using
  ▰ (filled) + ▱ (empty). We own per-state coloring entirely.

- Row layout is now a 5-line card:
    ▌ <name>                                  ↑ 62%
      → s3://bucket/dst/
      ▰▰▰▰▰▰▱▱▱▱                          [✕]
      1.2 GB / 1.9 GB        540 KB/s · 0:01
  with the left border-thick colored by state (\$accent running,
  \$success done, \$danger failed, \$warning paused, \$text-muted
  cancelled).

- State word in the top-right echoes the left-bar color via per-
  state CSS modifier classes (.-running, .-paused, .-done, etc.)
  that the widget sets on itself in _sync_state_class.

- Cancel chip is [✕] in a rounded \$bg-elev box; hover swaps to
  \$danger background.

- Meta row uses humanize_bytes (re-exported from
  vm/chrome/resume_vm.py) for bytes and speed; ETA formatted as
  m:ss / h:mm:ss via _format_eta.

Snapshots: tests/snapshot/apps/transfers.py composes a 3-transfer
scenario (running 62%, completed lingering, failed). 10 new
goldens.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: ToastStack polish (per-theme borders + level colors)

**Files:**
- Modify: 10× `src/aws_tui/ui/themes/{theme}.tcss` (add `Toast` + level rules)
- Create: `tests/snapshot/apps/toast.py`
- Create: `tests/snapshot/test_toast.py`

**Interfaces:**
- Consumes: `Toast.LEVEL_CLASS` already applies `-info` / `-success` / `-warning` / `-error` per the widget's `__init__`. No widget code changes needed.
- Produces: 10 new snapshot goldens at `tests/snapshot/__snapshots__/test_toast/`.

- [ ] **Step 1: Add Toast rules to carbon as the template**

Append to `src/aws_tui/ui/themes/carbon.tcss` (right before the `ServicesHamburger` block or after the `TransferRowWidget` rules):

```
/* ── Toast stack (top-right notifications layer) ─────────────────────── */
ToastStack {
    background: transparent;
}

Toast {
    background: $bg-elev;
    color: $text;
    border: round $rule-dim;
    padding: 0 1;
}
Toast.-info    { border: round $rule-dim; }
Toast.-success { border: round $success;  color: $success; }
Toast.-warning { border: round $warning;  color: $warning; }
Toast.-error   { border: round $danger;   color: $danger; }
```

- [ ] **Step 2: Copy to all 9 other themes**

Same block in each of the remaining 9 theme files. Token references are identical; the appearance differs because each theme's tokens differ.

- [ ] **Step 3: Create the snapshot harness**

Create `tests/snapshot/apps/toast.py`:

```python
"""ToastStack snapshot harness.

Composes one INFO toast (theme-change-style) and one ERROR toast (with
an action label so the action chip is exercised).
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Static
from vmx import MessageHub, RxDispatcher

from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.toast import ToastStack
from aws_tui.vm.chrome.toast_stack_vm import ToastStackVM
from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel


class ToastSnapshotApp(App[None]):
    def __init__(self, *, theme: str = "carbon") -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._theme = theme
        self._hub: MessageHub = MessageHub()
        self._dispatcher = RxDispatcher.immediate()
        self._stack = ToastStackVM(hub=self._hub, dispatcher=self._dispatcher)
        self._stack.construct()
        # Two toasts: one INFO (theme-change-like) and one ERROR with
        # action chip (auth-expired-like).
        self._stack.raise_toast(
            ToastModel(
                id="info-1",
                text="Theme changed to: carbon",
                level=ToastLevel.INFO,
                sticky=True,
                timeout_seconds=None,
                action_label=None,
                action_action=None,
            )
        )
        self._stack.raise_toast(
            ToastModel(
                id="err-1",
                text="Auth expired",
                level=ToastLevel.ERROR,
                sticky=True,
                timeout_seconds=None,
                action_label="authenticate",
                action_action="auth.authenticate",
            )
        )

    def compose(self) -> ComposeResult:
        yield Static("aws-tui (behind toasts)", id="placeholder")
        yield ToastStack(self._stack, hub=self._hub)


__all__ = ["ToastSnapshotApp"]
```

- [ ] **Step 4: Create the test file**

Create `tests/snapshot/test_toast.py`:

```python
"""Snapshot tests for ToastStack across all 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.toast import ToastSnapshotApp
from tests.snapshot.conftest import TERMINAL_SIZE, THEMES


@pytest.mark.parametrize("theme", THEMES)
def test_toast_stack(theme: str, snap_compare) -> None:
    assert snap_compare(ToastSnapshotApp(theme=theme), terminal_size=TERMINAL_SIZE)
```

- [ ] **Step 5: Generate snapshots**

```bash
uv run pytest tests/snapshot/test_toast.py --snapshot-update -q
```

Expected: `10 passed`. Ten new files under `tests/snapshot/__snapshots__/test_toast/`.

- [ ] **Step 6: Visually inspect carbon + voidline + amber**

Confirm: each toast has a visible rounded border in its level color (info = rule-dim, error = danger); text inside reads correctly; action chip `[authenticate]` is visible on the error toast.

- [ ] **Step 7: Confirm no other snapshots shifted**

```bash
git status tests/snapshot/__snapshots__/ | grep "\.raw" | grep -v "test_toast"
```

Expected: empty.

- [ ] **Step 8: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: 643 + 10 = `653 passed`.

- [ ] **Step 9: Commit**

```bash
git add src/aws_tui/ui/themes/*.tcss tests/snapshot/apps/toast.py tests/snapshot/test_toast.py tests/snapshot/__snapshots__/test_toast/
git commit -m "feat(themes): per-theme borders + level colors on Toast across all 10 themes

Per docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md §8:
toasts had padding but no border in v0.7.x — they read as floating
text rather than discrete units. Added per-level border + color:

  Toast.-info    → border \$rule-dim
  Toast.-success → border + color \$success
  Toast.-warning → border + color \$warning
  Toast.-error   → border + color \$danger

ToastStack itself gets background: transparent so the per-toast
borders read against the chrome rather than against a stack-level
background fill.

Snapshots: tests/snapshot/apps/toast.py composes one INFO + one
ERROR toast (the ERROR carries action_label='authenticate' so the
action chip is exercised). 10 new goldens.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: CHANGELOG + final verification

**Files:**
- Modify: `CHANGELOG.md` (new bullets in `[Unreleased] ### Changed` and `### Fixed`)

**Interfaces:** none.

- [ ] **Step 1: Add CHANGELOG entries**

Open `CHANGELOG.md`. Find the `[Unreleased] ### Changed` section. Add at the top of the existing bullets:

```
- **Theme picker now previews themes live as the cursor moves**
  through the picker; pressing `Esc` rolls back to the
  originally-active theme; `Enter` commits the cursored theme.
  Implemented via a new `ThemePickerVM.preview_command` distinct
  from `pick_theme_command` so commit and preview are semantically
  separate.
- **TransfersOverlay rows redesigned as state-coloured cards.**
  Each transfer is a 5-line card with a colored left border that
  reflects the state (accent = running, success = done, danger =
  failed, warning = paused, muted = cancelled), a custom 10-cell
  progress bar (replaced Textual's `ProgressBar` which fought
  theme tokens), and a meta row showing bytes done/total + speed
  + ETA. Speed/ETA derive from a new rolling 5-second sample
  window on `TransferVM`.
- **Toast notifications now have per-theme borders coloured by
  level** (info = rule-dim, success/warning/error use their
  respective tokens). Previously toasts had padding but no border,
  which read as floating text on the chrome.
```

Find the `[Unreleased] ### Fixed` section. Add at the top:

```
- **Modal button labels no longer spill past button borders.**
  `ModalButton` was fixed-width 18 with padding 0 3, leaving only
  12 cells for the label; long labels like "Authenticate" clipped
  through the right border. Buttons are now `width: auto` with a
  `min-width: 14` and `padding: 0 2`. Every theme's
  `ConfirmModal > Container > .modal-footer > ModalButton`
  override was updated to match.
- **ThemePickerModal now has full per-theme styling.** Previously
  it shipped only with inline `DEFAULT_CSS` and fell back to
  bare-terminal background/border on every theme. Each of the 10
  themes now defines a `ThemePickerModal` block with rounded
  $bg-elev frame, $accent title, and $bg-sel / $accent-soft
  cursor highlight matching the file-pane cursor pattern.
- **ConfirmModal long path values no longer push the modal wider
  than its 70-col bound.** Path-value chips now `text-wrap: nowrap`
  and `text-overflow: ellipsis`.
- **Delete-modal "Confirm" button shifted +2 cells right** as a UX
  guardrail so a reflex Enter immediately after the modal opens
  doesn't land where the cursor was. (Borrowed from macOS NSAlert.)
- **Services-rail selected-row treatment now matches the file-pane
  cursor pattern** ($bg-sel + $accent-soft + bold) across all 10
  themes — the rail and the panes now read as siblings.
```

- [ ] **Step 2: Confirm `[Unreleased]` still has one of each subsection (Keep-a-Changelog discipline)**

```bash
grep -nE "^### " CHANGELOG.md | head -10
```

Expected: `### Changed`, `### Added`, `### Fixed`, `### Removed`, `### Testing`, `### Deferred / v0.8 roadmap` — in that order, one of each, all inside the `[Unreleased]` block (which ends at the `## [0.7.0] - 2026-06-14` heading).

- [ ] **Step 3: Final full-suite gate**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --tb=short -q
uv run pytest -m integration --tb=short -q
bash scripts/check-layers.sh
uv run pre-commit run --all-files
```

Expected:
- ruff / ruff-format / mypy / check-layers / pre-commit: all clean.
- pytest default tier: `653 passed, 9 deselected` (613 baseline + 30 new snapshots + 6 transfer-speed + 3 theme-picker-preview + 1 register_vm).
- pytest integration tier: `9 passed`.

- [ ] **Step 4: Verify out-of-scope snapshots still untouched**

```bash
git diff --name-only origin/main..HEAD -- tests/snapshot/__snapshots__/ | grep -vE "(test_theme_picker|test_transfers|test_toast|test_confirm_modal_copy_paths|test_confirm_modal_danger|test_main_screen)" | head -20
```

Expected: empty. If any out-of-scope golden appears, that's a regression introduced by the polish — investigate before commit.

- [ ] **Step 5: Commit the changelog**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): modal + toast polish entries

Captures the user-visible changes from the polish branch under
[Unreleased]:

### Changed
- Theme picker live preview on cursor + Esc rollback.
- TransfersOverlay card redesign + speed/ETA meta.
- Toast per-theme borders + level colors.

### Fixed
- ModalButton label spill (auto-width + min-width 14).
- ThemePickerModal full per-theme CSS (was unstyled).
- ConfirmModal path-value chip nowrap + ellipsis.
- Delete-modal Confirm button +2 cell right-shift.
- Services-rail selected-row matches file-pane cursor pattern.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin polish/modal-toast-2026-06-19
gh pr create --base main --head polish/modal-toast-2026-06-19 --title "polish: modal + toast UI across all 10 themes" --body "$(cat <<'EOF'
## Summary

Implements the design spec at \`docs/superpowers/specs/2026-06-19-modal-toast-polish-design.md\`.

- **Bug fix:** \`ModalButton\` label spill across all confirm modals.
- **Bug fix:** \`ThemePickerModal\` had zero per-theme CSS; now fully themed in all 10 themes.
- **Feature:** Theme picker previews live on cursor move; Esc rolls back to the originally-active theme.
- **Feature:** TransfersOverlay rows redesigned as state-coloured cards with custom 10-cell progress bar + bytes/speed/eta meta row. Dropped Textual's \`ProgressBar\` so we fully own theme adoption.
- **Feature:** Toasts get per-theme borders coloured by level.
- **Audit:** Services-rail selected-row treatment standardised on the file-pane cursor pattern across all themes.
- **Tests:** +30 snapshot goldens (theme picker, transfers, toast, each \xc3\x97 10 themes); +9 unit tests (theme picker preview + transfer speed window). 30 existing goldens refreshed (confirm copy/danger + main screen \xc3\x97 10).

## Test plan

- [x] \`uv run pytest\` — 653 default-tier pass.
- [x] \`uv run pytest -m integration\` — 9 opt-in MinIO pass.
- [x] \`uv run ruff check src tests\`, \`uv run ruff format --check\`, \`uv run mypy src\`, \`scripts/check-layers.sh\`, \`uv run pre-commit run --all-files\` — clean.
- [x] Out-of-scope snapshots verified unchanged (74 goldens: 5 deferred modals \xc3\x97 10 + pane states \xc3\x97 24).
- [ ] CI on this PR (let it run; mirrors local).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR created. CI should mirror local results. After CI green, merge with `gh pr merge <num> --squash --delete-branch`.

---

## Acceptance check (re-stated)

After Task 9 commit + push, the spec's §11 acceptance criteria are checked:

1. **No label spill on any modal at ≥80 cols.** — Task 1 + Task 2.
2. **Every theme has a `ThemePickerModal` block referencing the 6 listed tokens.** — Task 3.
3. **Theme picker live preview + Esc rollback + Enter commit.** — Task 4.
4. **TransfersOverlay rows render the card layout.** — Task 7.
5. **ToastStack toasts have visible per-theme borders with level-appropriate accent.** — Task 8.
6. **All snapshot tests pass.** — every task.
7. **Out-of-scope 74 goldens unchanged.** — verified at every task.
8. **Full-suite gate clean.** — verified at every task.
9. **CHANGELOG documents the changes.** — Task 9.

---

## Self-review notes (for the writing-plans skill record)

- **Spec coverage:** every section §3–§9 of the spec maps to at least one task. §3 (frame primitive) is enacted across Tasks 1, 3, 7, 8. §4 (ConfirmModal) is Tasks 1+2. §5 (ThemePicker) is Tasks 3+4. §6 (Services rail) is Task 5. §7 (TransfersOverlay) is Tasks 6+7. §8 (Toast) is Task 8. §9 (Snapshot coverage) is distributed across Tasks 3, 7, 8 with refresh steps in 1, 2, 5. §10 (chunking) and §11 (acceptance) drove the task split. §12 (risks) is reflected in the per-task gate steps.
- **Placeholder scan:** no "TBD" / "TODO" / "implement later". Every code block is the actual code; every CSS block is the actual rule; every commit message is the literal text.
- **Type consistency:** `preview_command: RelayCommandOf[str]` consistent across Tasks 3, 4, plus matches the existing `pick_theme_command: RelayCommandOf[str]` signature. `current_speed: float | None` / `current_eta: float | None` consistent across Tasks 6 and 7. `clock: Callable[[], float] = time.monotonic` consistent across Task 6 test fixture and Task 7 snapshot harness.
- **No references to undefined types or functions.** `humanize_bytes` import in Task 7 verified during plan-writing (lives in `vm/chrome/resume_vm.py`, in `__all__`). `THEMES` and `TERMINAL_SIZE` confirmed in `tests/snapshot/conftest.py`. All 14 theme tokens used in the plan exist in every theme (carbon spot-checked).
