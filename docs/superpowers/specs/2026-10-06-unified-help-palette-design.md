# Unified contextual help and palette discovery

**Issue:** #245. **Status:** reviewed under the owner’s autonomous goal; implementation follows the task and verification gates in the matching plan. **Base:** `ed0cf58aa24d5e2fa2c1e33a27ecefaa8e019076`. **Date:** 2026-10-06.

## 1. Outcome and boundaries

Help and the existing command palette project one action catalog. An action has one label, category, scope, effective-key tuple, and availability reason. Help displays global and active-service sections; palette rows display the same label and formatted keys. Both omit unavailable commands. The palette additionally provides direct supported-service and exact configured connection/region choices; those choices also appear as read-only command rows in Help.

Keep `ActionRegistry` authoritative for executable IDs, `KeymapStore` authoritative for configured keys, `BindingResolver` authoritative for keyboard installation, and the existing navigation/adoption paths authoritative for service/source changes. Widget-local events stay local. There is no new palette, remote resource search, URI parser, dependency, or replacement for `FocusCoordinatorVM`. No live AWS calls occur while enumerating/projecting discovery entries.

## 2. Approaches considered

1. **Shared immutable catalog and projection (recommended).** Put declarative metadata and pure projection in a VM-layer module. App composes availability from existing view/VM predicates and supplies the result to both surfaces. Keep the handler registry small. This separates presentation from invocation without changing existing keyboard semantics; it adds one focused module and narrow app adapters.
2. **Make `ActionRegistry.register` own metadata and readiness callbacks.** This ensures registration and metadata arrive together, but touches every registration and couples a currently generic callable registry to app/view context. It makes static coverage and use by lightweight registry tests harder, and still needs an adapter for dynamic sources. More invasive than this feature needs.
3. **Generate Help from existing `PaletteEntry` objects in the app.** Small initial diff, but the palette's late registration, special EMR cancellation row, embedded Athena keys, and one-time population flag become the metadata owner. This preserves incomplete static coverage and lets the registry drift. Reject.

## 3. Ownership and exact interfaces

Create `src/aws_tui/vm/chrome/action_catalog.py`. It imports only standard-library types. `ActionSpec` is static metadata; `ActionPresentation` is the shared resolved result. The first five fields of `ActionPresentation` preserve the current `PaletteEntry` positional constructor. Re-export `ActionPresentation as PaletteEntry` from `command_palette_vm.py`, so existing standalone palette clients retain their API.

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    label: str
    category: str
    keywords: tuple[str, ...] = ()
    service_ids: frozenset[str] = field(default_factory=frozenset)
    key_source: Literal["keymap", "unbound"] = "keymap"

@dataclass(frozen=True, slots=True)
class ActionPresentation:
    id: str
    label: str
    category: str
    keywords: tuple[str, ...] = ()
    service_ids: frozenset[str] = field(default_factory=frozenset)
    effective_keys: tuple[str, ...] = ()
    availability_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.availability_reason is None

ACTION_SPECS: tuple[ActionSpec, ...]

def project_actions(
    specs: Sequence[ActionSpec], *, registered_ids: frozenset[str],
    bindings: Mapping[str, tuple[str, ...]], active_service_id: str | None,
    unavailable_reasons: Mapping[str, str],
) -> tuple[ActionPresentation, ...]: ...

def format_effective_keys(keys: tuple[str, ...]) -> str: ...
def scope_label(service_ids: frozenset[str], active_service_id: str | None) -> str: ...
```

Projection retains unavailable records for tests/diagnostics but UI filters `available`. Reason precedence is missing handler, wrong service, then app-provided readiness reason. Use finite, value-free reason codes such as `handler_missing`, `service_inactive`, `source_unsupported`, `source_missing`, `page_unavailable`, `selection_required`, `busy`, `focus_required`, and `no_more_rows`; never put user values or exception messages in reasons. Missing configured keys for a `keymap` spec are an invariant error, not silently converted to empty keys. An `unbound` spec explicitly resolves to `()`.

`format_effective_keys` moves Help's current modifier/named-key formatting here, normalizes modifier and named-key case while retaining literal character case, joins aliases with ` / `, and returns `Unbound` for an empty tuple. Labels never contain a shortcut suffix. `scope_label` returns `Global` for empty scope, otherwise the active service's display name (S3, Athena, Glue, EMR Serverless). The scope of a shared navigation action is explicit metadata, not inferred from the `pane.` prefix.

`ui/bindings.py::_describe` reads the static catalog label when present; retain its tail-name fallback for standalone registries and deferred defaults. Do not change `_NON_PRIORITY_ACTIONS`, `_VISIBLE_ACTIONS`, key normalization, collision handling, modal routing, or the compact footer's independent chip labels.

## 4. Static metadata and coverage contract

Every ID returned by the app's initial `ActionRegistry.known_actions()` has exactly one `ActionSpec`, including currently non-curated navigation actions, EMR clone/filter/cancel, and the palette opener. Do not add `pane.move` or `pane.new` as working commands: they are defaults without handlers. Four new static navigation IDs are `service.open.s3`, `service.open.athena`, `service.open.glue`, and `service.open.emr-serverless`; Settings reuses `app.open_settings` rather than adding a duplicate. Register their handlers before the resolver materializes bindings and give the four new IDs empty defaults in `KeymapStore`, allowing explicit future configuration without introducing shortcut collisions.

The five currently registered IDs absent from defaults are explicitly `key_source="unbound"`: `pane.sort`, `pane.clear_filter`, `glue.open_s3_location`, `athena.open_result_location`, and `athena.open_in_glue`. Keep them unbound; the feature does not assign new shortcuts. Dynamic source IDs are also unbound but are not `KeymapStore` overlay keys.

Canonical labels for current curated rows are copied verbatim from `_PALETTE_COMMANDS`, excluding the appended Athena shortcut strings; `emr.cancel` keeps `Cancel selected EMR job run`. Added coverage uses the following labels/scopes:

| IDs | Labels (same order) | Scope/category |
|---|---|---|
| `app.command_palette` | Command palette | Global / App |
| `pane.switch_focus`, `pane.switch_focus_back` | Next focus target; Previous focus target | Global / Navigation |
| `pane.move_up`, `pane.move_down` | Move up; Move down | Global / Navigation |
| `pane.descend` | Open focused item | Global / Navigation; omit where no focused target is actionable |
| `pane.ascend` | Ascend to parent | S3 / Navigation; omit at root; the handler's editor Backspace branch remains input behavior, not a discovery command |
| `pane.modal_left` | Move left / parent | S3, EMR Serverless / Navigation; S3 parent or EMR logs only |
| `pane.modal_right` | Next log file | EMR Serverless / Navigation; logs only outside modals |
| `pane.refresh` | Refresh active view | S3, Athena, Glue, EMR Serverless / Navigation |
| `pane.copy`, `pane.delete` | Copy selected entries; Delete selected entries | S3 / File operations; named copy remains file action here even though physical c has EMR alias behavior |
| `pane.mark_up`, `pane.mark_down` | Extend selection up; Extend selection down | S3 / Selection |
| `pane.quick_look` | Quick Look | S3 / File operations |
| `emr.clone`, `emr.logs.filter` | Clone selected EMR job run; Filter EMR logs | EMR Serverless / EMR Serverless |
| four `service.open.*` IDs | Go to S3; Go to Athena; Go to Glue; Go to EMR Serverless | Global / Services, capability gated |
| `app.open_settings` | Settings | Global / Services; keywords include go, open, settings |

For remaining curated specs, preserve service sets from current code; use readable category names (App, Source, File operations, Selection, Loaded listing, Athena, Glue, EMR Serverless). Global navigation labels deliberately describe actual cross-service handlers. Modal-only effects are not independently discoverable working commands outside a modal; applicable non-modal effects above are described. No new global bindings are added for local picker, results-table, or log-widget events.

One unit test constructs the actual app (demo context, no mount or AWS), compares `set(ACTION_SPECS IDs)` exactly with `set(app._actions.known_actions())` before dynamic source registration, and loops every spec to validate label/scope and key policy against the actual `KeymapStore`. This is the single exhaustive coverage assertion. Integration tests assert specific semantics or use projections; they do not duplicate literal inventories. Dynamic coverage is separate: each generated presentation ID must satisfy `ActionRegistry.has` while registered and disappear after reconciliation removes it.

## 5. Availability, origin, refresh, and dispatch

App adds `_discovery_unavailability(origin: _DiscoveryOrigin) -> dict[str, str]` and `_project_discovery_actions(origin: _DiscoveryOrigin) -> tuple[ActionPresentation, ...]`. `_DiscoveryOrigin` is an app-private frozen value containing hosted service ID and VM identity, captured focus widget/slot, ancestor widget IDs, and the existing object-details origin. Capture it before pushing either overlay; do not inspect the palette Input as the underlying command context.

Use existing eligibility sources rather than reimplementing their domain logic:

| Commands | Availability source |
|---|---|
| Service-specific commands | Hosted service ID and attached corresponding page / VM; stale selected nav ID is not enough |
| File listing and selection | Focused pane exists; ready `PaneState.IDLE` or `EMPTY`; selection commands' `can_execute()`; keep current explicit named-action semantics |
| S3 details | Existing `_object_details_origin()` and its exact live row/provider/source/revision validation |
| Quick Look, copy/delete/entry-path, marks | Existing handler prerequisites: real selected entry, parent-link restriction and applicable file/provider prerequisites; no command shown merely because its ID starts `pane.` |
| Athena execution/cancel | Query VM command predicates already used in `_recompute_hint_disables` |
| Athena insert reference | Existing clipboard-source equality check |
| Athena result controls | Active Results view with live results controls; inspect/copy/sort require current selection; filter/reset remain usable with an empty projection; use captured underlying focus and original result lifecycle guards |
| Glue reference/query/snapshot | `can_copy_table_reference`, `can_query_in_athena`, `can_time_travel_in_athena`; S3-location entry additionally needs a selected valid location |
| Glue/Athena load more | Existing `page.can_load_more()` logic using captured focus IDs; add optional `focused_ids: frozenset[str] | None = None` parameter to each page's method so modal focus cannot change the target |
| EMR cancel | `page.vm.can_cancel_selected_run()`; preserve live busy/terminal updates while palette is open |
| EMR clone/log filter | Existing selected detail and `can_clone_source` prerequisites / captured focus within the log pane |
| Cross-service result/table locations | Existing source/selection/location validation; evaluate pure validation only, never call mutating open/query methods to discover availability |
| Source actions | Current service supports source changes and fresh candidates exist; Settings has none; same exact source is an available idempotent choice |

Extract the existing footer readiness calculation into a pure app adapter or a shared reason-map helper so `_recompute_hint_disables` and discovery consume the same existing predicates. Preserve the footer's set and copy exactly; extra discovery reasons must not change unrelated footer assertions. Remove the special handwritten EMR palette row. Its metadata is always static; `_recompute_hint_disables` refreshes its projected availability through the common catalog. Existing lifecycle notifications retain responsibility for refreshing open palette rows. Help can receive `update_actions(actions)` from the same refresh path without changing its scroll position; opening either overlay always obtains fresh keys and context.

`_populate_command_palette` becomes reconciliation, not a once-only snapshot: register current static/source entries by ID with fresh callbacks and unregister vanished IDs. Add a minimal `CommandPaletteVM.register_entry` equality fast path: when existing `.entry == entry`, update only `_actions[entry.id]` and return, retaining its VM child. This refreshes captured origin even when display bytes are unchanged. Changed entries retain the existing replace/dispose path. The app uses public registration APIs, never private VM maps, and introduces no generic reconciliation framework. `_command_palette_populated` may remain as an initialized-state flag for established tests; it must not short-circuit future refreshes. Preserve query text. Capture the selected entry ID before reconciliation and, if still visible afterward while open, restore its index with the existing public `move_selection_command` using the delta from `selected_index`; otherwise retain the VM's safe clamp/reset. Reopening resets search through the existing VM open command and refreshes metadata/keymap/candidates. Runtime replacement of `ctx.keymap_store` must be reflected by newly opened surfaces; this does not claim a new live keyboard-rebinding feature, because keyboard bindings are installed at app construction today. Production startup with an overlay must still install and display the same keys.

Both widgets render literal `rich.text.Text` or `Static(..., markup=False)` values. Escape terminal control characters to visible notation for display, while retaining the raw tuple for exact selection; preserve punctuation and Unicode literally. Help uses `HelpActionRow` with public `action_id` and `presentation` attributes; palette items expose the same attributes. Those fields permit row-specific pilot assertions without encoding user data into DOM IDs. Group Help into `Global — <category>` and `<service display name> — <category>` headings. Keep its mouse instructions, documentation links, diagnostic commands/paths, theme styles, keyboard scrolling and close behavior. Footer close text uses the projected `app.help` keys plus Esc.

Add one guarded app invocation adapter:

```python
async def _invoke_discovery_action(
    self, action_id: str, origin: _DiscoveryOrigin,
) -> None: ...
```

Register it as the palette callable; it is awaited by `CommandPaletteVM`'s existing task ownership/failure path. Wait for one post-dismissal `call_after_refresh` callback using a Future; do not sleep or await navigation/provider I/O inside the Enter handler. The callback only releases the continuation if still alive. On continuation: if shutdown, another modal, replaced content/origin, missing registry handler, or freshly projected reason prevents the action, return without invoking anything. Otherwise restore captured attached focus/slot only for that still-owned context, and invoke the named registry ID (await an awaitable). Preserve `_finish_palette_object_details` and its strict origin checks rather than broadening them. A callback/task failure still reaches `PaletteActionFailedMessage` with opaque entry ID and exception type only. Direct registry/keyboard guard behavior remains unchanged.

## 6. Service and source navigation

### 6.1. Service jumps

`_select_discovery_service(service_id: str) -> None` checks shutdown, current registry support for `root_vm.active_connection`, and then calls `services_menu.switch_service_command.execute(service_id)`. Existing `_on_nav_selection_changed` → `_mount_external_navigation` owns generation, content-mount worker, navigation lock, required teardown and view/VM adoption. Settings reuses its existing handler. No direct `content_host.current_id` assignment or new mounting path. No-connection startup offers Settings; S3 follows the existing menu/no-connection placeholder availability rather than pretending an AWS service is usable. An S3-compatible active connection offers S3 and Settings, never Athena/Glue/EMR merely because an unrelated AWS profile is configured.

### 6.2. Dynamic source identity and enumeration

Add app-private `_DiscoverySourceTarget(service_id: str, connection_kind: str, connection_name: str, region: str)`; store only these non-credential selection values, never a cached credential-bearing `Connection` object. Contextual source entries target the service hosted when the overlay opened. Read fresh `_live_connections(ctx)` locally; for Athena/Glue/EMR match `_service_source_candidates` semantics (AWS + `service.supports`); for S3 accept supported AWS and S3-compatible kinds with the same unreachable filtering as its existing swap candidates. Do not suppress AWS page candidates because S3 previously marked that source unreachable.

Label: `Use <connection name> · <region> for <service name>`; if region is empty, show `(default region)`. Keywords include full name, region, kind, service, source, switch, and connection. The same name and region across kinds must remain distinct; add the kind in the label if needed to disambiguate. Do not synthesize a Cartesian product of regions or call AWS for region enumeration: a source is the exact configured/resolved tuple.

Use opaque monotonic app-session IDs (`source.choice.1`, `source.choice.2`, …), keyed internally by the complete `(service_id, kind, name, region)` tuple. Maintain retained IDs across reorder/reopening; unregister and delete mappings for removed targets. Never recycle a removed ID for a different target, so a retained old callback cannot switch somewhere else. Do not hash or interpolate raw values into action IDs, VM names, task names, log events or failure payloads. Enumerating repeated opens must not grow registry/VM children. Register each dynamic handler in `ActionRegistry` before projecting it. Dynamic key policy is explicitly unbound.

### 6.3. Exact source dispatch and guarded adoption

```python
async def _select_discovery_source(self, entry_id: str, origin: _DiscoveryOrigin) -> None: ...

async def _switch_single_context_source_to(
    self, service_id: str, connection_name: str, region: str,
    *, connection_kind: str | None = None,
) -> bool: ...
```

The source handler resolves its opaque ID, calls `_supersede_table_navigation()`, then acquires `_service_navigation_lock` and checks `_service_navigation_is_owned_by("external", generation)`, navigation intake, original hosted VM/service ownership, and the fresh candidate tuple before mutation. The PaletteVM owns/awaits this coroutine outside the input event. No independent fire-and-forget worker or unsynchronized mount is created. Always pass exact name/region and kind to `_switch_single_context_source_to`; existing header callers omit kind and retain AWS behavior.

For Athena/Glue/EMR the helper retains `_rebuild_single_context_source`, `switch_connection_and_service`, `_mount_service_view(required_connection=target)`, durable cancellation rollback and source-selection-store behavior. Capture the relevant attached `ServiceSourceHeader` before adoption; if refused and it still represents hosted content, call `restore_source()`. If rollback remounted a replacement header, restore the currently attached header instead. Test actual picker displayed value, current VM source, root active connection, menu selection and DOM together. Never call `restore_source` on a detached obsolete header as the only rollback check.

For S3 add a narrowly documented branch to the legacy-named helper: re-resolve `(kind, name, region)` against supported live connections, validate the captured focused pane still belongs to the hosted dual VM, then call existing `_rebind_pane_to_connection(pane, target)` under the same navigation lock. Provider creation/auth failure retains the original pane; provider `swap_provider` retains its existing atomic identity-before-reload/error-state behavior. Do not rebuild RootVM or overwrite `root_vm.active_connection`, do not change the other pane, and do not invent whole-service rollback for a pane listing failure that currently remains an error on the selected source. Source entry construction never calls the provider factory. Existing source-cycle Local behavior remains; this ticket's named connection/region entries do not need a synthetic local connection.

Stale catalog, kind change, service replacement, unsupported target, or superseding navigation produces no source mutation; existing generic failure/advisory surface can explain refusal without logging target fields. Reusing existing low-level logging is allowed; new discovery diagnostics include only opaque IDs, fixed service IDs and error/reason types, never the complete Connection or credentials.

## 7. Verification and seven-AC mapping

| AC | Concrete verification |
|---|---|
| 1: active-service Help and global marker | Real `AwsTuiApp.run_test` with demo Athena mounted through nav; open Help by key; assert an Athena section and Global headings; assert Glue/EMR commands absent. Repeat S3/Settings distinction. |
| 2: equal label and effective key after rebind | Row-specific Help/CommandPaletteItem comparison for one shared action with multi-key overlay and empty overlay; reopen after replacing KeymapStore; confirm startup remapped shortcut still invokes existing handler. |
| 3: consistent unavailable behavior | Extend contextual projection integration test using actually hosted services, not only VM set_active_service; compare omitted execute/cancel/load-more and wrong-service IDs; preserve live EMR busy/terminal removal and assert stale selected callback invokes nothing. |
| 4: searchable supported service jumps | Pilot types each Go to label, presses Enter and waits for matching `content_host.current_id`, nav ID and mounted widget/VM identity; S3-compatible and no-connection capability cases. |
| 5: exact connection/region and rollback | Pilot source choice traces `_switch_single_context_source_to`, forces adoption refusal, verifies header `restore_source` and displayed/current source coherence; cancellation and late-navigation regressions; S3-compatible focused-pane choice preserves other pane and RootVM connection. |
| 6: handler presence and containment | Missing-handler projection and unregister-after-open tests; every visible dynamic/static ID satisfies `has`; unchanged modal/input/alias/origin suites, including Athena result and object-details guards. |
| 7: one exhaustive coverage test | Actual initial registry compared once to ACTION_SPECS; explicit default/unbound policies validated in that same test; no duplicated surface inventories. |

Use in-memory providers/clients and isolated AWS files. Require baseline RED for each unmet behavior before product edits. Preserve unrelated assertions and goldens; adjust only rows/fixtures whose semantics this feature intentionally changes. Full applicable local checks and postpromotion smoke remain controller gates. GitHub Actions stays disabled. No installs, hosted jobs, live AWS, published packages/releases/docs, or invented Windows/Python 3.11/native clipboard/human evidence. #283's native Windows promotion AC remains unwaived.

## 8. Risks and decisions resolved

The most important risk is evaluating availability against the palette's own Input: captured origin plus post-dismissal revalidation addresses it. The next is adopting a stale source choice: opaque lifetime IDs, a fresh lookup under the existing navigation transaction, and regression tests address it. A registry entry is not enough to prove a command is currently actionable; tests exercise real hosted pages and existing readiness predicates.

Routine choices are resolved above: omission, complete app metadata, explicit unbound keys, contextual exact source entries, current-connection capability gates, no duplicate Settings action, opaque session identity, and existing S3 pane semantics. The legacy helper name remains for the required route and backward-compatible header callers; its docstring must explicitly describe the S3 delegation. No blocking product question remains for the controller.
