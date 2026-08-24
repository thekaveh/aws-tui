# Glue and Athena Segmented Tabs and Layout Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ambiguous underline rail with a shared segmented frame, remove Glue's detached source-header edge, and restore compact horizontal wrapping for Athena's command legend without changing interaction behavior.

**Architecture:** Keep `ServiceTabStrip`, `ServiceSourceHeader`, `ContextPicker`, and `HintLegend` in their existing presentation roles. Express segmented geometry with an outer strip border and non-focusable divided child segments, scope the source-header edge to EMR at the theme layer, and let Textual's `ItemGrid` pack non-regular rows without changing `HintLegendVM` data.

**Tech Stack:** Python 3.12, Textual, VMx, pytest, pytest-textual-snapshot, TCSS, MkDocs.

## Global Constraints

- Work only on `codex/fix-segmented-tabs-command-grid`, based on current `develop`.
- `ServiceTabStrip` remains one logical focus stop; tab ordering, Left/Right wrapping, immediate activation, numeric commands, click, Enter, Space, Tab, and Shift+Tab do not change.
- The rail has one untitled complete outer frame, equal-width segments, internal dividers, persistent active text treatment, and soft selected fill only while focused.
- Glue keeps an unframed context row and a completely bordered `AWS source` picker; only the detached theme-owned left edge is removed.
- Athena's grouped `AWS context` frame and EMR's source-header presentation remain unchanged.
- Command text, ordering, bindings, visibility, and `HintLegendVM` ownership do not change.
- No new VM, VMx abstraction, dependency, command, service behavior, or AWS request is introduced.
- Use TDD for every behavior change and preserve representative narrow/wide and all-theme visual coverage.

---

## File Map

- `src/aws_tui/ui/widgets/service_tab_strip.py`: segmented child classes and stable fallback geometry.
- `src/aws_tui/ui/themes/operational-panes.tcss`: canonical shared segmented-frame colors.
- `src/aws_tui/ui/themes/*.tcss`: scope the existing source-header edge to EMR.
- `src/aws_tui/ui/widgets/hint_legend.py`: opt out of equal-row regularization.
- `tests/unit/ui/test_service_tab_strip.py`: interaction-preserving segmented geometry tests.
- `tests/unit/ui/test_themes.py`: shared rail and source-header selector ownership tests.
- `tests/unit/ui/glue/test_page.py`: computed Glue row/header/picker framing test.
- `tests/unit/ui/test_chrome_widgets.py`: Athena prime-count and narrow/wide legend geometry tests.
- `tests/snapshot/test_glue.py`, `tests/snapshot/test_athena.py`, and their `.raw` snapshots: end-to-end visual contracts.
- `docs/architecture.md`, `docs/adding-a-service.md`: canonical wording for the shared segmented control.
- `generated/site/**`, `generated/wiki/**`, `mkdocs.yml`: regenerated documentation surfaces when canonical text changes.

---

### Task 1: Render Service Views as One Segmented Frame

**Files:**
- Modify: `tests/unit/ui/test_service_tab_strip.py`
- Modify: `tests/unit/ui/test_themes.py`
- Modify: `src/aws_tui/ui/widgets/service_tab_strip.py`
- Modify: `src/aws_tui/ui/themes/operational-panes.tcss`

**Interfaces:**
- Consumes: existing `ServiceTabStrip(tabs, active, id, tab_id_prefix)`, `active`, `set_active(value)`, and `Changed(value)` contracts.
- Produces: `.service-tab.-divided` on every child after the first; stable three-row strip geometry with one complete border and internal dividers.

- [ ] **Step 1: Write failing segmented-geometry tests**

Replace underline-specific assertions in `test_service_tab_strip_keeps_selection_and_adds_soft_focus_fill` and add a divider test:

```python
@pytest.mark.asyncio
async def test_service_tab_strip_renders_one_stable_segmented_frame() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test(size=(90, 12)) as pilot:
        await pilot.pause()
        children = list(tabs.query(".service-tab"))

        assert tabs.border_title is None
        assert tabs.styles.border_top[0] in {"solid", "heavy"}
        assert tabs.styles.border_right[0] in {"solid", "heavy"}
        assert tabs.styles.border_bottom[0] in {"solid", "heavy"}
        assert tabs.styles.border_left[0] in {"solid", "heavy"}
        assert [child.has_class("-divided") for child in children] == [False, True, True]
        assert all(child.region.height == 1 for child in children)
        assert len({child.region.width for child in children}) <= 2


@pytest.mark.asyncio
async def test_service_tab_strip_keeps_selection_and_adds_soft_focus_fill() -> None:
    tabs = _tabs()

    async with TabHost(tabs).run_test() as pilot:
        after = pilot.app.query_one("#after-tabs", Button)
        active = tabs.query_one("#service-tab-catalog")
        inactive = tabs.query_one("#service-tab-jobs")
        after.focus()
        await pilot.pause()

        resting_size = tabs.region.size
        assert active.has_class("-active")
        assert active.styles.color != inactive.styles.color
        assert active.styles.background == inactive.styles.background

        tabs.focus()
        await pilot.pause()

        assert active.styles.background != inactive.styles.background
        assert tabs.region.size == resting_size

        after.focus()
        await pilot.pause()

        assert active.has_class("-active")
        assert active.styles.color != inactive.styles.color
        assert active.styles.background == inactive.styles.background
        assert tabs.region.size == resting_size
```

Update `test_service_tab_strip_structure_is_shared_theme_owned` to require:

```python
expected = {
    "ServiceTabStrip": (
        "background: $bg;",
        "color: $text-muted;",
        "border: solid $rule-dim;",
    ),
    "ServiceTabStrip > .service-tab": ("color: $text-muted;",),
    "ServiceTabStrip > .service-tab.-divided": (
        "border-left: solid $rule-dim;",
    ),
    "ServiceTabStrip > .service-tab.-active": (
        "color: $accent;",
        "text-style: bold;",
    ),
    "ServiceTabStrip:focus > .service-tab.-active": (
        "background: $bg-sel;",
        "color: $text;",
    ),
}
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_service_tab_strip.py tests/unit/ui/test_themes.py::test_service_tab_strip_structure_is_shared_theme_owned -q
```

Expected: failures show no outer frame, no `-divided` classes, and underline-only theme declarations.

- [ ] **Step 3: Implement stable segmented structure**

In `_ServiceTab.__init__`, accept `divided: bool` and append `-divided` to the class list when true. In `ServiceTabStrip.compose`, enumerate tabs and pass `divided=index > 0`.

Replace `ServiceTabStrip.DEFAULT_CSS` with:

```tcss
ServiceTabStrip {
    width: 1fr;
    height: 3;
    min-height: 3;
    layout: horizontal;
    border: solid $accent;
}
ServiceTabStrip > .service-tab {
    width: 1fr;
    height: 1;
    content-align: center middle;
    color: $text-muted;
}
ServiceTabStrip > .service-tab.-divided {
    border-left: solid $accent;
}
ServiceTabStrip > .service-tab.-active {
    color: $accent;
    text-style: bold;
}
ServiceTabStrip:focus > .service-tab.-active {
    background: $accent 20%;
    color: $text;
}
```

Replace the shared rail block in `operational-panes.tcss` with:

```tcss
/* Shared service-local segmented tab frame */
ServiceTabStrip {
    background: $bg;
    color: $text-muted;
    border: solid $rule-dim;
}
ServiceTabStrip > .service-tab {
    color: $text-muted;
}
ServiceTabStrip > .service-tab.-divided {
    border-left: solid $rule-dim;
}
ServiceTabStrip > .service-tab.-active {
    color: $accent;
    text-style: bold;
}
ServiceTabStrip:focus > .service-tab.-active {
    background: $bg-sel;
    color: $text;
}
```

- [ ] **Step 4: Run tab behavior, geometry, and theme tests**

Run:

```bash
uv run pytest tests/unit/ui/test_service_tab_strip.py tests/unit/ui/test_themes.py -q
```

Expected: PASS; existing one-focus-stop and activation tests remain unchanged.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/aws_tui/ui/widgets/service_tab_strip.py src/aws_tui/ui/themes/operational-panes.tcss tests/unit/ui/test_service_tab_strip.py tests/unit/ui/test_themes.py
git commit -m "fix(ui): frame service tabs as segments"
```

---

### Task 2: Remove the Detached Glue Source Edge

**Files:**
- Modify: `src/aws_tui/ui/themes/amber.tcss`
- Modify: `src/aws_tui/ui/themes/carbon.tcss`
- Modify: `src/aws_tui/ui/themes/dracula.tcss`
- Modify: `src/aws_tui/ui/themes/github-light.tcss`
- Modify: `src/aws_tui/ui/themes/gruvbox-dark.tcss`
- Modify: `src/aws_tui/ui/themes/lattice.tcss`
- Modify: `src/aws_tui/ui/themes/nord.tcss`
- Modify: `src/aws_tui/ui/themes/one-light.tcss`
- Modify: `src/aws_tui/ui/themes/solarized-light.tcss`
- Modify: `src/aws_tui/ui/themes/voidline.tcss`
- Modify: `tests/unit/ui/test_themes.py`
- Modify: `tests/unit/ui/glue/test_page.py`

**Interfaces:**
- Consumes: the current `ServiceSourceHeader` composition in Glue, Athena, and EMR.
- Produces: no unscoped theme-owned `ServiceSourceHeader` left edge; an EMR-scoped edge with identical theme tokens.

- [ ] **Step 1: Write failing ownership and computed-style tests**

Add to `tests/unit/ui/test_themes.py`:

```python
@pytest.mark.parametrize("name", ALL_THEMES)
def test_source_header_edge_is_scoped_to_emr(name: str) -> None:
    content = _raw_builtin_theme(name)

    assert not _bodies_for_selector(content, "ServiceSourceHeader")
    bodies = _bodies_for_selector(content, "EmrServerlessPage ServiceSourceHeader")
    assert bodies
    assert any("border-left: solid $rule-dim;" in body for body in bodies)
```

Extend `test_glue_context_controls_use_an_unframed_layout_row`:

```python
header = page.query_one("#glue-source-header", ServiceSourceHeader)
assert header.styles.border_left[0] in {"", "none"}
assert source.styles.border_left[0] in {"solid", "heavy"}
```

Import `ServiceSourceHeader` in the Glue test module.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_themes.py::test_source_header_edge_is_scoped_to_emr tests/unit/ui/glue/test_page.py::test_glue_context_controls_use_an_unframed_layout_row -q
```

Expected: failures report the existing unscoped selector and Glue header left border.

- [ ] **Step 3: Scope all ten built-in theme selectors to EMR**

In every listed theme, change only:

```tcss
ServiceSourceHeader {
```

to:

```tcss
EmrServerlessPage ServiceSourceHeader {
```

Keep the existing background, color, and `border-left` declarations intact.

- [ ] **Step 4: Run Glue, EMR, Athena, and theme regressions**

Run:

```bash
uv run pytest tests/unit/ui/glue/test_page.py tests/unit/ui/emr_serverless tests/unit/ui/athena/test_page.py tests/unit/ui/test_themes.py -q
```

Expected: PASS; Glue has no detached edge, while Athena grouping and EMR layout remain valid.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/aws_tui/ui/themes tests/unit/ui/test_themes.py tests/unit/ui/glue/test_page.py
git commit -m "fix(ui): scope source header edge to EMR"
```

---

### Task 3: Restore Responsive Athena Command Packing

**Files:**
- Modify: `tests/unit/ui/test_chrome_widgets.py`
- Modify: `src/aws_tui/ui/widgets/hint_legend.py`

**Interfaces:**
- Consumes: `HintLegendVM.actions`, `HintLegendVM.global_actions`, and existing chip ordering.
- Produces: non-regular `ItemGrid` packing that supports prime command counts without a one-column collapse.

- [ ] **Step 1: Add a failing Athena prime-count geometry test**

Refactor the local hint host if useful, then add:

```python
@pytest.mark.asyncio
async def test_hint_legend_packs_prime_athena_commands_across_wide_rows() -> None:
    hub: MessageHub = MessageHub()
    vm = HintLegendVM(hub=hub, dispatcher=RxDispatcher.immediate(), keymap=KeymapStore())
    vm.set_current_service("athena")
    vm.construct()
    try:
        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield HintLegend(vm, hub=hub)

        app = _App()
        async with app.run_test(size=(245, 62)) as pilot:
            await pilot.pause()
            legend = app.query_one(HintLegend)
            chips = list(legend.query(".hint-chip"))
            columns = {chip.region.x for chip in chips}
            rows = {chip.region.y for chip in chips}

            assert len(chips) == 17
            assert len(columns) > 1
            assert len(rows) <= 2
            assert all(chip.region.right <= legend.content_region.right for chip in chips)
            assert all(chip.region.bottom <= legend.content_region.bottom for chip in chips)
    finally:
        vm.dispose()
        hub.dispose()
```

Strengthen the existing Glue viewport test with `assert len({chip.region.x for chip in chips}) > 1`.

- [ ] **Step 2: Run the Athena test and confirm RED**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py::test_hint_legend_packs_prime_athena_commands_across_wide_rows -q
```

Expected: FAIL because all 17 chips share one x-coordinate and occupy 17 rows.

- [ ] **Step 3: Disable regular-row divisor reduction**

In `HintLegend.compose`, change only:

```python
regular=False,
```

Update the nearby comment to explain that non-regular packing prevents prime command counts from collapsing to one column while preserving source order.

- [ ] **Step 4: Run chrome and command-VM tests**

Run:

```bash
uv run pytest tests/unit/ui/test_chrome_widgets.py tests/unit/vm/chrome/test_hint_legend.py -q
```

Expected: PASS with Athena in compact horizontal rows and unchanged command data.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/aws_tui/ui/widgets/hint_legend.py tests/unit/ui/test_chrome_widgets.py
git commit -m "fix(ui): pack prime command counts responsively"
```

---

### Task 4: Refresh Visual Contracts and Canonical Documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/adding-a-service.md`
- Modify: `tests/snapshot/__snapshots__/test_glue/*.raw`
- Modify: `tests/snapshot/__snapshots__/test_athena/*.raw`
- Regenerate as required: `generated/site/**`, `generated/wiki/**`, `mkdocs.yml`

**Interfaces:**
- Consumes: completed segmented rail, Glue edge scoping, and responsive command grid.
- Produces: all-theme wide/compact/narrow visual baselines and synchronized three-surface documentation.

- [ ] **Step 1: Update canonical wording**

In `docs/architecture.md`, replace “persistent underline rail” with “persistent segmented frame”. In `docs/adding-a-service.md`, replace “one-stop underline view selector” with “one-stop segmented view selector”. Do not change the documented focus or activation semantics.

- [ ] **Step 2: Regenerate Glue and Athena snapshots**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py --snapshot-update -q
```

Expected: snapshot baselines update for the complete rail frame, removed Glue edge, and compact Athena command rows.

- [ ] **Step 3: Review representative raw snapshots**

Inspect at least Glue Jobs wide and Athena empty Query wide/compact snapshots. Confirm the rail has top, bottom, left, and right edges; separators occur between labels; the Glue source has no detached left edge; and Athena commands occupy compact horizontal rows. Reject and fix any clipping, overlap, or unexpected view-order change before proceeding.

- [ ] **Step 4: Regenerate and verify documentation surfaces**

Run:

```bash
make docs-check
make docs-wiki
```

Expected: diagram rendering, canonical doc checks, generated site/wiki parity, and strict MkDocs build all pass.

- [ ] **Step 5: Run the full repository verification suite**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -q
```

Expected: every command exits 0.

- [ ] **Step 6: Commit Task 4**

```bash
git add docs/architecture.md docs/adding-a-service.md generated mkdocs.yml tests/snapshot/__snapshots__/test_glue tests/snapshot/__snapshots__/test_athena
git commit -m "test(ui): refresh segmented layout contracts"
```

- [ ] **Step 7: Push and open the integration PR**

```bash
git push origin codex/fix-segmented-tabs-command-grid
gh pr create --base develop --head codex/fix-segmented-tabs-command-grid --title "fix(ui): repair Glue and Athena segmented layouts" --body "## Summary
- render Glue and Athena view choices in one segmented frame
- remove Glue's detached source-header edge while preserving its picker border
- keep Athena's command legend compact for prime command counts

## Verification
- full unit and snapshot suite
- ruff, mypy, docs checks, and all-theme visual contracts"
```

Wait for required checks, merge the PR into `develop`, pull latest `develop`, and remove the merged local and remote feature branch.
