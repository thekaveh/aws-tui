# Unified Help and Palette Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking. The controller has reviewed this plan under the owner’s autonomous goal. Implement tasks sequentially, with the required review gates.

**Goal:** Deliver all seven #245 acceptance criteria through one contextual command catalog and safe direct service/source navigation.

**Architecture:** Pure immutable action metadata and presentations are projected from the existing handler registry, effective keymap and app readiness. Help and the existing palette consume those presentations. Service jumps select the existing navigation menu; source choices enter the existing generation/lock/adoption transaction, retaining S3 pane semantics and AWS-page rollback.

**Tech Stack:** Python `>=3.11,<3.14`, Textual 8.2.8, VMx, existing pytest/pilot fixtures, demo/in-memory service clients. Available local interpreter is Python 3.12.9 on macOS.

## 1. Global constraints

- Work in `/Users/kaveh/repos/aws-tui`, branch `codex/issue-245-unified-help-palette`; base `ed0cf58aa24d5e2fa2c1e33a27ecefaa8e019076`.
- Canonical specification is `docs/superpowers/specs/2026-10-06-unified-help-palette-design.md`, promoted by the controller from adjacent `design-draft.md`; all seven ACs and sections 1–8 apply to every task.
- No new dependencies, second palette, remote resource search, deep-link URI parsing, or FocusCoordinator replacement. Widget-local events stay local.
- Registry, keymap and keyboard/modal rules retain their existing ownership. Complete static metadata coverage includes explicit unbound commands; defaults are not a proxy for registered IDs.
- Omit unavailable commands consistently, retain value-free reasons, and revalidate before dispatch. User names/regions render literally. No secret-bearing IDs, VM/task names, messages or new diagnostic payloads.
- No live AWS, hosted CI, tool/dependency installation, gate/assertion/timeout weakening, unrelated changes, or package/release/remote-doc publication. GitHub Actions remains disabled; applicable verification is local.
- Native Windows/Python 3.11/native clipboard/human evidence cannot be claimed. #283's Windows promotion requirement remains unwaived.
- Each task gets a fresh implementer and one fresh combined scoped spec+quality reviewer reporting both verdicts. Implementer tests, self-reviews and commits work before the reviewer receives `BASE..HEAD`; findings require fresh fix commits and re-review before controller acceptance. Final whole-branch review uses the most-capable available model. Do not reuse an implementer's self-rating as review.
- Genuine baseline RED precedes code changes for new behavior. Record command, exact expected failing assertion and result in task evidence. A syntax/import/fixture failure is not sufficient evidence for an unmet user behavior. Existing tests are preserved unless their literal rows/fixtures intentionally change under this spec.
- Run task checks once at each meaningful state; a code change or specific failure can justify rerun. No unchanged passing suite loops.

## 2. Files and responsibility boundaries

All paths below are relative to the repository root stated above; do not edit outside this scope without explaining a direct requirement.

| File | Responsibility |
|---|---|
| `src/aws_tui/vm/chrome/action_catalog.py` (new) | Static `ACTION_SPECS`, immutable spec/presentation types, pure projection and shared key/scope formatting |
| `src/aws_tui/vm/chrome/command_palette_vm.py` | Backward-compatible `PaletteEntry` re-export; omit unavailable presentations; preserve VMx lifecycle/scoring/task ownership |
| `src/aws_tui/ui/widgets/help_modal.py` | Render shared rows/sections; preserve informational content, diagnostics, theme and scrolling |
| `src/aws_tui/ui/widgets/command_palette.py` | Literal shared row label, scope/category and effective keys; expose action identity for pilot checks |
| `src/aws_tui/ui/bindings.py` | Canonical description lookup only; priority/collision/modal rules unchanged |
| `src/aws_tui/app.py` | Context capture, readiness adapter, reconciliation, guarded dispatch; service/source registration and navigation adapters |
| `src/aws_tui/ui/widgets/athena/page.py`, `src/aws_tui/ui/widgets/glue/page.py` | Optional captured-focus argument on existing `can_load_more` predicate; same branch logic |
| `src/aws_tui/infra/keymap_store.py` | Four empty defaults for new service action IDs; no change to existing shortcuts or five deliberately absent defaults |
| `tests/unit/vm/chrome/test_action_catalog.py` (new) | Pure projection/formatting and single exhaustive real-registry metadata/key-policy coverage test |
| `tests/unit/vm/chrome/test_command_palette.py` | Unavailable presentation omission, refresh/lifecycle/failure semantics |
| `tests/unit/ui/test_overlay_widgets.py`, `tests/unit/ui/test_bindings.py` | Shared literal row rendering, correct action's keys, existing style/description contracts |
| `tests/integration/test_command_palette_wiring.py` | Real-app Help/palette parity and filtering; preserve existing dispatch/empty/search/modal/result assertions |
| `tests/integration/test_discovery_navigation.py` (new) | Service/source pilot selection, capability, identity, privacy, stale choice and failure/lifecycle tests |
| `tests/integration/test_service_source_swap.py` | Reused exact source/header rollback assertions; any narrowly shared fixture factoring |
| `docs/keybindings.md`, `docs/cookbook.md` | Shipped unified discovery, effective/unbound keys and direct service/source usage |
| generated docs outputs | Only changes produced by the repository's canonical docs generators; never manually edit mirrors |

Do not modify `ActionRegistry` to store metadata or replace the input router. Do not move service composition into the VM catalog. The catalog may not import Textual, UI, services or connection credentials. Keep existing snapshot apps as lightweight consumers of the compatible PaletteEntry constructor.

## 3. Task 1: Shared metadata, availability and both discovery surfaces

**Files:** Create the catalog and its unit test; modify app population/help dispatch/readiness, both overlay widgets, palette VM, binding descriptions, two page readiness methods, focused tests named in section 2. Do not implement new service/source IDs in this task.

**Consumes:** `ActionRegistry.known_actions/has/invoke`; `KeymapStore.all/resolve`; `_object_details_origin`/`_finish_palette_object_details`; query/Glue/EMR command predicates and footer readiness; existing `CommandPaletteVM.register_entry/unregister_entry` and task/failure ownership.

**Produces (exact task interfaces):**

```python
# vm/chrome/action_catalog.py
ActionSpec(id, label, category, keywords=(), service_ids=frozenset(), key_source="keymap")
ActionPresentation(id, label, category, keywords=(), service_ids=frozenset(),
                   effective_keys=(), availability_reason=None)
ACTION_SPECS: tuple[ActionSpec, ...]
project_actions(specs, *, registered_ids, bindings, active_service_id, unavailable_reasons)
format_effective_keys(keys: tuple[str, ...]) -> str
scope_label(service_ids: frozenset[str], active_service_id: str | None) -> str
# See design section 3 for complete typed signatures and frozen dataclasses.

# ui/widgets/help_modal.py
class HelpActionRow(Static):
    action_id: str
    presentation: ActionPresentation
class HelpModal(ModalScreen[None]):
    def __init__(self, *, actions: tuple[ActionPresentation, ...] = (),
                 active_service_id: str | None = None,
                 keymap: KeymapStore | None = None,
                 log_path: Path | None = None, crash_path: Path | None = None) -> None: ...
    def update_actions(self, actions: tuple[ActionPresentation, ...]) -> None: ...

# ui/widgets/command_palette.py
class CommandPaletteItem(Static):
    action_id: str
    presentation: ActionPresentation
    def __init__(self, entry: PaletteEntry, *, active_service_id: str | None = None,
                 is_selected: bool = False) -> None: ...

# app.py, app-private methods
def _capture_discovery_origin(self) -> _DiscoveryOrigin: ...
def _discovery_unavailability(self, origin: _DiscoveryOrigin) -> dict[str, str]: ...
def _project_discovery_actions(self, origin: _DiscoveryOrigin) -> tuple[ActionPresentation, ...]: ...
def _refresh_discovery_surfaces(self) -> None: ...
async def _invoke_discovery_action(self, action_id: str, origin: _DiscoveryOrigin) -> None: ...

# Both existing page classes; default behavior unchanged.
def can_load_more(self, *, focused_ids: frozenset[str] | None = None) -> bool: ...
```

The optional Help `keymap` remains for diagnostic footer/standalone compatibility; it must not manufacture working action rows from an invented registry. Empty `actions` means no command rows, while mouse/docs/diagnostics still render. Existing standalone row tests must pass explicitly projected actions with a small explicit test registry; the exhaustive product inventory lives only in the coverage test.

- [ ] **1.1 Add real-app failing acceptance probes before introducing new imports/types.** In `test_command_palette_wiring.py`, create a demo context, mount Athena by `services_menu.switch_service_command.execute("athena")`, and `wait_until` current ID AND attached AthenaPage match. Open Help using `question_mark`, then inspect actual section and row widgets. Add a second test opening palette and asserting the actual `Cycle theme` widget contains configured `Ctrl+g` after `ctx.keymap_store = KeymapStore(overlay={"app.cycle_theme": "ctrl+g"})` before app construction. These tests can use existing `Static.content` and fail on current missing Athena section / missing key, not a future class import.

```python
sections = [str(row.content) for row in app.screen.query(".help-section")]
assert any("Athena" in text and "Loaded Athena" not in text for text in sections)
assert any("Global" in text for text in sections)
# Palette assertion is on the matching widget, not all page text:
row = next(row for row in app.screen.query(".palette-item")
           if "Cycle theme" in str(row.content))
assert "Ctrl+g" in str(row.content)
```

Run `.venv/bin/python -m pytest tests/integration/test_command_palette_wiring.py -k 'help_projects_hosted_athena or palette_renders_effective_key' -q`. Expected baseline: fails for missing global/Athena heading or missing key. Keep existing diagnostic/help tests intact while recording RED.

- [ ] **1.2 Add pure catalog contracts and the one exhaustive coverage test.** Use actual demo `AwsTuiApp` without mounting for registry coverage; always dispose root/close sink as current unit contexts do. First run shows the new catalog missing; the behavioral RED from 1.1 is the user-visible baseline. Include wrong-service, absent handler, explicit unbound, missing required key, no data in reason, multiple/empty/case-normalized keys, and retained unavailable records.

```python
def assert_catalog_coverage(app: AwsTuiApp) -> None:
    by_id = {spec.id: spec for spec in ACTION_SPECS}
    assert len(by_id) == len(ACTION_SPECS)
    assert set(by_id) == set(app._actions.known_actions())
    bindings = app.app_ctx.keymap_store.all()
    for action_id, spec in by_id.items():
        assert spec.label.strip() and spec.category.strip()
        if spec.key_source == "keymap":
            assert action_id in bindings
            expected = app.app_ctx.keymap_store.resolve(action_id)
        else:
            assert action_id not in bindings
            expected = ()
        row = project_actions((spec,), registered_ids=frozenset({action_id}),
                              bindings=bindings, active_service_id=None,
                              unavailable_reasons={})[0]
        assert row.effective_keys == expected
```

Keep this helper used by exactly one exhaustive test. Do not repeat complete static ID sets in either surface test. Specific behavior tests may name the few action IDs whose semantics they validate.

- [ ] **1.3 Implement the immutable catalog and projection.** Move all existing curated label/keyword/scope data into `ACTION_SPECS`, add the registered omissions specified by design section 4, and mark the five unbound IDs explicitly. Do not add navigation IDs until Task 2 registers them. Replace the PaletteEntry class definition with the compatible alias. Projection's core is:

```python
rows = []
for spec in specs:
    keys = bindings[spec.id] if spec.key_source == "keymap" else ()
    reason = (
        "handler_missing" if spec.id not in registered_ids else
        "service_inactive" if spec.service_ids and active_service_id not in spec.service_ids else
        unavailable_reasons.get(spec.id)
    )
    rows.append(ActionPresentation(spec.id, spec.label, spec.category,
                                   spec.keywords, spec.service_ids, tuple(keys), reason))
return tuple(rows)
```

Use finite key/scope formatting maps in this same module. In palette scoring return `None` before fuzzy scoring when `entry.availability_reason is not None`; retain the existing `service_ids` gate for standalone clients. `_describe` looks up this static catalog and preserves its fallback. No priority changes.

- [ ] **1.4 Implement origin/readiness and a shared reconciliation path.** Capture actual host VM/service and focus before pushing either overlay. Build reason-map from design section 5's existing predicates. Extract footer-ready conditions without changing its disabled-action output. Pass captured ancestor IDs from discovery to the focus-dependent load-more predicates as detailed next. Keep strict result lifecycle/selection and object-details validation separate from a modal's own current focus.

For Athena, add the optional `focused_ids` argument directly to `can_load_more` and replace its initial focused-ID lookup. For Glue, add the same optional argument to `_load_more_target`, replace its `focused = self._focused_ids()` line with the supplied/default IDs, and make `can_load_more` call `_load_more_target(focused_ids=focused_ids)`. Keep `action_load_more` calling the no-argument path, with every loader branch unchanged.

`_populate_command_palette` delegates to reconciliation every time; an initialized flag may remain but never suppress fresh projection. Call public `register_entry` for every current row with its fresh origin-bound callback, and unregister removed IDs. Add this minimal equality fast path inside `CommandPaletteVM.register_entry`, before its existing replacement/disposal branch:

```python
existing = self._items.get(entry.id)
if existing is not None and existing.entry == entry:
    self._actions[entry.id] = action
    return
```

The private maps above remain owned by the VM; the app must never write them. This retains unchanged children while refreshing callbacks, avoiding stale origin closures. Add unit tests proving identical presentation retains its constructed child and invokes the new callback, and changed presentation still disposes the replaced child. Track the IDs the app owns rather than deleting unrelated standalone palette entries. Capture selected entry ID before row changes; afterward, if it survives in `vm.filtered_entries` and the palette is open, restore selection via `vm.move_selection_command.execute(target_index - vm.selected_index)`. Keep the filter unchanged; allow safe VM reset when the selected row disappears. No new generic reconciliation API is needed. Replace the special `_recompute_hint_disables` EMR register/unregister metadata block with common refresh after readiness calculation, with a recursion guard or a one-way data flow (readiness computation cannot call refresh). Existing EMR refresh/terminal/busy events must update the open palette immediately.

- [ ] **1.5 Render both surfaces from presentations.** Create HelpActionRow and CommandPaletteItem attributes above. Palette `_rebuild_list` supplies existing `vm.active_service_id` to each item so multi-service scopes render the current service name. Build each row as a literal `Text`; styling is applied with `Text.stylize`/`append(style=...)`, never an f-string of user values interpreted as Rich markup. Separate keys from the unchanged canonical label. Help groups available rows under Global/service headings, with existing mouse/docs/diagnostics outside the action container. Implement `update_actions` by replacing only that container, preserving scroll offset and footer. Standalone no-action Help remains valid for theme/focus tests. Remove handwritten action labels and appended Athena keys.

```python
text = Text(format_effective_keys(entry.effective_keys))
text.append("  ")
text.append(entry.label)
text.append("  " + scope_label(entry.service_ids, active_service_id), style="dim")
# Pass Text directly to Static; never interpolate name/region into markup.
```

- [ ] **1.6 Preserve deferred invocation, failures and strict origin guards.** Palette entry callback returns `_invoke_discovery_action(entry.id, origin)`, so existing VM `_pending_tasks`, shutdown cancellation and `PaletteActionFailedMessage` remain responsible. The adapter awaits a Future released by `call_after_refresh` after modal dismissal; checks shutdown/modal/origin and fresh availability; then invokes registry and awaits an awaitable. For object details use the existing specialized validation/restoration. Restore captured focus only if it still belongs to the same live underlying content. Never relax `_MODAL_ROUTED_ACTIONS` or force an action through a guard by globally clearing modal state.

```python
ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
def after_dismissal() -> None:
    if not ready.done():
        ready.set_result(None)
self.call_after_refresh(after_dismissal)
await ready
# Next: owned-origin, no-modal, handler and projected-reason checks.
# Invoke only after these checks; VM catches raised exceptions.
result = self._actions.invoke(action_id)
if inspect.isawaitable(result):
    await result
```

The comment identifies the exact checks described in section 5 of the design; none may be omitted. Cancellation before callback is safe because `ready.done()` guards completion. Existing object-details callback is retained instead of replaced with this generic invocation.

- [ ] **1.7 Strengthen row-specific tests and adapt only obsolete inventories.** Replace `_GLOBAL/_PANE/_GLUE/_ATHENA` whole-surface literal sets with actual-hosted context checks plus shared projection comparison, while retaining explicit wrong-service and unavailable examples. Convert the #253 appended-key-label test to assert label and key field/rendering separately; preserve all original result-copy, no-extra-fetch, modal, keyboard, and focus assertions. Add a two-opening parity test that changes `ctx.keymap_store`, compares row `.presentation.label/.effective_keys` AND actual rendered text in each surface, and includes an empty overlay. Keep startup remapped-key dispatch test. Make a missing handler disappear and ensure unregistering after opening causes zero dispatches. A registry spy counting calls is required; absence of a crash alone is insufficient.

- [ ] **1.8 Run scoped GREEN and regressions.**

```bash
.venv/bin/python -m pytest tests/unit/vm/chrome/test_action_catalog.py tests/unit/vm/chrome/test_command_palette.py tests/unit/ui/test_overlay_widgets.py tests/unit/ui/test_bindings.py tests/integration/test_command_palette_wiring.py tests/integration/test_emr_cancellation.py tests/integration/test_modal_key_containment.py tests/integration/test_keybinding_wiring.py tests/integration/test_s3_object_details.py tests/integration/test_pane_listing_controls.py -q
.venv/bin/python -m ruff check src/aws_tui/vm/chrome/action_catalog.py src/aws_tui/vm/chrome/command_palette_vm.py src/aws_tui/ui/widgets/help_modal.py src/aws_tui/ui/widgets/command_palette.py src/aws_tui/ui/bindings.py src/aws_tui/app.py src/aws_tui/ui/widgets/athena/page.py src/aws_tui/ui/widgets/glue/page.py
```

Expected: all selected tests/checks pass; no warnings about un-awaited action coroutines or palette task exceptions. Tests that formerly mounted a page without updating ContentHost must be made coherent fixtures, not exempted from context checks. Reviewer inspects test changes to ensure no meaningful assertion disappeared.

- [ ] **1.9 Commit tested work and hand off combined scoped review.** Implementer self-reviews and commits the tested change, e.g. `feat: share contextual action discovery between help and palette`. Report BASE/HEAD, exact changed files, RED/GREEN commands/results and risks. One fresh combined reviewer checks AC1/2/3/6/7 and design sections 3–5, plus layering, single metadata ownership, task/focus lifetimes, literal rendering and scope. Require separate explicit spec and quality verdicts in that review. Implementer fixes findings in new tested commits; reviewer receives updated BASE..HEAD for re-review; controller accepts after both verdicts pass.

## 4. Task 2: Capability-gated service and exact source navigation

**Files:** Modify app navigation/registration adapters and the catalog; add four empty key defaults; create `tests/integration/test_discovery_navigation.py`; extend source rollback tests only where necessary. Preserve Task 1's complete coverage test unchanged so missing metadata/handlers is automatically detected.

**Consumes:** All Task 1 presentation/origin/projection/dispatch contracts; existing `NavMenuVM.switch_service_command`, `_on_nav_selection_changed`, `_mount_external_navigation`, `_supersede_table_navigation`, `_service_navigation_lock`, `_service_navigation_is_owned_by`, `_switch_single_context_source_to`, `_rebuild_single_context_source`, `_rebind_pane_to_connection`, ServiceSourceHeader.restore_source.

**Produces:**

```python
@dataclass(frozen=True, slots=True)
class _DiscoverySourceTarget:
    service_id: str
    connection_kind: str
    connection_name: str
    region: str

def _select_discovery_service(self, service_id: str) -> None: ...
def _reconcile_discovery_sources(self, origin: _DiscoveryOrigin) -> tuple[ActionSpec, ...]: ...
async def _select_discovery_source(self, entry_id: str, origin: _DiscoveryOrigin) -> None: ...
async def _switch_single_context_source_to(
    self, service_id: str, connection_name: str, region: str,
    *, connection_kind: str | None = None,
) -> bool: ...
```

Dynamic registry handler is `partial(self._select_discovery_source, entry_id, origin)`; the captured origin is updated by reconciliation for a newly opened surface. The outer Task 1 invocation adapter retains its own origin and revalidates it before registry invocation. Dictionary keys include all four target fields; entry IDs never include them.

- [ ] **2.1 Add service navigation pilot RED.** Use `build_app_context(demo=True, cache_dir=...)` with all four in-memory services. Pilot helper opens palette, sets Input.value to full `Go to ...` label, waits for the expected ID in filtered entries, then presses Enter; wait for ContentHost, nav and attached page to agree. Settings searches `Settings` and uses existing ID. First run before navigation code fails because `service.open.*` entries do not exist, while Settings baseline passes. Assert behavior, not a future method import.

```python
await pilot.press("ctrl+k")
app.screen.query_one("#palette-input", Input).value = "Go to Athena"
await pilot.pause()
assert any(row.id == "service.open.athena" for row in ctx.command_palette_vm.filtered_entries)
await pilot.press("enter")
await wait_until(lambda: ctx.root_vm.content_host.current_id == "athena"
                 and bool(app.query(AthenaPage)), what="palette Athena adoption")
assert ctx.root_vm.services_menu.selected_id == "athena"
assert app.query_one(AthenaPage).vm is ctx.root_vm.content_host.current
```

Run `.venv/bin/python -m pytest tests/integration/test_discovery_navigation.py -k service -q`. Record missing entry as baseline failure. Add explicit capability cases for active AWS, active S3-compatible, and no connection; enumeration must not call client/provider factories.

- [ ] **2.2 Register and project four service actions.** Add the four key defaults `()` and ActionSpecs in the same change. Use `partial(self._select_discovery_service, service_id)` registrations before resolver installation. The selector's mutation is only:

```python
ctx.root_vm.services_menu.switch_service_command.execute(service_id)
```

Precede it with intake/support checks against the current active connection and service registry. The handler and projection use the same predicate. Settings remains `action_open_settings`. Do not invoke `_mount_service_view` independently; the existing nav subscriber owns the content-mount worker and late-generation protection. Run service tests and single catalog coverage test to GREEN.

- [ ] **2.3 Add source behavior RED before source code.** Reuse the in-memory EMR/Glue source patterns from `test_service_source_swap.py` and the isolated AWS fixture; configure source tuples `aws/dev/us-east-1`, `aws/prod-west/us-west-2`, plus an S3-compatible source. Pilot search for `Use dev · us-east-1 for Glue`; assert a resulting entry and execute it, tracing calls to existing `_switch_single_context_source_to`. Baseline fails at missing source row. Add pure reconstruction tests for same name/different region/kind, reorder/remove/readd, and monotonic opaque IDs after implementation makes the contract available. Do not read actual user config in tests.

- [ ] **2.4 Reconcile dynamic source registration with opaque stable session IDs.** Maintain two app-owned maps (target tuple to ID, ID to target) and an increment-only integer. `_live_connections` supplies candidates; support and unreachable filters match design section 6. For each new target allocate `source.choice.<counter>`, then register its closure in ActionRegistry before projecting an unbound ActionSpec. Remove absent mappings and unregister both registry and palette rows. Preserve surviving IDs across resolver reorder; do not reuse removed IDs. Bind fields from a frozen target rather than late-binding a loop variable.

```python
entry_id = self._discovery_source_ids.get(target)
if entry_id is None:
    self._discovery_source_counter += 1
    entry_id = f"source.choice.{self._discovery_source_counter}"
    self._discovery_source_ids[target] = entry_id
self._discovery_source_targets[entry_id] = target
self._actions.register(entry_id, partial(self._select_discovery_source, entry_id, origin))
spec = ActionSpec(entry_id, source_label, "Source", keywords,
                  frozenset({target.service_id}), "unbound")
```

Deduplicate exactly equal target tuples before allocating. Labels/keywords use display values only; no profile/endpoint/access-token/secret fields are copied. Do not call the resolver repeatedly per row; take one local catalog snapshot per reconciliation. Re-resolve at execution separately.

- [ ] **2.5 Implement the exact source transaction through the named helper.** In `_select_discovery_source`, validate opaque ID and captured origin, advance generation with `_supersede_table_navigation`, acquire the existing lock, reject stale generation/intake/current VM, re-read the live catalog and exact target, then await the helper with `connection_kind`. The coroutine is already owned by PaletteVM after dismissal; do not await it from Enter's synchronous handler or start an unowned task.

```python
generation = self._supersede_table_navigation()
async with self._service_navigation_lock:
    if self._service_navigation_closed or not self._service_navigation_is_owned_by("external", generation):
        return
    # Recheck origin identity and exact fresh candidate tuple here.
    accepted = await self._switch_single_context_source_to(
        target.service_id, target.connection_name, target.region,
        connection_kind=target.connection_kind,
    )
    if not accepted:
        # Restore the currently hosted matching source header, if selectable.
        for header in self.query(ServiceSourceHeader):
            if header.is_attached and header.display:
                header.restore_source()
```

Only restore headers belonging to the still-owned/current page; do not iterate into unrelated content if navigation superseded the source operation. Check header selectability before `restore_source` (compact header has no picker). Preserve cancellation propagation and `_restore_single_context_source_durably`; no `except BaseException: return False`.

For AWS services retain existing resolution/rebuild route with optional kind check. For S3, resolve supported `(kind,name,region)` under the lock, validate captured focused pane still belongs to the same dual VM, and call `_rebind_pane_to_connection`. Catch provider-construction/auth refusal using the existing notification style; leave the old pane intact. Keep `swap_provider`'s identity-before-reload behavior and transfer identity. Do not set RootVM connection or affect the other pane. No new local source entry is required.

- [ ] **2.6 Add failure, staleness, identity and lifecycle regressions.** Each named scenario is a separate test with an observable state/side-effect assertion:

  - Force AWS candidate adoption failure after selecting a real source row. Spy `restore_source`, assert attached header picker/rendered label, RootVM connection, current VM `.source.connection_key`, menu selection and DOM VM identity return to prior values; no new service remains half-mounted.
  - Fail provider construction for an exact S3-compatible target: unchanged selected-pane provider/identity/path, unchanged other pane and unchanged RootVM connection. Successful case changes only selected pane and carries exact kind/name/region to provider factory.
  - Remove or mutate target after palette opens but before Enter: no provider construction or adoption. Reopen removes old action/VM child. Same name + same region + different kind selects exactly the chosen kind.
  - Resolver throws local config error: palette still opens with global commands; no source rows; existing generic discovery toast is retained; no AWS calls.
  - Suspend source adoption with `asyncio.Event`, supersede via Settings/service navigation, release old work, assert newer content stays mounted; cancellation drains rollback and palette tasks. Use events/wait_until rather than longer timeouts or sleeps.
  - Shutdown while a palette source action is in flight: existing shutdown drains palette and navigation lock, no late mount/provider mutation after shutdown. Reuse existing source-cancellation machinery and lifecycle fixtures.
  - Values containing `[bold]`, `[/]`, quotes, slash, `·`, Unicode and terminal-control characters: inspect actual row plain text and render output; punctuation remains literal, control characters have safe visible escaping, names are not parsed or truncated for selection.
  - Put distinctive fake credentials/endpoint/token sentinels on a fake Connection and force failure: serialized captured PaletteActionFailedMessage and new discovery log fields contain only opaque ID/fixed reason/error type; none of the sentinel/name/region strings are in an ID, VM name or task name. Display labels deliberately contain name/region; do not require those to be redacted from the UI.
  - Repeat open/close with unchanged candidates and reorder them: registry count/VM child count stable, no duplicate Settings entry, retained target IDs stable. Removed IDs never address a newly added source.

- [ ] **2.7 Run scoped GREEN and source/navigation regressions.**

```bash
.venv/bin/python -m pytest tests/integration/test_discovery_navigation.py tests/integration/test_service_source_swap.py tests/integration/test_pane_source_swap.py tests/integration/test_swap_source_skips_unreachable.py tests/integration/test_swap_source_recovery.py tests/integration/test_glue_athena_navigation.py tests/integration/test_emr_cancellation.py tests/integration/test_command_palette_wiring.py tests/unit/vm/chrome/test_action_catalog.py -q
```

Expected: all pass using fake/local clients. Do not weaken existing same-region/cancellation/mount-adoption assertions to accommodate the new path. A failure must be repaired before review.

- [ ] **2.8 Commit tested work and hand off combined scoped review.** Implementer self-reviews and commits, e.g. `feat: add guarded service and source discovery commands`. One fresh combined reviewer receives BASE..HEAD and reports explicit spec and quality verdicts: AC4/5/6, capability/privacy/source identity, generation/lock ownership, S3 other-pane isolation, stale handlers, coroutine shutdown and header restoration. Findings require new tested fix commits and re-review; controller accepts only after both verdicts pass.

## 5. Task 3: Acceptance coverage, user documentation and local delivery gates

**Files:** `tests/integration/test_command_palette_wiring.py`, `tests/integration/test_discovery_navigation.py`, relevant overlay tests, `docs/keybindings.md`, `docs/cookbook.md`; generated local docs only if canonical content changes require them. Snapshot files only after a demonstrated intended visual diff.

**Consumes:** All exact Task 1/2 interfaces and complete AC matrix in design section 7. No new product APIs are planned.

**Produces:** A reviewable seven-AC evidence table, updated canonical docs, passing applicable local gates and explicit limitations. Each AC maps to an actual named test, its exact local command and passing result. This is acceptance integration work, not a second exhaustive static inventory.

- [ ] **3.1 Audit each AC against actual-app tests.** Ensure the Help Athena test genuinely navigates/mounts Athena and asserts section/Global labels. Rebind test compares correct rows in both surfaces before and after reopen. Service jumps must type/search/select and observe current host, not invoke helper methods directly. Source rollback must fail after real palette selection and verify displayed source. If any gap remains, first demonstrate RED by a new scenario on current branch, then make the narrow repair; do not silently claim the AC from a unit projection test.

- [ ] **3.2 Inspect 80×24 and 120×40 overlays with pilots.** Confirm long literal source labels/keys remain readable and scrollable; no markup execution; global/active-service headings; help keyboard scrolling reaches final diagnostics; palette focus/Enter/Escape containment unchanged. Preserve all theme assertions. If a supported snapshot harness changes, review actual rendered differences before accepting only intended palette goldens. Never bulk-update unrelated goldens.

- [ ] **3.3 Update canonical docs with exact shipped behavior.** Read the applicable three-surface-docs skill when executing this task; authorization remains local files/generation only. Replace docs/keybindings.md §1.6's deferred connection row and obsolete paragraph with contextual `Use <name> · <region> for <service>` commands, and explain current-connection supported-service entries, effective keys, `Unbound`, literal source values, omission and S3 focused-pane versus AWS whole-page changes. Add a concise cookbook walkthrough for Help → palette → exact source. Keep existing deferred `pane.move`/`pane.new` status and source-cycle semantics unchanged. Do not rewrite historical July design records as current documentation.

Proposed user copy to adapt only for final verified labels:

```markdown
Help (`?`) and the command palette (`:` / `Ctrl+K`) show the same command
names and configured shortcuts. Global commands are marked; service commands
follow the active page. Commands unavailable in the current context are omitted.
`Unbound` means a command has no configured keyboard shortcut.

Search **Go to Athena**, **Go to Glue**, **Go to EMR Serverless**, **Go to S3**,
or **Settings** to change pages. The service choices follow the active
connection's supported services. Search a connection name or region to choose
an exact configured source. On S3 this changes the focused pane; on Athena,
Glue and EMR Serverless it changes the page's source. No remote resource search
or additional regions are fetched when the palette opens.
```

- [ ] **3.4 Run docs generation/checks without publishing.** Use existing installed runtimes, no `uv sync` or install command:

```bash
.venv/bin/python -m scripts.docs.build_docs --site --wiki
.venv/bin/python -m scripts.docs.build_docs --check
.venv/bin/python -m scripts.docs.check_docs
.venv/bin/mkdocs build --strict
.venv/bin/python -m pytest tests/docs -q
```

On macOS preserve the established local Cairo environment for document asset rendering; inspect `Makefile` and available Cairo path before setting it. If a local runtime is unavailable, report the exact blocked check; do not install or claim pass. `scripts.docs.push_wiki --check` is offline only if the docs skill/repository gate requires it; never call its publishing mode.

- [ ] **3.5 Commit Task 3 and complete review before the controller gate.** This task owns focused acceptance/docs checks from steps 3.1–3.4, not a second full suite. Implementer self-reviews and commits the tested acceptance/docs changes. One fresh combined task reviewer receives BASE..HEAD and returns explicit spec and quality verdicts. New tested fix commits and re-review resolve findings. Then a fresh most-capable whole-branch reviewer checks all seven ACs, complete design/plan compliance, source/navigation safety, regression preservation and scope. Complete that review and any fixes before choosing the final reviewed head for the gate.

- [ ] **3.6 Controller runs ONE complete local gate on final reviewed head.** The controller has prepared `.superpowers/sdd/2026-10-06-unified-help-palette/controller-local-gates.py`. Prepare reviewer-approved golden accounting and the implemented-interface built-wheel discovery smoke. Obtain the full SHA from `git rev-parse HEAD`; the checkout must be clean and the SHA must be the final reviewed commit. Run `.venv/bin/python .superpowers/sdd/2026-10-06-unified-help-palette/controller-local-gates.py <reviewed-commit-sha>` once for that state. This is the concrete complete gate, not an optional alternative.

The runner must retain all **15 actual installed/cached pre-commit hooks under native macOS**, the minimum-version syntax check, and **full default pytest discovery with coverage** (no explicit tier list that excludes `tests/minimum_runtime`). Default test-marker exclusions remain exactly as configured. It also runs docs generation/checks, a local wheel+sdist build, distribution checks, Twine metadata checks and built-wheel discovery smoke. Pass the resolved exact wheel and sdist paths as explicit arguments to distribution/Twine checks; never use `dist/*`. Preserve dependency/skip/golden integrity and the existing combined coverage floor. No equivalent-tool substitution or limitation disclosure counts as passing a required cached hook. Any failed/blocked required check keeps the gate incomplete.

No install, hosted workflow or live AWS is permitted. Do not fetch/start missing Docker infrastructure. Do not claim a native platform matrix from this macOS run. A failed gate requires the smallest repair, new tested fix commit, appropriate review, and the controller's justified gate rerun on the newly reviewed head; do not rerun an unchanged passing full suite. Promotion follows the complete passing evidence, using the authorized protected flow without bypasses. Final delivery reports local OS/interpreter, exact outcomes and genuine exclusions.

- [ ] **3.7 Postpromotion checks.** After the controller's authorized promotion, verify promoted commit ancestry/clean tracked state and run a bounded smoke covering shared Help/key parity, one service jump, exact-source rejection rollback and modal containment on that promoted checkout. This is justified by changed promotion state; do not rerun the unchanged full suite. If source differs from the reviewed branch, require relevant checks again. #283 remains separate and unwaived; no issue closure is justified by invented native evidence.

## 6. Plan self-review checklist

- [x] Every AC points to an actual test and task; source failure and remap tests use production modal routes.
- [x] Catalog/registry coverage exists once; five unbound existing actions and four empty-default new service actions are intentional.
- [x] Named interfaces match across tasks; dynamic source handler includes captured origin and kind/name/region identity.
- [x] VM catalog remains pure; no widget/service/credential import crosses into it.
- [x] Existing EMR live palette updates, object-details origin guards, Athena results containment and source durable rollback are retained.
- [x] Scope-changing test/golden updates are explained individually; no unrelated assertions or default test exclusions changed.
- [x] Documentation is generated locally only; all evidence reflects actual local capabilities.

The architecture and controller self-reviews found no blocking requirements gap. The controller reviewed the exact app adapters and task scope; Task 1 follows the subagent-driven workflow.

Canonical numbered headings are retained. If the skill's task-brief extractor requires another heading shape, normalize only an ignored extraction copy; do not rewrite canonical numbering to satisfy the tool.
