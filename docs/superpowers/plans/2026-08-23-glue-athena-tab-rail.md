# Glue and Athena Tab Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Glue's double-framed context presentation with independently framed controls and replace the shared Glue/Athena `Views` pane with a persistent underline tab rail using the approved soft-fill focus treatment.

**Architecture:** Keep `ServiceTabStrip` as the single focusable interaction owner and centralize its real theme selectors in `operational-panes.tcss`, while retaining a usable component-level fallback for custom themes. Rename Glue's enclosing context widget to an unframed layout row, preserving the existing `ServiceSourceHeader`, `ContextPicker`, `FocusCoordinatorVM`, and page/VM event boundaries.

**Tech Stack:** Python 3.11-3.13, Textual 8.2.8, VMx, TCSS, pytest, pytest-asyncio, pytest-textual-snapshot, MkDocs Material

## Global Constraints

- Work on `codex/glue-athena-tab-rail-design`, based on current `develop`.
- The approved design is `docs/superpowers/specs/2026-08-23-glue-athena-tab-rail-design.md`.
- Glue alone loses the enclosing `AWS context` frame; Athena, S3, and EMR context framing remains unchanged.
- `ServiceTabStrip` remains one logical focus stop with immediate Left/Right activation.
- The active tab always has an accent underline and stronger label, including while unfocused.
- A focused tab rail adds only the approved soft `$bg-sel` fill behind the active tab.
- Do not add a preview selection state, commands, key bindings, dependencies, VM state, or VMx abstractions.
- Preserve all source switching, filtering, pagination, refresh, AWS gateway, and error-state behavior.
- Use existing theme tokens and keep all ten built-in themes behaviorally equivalent.
- Keep stable geometry at wide `(150, 44)`, compact `(100, 30)`, and narrow `(80, 24)` terminal sizes.
- Update canonical documentation and verify generated site and wiki parity when canonical text changes.

---

## File Structure

- `src/aws_tui/ui/widgets/service_tab_strip.py`: one-stop tab interaction plus structural and custom-theme fallback styling.
- `src/aws_tui/ui/themes/operational-panes.tcss`: canonical built-in-theme styling for the shared tab rail and operational panes.
- `src/aws_tui/ui/themes/{amber,carbon,dracula,github-light,gruvbox-dark,lattice,nord,one-light,solarized-light,voidline}.tcss`: remove legacy Glue/Athena tab selectors that target classes no longer rendered.
- `src/aws_tui/ui/widgets/glue/page.py`: compose and size Glue's unframed source/filter row.
- `tests/unit/ui/test_service_tab_strip.py`: selected, focused, one-stop, and activation behavior.
- `tests/unit/ui/test_themes.py`: shared-selector ownership, token contrast, legacy-selector removal, and Glue/Athena frame contracts.
- `tests/unit/ui/glue/test_page.py`: Glue row identity, focusable child controls, inline expansion geometry, and unchanged focus ring.
- `tests/unit/ui/athena/test_page.py`: regression guard for Athena's retained grouped context frame.
- `tests/snapshot/apps/glue.py`: deterministic focused-tab snapshot state.
- `tests/snapshot/apps/athena.py`: focus the actual `ServiceTabStrip` in the existing focused fixture.
- `tests/snapshot/test_glue.py`: focused, unfocused, open-picker, narrow, compact, wide, and content-guard coverage.
- `tests/snapshot/test_athena.py`: retained context frame, shared rail, focused rail, and narrow open-picker coverage.
- `tests/snapshot/__snapshots__/test_glue/*.raw`: regenerated Glue SVG goldens.
- `tests/snapshot/__snapshots__/test_athena/*.raw`: regenerated Athena SVG goldens.
- `docs/keybindings.md`: user-facing Glue/Athena layout and focus explanation.
- `docs/architecture.md`: shared widget responsibility description.
- `docs/adding-a-service.md`: guidance for reusing the shared tab rail.

---

### Task 1: Build the shared underline tab rail and remove legacy theme rules

**Files:**
- Modify: `tests/unit/ui/test_service_tab_strip.py`
- Modify: `tests/unit/ui/test_themes.py`
- Modify: `src/aws_tui/ui/widgets/service_tab_strip.py:23-82`
- Modify: `src/aws_tui/ui/themes/operational-panes.tcss:1-14`
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

**Interfaces:**
- Consumes: existing `ServiceTabStrip(tabs, active, id, tab_id_prefix)`, `set_active(value)`, `Changed(value)`, `-active`, and `-highlighted` contracts.
- Produces: the same Python API and messages, with a borderless two-row rail whose rendered `.service-tab.-active` state persists independently of focus.

- [ ] **Step 1: Add a failing persistent-selection and soft-focus test**

Add `Button` to the Textual widget imports, yield a deterministic second focus target from `TabHost.compose()`, and add this test:

```python
from textual.widgets import Button


class TabHost(App[None]):
    def __init__(self, tabs: ServiceTabStrip) -> None:
        super().__init__()
        self.tabs = tabs
        self.changes: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.tabs
        yield Button("After", id="after-tabs")

    def on_service_tab_strip_changed(self, event: ServiceTabStrip.Changed) -> None:
        self.changes.append(event.value)


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
        assert tabs.border_title is None
        assert active.has_class("-active")
        assert active.styles.border_bottom != inactive.styles.border_bottom
        assert active.styles.background == inactive.styles.background

        tabs.focus()
        await pilot.pause()

        assert active.styles.background != inactive.styles.background
        assert tabs.region.size == resting_size

        after.focus()
        await pilot.pause()

        assert active.has_class("-active")
        assert active.styles.border_bottom != inactive.styles.border_bottom
        assert active.styles.background == inactive.styles.background
        assert tabs.region.size == resting_size
```

- [ ] **Step 2: Run the widget test and confirm the old pane styling fails**

Run:

```bash
uv run pytest tests/unit/ui/test_service_tab_strip.py::test_service_tab_strip_keeps_selection_and_adds_soft_focus_fill -v
```

Expected: FAIL because `border_title` is still `Views`, the active and inactive bottom borders match, or the focused state still depends on the full accent block.

- [ ] **Step 3: Replace dead-selector theme tests with the actual shared widget contract**

In `tests/unit/ui/test_themes.py`, replace `test_focused_athena_tab_uses_contrast_safe_tokens` and add shared ownership and legacy-removal guards:

```python
def test_service_tab_strip_structure_is_shared_theme_owned() -> None:
    shared = (
        resources.files("aws_tui.ui.themes")
        .joinpath("operational-panes.tcss")
        .read_text(encoding="utf-8")
    )
    expected = {
        "ServiceTabStrip": ("background: $bg;", "color: $text-muted;"),
        "ServiceTabStrip > .service-tab": (
            "color: $text-muted;",
            "border-bottom: solid $rule-dim;",
        ),
        "ServiceTabStrip > .service-tab.-active": (
            "color: $text;",
            "border-bottom: solid $accent;",
            "text-style: bold;",
        ),
        "ServiceTabStrip:focus > .service-tab.-active": (
            "background: $bg-sel;",
            "color: $text;",
        ),
    }

    for selector, declarations in expected.items():
        bodies = _bodies_for_selector(shared, selector)
        assert bodies, f"shared stylesheet missing {selector}"
        assert any(all(declaration in body for declaration in declarations) for body in bodies)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_focused_service_tab_uses_contrast_safe_tokens(name: str) -> None:
    content = ThemeStore().load(name)
    tokens = _theme_tokens(content)
    bodies = _bodies_for_selector(
        content,
        "ServiceTabStrip:focus > .service-tab.-active",
    )

    assert bodies
    assert any("background: $bg-sel;" in body and "color: $text;" in body for body in bodies)
    ratio = _contrast_ratio(tokens["$text"], tokens["$bg-sel"])
    assert ratio >= 4.5, f"theme {name}: focused service tab contrast is {ratio:.2f}:1"


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_themes_do_not_retain_legacy_service_tab_selectors(name: str) -> None:
    content = _raw_builtin_theme(name)

    for selector in (
        "GluePage > #glue-view-tabs",
        "GluePage .glue-view-tab",
        "AthenaPage > #athena-view-tabs",
        "AthenaPage .athena-view-tab",
    ):
        assert selector not in content
```

- [ ] **Step 4: Run the theme contract tests and confirm they fail against the legacy rules**

Run:

```bash
uv run pytest tests/unit/ui/test_themes.py -k 'service_tab or operational_pane' -v
```

Expected: FAIL because `operational-panes.tcss` does not yet own real `.service-tab` styling and every built-in still contains legacy service-specific tab rules.

- [ ] **Step 5: Implement the borderless, stable-height component fallback**

Replace `ServiceTabStrip.DEFAULT_CSS` and remove `self.border_title = "Views"` from `__init__`:

```python
    DEFAULT_CSS: ClassVar[str] = """
    ServiceTabStrip {
        width: 1fr;
        height: 2;
        min-height: 2;
        layout: horizontal;
        border: none;
    }
    ServiceTabStrip > .service-tab {
        width: 1fr;
        height: 2;
        content-align: center middle;
        color: $text-muted;
        border-bottom: solid transparent;
    }
    ServiceTabStrip > .service-tab.-active {
        color: $text;
        text-style: bold;
        border-bottom: solid $accent;
    }
    ServiceTabStrip:focus > .service-tab.-active {
        background: $accent 20%;
    }
    """
```

Do not change `Changed`, `set_active`, `_move`, `_commit_highlighted`, or `_sync_tabs`; they already enforce immediate activation and synchronized active/highlighted state.

- [ ] **Step 6: Centralize built-in theme styling and remove legacy duplication**

Append this shared block to `operational-panes.tcss`:

```tcss
/* Shared service-local underline tab rail */
ServiceTabStrip {
    background: $bg;
    color: $text-muted;
}
ServiceTabStrip > .service-tab {
    color: $text-muted;
    border-bottom: solid $rule-dim;
}
ServiceTabStrip > .service-tab.-active {
    color: $text;
    border-bottom: solid $accent;
    text-style: bold;
}
ServiceTabStrip:focus > .service-tab.-active {
    background: $bg-sel;
    color: $text;
}
```

In every built-in theme file listed for this task:

1. Delete each complete rule whose selector starts with `GluePage > #glue-view-tabs`, `GluePage .glue-view-tab`, or `AthenaPage .athena-view-tab`.
2. Remove `AthenaPage > #athena-view-tabs,` from the grouped Athena background selector while leaving `#athena-context-header`, query controls, result status, and result footer unchanged.

The resulting Athena grouped selector must have this exact shape:

```tcss
AthenaPage > #athena-context-header,
AthenaPage #athena-query-controls,
AthenaPage #athena-results-status,
AthenaPage #athena-results-footer {
    background: $bg-elev; color: $text-muted;
}
```

- [ ] **Step 7: Run focused tab behavior and all theme tests**

Run:

```bash
uv run pytest tests/unit/ui/test_service_tab_strip.py tests/unit/ui/test_themes.py -v
```

Expected: PASS, including existing one-stop, Left/Right, Enter, Space, and `set_active` behavior tests plus all ten theme parsers and contrast guards.

- [ ] **Step 8: Commit the shared rail**

```bash
git add src/aws_tui/ui/widgets/service_tab_strip.py src/aws_tui/ui/themes tests/unit/ui/test_service_tab_strip.py tests/unit/ui/test_themes.py
git commit -m "refactor(ui): render service views as tab rail"
```

---

### Task 2: Replace Glue's context pane with an unframed control row

**Files:**
- Modify: `tests/unit/ui/glue/test_page.py`
- Modify: `tests/unit/ui/athena/test_page.py`
- Modify: `tests/unit/ui/test_themes.py`
- Modify: `src/aws_tui/ui/widgets/glue/page.py:79-174`
- Modify: `src/aws_tui/ui/themes/operational-panes.tcss:1-14`

**Interfaces:**
- Consumes: `ServiceSourceHeader`, `ContextPicker`, `FocusSlot.GLUE_SOURCE`, `FocusSlot.GLUE_FILTER`, and the existing Glue view visibility synchronization.
- Produces: `Horizontal#glue-context-row`, an unframed layout-only parent containing the same source and filter widget IDs and preserving the existing typed focus order.

- [ ] **Step 1: Add failing Glue structure and expansion-geometry tests**

Add `Horizontal` and `NoMatches` imports to `tests/unit/ui/glue/test_page.py`, then add:

```python
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches


@pytest.mark.asyncio
async def test_glue_context_controls_use_an_unframed_layout_row() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        row = page.query_one("#glue-context-row", Horizontal)
        source = page.query_one("#glue-source-header-picker", ContextPicker)

        assert row.border_title is None
        assert row.styles.border_top[0] == "none"
        assert source.border_title == "AWS source"
        assert source.styles.border_top[0] in {"solid", "heavy"}
        with pytest.raises(NoMatches):
            page.query_one("#glue-context-pane")


@pytest.mark.asyncio
async def test_open_glue_filter_stays_inside_layout_flow_at_narrow_width() -> None:
    vm, _fake = _build_vm()
    await vm.setup()
    await vm.select_view("jobs")
    app = _GlueApp(vm)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        row = page.query_one("#glue-context-row", Horizontal)
        source = page.query_one("#glue-source-header")
        run_filter = page.query_one("#glue-run-state-filter", ContextPicker)
        tabs = page.query_one("#glue-view-tabs", ServiceTabStrip)
        view_host = page.query_one("#glue-view-host")

        run_filter.open()
        await pilot.pause()

        assert source.region.right <= run_filter.region.x
        assert row.region.bottom <= tabs.region.y
        assert tabs.region.bottom <= view_host.region.y
```

- [ ] **Step 2: Add an Athena regression guard for the retained grouped context frame**

Add `Horizontal` to `tests/unit/ui/athena/test_page.py` and add:

```python
from textual.containers import Horizontal


@pytest.mark.asyncio
async def test_athena_retains_its_grouped_context_header() -> None:
    vm, _client = _build_vm()
    await vm.setup()
    app = _AthenaApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.query_one("#athena-context-header", Horizontal)

        assert header.border_title == "AWS context"
        assert app.query_one("#athena-source-header") in header.children
        assert app.query_one("#athena-workgroup") in header.children
        assert app.query_one("#athena-catalog") in header.children
        assert app.query_one("#athena-database") in header.children
```

- [ ] **Step 3: Update theme ownership tests for the Glue-only exception**

Remove Glue's context pane selectors from both loops in `test_operational_pane_structure_is_shared_theme_owned`, retain every Athena and content-pane assertion, and add:

```python
def test_glue_context_layout_has_no_theme_owned_frame() -> None:
    shared = (
        resources.files("aws_tui.ui.themes")
        .joinpath("operational-panes.tcss")
        .read_text(encoding="utf-8")
    )

    assert not _bodies_for_selector(shared, "GluePage > #glue-context-pane")
    assert not _bodies_for_selector(shared, "GluePage > #glue-context-row")
    assert _bodies_for_selector(shared, "AthenaPage > #athena-context-header")
    assert _bodies_for_selector(shared, "AthenaPage > #athena-context-header:focus-within")
```

Also remove `GluePage > #glue-context-pane` and its `:focus-within` form from the built-in duplication-guard selector tuples because the old selector must no longer be part of any active contract.

- [ ] **Step 4: Run the new layout tests and confirm the old Glue pane fails**

Run:

```bash
uv run pytest tests/unit/ui/glue/test_page.py::test_glue_context_controls_use_an_unframed_layout_row tests/unit/ui/glue/test_page.py::test_open_glue_filter_stays_inside_layout_flow_at_narrow_width tests/unit/ui/athena/test_page.py::test_athena_retains_its_grouped_context_header tests/unit/ui/test_themes.py::test_glue_context_layout_has_no_theme_owned_frame -v
```

Expected: the Glue tests and theme ownership test FAIL because `#glue-context-pane` still exists and owns the outer border; the Athena guard PASSes.

- [ ] **Step 5: Rename and restyle the Glue layout container without changing child controls**

In `GluePage.DEFAULT_CSS`, replace the context-pane rules with:

```python
    GluePage > #glue-context-row {
        width: 1fr;
        height: auto;
        min-height: 3;
        layout: horizontal;
    }
    GluePage > #glue-context-row > ServiceSourceHeader,
    GluePage > #glue-context-row > ContextPicker {
        width: 1fr;
        height: auto;
        min-height: 3;
    }
```

Change only the layout container ID in `compose()`:

```python
        with Horizontal(id="glue-context-row"):
```

Delete this line from `on_mount()`:

```python
        self.query_one("#glue-context-pane").border_title = "AWS context"
```

Do not rename `glue-source-header`, `glue-run-state-filter`, `glue-crawler-state-filter`, or `glue-view-tabs`; focus mapping and commands depend on those stable IDs.

- [ ] **Step 6: Remove only Glue's context frame from the shared operational stylesheet**

Replace the opening context rules in `operational-panes.tcss` with Athena-only selectors:

```tcss
/* Athena grouped context header */
AthenaPage > #athena-context-header {
    background: $bg;
    color: $text;
    border: solid $rule-dim;
    border-title-color: $text;
}
AthenaPage > #athena-context-header:focus-within {
    border: solid $accent;
    border-title-color: $accent;
}
```

Leave the Glue Iceberg and all other Glue/Athena operational content-pane rules unchanged.

- [ ] **Step 7: Run Glue, Athena, theme, focus, command, and routing regressions**

Run:

```bash
uv run pytest tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py tests/unit/ui/test_themes.py tests/integration/test_glue_page.py tests/integration/test_glue_page_routing.py tests/integration/test_athena_page.py -q
```

Expected: PASS. Existing forward/reverse focus rings, numeric commands, click/keyboard tab activation, source selection, filters, refresh, and routing remain green.

- [ ] **Step 8: Commit the Glue-only framing change**

```bash
git add src/aws_tui/ui/widgets/glue/page.py src/aws_tui/ui/themes/operational-panes.tcss tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py tests/unit/ui/test_themes.py
git commit -m "refactor(glue): unframe context controls"
```

---

### Task 3: Prove focused, unfocused, open-picker, and responsive visual states

**Files:**
- Modify: `tests/snapshot/apps/glue.py`
- Modify: `tests/snapshot/apps/athena.py`
- Modify: `tests/snapshot/test_glue.py`
- Modify: `tests/snapshot/test_athena.py`
- Modify: `tests/snapshot/__snapshots__/test_glue/*.raw`
- Modify: `tests/snapshot/__snapshots__/test_athena/*.raw`

**Interfaces:**
- Consumes: the production `ServiceTabStrip` CSS and Glue/Athena page composition from Tasks 1 and 2.
- Produces: deterministic fixture controls for focused rails and SVG goldens covering all themes plus wide, compact, narrow, focused, unfocused, and open-picker states.

- [ ] **Step 1: Add deterministic Glue focused-tab fixture support**

Import `ServiceTabStrip`, add a `focus_tabs` argument, store it, and focus the parent strip after mounting:

```python
from aws_tui.ui.widgets.service_tab_strip import ServiceTabStrip


class GluePageApp(App[None]):
    def __init__(
        self,
        *,
        theme: str,
        view: GlueView = "catalog",
        fixture: GlueFixture = "populated",
        iceberg_view: IcebergView = "snapshots",
        open_picker: bool = False,
        focus_tabs: bool = False,
    ) -> None:
        super().__init__()
        self.CSS = ThemeStore().load(theme)
        self._focus_tabs = focus_tabs
```

At the end of `on_mount()`:

```python
        if self._open_picker:
            self.query_one("#glue-run-state-filter").open()
        if self._focus_tabs:
            self.query_one("#glue-view-tabs", ServiceTabStrip).focus()
```

- [ ] **Step 2: Make Athena's focused fixture focus the actual one-stop widget**

Import `ServiceTabStrip` in `tests/snapshot/apps/athena.py` and replace the child-focus line with:

```python
        if self._fixture == "focused-rebound-tabs":
            self.query_one("#athena-view-tabs", ServiceTabStrip).focus()
```

This is snapshot-fixture correction only; production focus behavior does not change.

- [ ] **Step 3: Add focused rail, narrow open-picker, and textual content guards**

Add these Glue snapshot cases:

```python
@pytest.mark.parametrize("theme", THEMES)
def test_glue_focused_tab_snapshot(theme: str, snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme=theme, focus_tabs=True),
        terminal_size=WIDE,
    )


def test_glue_open_context_picker_narrow_snapshot(snap_compare) -> None:
    assert snap_compare(
        GluePageApp(theme="carbon", view="jobs", open_picker=True),
        terminal_size=NARROW,
    )
```

In `test_glue_snapshot_content_guards`, add:

```python
    assert "&#160;AWS&#160;source&#160;" in catalog
    assert "&#160;AWS&#160;context&#160;" not in catalog
    assert "&#160;Views&#160;" not in catalog
```

Add this Athena snapshot case:

```python
def test_athena_open_context_picker_narrow_snapshot(snap_compare) -> None:
    assert snap_compare(
        AthenaPageApp(
            theme="carbon",
            fixture="empty-query",
            open_picker=True,
        ),
        terminal_size=NARROW,
    )
```

In `test_athena_snapshot_content_guards`, add:

```python
    assert "&#160;AWS&#160;context&#160;" in empty
    assert "&#160;Views&#160;" not in empty
```

- [ ] **Step 4: Run snapshots without updating and confirm the approved visual changes are detected**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py -q
```

Expected: FAIL with snapshot mismatches and missing new focused/narrow goldens. The diffs should be limited to removal of Glue's outer context frame, removal of both `Views` frames, the two-row underline rail, soft focused-tab fill, and consequent vertical space redistribution.

- [ ] **Step 5: Regenerate the affected SVG goldens**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py --snapshot-update
```

Expected: PASS and updated files only beneath `tests/snapshot/__snapshots__/test_glue/` and `tests/snapshot/__snapshots__/test_athena/`.

- [ ] **Step 6: Visually inspect four representative goldens**

Inspect these exact generated SVG files, copying each `.raw` file to a temporary `.svg` filename first if the image viewer requires an SVG extension:

```text
tests/snapshot/__snapshots__/test_glue/test_glue_focused_tab_snapshot[carbon].raw
tests/snapshot/__snapshots__/test_glue/test_glue_open_context_picker_narrow_snapshot.raw
tests/snapshot/__snapshots__/test_athena/test_athena_all_theme_snapshot[focused-rebound-tabs-carbon].raw
tests/snapshot/__snapshots__/test_athena/test_athena_open_context_picker_narrow_snapshot.raw
```

Confirm all of the following in the images:

- Glue has no outer `AWS context` frame and its source/filter controls do not overlap.
- Athena still has its `AWS context` frame.
- Neither service displays a `Views` frame or title.
- The active underline is visible while unfocused.
- Focus adds a muted fill only behind the active tab.
- Labels, tab widths, and content columns remain aligned at narrow and wide sizes.

- [ ] **Step 7: Re-run snapshot tests without update mode**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py -q
```

Expected: PASS with all content guards and generated goldens stable.

- [ ] **Step 8: Commit snapshot fixtures and reviewed goldens**

```bash
git add tests/snapshot/apps/glue.py tests/snapshot/apps/athena.py tests/snapshot/test_glue.py tests/snapshot/test_athena.py tests/snapshot/__snapshots__/test_glue tests/snapshot/__snapshots__/test_athena
git commit -m "test(ui): cover service tab rail states"
```

---

### Task 4: Synchronize canonical docs and run the complete verification gate

**Files:**
- Modify: `docs/keybindings.md:127-182`
- Modify: `docs/architecture.md:22-34`
- Modify: `docs/adding-a-service.md:100-107`

**Interfaces:**
- Consumes: final UI behavior and screenshots from Tasks 1-3.
- Produces: matching in-repo, generated site, and generated wiki descriptions with no claim that Glue retains an outer context frame or that either service uses a bordered `Views` pane.

- [ ] **Step 1: Update the Glue and Athena user workflow descriptions**

Replace the opening Glue paragraph in `docs/keybindings.md` with:

```markdown
Glue is a single-context AWS service. It keeps one active connection
and region for the whole page; S3-compatible connections are excluded. The
bordered **AWS source** selector stands on its own. Jobs and Crawlers add an
adjacent bordered state selector without an enclosing context frame. The
underline view rail is one Tab stop; its active view remains underlined after
focus moves into content. Focus a selector and press `Enter` or `Space` to open
it; use the arrow keys and `Enter` to commit a value, or `Esc` to close it
without changing the value.
```

After the opening Athena context paragraph, add:

```markdown
The underline view rail uses the same persistent selected state and single
keyboard focus stop as Glue; Athena's grouped **AWS context** frame remains
because its selectors form one dependent control region.
```

- [ ] **Step 2: Update architecture and service-extension guidance**

Replace the shared widget sentences in `docs/architecture.md` with:

```markdown
  `ContextPicker` provides bordered keyboard-focusable context selection, and
  `ServiceTabStrip` renders a persistent underline rail while providing one
  predictable focus stop for service-local views.
```

Replace the shared-widget bullet in `docs/adding-a-service.md` with:

```markdown
    - Reuse `ServiceSourceHeader` and `ContextPicker` for shared source and
      context controls, and `ServiceTabStrip` for a one-stop underline view
      rail. Extend `FocusCoordinatorVM` instead of creating another focus
      authority. Add an enclosing context frame only when multiple dependent
      controls form one coherent region; a standalone source control does not
      require a second frame.
```

- [ ] **Step 3: Verify canonical, site, and wiki documentation parity**

Run:

```bash
make docs-check
uv run pytest tests/docs -q
```

Expected: both commands PASS; `check_docs: clean`; strict MkDocs build succeeds; generated site and wiki inputs have no drift.

- [ ] **Step 4: Run the focused implementation regression suite**

Run:

```bash
uv run pytest tests/unit/ui/test_service_tab_strip.py tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py tests/unit/ui/test_themes.py tests/integration/test_glue_page.py tests/integration/test_glue_page_routing.py tests/integration/test_athena_page.py tests/integration/test_service_source_swap.py tests/snapshot/test_glue.py tests/snapshot/test_athena.py -q
```

Expected: PASS with no failures, errors, or snapshot changes.

- [ ] **Step 5: Run repository-wide tests and static gates**

Run:

```bash
uv run pytest tests/unit tests/integration tests/e2e -q
uv run pytest tests/snapshot -q
uv run pre-commit run --all-files --show-diff-on-failure
```

Expected: every command PASS. Pre-commit includes Ruff, Ruff formatting, mypy, architecture-layer checks, whitespace, YAML/TOML, and shell/Taplo checks.

- [ ] **Step 6: Confirm scope and worktree cleanliness**

Run:

```bash
git diff --check
git status --short
git diff --stat develop...HEAD
```

Expected: `git diff --check` prints nothing; status lists only the three intended documentation files before the final commit; the branch diff contains the approved UI, tests, goldens, design, plan, and docs with no dependency, VM, gateway, or unrelated changes.

- [ ] **Step 7: Commit synchronized documentation**

```bash
git add docs/keybindings.md docs/architecture.md docs/adding-a-service.md
git commit -m "docs: describe service tab rail layout"
```

- [ ] **Step 8: Run final post-commit verification**

Run:

```bash
git status --short --branch
uv run pytest tests/unit/ui/test_service_tab_strip.py tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py tests/snapshot/test_glue.py tests/snapshot/test_athena.py -q
make docs-check
```

Expected: the branch is clean and every final focused and documentation gate PASSes.
