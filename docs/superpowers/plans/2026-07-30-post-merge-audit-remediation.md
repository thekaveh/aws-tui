# Post-Merge Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the shipped keymap contract, make command discovery context-correct, remove new theme duplication, and bring all canonical and three-surface documentation into exact semantic parity with the current Glue/Athena implementation.

**Architecture:** The composition root will pass one validated, overlaid `KeymapStore` into the existing `BindingResolver` and VM tree. `CommandPaletteVM` will extend its existing VMx `ScoredFilteredCompositeVM` projection with active-service eligibility, while theme-independent Glue/Athena framing moves into the owning widgets' `DEFAULT_CSS`. Source-derived documentation tests will make action, message, dependency, and architecture ledgers complete by construction.

**Tech Stack:** Python 3.11-3.13, Textual 8.2.8, VMx 3.1.0 public APIs, pytest, pytest-textual-snapshot, Ruff, mypy, MkDocs Material, CairoSVG, GitHub Actions.

## Global Constraints

- Work only on `codex/post-merge-audit-remediation`, based on `develop` commit `a14bc98fce5847f31199d9d44cc2ff255448e09f`.
- Target a later merge into `develop`; do not merge or push changes to `main`.
- Preserve Glue and Athena read-only behavior and explicit Athena execution.
- Preserve exact connection-name and region guards for copy, insert, navigation, and S3 handoff.
- Use only `vmx>=3.1.0,<4.0.0` public APIs.
- Keep `CommandPaletteVM` as the sole visible-entry authority and reuse its existing `CompositeVM` plus `ScoredFilteredCompositeVM`.
- Runtime keymap overlays apply at startup; file watching and hot reload are out of scope.
- Invalid overlays fall back atomically to the entire default keymap.
- Do not approve `pane.copy` and `glue.copy_table_ref` as an alias pair.
- Do not add dependencies.
- Do not regenerate snapshots unless an intentional rendered change remains after focused diagnosis.
- Generated site/wiki trees and `mkdocs.yml` remain ignored; only canonical docs and committed diagram artifacts are staged.
- No completion claim may rely on an automatic pytest rerun.

---

## File and Responsibility Map

**Runtime keymap**

- `src/aws_tui/composition.py`: load, validate, and retain the startup overlay.
- `src/aws_tui/infra/keymap_store.py`: collision and empty-binding semantics; no new scope system.
- `src/aws_tui/app.py`: install resolver output and provide active palette service context.
- `tests/unit/test_composition_initial_theme.py`: composition-level overlay/fallback contract.
- `tests/integration/test_keybinding_wiring.py`: config-to-App-to-Textual dispatch contract.

**Contextual command palette**

- `src/aws_tui/vm/chrome/command_palette_vm.py`: immutable entry availability and VMx-filtered projection.
- `src/aws_tui/app.py`: complete command metadata and current service ID.
- `tests/unit/vm/chrome/test_command_palette.py`: service/global filtering and selection reset.
- `tests/integration/test_command_palette_wiring.py`: user-visible command sets per service.

**Styling**

- `src/aws_tui/ui/widgets/glue/page.py`: Glue-owned context and Iceberg framing.
- `src/aws_tui/ui/widgets/athena/page.py`: Athena-owned context and operational framing.
- `src/aws_tui/ui/themes/*.tcss`: color/theme rules only; remove the repeated 33-line block.
- `tests/unit/ui/test_themes.py`: source-level ownership guard.
- `tests/snapshot/test_glue.py`, `tests/snapshot/test_athena.py`: rendered parity.

**Contract and publication tests**

- `tests/docs/test_contract_parity.py`: source-derived action, message, and version contracts.
- `tests/docs/test_scaffolding.py`: high-level architecture and workflow assertions.
- `tests/docs/test_render_diagrams.py`: architecture entities, landscape geometry, and routing assertions.
- `tests/docs/test_shipped_behavior.py`: shipped keymap prose contract.

**Canonical documentation**

- `README.md`, `CHANGELOG.md`, `docs/index.md`, `docs/keybindings.md`,
  `docs/cookbook.md`, `docs/connections.md`, `docs/architecture.md`,
  `docs/adding-a-service.md`, `docs/contract-ledger.md`, and
  `docs/RELEASING.md`: current behavior.
- `docs/diagrams/architecture.html` and `docs/diagrams/img/architecture.{svg,png}`:
  clipboard/message architecture and generated artifacts.
- `.superpowers/sdd/2026-07-30-task-7-report.md`: missing commit traceability.
- `.github/workflows/ci.yml`: remove the stale hardcoded E2E journey count.

---

### Task 1: Restore the Runtime Keymap Overlay

**Files:**
- Modify: `tests/unit/test_composition_initial_theme.py`
- Modify: `tests/integration/test_keybinding_wiring.py`
- Modify: `src/aws_tui/composition.py:188-222`

**Interfaces:**
- Consumes: `Config.keybindings.bindings: dict[str, str | list[str]]`
- Produces: `AppContext.keymap_store: KeymapStore` containing the valid startup overlay or the complete defaults after one atomic fallback.

- [ ] **Step 1: Verify the branch and baseline are clean**

Run:

```bash
git branch --show-current
git status --short
git merge-base HEAD develop
git rev-parse main origin/main
```

Expected:

```text
codex/post-merge-audit-remediation
<no status output>
a14bc98fce5847f31199d9d44cc2ff255448e09f
0b63c4a73f29a7fa58671163492fd3d0d17b2348
0b63c4a73f29a7fa58671163492fd3d0d17b2348
```

- [ ] **Step 2: Replace the unit test that protects the broken behavior**

Replace
`test_keybinding_overlay_validates_but_does_not_change_live_legend_keys`
with:

```python
def test_keybinding_overlay_becomes_the_runtime_keymap(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = "x"\n',
    )
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.resolve("pane.delete") == ("x",)
        assert ctx.root_vm.chrome.hint_legend._keymap is ctx.keymap_store
    finally:
        ctx.root_vm.dispose()


def test_empty_keybinding_overlay_disables_the_runtime_binding(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = []\n',
    )
    cache.mkdir()
    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.resolve("pane.delete") == ()
    finally:
        ctx.root_vm.dispose()
```

Keep `test_keybinding_collision_logs_clear_error_and_falls_back` unchanged:
`pane.copy = "y"` must still log the collision with
`glue.copy_table_ref` and return `("c",)`. Strengthen its fallback assertion:

```python
assert ctx.keymap_store.all() == KeymapStore().all()
```

Add `KeymapStore` to the test imports and add:

```python
def test_unknown_keybinding_action_logs_and_falls_back_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    _write_config(
        cfg,
        '[keybindings]\n"pane.delete" = "x"\n"unknown.action" = "z"\n',
    )
    cache.mkdir()
    warnings: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        "aws_tui.composition._logger.warning",
        lambda event, *, extra: warnings.append((event, extra)),
    )

    ctx = build_app_context(config_dir=cfg, cache_dir=cache)
    try:
        assert ctx.keymap_store.all() == KeymapStore().all()
        assert warnings[-1][1]["error_type"] == "UnknownAction"
    finally:
        ctx.root_vm.dispose()
```

- [ ] **Step 3: Add a config-to-Textual integration regression**

Add these imports to `tests/integration/test_keybinding_wiring.py`:

```python
from pathlib import Path

from aws_tui.composition import build_app_context
```

Add:

```python
def test_config_overlay_reaches_live_textual_bindings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    config_dir.mkdir()
    cache_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[keybindings]\n"pane.copy" = "ctrl+y"\n"pane.delete" = []\n',
        encoding="utf-8",
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=cache_dir)
    try:
        app = AwsTuiApp(ctx)
        installed = _installed(app)
        assert ("ctrl+y", "dispatch('pane.copy')", True, True) in installed
        assert not any(
            key == "c" and action == "dispatch('pane.copy')"
            for key, action, _, _ in installed
        )
        assert not any(action == "dispatch('pane.delete')" for _, action, _, _ in installed)
        assert ctx.keymap_store.resolve("pane.copy") == ("ctrl+y",)
        assert ctx.root_vm.chrome.hint_legend._keymap is ctx.keymap_store
    finally:
        ctx.root_vm.dispose()
```

`ctrl+y` is non-printable under `_binding_priority`, so its expected Textual
priority is exactly `True`. The test must also prove the former `c` binding
and disabled delete binding are absent.

Add an end-to-end dispatch assertion:

```python
@pytest.mark.asyncio
async def test_config_overlay_dispatches_the_registered_action_once(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    config_dir.mkdir()
    cache_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[keybindings]\n"pane.copy" = "ctrl+y"\n',
        encoding="utf-8",
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=cache_dir)
    app = AwsTuiApp(ctx)
    calls: list[str] = []
    app._actions.register("pane.copy", lambda: calls.append("copy"))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("ctrl+y")
        await pilot.pause()

    assert calls == ["copy"]
```

- [ ] **Step 4: Run the RED tests**

Run:

```bash
uv run pytest tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py -q
```

Expected: the new runtime-store and integration tests fail because
`build_app_context()` still returns the default `KeymapStore`.

- [ ] **Step 5: Retain the valid overlay in the composition root**

Replace the temporary-validation block in `src/aws_tui/composition.py` with:

```python
    try:
        keymap_store = KeymapStore(overlay=keybindings_overlay)
    except (KeybindingCollision, UnknownAction) as exc:
        _logger.warning(
            "composition.keymap_overlay.invalid",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        keymap_store = KeymapStore()
```

Delete the obsolete comment claiming overlays remain deferred and delete the
unconditional `keymap_store = KeymapStore()` after the exception block.

- [ ] **Step 6: Run focused unit and integration tests**

Run:

```bash
uv run pytest tests/unit/test_composition_initial_theme.py tests/unit/infra/test_keymap_store.py tests/unit/ui/test_bindings.py tests/integration/test_keybinding_wiring.py -q
```

Expected: all tests pass. The collision test must still prove atomic fallback.

- [ ] **Step 7: Run formatting and type checks for the touched Python**

Run:

```bash
uv run ruff check src/aws_tui/composition.py tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py
uv run ruff format --check src/aws_tui/composition.py tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py
uv run mypy src/aws_tui/composition.py
```

Expected: all commands exit zero.

- [ ] **Step 8: Commit the keymap restoration**

```bash
git add src/aws_tui/composition.py tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py
git commit -m "fix(keymap): apply configured runtime overlays"
```

---

### Task 2: Scope Command-Palette Entries Through the Existing VMx Projection

**Files:**
- Modify: `tests/unit/vm/chrome/test_command_palette.py`
- Modify: `tests/integration/test_command_palette_wiring.py`
- Modify: `src/aws_tui/vm/chrome/command_palette_vm.py`
- Modify: `src/aws_tui/app.py:118-146`
- Modify: `src/aws_tui/app.py:1418-1440`

**Interfaces:**
- Produces: `PaletteEntry.service_ids: frozenset[str]`
- Produces: `CommandPaletteVM.active_service_id: str | None`
- Produces: `CommandPaletteVM.set_active_service(service_id: str | None) -> None`
- Consumes: `RootVM.content_host.current_id`

- [ ] **Step 1: Add RED VM tests for service eligibility**

Extend the `_entry` helper:

```python
def _entry(
    id_: str,
    label: str,
    category: str = "test",
    keywords: tuple[str, ...] = (),
    service_ids: frozenset[str] | None = None,
) -> PaletteEntry:
    return PaletteEntry(
        id=id_,
        label=label,
        category=category,
        keywords=keywords,
        service_ids=service_ids or frozenset(),
    )
```

Add:

```python
def test_active_service_filters_entries_through_vmx_projection() -> None:
    vm = _build()
    vm.register_entry(_entry("global", "Help"), lambda: None)
    vm.register_entry(
        _entry("glue", "Choose Glue run state", service_ids=frozenset({"glue"})),
        lambda: None,
    )
    vm.register_entry(
        _entry("athena", "Choose Athena workgroup", service_ids=frozenset({"athena"})),
        lambda: None,
    )

    vm.set_active_service("glue")
    assert [entry.id for entry in vm.filtered_entries] == ["global", "glue"]

    vm.set_active_service("athena")
    assert [entry.id for entry in vm.filtered_entries] == ["global", "athena"]

    vm.set_active_service(None)
    assert [entry.id for entry in vm.filtered_entries] == ["global"]
    vm.dispose()


def test_context_change_resets_palette_selection() -> None:
    vm = _build()
    vm.register_entry(_entry("global", "Help"), lambda: None)
    vm.register_entry(
        _entry("glue-1", "Glue catalog", service_ids=frozenset({"glue"})),
        lambda: None,
    )
    vm.register_entry(
        _entry("glue-2", "Glue jobs", service_ids=frozenset({"glue"})),
        lambda: None,
    )
    vm.set_active_service("glue")
    vm.open_command.execute()
    vm.move_selection_command.execute(2)
    assert vm.selected_index == 2

    vm.set_active_service("athena")
    assert vm.selected_index == 0
    assert [entry.id for entry in vm.filtered_entries] == ["global"]
    vm.dispose()
```

- [ ] **Step 2: Replace the global integration expectation with contextual sets**

In `tests/integration/test_command_palette_wiring.py`, define:

```python
_GLOBAL = {"Theme picker", "Cycle theme", "Settings", "Help", "Quit"}
_SOURCE = {"Switch source"}
_GLUE = {
    "Choose Glue run state",
    "Choose Glue crawler state",
    "Copy Glue table reference",
    "Open table location in S3",
    "Query table in Athena",
    "Query Iceberg snapshot in Athena",
}
_ATHENA = {
    "Athena query",
    "Athena history",
    "Athena results",
    "Athena saved queries",
    "Choose Athena workgroup",
    "Choose Athena catalog",
    "Choose Athena database",
    "Insert copied table reference",
    "Execute Athena query",
    "Cancel Athena query",
    "Load more Athena rows",
    "Open Athena result in S3",
    "Open query table in Glue",
}
```

Replace `test_populate_registers_curated_commands` with:

```python
@pytest.mark.asyncio
async def test_palette_projects_only_global_and_active_service_commands(
    app_context_factory,  # type: ignore[no-untyped-def]
) -> None:
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app._populate_command_palette()
        vm = app._app_ctx.command_palette_vm

        vm.set_active_service("glue")
        assert {entry.label for entry in vm.filtered_entries} == (
            _GLOBAL | _SOURCE | _GLUE
        )

        vm.set_active_service("athena")
        assert {entry.label for entry in vm.filtered_entries} == (
            _GLOBAL | _SOURCE | _ATHENA
        )

        for service_id in ("s3", "emr-serverless"):
            vm.set_active_service(service_id)
            assert {entry.label for entry in vm.filtered_entries} == _GLOBAL | _SOURCE

        vm.set_active_service("settings")
        assert {entry.label for entry in vm.filtered_entries} == _GLOBAL
```

Extend `test_colon_opens_command_palette` after the screen assertion:

```python
labels = {
    entry.label
    for entry in app._app_ctx.command_palette_vm.filtered_entries
}
assert labels == _GLOBAL | _SOURCE
```

This proves `action_command_palette()` supplies the current S3 service
context before opening, instead of relying only on direct VM test setup.

- [ ] **Step 3: Run the RED palette tests**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_command_palette.py tests/integration/test_command_palette_wiring.py -q
```

Expected: collection or assertion failure because `service_ids` and
`set_active_service` do not exist.

- [ ] **Step 4: Add service metadata and active context to the VM**

Import `field` beside `dataclass`, then extend `PaletteEntry`:

```python
@dataclass(frozen=True, slots=True)
class PaletteEntry:
    id: str
    label: str
    category: str
    keywords: tuple[str, ...] = ()
    service_ids: frozenset[str] = field(default_factory=frozenset)
```

Document `service_ids` in the dataclass docstring as the eligible content-host
service IDs; an empty set means the entry is global.

Initialize this state in `CommandPaletteVM.__init__`:

```python
self._active_service_id: str | None = None
```

Add:

```python
@property
def active_service_id(self) -> str | None:
    return self._active_service_id

def set_active_service(self, service_id: str | None) -> None:
    if self._active_service_id == service_id:
        return
    self._active_service_id = service_id
    self._hub.send(PropertyChangedMessage.create(self, self.name, "active_service_id"))
    self._recompute_filtered()
```

Change `_score_for_vmx`:

```python
def _score_for_vmx(self, item_inner: ComponentVMOf[PaletteEntry]) -> int | None:
    entry = item_inner.model
    if entry.service_ids and self._active_service_id not in entry.service_ids:
        return None
    score = _score(entry, self._filter_text)
    if score is None:
        return None
    return -score
```

Do not add a second filtered list or view-side predicate.

- [ ] **Step 5: Replace `_PALETTE_COMMANDS` with complete entry metadata**

Define immutable scope constants and use `PaletteEntry` objects:

```python
_SOURCE_SERVICE_IDS = frozenset({"s3", "emr-serverless", "glue", "athena"})
_GLUE_SERVICE_IDS = frozenset({"glue"})
_ATHENA_SERVICE_IDS = frozenset({"athena"})

_PALETTE_COMMANDS: tuple[PaletteEntry, ...] = (
    PaletteEntry("app.themes", "Theme picker", "app"),
    PaletteEntry("app.cycle_theme", "Cycle theme", "app"),
    PaletteEntry(
        "app.swap_source",
        "Switch source",
        "source",
        service_ids=_SOURCE_SERVICE_IDS,
    ),
    PaletteEntry("glue.choose_run_state", "Choose Glue run state", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.choose_crawler_state", "Choose Glue crawler state", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.copy_table_ref", "Copy Glue table reference", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.open_s3_location", "Open table location in S3", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.query_in_athena", "Query table in Athena", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("glue.time_travel_in_athena", "Query Iceberg snapshot in Athena", "glue", service_ids=_GLUE_SERVICE_IDS),
    PaletteEntry("athena.query", "Athena query", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.history", "Athena history", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.results", "Athena results", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.saved", "Athena saved queries", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.choose_workgroup", "Choose Athena workgroup", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.choose_catalog", "Choose Athena catalog", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.choose_database", "Choose Athena database", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.insert_table_ref", "Insert copied table reference", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.execute", "Execute Athena query", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.cancel", "Cancel Athena query", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.load_more", "Load more Athena rows", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.open_result_location", "Open Athena result in S3", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("athena.open_in_glue", "Open query table in Glue", "athena", service_ids=_ATHENA_SERVICE_IDS),
    PaletteEntry("app.open_settings", "Settings", "app"),
    PaletteEntry("app.help", "Help", "app"),
    PaletteEntry("app.quit", "Quit", "app"),
)
```

Format this block with Ruff after implementation. Do not omit any current
entry.

- [ ] **Step 6: Update population and open-time context projection**

Change `_populate_command_palette`:

```python
for entry in _PALETTE_COMMANDS:
    vm.register_entry(
        entry,
        partial(self._actions.invoke, entry.id),
    )
```

Change `action_command_palette`:

```python
def action_command_palette(self) -> None:
    self.record_action("app.command_palette")
    self._populate_command_palette()
    vm = self._app_ctx.command_palette_vm
    vm.set_active_service(self._app_ctx.root_vm.content_host.current_id)
    vm.open_command.execute()
    self.push_screen(CommandPalette(vm, hub=self._app_ctx.hub))
```

- [ ] **Step 7: Run focused palette and app tests**

Run:

```bash
uv run pytest tests/unit/vm/chrome/test_command_palette.py tests/integration/test_command_palette_wiring.py tests/integration/test_keybinding_wiring.py tests/unit/test_app_sanity.py -q
```

Expected: all tests pass and no visible wrong-service command remains.

- [ ] **Step 8: Verify VMx and type contracts**

Run:

```bash
uv run pytest tests/unit/vm/test_vmx_smoke.py tests/unit/vm/test_round3_compliance.py -q
uv run ruff check src/aws_tui/app.py src/aws_tui/vm/chrome/command_palette_vm.py tests/unit/vm/chrome/test_command_palette.py tests/integration/test_command_palette_wiring.py
uv run mypy src/aws_tui/app.py src/aws_tui/vm/chrome/command_palette_vm.py
```

Expected: all commands exit zero; `ScoredFilteredCompositeVM` remains the
visible projection.

- [ ] **Step 9: Commit contextual command discovery**

```bash
git add src/aws_tui/app.py src/aws_tui/vm/chrome/command_palette_vm.py tests/unit/vm/chrome/test_command_palette.py tests/integration/test_command_palette_wiring.py
git commit -m "fix(palette): scope commands to active services"
```

---

### Task 3: Move Operational Framing to Widget-Owned CSS

**Files:**
- Modify: `src/aws_tui/ui/widgets/glue/page.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
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
- Modify: `tests/integration/test_theme_runtime_propagation.py`
- Verify: `tests/snapshot/test_glue.py`
- Verify: `tests/snapshot/test_athena.py`

**Interfaces:**
- Produces: equivalent rendered CSS with Glue selectors owned by `GluePage.DEFAULT_CSS` and Athena selectors owned by `AthenaPage.DEFAULT_CSS`.
- Preserves: theme token values and user overlay precedence.

- [ ] **Step 1: Replace theme-ownership tests and add source regressions**

Add imports:

```python
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.glue.page import GluePage
```

Replace
`test_glue_and_athena_context_panes_use_operational_borders` and
`test_glue_and_athena_operational_surfaces_have_rest_and_focus_borders`.
Those tests currently require structural selectors in every theme and would
contradict the new ownership contract.

Add:

```python
def test_operational_pane_structure_is_widget_owned() -> None:
    for content, selector in (
        (GluePage.DEFAULT_CSS, "GluePage > #glue-context-pane"),
        (GluePage.DEFAULT_CSS, "GluePage GlueIcebergView"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage > #athena-context-header"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage TextArea"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage #athena-query-controls"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage #athena-query-detail"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage #athena-results-summary"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage DataTable"),
    ):
        bodies = _bodies_for_selector(content, selector)
        assert bodies, f"widget stylesheet missing {selector}"
        assert any("border: solid $rule-dim;" in body for body in bodies)

    for content, selector in (
        (GluePage.DEFAULT_CSS, "GluePage > #glue-context-pane:focus-within"),
        (GluePage.DEFAULT_CSS, "GluePage GlueIcebergView:focus-within"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage > #athena-context-header:focus-within"),
        (AthenaPage.DEFAULT_CSS, "AthenaPage TextArea:focus"),
        (
            AthenaPage.DEFAULT_CSS,
            "AthenaPage #athena-query-controls:focus-within",
        ),
        (
            AthenaPage.DEFAULT_CSS,
            "AthenaPage #athena-query-detail:focus-within",
        ),
        (
            AthenaPage.DEFAULT_CSS,
            "AthenaPage #athena-results-summary:focus-within",
        ),
        (AthenaPage.DEFAULT_CSS, "AthenaPage DataTable:focus"),
    ):
        bodies = _bodies_for_selector(content, selector)
        assert bodies, f"widget stylesheet missing {selector}"
        assert any("border: solid $accent;" in body for body in bodies)


@pytest.mark.parametrize("name", ALL_THEMES)
def test_builtin_themes_do_not_duplicate_operational_structure(name: str) -> None:
    content = ThemeStore().load(name)
    assert "Glue / Athena operational pane hierarchy" not in content
    assert "GluePage > #glue-context-pane" not in content
    assert "AthenaPage > #athena-context-header" not in content
```

- [ ] **Step 2: Run the RED ownership test**

Run:

```bash
uv run pytest tests/unit/ui/test_themes.py -k "operational_pane_structure or builtin_themes_do_not_duplicate" -q
```

Expected: one ownership failure plus ten duplication failures because the
widgets do not yet own the rules and every built-in theme contains the copied
block.

- [ ] **Step 3: Add Glue-owned rules to `GluePage.DEFAULT_CSS`**

Add:

```tcss
GluePage > #glue-context-pane {
    background: $bg;
    color: $text;
    border: solid $rule-dim;
    border-title-color: $text;
}
GluePage > #glue-context-pane:focus-within {
    border: solid $accent;
    border-title-color: $accent;
}
GluePage GlueIcebergView {
    background: $bg;
    color: $text;
    border: solid $rule-dim;
    border-title-color: $text;
}
GluePage GlueIcebergView:focus-within {
    border: solid $accent;
    border-title-color: $accent;
}
```

- [ ] **Step 4: Add Athena-owned rules to `AthenaPage.DEFAULT_CSS`**

Add:

```tcss
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
AthenaPage TextArea,
AthenaPage #athena-query-controls,
AthenaPage #athena-query-detail,
AthenaPage #athena-results-summary,
AthenaPage DataTable {
    background: $bg;
    color: $text;
    border: solid $rule-dim;
    border-title-color: $text;
}
AthenaPage TextArea:focus,
AthenaPage #athena-query-controls:focus-within,
AthenaPage #athena-query-detail:focus-within,
AthenaPage #athena-results-summary:focus-within,
AthenaPage DataTable:focus {
    border: solid $accent;
    border-title-color: $accent;
}
```

- [ ] **Step 5: Remove the identical block from all ten themes**

Delete each complete block beginning with:

```tcss
/* ── Glue / Athena operational pane hierarchy ─────────────────────────── */
```

and ending after the focused `AthenaPage DataTable:focus` rule. Do not alter
theme token declarations or unrelated Glue/Athena color rules.

- [ ] **Step 6: Run source, page, and theme tests**

Run:

```bash
uv run pytest tests/unit/ui/test_themes.py tests/unit/infra/test_theme_store.py tests/unit/ui/glue/test_page.py tests/unit/ui/athena/test_page.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Prove user overlays retain final precedence**

Add an integration test to
`tests/integration/test_theme_runtime_propagation.py` that writes a user
`theme.tcss` rule for `AthenaPage > #athena-context-header`, loads it through
the production `ThemeStore`, mounts a representative Athena app, and asserts
the header's computed border color is the user-selected sentinel color rather
than `$rule-dim`. Use Textual's computed style API; do not inspect
concatenated source as a proxy for cascade precedence.

Add imports:

```python
from pathlib import Path

from textual.color import Color

from aws_tui.infra.theme_store import ThemeStore
from tests.snapshot.apps.athena import AthenaPageApp
```

Add:

```python
@pytest.mark.asyncio
async def test_user_overlay_overrides_widget_owned_operational_border(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "theme.tcss"
    overlay.write_text(
        "AthenaPage > #athena-context-header { border: solid #ff00ff; }\n",
        encoding="utf-8",
    )
    theme = ThemeStore(
        user_themes_dir=tmp_path / "themes",
        user_overlay=overlay,
    ).load("carbon")
    app = AthenaPageApp(theme="carbon", fixture="empty-query")
    app.CSS = theme

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        header = app.query_one("#athena-context-header")
        assert header.styles.border_top == ("solid", Color.parse("#ff00ff"))
```

Run:

```bash
uv run pytest tests/integration/test_theme_runtime_propagation.py -q
```

Expected: all tests pass, including the computed-style precedence assertion.

- [ ] **Step 8: Run snapshot parity without update mode**

Run:

```bash
uv run pytest tests/snapshot/test_glue.py tests/snapshot/test_athena.py -q
```

Expected: all snapshots pass unchanged. If a snapshot differs, inspect the
rendered output and CSS precedence before deciding whether any golden change
is intentional.

- [ ] **Step 9: Verify the duplication is gone**

Run:

```bash
rg -n "Glue / Athena operational pane hierarchy" src/aws_tui/ui/themes
git diff --numstat -- src/aws_tui/ui/themes
```

Expected: `rg` finds nothing; the theme diff records 33 deletions in each of
ten files and no additions.

- [ ] **Step 10: Commit CSS ownership cleanup**

```bash
git add src/aws_tui/ui/widgets/glue/page.py src/aws_tui/ui/widgets/athena/page.py src/aws_tui/ui/themes tests/unit/ui/test_themes.py tests/integration/test_theme_runtime_propagation.py
git commit -m "refactor(ui): centralize service pane framing"
```

---

### Task 4: Make Documentation Contracts Source-Derived

**Files:**
- Create: `tests/docs/test_contract_parity.py`
- Modify: `docs/keybindings.md`
- Modify: `docs/contract-ledger.md`
- Modify: `tests/docs/test_shipped_behavior.py`
- Modify: `tests/docs/test_scaffolding.py`

**Interfaces:**
- Produces: source-derived default action set, registered Glue/Athena action set, public request set, and locked dependency-version assertions.
- Consumes: Python AST from source files plus `uv.lock` and `pyproject.toml` parsed with `tomllib`.

- [ ] **Step 1: Create exact source-extraction helpers and RED parity tests**

Create `tests/docs/test_contract_parity.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _module(path: str) -> ast.Module:
    return ast.parse(_text(path), filename=path)


def _default_binding_actions() -> tuple[str, ...]:
    for node in ast.walk(_module("src/aws_tui/infra/keymap_store.py")):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "DEFAULT_BINDINGS"
            and isinstance(node.value, ast.Dict)
        ):
            return tuple(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    raise AssertionError("KeymapStore.DEFAULT_BINDINGS not found")


def _registered_service_actions() -> tuple[str, ...]:
    actions: set[str] = set()
    for node in ast.walk(_module("src/aws_tui/app.py")):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith(("glue.", "athena."))
        ):
            actions.add(node.args[0].value)
    return tuple(sorted(actions))


def _public_requests() -> tuple[str, ...]:
    return tuple(
        node.name
        for node in _module("src/aws_tui/vm/messages.py").body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Request")
    )


def _text_ledger_block(text: str, heading: str) -> tuple[str, ...]:
    tail = text.split(heading, maxsplit=1)[1]
    block = tail.split("```text", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return tuple(line.strip() for line in block.splitlines() if line.strip())


def test_keybinding_action_table_covers_every_default_action() -> None:
    keybindings = _text("docs/keybindings.md")
    missing = [
        action
        for action in _default_binding_actions()
        if f"`{action}`" not in keybindings
    ]
    assert missing == []


def test_public_service_action_ledger_matches_registered_source() -> None:
    ledger = _text("docs/contract-ledger.md")
    assert _text_ledger_block(
        ledger,
        "Public Glue and Athena action ledger",
    ) == _registered_service_actions()


def test_cross_service_message_ledger_matches_request_classes() -> None:
    ledger = _text("docs/contract-ledger.md")
    assert _text_ledger_block(
        ledger,
        "Public cross-service message ledger",
    ) == _public_requests()


def test_dependency_ledger_matches_locked_runtime_and_build_versions() -> None:
    lock = tomllib.loads(_text("uv.lock"))
    versions = {
        package["name"]: package["version"]
        for package in lock["package"]
        if "version" in package
    }
    project = tomllib.loads(_text("pyproject.toml"))
    ledger = _text("docs/contract-ledger.md")

    for name in ("textual", "vmx", "hatchling"):
        assert f"`{name}=={versions[name]}`" in ledger

    build_requirement = project["build-system"]["requires"][0]
    assert f"`build-system.requires` constrained to `{build_requirement}`" in ledger


def test_workflow_metadata_avoids_stale_e2e_counts() -> None:
    workflow = _text(".github/workflows/ci.yml")
    assert re.search(r"name: e2e \(user journeys\)", workflow)
    assert not re.search(r"name: e2e \(\d+ user journeys\)", workflow)
```

- [ ] **Step 2: Run the RED contract suite**

Run:

```bash
uv run pytest tests/docs/test_contract_parity.py -q
```

Expected failures:

- three undocumented default actions;
- no complete public action ledger;
- missing `CopyTableReferenceRequest`;
- stale Textual/Hatchling versions and build lower bound;
- stale hardcoded E2E count.

- [ ] **Step 3: Complete the keybinding action table and examples**

Add exact rows for:

```markdown
| `app.open_settings` | `,` | ✓ | Open the Settings navigation page |
| `pane.modal_left` | `left` | ✓ | Route left-arrow modal or pane navigation |
| `pane.modal_right` | `right` | ✓ | Route right-arrow modal or pane navigation |
```

Replace every current executable `pane.copy = "y"` customization example in
canonical docs with:

```toml
[keybindings]
"pane.copy" = "ctrl+y"
```

Keep `glue.copy_table_ref = "y"` unchanged.

- [ ] **Step 4: Add exact action and request blocks to the contract ledger**

Add this sorted source ledger:

```text
athena.cancel
athena.choose_catalog
athena.choose_database
athena.choose_workgroup
athena.execute
athena.history
athena.insert_table_ref
athena.load_more
athena.open_in_glue
athena.open_result_location
athena.query
athena.results
athena.saved
glue.catalog
glue.choose_crawler_state
glue.choose_run_state
glue.copy_table_ref
glue.crawlers
glue.jobs
glue.open_s3_location
glue.query_in_athena
glue.time_travel_in_athena
```

Replace the public request block with source order:

```text
OpenS3LocationRequest
OpenAthenaTableRequest
CopyTableReferenceRequest
OpenGlueTableRequest
```

Update the prose rows to include the typed clipboard, selector commands,
Athena in single-context identity, and all copy/insert source guards.

- [ ] **Step 5: Correct dependency and workflow metadata**

Change the ledger to:

```markdown
`textual==8.2.8`
`vmx==3.1.0`
`hatchling==1.31.0`
`build-system.requires` constrained to `hatchling>=1.31.0,<2`
```

Review every GitHub Action SHA and version comment in
`docs/contract-ledger.md` against `.github/workflows/*.yml`; update the ledger
to the refs actually consumed by the current workflows.

Change `.github/workflows/ci.yml`:

```yaml
snapshot:
  name: snapshot (Textual SVG goldens)

e2e:
  name: e2e (user journeys)
```

- [ ] **Step 6: Remove the old fixed-subset test**

Delete `test_cross_service_action_and_message_ledgers_match_source` from
`tests/docs/test_scaffolding.py`. Its replacement in
`test_contract_parity.py` derives complete inventories from source.

Update `tests/docs/test_shipped_behavior.py` to assert the collision-free
`ctrl+y` example and retain the live-overlay claims now proven by Task 1.

- [ ] **Step 7: Run documentation contract tests**

Run:

```bash
uv run pytest tests/docs/test_contract_parity.py tests/docs/test_shipped_behavior.py tests/docs/test_scaffolding.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit source-derived contracts**

```bash
git add tests/docs/test_contract_parity.py tests/docs/test_shipped_behavior.py tests/docs/test_scaffolding.py docs/keybindings.md docs/contract-ledger.md .github/workflows/ci.yml
git commit -m "test(docs): derive public contracts from source"
```

---

### Task 5: Update Every Affected Canonical Document

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/index.md`
- Modify: `docs/cookbook.md`
- Modify: `docs/connections.md`
- Modify: `docs/architecture.md`
- Modify: `docs/adding-a-service.md`
- Modify: `docs/RELEASING.md`
- Modify: `.superpowers/sdd/2026-07-30-task-7-report.md`
- Review and modify current-state claims in:
  `docs/superpowers/specs/2026-07-21-binding-resolver-keystone-design.md`
- Review and modify current-state claims in:
  `docs/superpowers/plans/2026-07-21-binding-resolver-keystone.md`
- Test: `tests/docs/test_scaffolding.py`
- Test: `tests/docs/test_shipped_behavior.py`

**Interfaces:**
- Consumes: the runtime contracts completed in Tasks 1-4.
- Produces: one canonical narrative that the repo, site, and wiki generators can publish unchanged in meaning.

- [ ] **Step 1: Add RED canonical-surface assertions**

Extend `test_athena_canonical_surfaces_and_diagram_match_current_tree` with:

```python
for current_claim in (
    "ContextPicker",
    "ServiceTabStrip",
    "TableClipboardVM",
    "CopyTableReferenceRequest",
):
    assert current_claim in _squash(public_docs)
```

Extend `test_public_docs_cover_integrated_iceberg_workflow` with:

```python
assert "exact source" in _squash(connections).casefold()
assert "copy table reference" in _squash(cookbook).casefold()
assert "insert copied table reference" in _squash(cookbook).casefold()
assert "ctrl+y" in keybindings
assert "pane.copy = \"y\"" not in f"{readme}\n{cookbook}\n{keybindings}\n{changelog}"
```

Extend `test_athena_release_framing_and_smoke_are_minor_unreleased_work` with:

```python
for step in (
    "open the exact source selector",
    "copy the selected table reference",
    "insert the copied table reference",
    "reject a copied reference from another source",
):
    assert step in normalized_releasing.casefold()
```

- [ ] **Step 2: Run the RED documentation tests**

Run:

```bash
uv run pytest tests/docs/test_scaffolding.py tests/docs/test_shipped_behavior.py -q
```

Expected: failures enumerate the canonical pages that still omit the new
abstractions and workflows.

- [ ] **Step 3: Update README and documentation index**

The Glue feature summary must state:

```markdown
The bordered AWS source selector chooses an exact configured profile and
region; `Shift+S` still cycles in resolver order. Jobs and Crawlers expose
bordered state selectors through `Shift+F` and `Shift+G`. On a selected
Catalog table, `y` copies the fully quoted table reference into the
VMx-backed app clipboard and best-effort OS clipboard.
```

The Athena summary must state:

```markdown
Workgroup, catalog, and database are keyboard-focusable selectors opened by
`Shift+W`, `Shift+C`, and `Shift+D`. Press `i` outside the editor, or choose
the contextual palette command, to insert a same-source copied table
reference at the editor selection or cursor without executing SQL.
```

The command-palette summary must say service commands appear only for their
active service. The keymap summary must say valid overlays apply on the next
launch and invalid overlays fall back atomically.

Mirror the concise behavior summary in `docs/index.md`.

- [ ] **Step 4: Update the cookbook workflows**

Expand Glue and Athena sections with these exact operational sequences:

```markdown
1. Focus the bordered AWS source selector with `Tab`, or open it directly
   from the command palette.
2. Use `Up` / `Down`, commit with `Enter`, and cancel with `Escape`.
3. In Glue Jobs press `Shift+F`; in Crawlers press `Shift+G`.
4. In Athena press `Shift+W`, `Shift+C`, or `Shift+D` for the corresponding
   context selector.
5. In Glue Catalog select a table and press `y` to copy its canonical,
   fully quoted identifier.
6. In Athena press `i` outside the editor, or choose **Insert copied table
   reference**, to replace the editor selection or insert at the cursor.
7. A connection/region mismatch is refused without changing the editor,
   clipboard, or active profile.
```

Replace the keymap customization recipe with `pane.copy = "ctrl+y"` and
explain why bare `y` is reserved by `glue.copy_table_ref`.

- [ ] **Step 5: Update connections, architecture, and service-authoring guidance**

`docs/connections.md` must distinguish:

- exact source selection from the bordered Glue/Athena picker;
- resolver-order cycling through `Shift+S`;
- compact passive EMR source identity;
- per-pane S3 source selection.

`docs/architecture.md` must add:

- `ContextPicker` and `ServiceTabStrip` as shared view artifacts;
- `TableClipboardVM` as an app-lifetime VMx component;
- `CopyTableReferenceRequest` in the message list and flow;
- app composition ownership of best-effort OS clipboard copying;
- palette eligibility as VM-owned state.

`docs/adding-a-service.md` must direct future services to:

- reuse `ServiceSourceHeader`, `ContextPicker`, and `ServiceTabStrip`;
- extend `FocusCoordinatorVM` rather than create another focus authority;
- declare command-palette service IDs;
- publish immutable typed requests for app-level cross-service state.

- [ ] **Step 6: Update release checks, changelog, and historical current-state claims**

Add manual release checks for:

- exact source picker with at least two demo profiles;
- forward and reverse focus rings;
- Glue filter commands;
- typed copy and same-source Athena insertion;
- cross-source refusal;
- contextual palette visibility.

Add an Unreleased Changed entry describing keymap restoration, contextual
palette filtering, CSS ownership cleanup, and documentation parity.

In `.superpowers/sdd/2026-07-30-task-7-report.md`, replace:

```markdown
- `fix(ui): preserve one-profile source rebuilds`
- `test(e2e): read compact EMR source identity`
```

with:

```markdown
- `3660c39 fix(ui): preserve one-profile source rebuilds`
- `5dffc81 test(e2e): read compact EMR source identity`
```

Search the binding-resolver design and plan for executable
`pane.copy = "y"` examples. Change current-state examples to `ctrl+y`; retain
historical narrative only where it is explicitly labeled as historical.

- [ ] **Step 7: Run canonical documentation tests**

Run:

```bash
uv run pytest tests/docs -q
uv run python -m scripts.docs.build_docs --site --wiki
uv run python -m scripts.docs.check_docs
```

Expected: all docs tests pass and `check_docs: clean`.

- [ ] **Step 8: Build the site strictly and check wiki output**

Run:

```bash
uv run mkdocs build --strict
make docs-wiki
```

Expected: strict build exits zero; `push_wiki --check` reports no forbidden
or missing generated content.

- [ ] **Step 9: Commit canonical documentation parity**

```bash
git add README.md CHANGELOG.md docs/index.md docs/cookbook.md docs/connections.md docs/architecture.md docs/adding-a-service.md docs/RELEASING.md .superpowers/sdd/2026-07-30-task-7-report.md docs/superpowers/specs/2026-07-21-binding-resolver-keystone-design.md docs/superpowers/plans/2026-07-21-binding-resolver-keystone.md tests/docs/test_scaffolding.py tests/docs/test_shipped_behavior.py
git commit -m "docs: align Glue and Athena interaction surfaces"
```

Stage only historical files that actually required a current-state
correction.

---

### Task 6: Regenerate the Landscape Architecture Diagram

**Files:**
- Modify: `docs/diagrams/architecture.html`
- Modify: `docs/diagrams/img/architecture.svg`
- Modify: `docs/diagrams/img/architecture.png`
- Modify: `docs/architecture.md`
- Modify: `tests/docs/test_render_diagrams.py`
- Modify: `tests/docs/test_scaffolding.py`

**Interfaces:**
- Produces: a landscape master and deterministic SVG/PNG containing the typed clipboard and copy/insert route.
- Preserves: existing five layers, AWS boundary, exact source identity, and orthogonal routing.

- [ ] **Step 1: Add RED diagram entity assertions**

Extend the required labels in
`test_architecture_diagram_is_landscape_and_current`:

```python
for label in (
    "ContextPicker",
    "ServiceTabStrip",
    "TableClipboardVM",
    "CopyTableReferenceRequest",
    "copy quoted table ref",
    "same-source insert",
):
    assert label in svg
```

Add group ownership checks:

```python
assert "ContextPicker" in groups["textual-views"]
assert "ServiceTabStrip" in groups["textual-views"]
assert "TableClipboardVM" in groups["viewmodels"]
assert "CopyTableReferenceRequest" in groups["cross-service-handoffs"]
```

If the existing handoff group has no `id`, add
`id="cross-service-handoffs"` to its `<g>` wrapper in the master.

- [ ] **Step 2: Run the RED diagram tests**

Run:

```bash
uv run pytest tests/docs/test_render_diagrams.py tests/docs/test_scaffolding.py -q
```

Expected: failures for the six missing labels and handoff group.

- [ ] **Step 3: Invoke the architecture-diagram skill and revise the master**

Use the `architecture-diagram` skill in landscape mode. Preserve the existing
1600×900 viewBox and dark visual language. Make these exact semantic changes:

- Textual View group: add `ContextPicker + ServiceTabStrip` beneath the Glue
  and Athena page labels.
- ViewModel / VMx group: add a distinct `TableClipboardVM` box labeled
  `ComponentVMOf + RelayCommandOf`.
- MessageHub annotation: include `copy + navigation requests`.
- Cross-service handoff group: add `CopyTableReferenceRequest`.
- Add an orthogonal route:
  `GluePageVM → CopyTableReferenceRequest → TableClipboardVM → AthenaPage`.
- Label the first segment `copy quoted table ref`.
- Label the final segment `same-source insert`.

Keep all arrows behind opaque boxes. Use perpendicular bends where a direct
line would cross a component. Do not reduce body text below the current
smallest readable label size.

- [ ] **Step 4: Render committed artifacts**

Run:

```bash
make docs-diagrams
```

Expected: deterministic updates to
`docs/diagrams/img/architecture.svg` and `.png`.

- [ ] **Step 5: Run geometry and semantic checks**

Run:

```bash
uv run pytest tests/docs/test_render_diagrams.py tests/docs/test_scaffolding.py -q
make docs-check
```

Expected: all tests and strict docs build pass.

- [ ] **Step 6: Visually inspect the generated PNG**

Open:

```text
/Users/kaveh/repos/aws-tui/docs/diagrams/img/architecture.png
```

Verify:

- no overlapping boxes or labels;
- no arrow crosses a non-endpoint box;
- all arrowheads are visible;
- the clipboard flow reads left-to-right;
- the landscape image remains readable at 1600 px width.

If any check fails, edit the HTML master, rerender, and repeat Steps 5-6.

- [ ] **Step 7: Verify deterministic regeneration**

Run:

```bash
git diff -- docs/diagrams/img/architecture.svg docs/diagrams/img/architecture.png
make docs-diagrams
git diff --check
```

Expected: the second render introduces no additional diff and
`git diff --check` exits zero.

- [ ] **Step 8: Commit architecture parity**

```bash
git add docs/diagrams/architecture.html docs/diagrams/img/architecture.svg docs/diagrams/img/architecture.png docs/architecture.md tests/docs/test_render_diagrams.py tests/docs/test_scaffolding.py
git commit -m "docs(architecture): add typed table clipboard flow"
```

---

### Task 7: Close Stability Risk, Record Metrics, and Run the Full Branch Gate

**Files:**
- Create: `.superpowers/sdd/2026-07-30-post-merge-audit-remediation-report.md`
- Modify: `docs/superpowers/specs/2026-07-30-post-merge-audit-remediation-design.md`
- Modify: `docs/superpowers/plans/2026-07-30-post-merge-audit-remediation.md`
- Modify only if a retry-disabled failure reproduces:
  `tests/integration/test_athena_s3_handoff.py` and the source file identified
  through systematic debugging.

**Interfaces:**
- Produces: exact-head verification evidence and implementation metrics.
- Preserves: clean branch, no ignored generated outputs staged, and `main` unchanged.

- [ ] **Step 1: Run the observed flaky module repeatedly with reruns disabled**

Run five independent processes:

```bash
for run in 1 2 3 4 5; do
  echo "athena-s3 pass ${run}"
  uv run pytest --reruns 0 tests/integration/test_athena_s3_handoff.py -q
done
```

Expected: each process reports `40 passed` with no `R` marker.

If any process fails, stop the plan, invoke `systematic-debugging`, preserve
the first traceback, identify the shared state or timing boundary, add one
deterministic RED regression, implement the minimum fix, and rerun all five
processes. Do not increase sleeps or rerun counts as a fix.

- [ ] **Step 2: Run focused remediation suites**

Run:

```bash
uv run pytest --reruns 0 tests/unit/test_composition_initial_theme.py tests/integration/test_keybinding_wiring.py tests/unit/vm/chrome/test_command_palette.py tests/integration/test_command_palette_wiring.py tests/unit/ui/test_themes.py tests/docs -q
```

Expected: all selected tests pass without reruns.

- [ ] **Step 3: Run unit and in-process integration with coverage**

Run:

```bash
uv run pytest --reruns 0 tests/unit tests/integration --cov=aws_tui --cov-report=term-missing --cov-report=xml
```

Expected:

- no failures or reruns;
- overall coverage at least 85.75%;
- no material regression in `composition.py`,
  `command_palette_vm.py`, Glue/Athena page widgets, or documentation helpers.

- [ ] **Step 4: Run all snapshots without update mode**

Run:

```bash
uv run pytest --reruns 0 tests/snapshot -v
```

Expected: all snapshot tests and all comparisons pass without regeneration.
If Task 3 caused intentional golden changes, record the exact affected cases,
visually inspect them, regenerate only those cases, and rerun the entire
snapshot tier without update mode.

- [ ] **Step 5: Run E2E and architecture gates**

Run:

```bash
uv run pytest --reruns 0 tests/e2e -v
scripts/check-layers.sh
```

Expected: all nine current journeys pass; layer rules are clean.

- [ ] **Step 6: Run lint, format, typing, and docs gates**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
make docs-check
make docs-wiki
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 7: Calculate exact authored change metrics**

Run:

```bash
git diff --numstat a14bc98fce5847f31199d9d44cc2ff255448e09f...HEAD -- src/aws_tui
git diff --numstat a14bc98fce5847f31199d9d44cc2ff255448e09f...HEAD -- tests
git diff --numstat a14bc98fce5847f31199d9d44cc2ff255448e09f...HEAD -- README.md CHANGELOG.md docs .superpowers .github/workflows/ci.yml
git diff --shortstat a14bc98fce5847f31199d9d44cc2ff255448e09f...HEAD
```

Record separately:

- runtime Python additions/deletions;
- the 330 expected TCSS deletions;
- tests;
- canonical docs and diagram source;
- generated diagram artifacts;
- snapshot goldens, expected to remain unchanged unless visually justified.

- [ ] **Step 8: Write the completion report**

Create
`.superpowers/sdd/2026-07-30-post-merge-audit-remediation-report.md`
with these sections and actual values from Steps 1-7:

```markdown
# Post-Merge Audit Remediation Report

## Scope

Branch, base SHA, exact head SHA, and implemented tasks.

## Runtime Corrections

Keymap overlay behavior, collision fallback, contextual palette behavior, and
CSS ownership.

## Documentation and Three-Surface Parity

Canonical pages changed, source-derived contracts, diagram regeneration,
site/wiki build status, and the fact that live publication still waits for
normal promotion to main.

## VMx Decisions

Why ScoredFilteredCompositeVM remains the palette projection and why no
parallel state authority was added.

## Verification

Exact command results, test counts, snapshot comparisons, E2E journeys,
coverage, Ruff, mypy, layers, docs, and retry-disabled Athena-to-S3 runs.

## Metrics

Authored additions/deletions by runtime, tests, docs, themes, diagrams, and
snapshots.

## Residual Risk

Any reproducible remaining issue; write "None observed" only when every gate
passed without reruns.
```

- [ ] **Step 9: Update design and plan completion records**

Append an `## Implementation Record` to the design spec and an
`## Completion Record` to this plan. Include:

- exact head SHA before the final documentation commit;
- commit list by task;
- exact test and coverage totals;
- exact LOC metrics;
- confirmation that `main` was not changed;
- confirmation that generated site/wiki outputs remain ignored.

Do not mark checkbox steps complete until their commands have actually passed.

- [ ] **Step 10: Run final documentation checks and commit the report**

Run:

```bash
uv run pytest tests/docs -q
git diff --check
```

Then commit:

```bash
git add .superpowers/sdd/2026-07-30-post-merge-audit-remediation-report.md docs/superpowers/specs/2026-07-30-post-merge-audit-remediation-design.md docs/superpowers/plans/2026-07-30-post-merge-audit-remediation.md
git commit -m "docs: record audit remediation results"
```

- [ ] **Step 11: Verify exact final branch state**

Run:

```bash
git status --short
git log --oneline --decorate a14bc98fce5847f31199d9d44cc2ff255448e09f..HEAD
git diff --stat develop...HEAD
git rev-parse main
git rev-parse origin/main
```

Expected:

- clean worktree;
- two planning commits plus seven implementation/report commits;
- only the scoped files in this plan changed;
- local and remote `main` unchanged from their pre-execution values.

- [ ] **Step 12: Request final code review**

Invoke `superpowers:requesting-code-review` against
`develop...codex/post-merge-audit-remediation`. Resolve every confirmed
finding with a focused RED test where behavior changes, rerun the affected
gate, and keep the branch ready for a pull request into `develop`.

Do not create or merge the pull request unless the user separately requests
integration.
