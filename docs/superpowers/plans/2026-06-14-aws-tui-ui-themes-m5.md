# aws-tui M5 (UI layer + themes) Implementation Plan

> **For agentic workers:** Compact-plan format. Largest milestone. Spec §3 (mockups), §4 (UI + themes), §5 (binding), §7 (pane states), §8 (snapshot tests).

**Goal:** Land the entire Textual View layer — widgets bound to the VM tree (M3+M4), the 4 themes (Carbon default + Voidline + Lattice + Amber CRT), action/binding registry, snapshot tests per theme, and 5 E2E journeys.

**Architecture:** Each widget gets a VM injected via ctor. Widgets subscribe to `PropertyChangedMessage` on the hub for their VM and re-render the affected attribute (no full redraw). Inputs route through `ui/actions.py` (action registry id → handler) → `ui/bindings.py` (action ↔ key map from `KeymapStore`) → VM commands. Themes are Textual `.tcss` files in `ui/themes/`; `ThemeStore` (M1) layers user overrides.

**Tech Stack:** Textual ≥0.79 (already a dep), `pytest-textual-snapshot` (already a dev dep). NO `boto3` / `aioboto3` imports in `ui/`. NO direct `infra.aws_session` / `infra.connection_resolver` imports either (per the layer rule).

**Reference:** spec §3 has SVG mockups in `.superpowers/brainstorm/.../03e-svg.html` (don't rely on those — read the spec text instead). spec §4.5 has the Carbon palette table; the other themes have abbreviated palettes (Voidline, Lattice, Amber CRT).

---

## Task 1: `ui/actions.py` + `ui/bindings.py` — action/binding registry

**Files:**
- Create: `src/aws_tui/ui/actions.py`
- Create: `src/aws_tui/ui/bindings.py`
- Create: `tests/unit/ui/__init__.py`
- Create: `tests/unit/ui/test_actions.py`
- Create: `tests/unit/ui/test_bindings.py`

```python
ActionHandler = Callable[[], None | Awaitable[None]]

class ActionRegistry:
    def __init__(self) -> None: ...
    def register(self, action_id: str, handler: ActionHandler) -> None: ...
    def invoke(self, action_id: str) -> None | Awaitable[None]: ...    # raises UnknownAction
    def has(self, action_id: str) -> bool: ...

class BindingResolver:
    """Bridge between Textual's BINDINGS and our KeymapStore."""

    def __init__(self, *, keymap: KeymapStore, actions: ActionRegistry) -> None: ...
    def to_textual_bindings(self) -> list[Binding]: ...    # for App.BINDINGS / Screen.BINDINGS
    def resolve_action_id(self, key: str) -> str | None: ...
```

**Acceptance:**
- Register 3 actions; `invoke` returns awaitable or None.
- `to_textual_bindings()` returns a list of Textual `Binding` objects covering all known actions × all configured keys.
- Strict mypy + layer rules clean.

---

## Task 2: `ui/themes/*.tcss` — full theme files

**Files:**
- Modify: `src/aws_tui/ui/themes/carbon.tcss` (full content per spec §4.5)
- Modify: `src/aws_tui/ui/themes/voidline.tcss`
- Modify: `src/aws_tui/ui/themes/lattice.tcss`
- Modify: `src/aws_tui/ui/themes/amber.tcss`

Each theme:
- Defines `$bg`, `$bg-elev`, `$bg-sel`, `$text`, `$text-muted`, `$text-dim`, `$accent`, `$accent-soft`, `$accent-hot`, `$success`, `$warning`, `$danger`, `$rule-dim`, `$rule-accent`.
- Styles each widget class:
  - `Screen` (full background)
  - `ServicesMenu`, `ServicesMenu > .selected`, `ServicesMenu > .dimmed`
  - `Pane`, `Pane.-focused`, `Pane > .row.-selected`
  - `Breadcrumb`, `ColumnHeader`, `PaneFooter`
  - `HintLegend`, `HintLegend > .key`
  - `StatusBar`
  - `CommandPalette`, `CommandPalette > .selected`, `CommandPalette > .prompt`
  - `ConfirmModal`, `ConfirmModal.-danger`
  - `QuickLook`
  - `ToastStack`, `Toast.-info`, `Toast.-success`, `Toast.-warning`, `Toast.-error`
  - `TransfersTray`, `TransferRow`, `ProgressBar.-success`, `ProgressBar.-accent`
- Bold + dim modifiers per the design

Carbon is the canonical default; other 3 swap palette but keep widget structure. Cheatsheet in `docs/theming.md` lists tokens; use those names.

**Acceptance:**
- Each .tcss parses cleanly via Textual's CSS validator (use `textual.css.parse` or just smoke-load it via `App.stylesheet`).
- Snapshot tests (Task 6) render each theme correctly.

---

## Task 3: `ui/widgets/` — chrome widgets (status bar, hint legend, toast, services menu)

**Files:**
- Create: `src/aws_tui/ui/widgets/status_bar.py` — binds to `StatusBarVM`. Single row at top of screen.
- Create: `src/aws_tui/ui/widgets/hint_legend.py` — binds to `HintLegendVM`. Single row at bottom.
- Create: `src/aws_tui/ui/widgets/toast.py` — `ToastStack` widget binds to `ToastStackVM`; renders one `Toast` per child.
- Create: `src/aws_tui/ui/widgets/services_menu.py` — binds to `ServicesMenuVM`. Vertical list of `ServiceItem`s with `>` accent on focused.
- Tests: `tests/unit/ui/test_chrome_widgets.py` — smoke tests via `App.run_test()` pilot.

Each widget:
- Constructor takes the VM as a parameter
- Subscribes to `PropertyChangedMessage` on the VM's hub (or directly on the VM's reactive properties — pick the cleanest based on M3's pattern)
- Overrides `render()` or uses reactive attributes to redraw

**Acceptance:**
- Each widget mounts in a test App without errors.
- StatusBar shows `connection_label`, `region`, `auth_indicator`, `transfers_summary`.
- HintLegend shows action keys/labels (test with a hint_actions tuple injected via the VM).
- ToastStack renders 3 toasts, each as its own line, with level-based styling.
- ServicesMenu: 3 entries, one focused (with `>` accent).

---

## Task 4: `ui/widgets/pane.py` + `ui/widgets/dual_pane.py`

**Files:**
- Create: `src/aws_tui/ui/widgets/pane.py` — binds to `PaneVM`. Renders breadcrumb header, column headers (name | size | modified), entry rows, footer summary.
- Create: `src/aws_tui/ui/widgets/dual_pane.py` — binds to `DualPaneVM`. Composes 2 `Pane`s side-by-side with a vertical divider.
- Tests: `tests/unit/ui/test_pane_widgets.py`

Pane widget:
- Uses Textual `DataTable` or a custom `ListView` for the entry rows
- Cursor row gets `>` accent + bg-sel tint
- Multi-select rows get `*` mark or similar (theme-configurable)
- Empty / loading / error states rendered in the body area
- Focused-pane border accent (`-focused` class)

**Acceptance:**
- Mounting in a test App with an InMemoryFS-backed PaneVM populates 5 rows.
- Up/down keys navigate (via Textual key router; ultimately call `move_cursor_cmd(±1)` on the VM).
- Switching focus toggles `-focused` class.
- Snapshot test in Task 6 produces a golden for "main screen with left focused".

---

## Task 5: overlay widgets — command palette, confirm modal, quick look, transfers tray

**Files:**
- Create: `src/aws_tui/ui/widgets/command_palette.py` — bound to `CommandPaletteVM`. ModalScreen.
- Create: `src/aws_tui/ui/widgets/confirm_modal.py` — bound to `ConfirmationVM`. ModalScreen with confirm/cancel.
- Create: `src/aws_tui/ui/widgets/quick_look.py` — bound to `QuickLookVM`. ModalScreen with streamed content.
- Create: `src/aws_tui/ui/widgets/transfers_tray.py` — bound to `TransfersVM`. Slide-up panel at bottom (or a screen).
- Tests: `tests/unit/ui/test_overlay_widgets.py`

**Acceptance:** each renders in a test App; keyboard interactions route to VM commands.

---

## Task 6: snapshot tests per theme

**Files:**
- Create: `tests/snapshot/__init__.py`
- Create: `tests/snapshot/conftest.py`
- Create: `tests/snapshot/test_main_screen.py`
- Create: `tests/snapshot/test_modals.py`
- Create: `tests/snapshot/test_pane_states.py`
- Create: `tests/snapshot/snapshots/<theme>/...svg` (auto-generated on first run via `--snapshot-update`)

Per theme (4 themes × N screens):
- Main screen, left pane focused (services menu + dual pane + chrome)
- Command palette overlay
- Confirm modal (destructive)
- Quick Look modal (with sample JSON content)
- Transfers tray (2 active)
- Connection switcher (in command palette)
- Each PaneState placeholder: loading / empty / auth_required / forbidden / unreachable / error
- First-run modal

`tests/snapshot/conftest.py` parametrizes over the 4 themes.

Use `pytest-textual-snapshot`'s `snap_compare` fixture. Terminal size `(120, 40)`. Goldens committed to `tests/snapshot/snapshots/<theme>/`.

**Acceptance:**
- Initial run generates goldens (run with `--snapshot-update`).
- Second run validates against goldens (no diff).
- A theme/widget change requires re-generating goldens (manual reviewer step in PRs).
- Add a `snapshot` job in `.github/workflows/ci.yml` that runs `uv run pytest tests/snapshot -v` (sans `--snapshot-update`).

---

## Task 7: app composition — replace M0 hello-world

**Files:**
- Modify: `src/aws_tui/app.py` — replace the M0 placeholder with the real composition.
- Add: `src/aws_tui/app_screen.py` if it helps separation.
- Modify: `tests/unit/test_app_sanity.py` to check the real composition (still 3 tests).
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_journeys.py` — 5 journeys per spec §8.5:
  1. First launch with cached SSO → silent S3 view
  2. Copy one object from S3 to local (via moto)
  3. Switch AWS → MinIO mid-session with cancel-transfers prompt
  4. Resume a transfer from journal after simulated "crash" (manually write a journal entry, launch, assert resume modal)
  5. Delete with confirm → cancel → no AWS call made

E2E uses `App.run_test()` Pilot + moto + (for #3) MinIO testcontainer.

**Acceptance:**
- `uv run aws-tui` launches the real UI (not the placeholder).
- `q` quits cleanly via graceful shutdown sequence (cancel transfers → close clients → dispose root).
- 5 E2E journeys pass.

Add `e2e` job to ci.yml.

---

## Task 8: commit per task + push + tag v0.6.0

- Per-task commits.
- CHANGELOG bump for `## [0.6.0]`.
- Push, watch CI green (unit matrix + integration MinIO + snapshot per theme + e2e + lint+type + pkg).
- Tag `v0.6.0` ("v0.6.0 — ui + themes (M5)"), gh release.

**Acceptance:** all CI green; aws-tui from `pipx install git+...` launches the real UI; M5 deliverables shipped.

---

## Watch-outs

- **Layer rules**: `ui/` may NOT import `boto3`, `aioboto3`, `botocore`, `aws_tui.infra.aws_session`, `aws_tui.infra.connection_resolver`. Anything UI needs from those is passed via VM construction or via app composition layer. The `app.py` composition lives at the top — it may import from any layer. Update `scripts/check-layers.sh` exemption for `app.py` if needed.

- **Textual reactive system** — Textual has its own reactive attributes (`reactive(...)`). Bridge VMx's `PropertyChangedMessage` to Textual's reactive system: subscribe to hub messages and call `self.<reactive_attr> = new_value` in the widget. Wrap in `@on(PropertyChangedMessage)` if the API allows.

- **Snapshot tests on truecolor backgrounds** — `pytest-textual-snapshot` may have anti-aliasing tolerance. Configure `terminal_size=(120, 40)` consistently. Run against a fixed Python version (3.12) to avoid font-rendering drift in CI.

- **E2E journey #1 (silent SSO)** — fake the SSO cache JSON in tmp_path; assert the app boots without raising a toast or modal.

- **E2E journey #3** — needs a real MinIO container via testcontainers. If Docker isn't available in the CI runner, skip cleanly.

- **`aws-tui` console script** — already wired in pyproject from M0. Verify it still routes to `aws_tui.app:main`.

- **`.tcss` user override** — wire via `ThemeStore`. The `App.CSS_PATH` can't dynamically reload from disk; use Textual's `App.stylesheet.update(...)` or compose CSS at startup. Document the chosen approach in `docs/theming.md`.

- **Time budget**: this milestone is 2 weeks per spec §9.7 estimate. If you hit something genuinely hard at task 5 or 6, scope-cut: ship Carbon + Voidline themes only, leave Lattice + Amber as TODO for a v0.7.0 patch. Document the cut clearly. Do NOT silently leave themes broken.
