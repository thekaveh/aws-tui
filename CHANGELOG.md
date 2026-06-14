# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-06-14

### Added

- **UI layer (M5).** Full Textual widget tree binding to the M3 + M4
  VM hierarchy:
  - `ui/actions.py` — `ActionRegistry` mapping action id ->
    callable (sync or async).
  - `ui/bindings.py` — `BindingResolver` bridging `KeymapStore` to
    Textual's `Binding` list with dotted action ids translated to
    Textual action method names.
  - `ui/widgets/_subscriber.py` — `HubSubscriberMixin` that
    subscribes a widget to its VM's hub messages and dispatches
    `PropertyChangedMessage` filtered by `sender_object`.
  - Chrome widgets: `StatusBar`, `HintLegend`, `ToastStack` + `Toast`,
    `ServicesMenu` + `ServiceItemView`. Each constructor takes the
    bound VM + hub; CSS classes mirror VM flags so themes can tint
    selectively.
  - File-manager widgets: `Pane` (Breadcrumb + ColumnHeader + body
    + footer; entry rows with `-selected` / `-marked` / `-dir`
    classes; placeholder body for each `PaneState`) and `DualPane`
    (horizontal split with `-focused` class swap).
  - Overlay screens: `CommandPalette` (ModalScreen), `ConfirmModal`
    (ModalScreen with `-danger` class), `QuickLook` (ModalScreen
    streaming up-to-64KB), `TransfersTray` (rebuilds rows on every
    `transfers` property change).
- **Themes.** All four built-in `.tcss` files filled per spec §4.5:
  - Carbon (default) — near-monochrome, ice-blue accent, three-tier
    text hierarchy.
  - Voidline — neon cyan + magenta on near-black with double-line
    borders.
  - Lattice — mint-teal + lavender on deep teal with round borders.
  - Amber CRT — retro phosphor, single-color accent with thick
    borders.
  Each theme defines 14 palette tokens and styles every common
  widget class. `ThemeStore.load(name)` keeps working unchanged.
- **App composition root.**
  - `src/aws_tui/composition.py` — `AppContext` + `build_app_context`
    wire infra (`ConfigStore`, `LogSink`, `KeymapStore`, `ThemeStore`,
    `ConnectionResolver`, `AwsSession`, `TransferJournal`),
    `ServiceRegistry` with `S3Service` registered, `RootVM`, plus
    the four overlay VMs (`CommandPaletteVM`, `ConfirmationVM`,
    `QuickLookVM`, `TransfersVM`) + the shared `MessageHub` and
    dispatcher.
  - `src/aws_tui/app.py` — real composition root replacing the M0
    hello-world. Constructs the VM tree in `on_mount`, applies the
    theme via `stylesheet.add_source`, mounts the chrome + content
    host, and runs a graceful shutdown sequence (cancel transfers,
    close aioboto3 clients, flush log sink, dispose VMs).

### Testing

- **Unit tier (+47 tests).** New `tests/unit/ui/` suite covers
  `ActionRegistry`, `BindingResolver`, themes parsing, and smoke
  tests for every chrome + file-manager + overlay widget driven
  through `App.run_test()` Pilot.
- **Snapshot tier (32 goldens).** New `tests/snapshot/` runs
  `pytest-textual-snapshot` against full-app harnesses for main
  screen + 4 modal screens x 4 themes plus pane-state placeholders.
  Pinned to `(120, 40)` terminal and Python 3.12 / Ubuntu only.
- **E2E tier (5 journeys).** `tests/e2e/test_journeys.py` covers
  silent SSO, copy across panes, connection switch orchestration,
  resume-from-journal scan, and delete cancel spy.

### Layer rules

- Composition root (`composition.py`) and Textual app (`app.py`)
  live at the top of `src/aws_tui/` (not under any of the five
  layer dirs), so `scripts/check-layers.sh` does not need to be
  exempted — it only walks the five layer folders.

### Watch-outs captured

- `_context` attribute name on an `App` subclass collides with
  Textual's internal `App._context`; rename to e.g. `_app_ctx`.
- `_shutdown` method name on an `App` subclass collides with
  Textual's `App._shutdown` lifecycle hook; rename to e.g.
  `_aws_tui_shutdown`.
- Snapshot `.raw` files are excluded from the `end-of-file-fixer`
  and `trailing-whitespace` pre-commit hooks since they're
  byte-exact match targets.

### CI

- New `snapshot` job (ubuntu-22.04 / py3.12) running
  `tests/snapshot`.
- New `e2e` job (ubuntu-22.04 / py3.12) running `tests/e2e`.

## [0.5.0] - 2026-06-14

### Added

- **VM file-manager layer (M4).** First-class dual-pane Norton Commander
  viewmodels under `src/aws_tui/vm/file_manager/`, all wrapping VMx
  primitives via the facade pattern and free of Textual / boto3 / aws_tui.ui
  imports:
  - `vm/file_manager/entry_vm.py` — `EntryVM` facade over a
    `ComponentVMOf[EntryState]` with `toggle_select_command` and
    `toggle_mark_command` plus `set_selected` / `set_marked` setters used
    by `PaneVM` to drive cursor moves and select-all batches.
  - `vm/file_manager/pane_vm.py` — `PaneVM` facade over a
    `CompositeVM<EntryVM-inner>` with reactive `PaneViewModel` projection
    (breadcrumb, state, cursor, filter, summary). Async `setup()` /
    `navigate_to(path)` / `refresh()` re-run `provider.list()`; sync
    LOADING → IDLE/error state transitions around the awaitable work.
    Provider errors map per spec §7.7: `NotFoundError` at root → `EMPTY`,
    `PermissionDeniedError` → `FORBIDDEN`, `ProviderUnreachableError` →
    `UNREACHABLE`, other `ProviderError` → `ERROR` with `error_text`.
    `set_auth_required()` is the externally-driven `AUTH_REQUIRED`
    transition `RootVM` will invoke after observing
    `AuthExpiredMessage`. Commands cover open/ascend/refresh, cursor
    moves, multi-select (`toggle_select` enters multi-select mode if not
    already), `enter_multiselect`, `exit_multiselect`, `select_all`,
    `clear_selection`, `set_filter`. Async ops also expose
    `delete_marked`, `make_directory`, `rename_cursor`.
  - `vm/file_manager/dual_pane_vm.py` — `DualPaneVM` holding two
    `PaneVM`s + a `TransferJournal`. `copy_across` / `move_across` route
    through `domain.CrossFsCopy` / `CrossFsMove`, bridging per-chunk
    `TransferProgress` callbacks to `TransferProgressMessage` envelopes
    on the hub so `TransfersVM` and the chrome status bar can render
    aggregate progress. `switch_focus_command` toggles the focused pane;
    relay commands signal `*_requested` property-changed messages, the
    async methods do the actual work.
  - `vm/file_manager/transfer_vm.py` + `transfers_vm.py` — `TransferVM`
    facade over `ComponentVMOf[TransferModel]` with cancel/retry relay
    commands gated on state; `TransfersVM` holds a `CompositeVM` of inner
    VMs plus a subscription to `TransferProgressMessage` on the hub.
    Unknown transfer ids auto-register as placeholders so progress
    messages from direct `CrossFsCopy` callers don't get dropped. Exposes
    `active` / `finished` derived collections, `active_count`, and
    `total_bytes_done` / `total_bytes_total` totals the status bar
    consumes. `cancel_all_command` flips every active / pending transfer
    to `CANCELLED`.
- **First concrete service: S3.** `src/aws_tui/services/s3/service.py`
  implements the `Service` protocol from `vm.services_protocol`. The
  `supports()` predicate accepts both `aws` and `s3-compatible`
  connection kinds; `build_vm(connection)` composes a fresh
  `DualPaneVM(left=PaneVM(S3FS), right=PaneVM(LocalFS))` each call. An
  optional `s3_fs_factory` test hook replaces the real `S3FS` with an
  `InMemoryFS` for unit-level integration tests so no AWS calls leak
  out. `bind_hub(hub)` late-wires the `RootVM` `MessageHub` since the
  service is registered before `RootVM` has a hub.

### Testing

- **PaneVM capability contracts.** Hand-rolled selectable / filterable /
  pageable contract suite at
  `tests/unit/vm/file_manager/test_pane_vm_contracts.py` — VMx doesn't
  ship a Python `vmx.testing.conformance` package; this is the
  equivalent that pins the invariants any future PaneVM refactor must
  uphold.
- **M4 integration test.** `tests/unit/vm/file_manager/test_m4_integration.py`
  composes the full stack (RootVM ← ServiceRegistry ← S3Service) and
  drives switch_connection → switch_service('s3'), asserting a real
  `DualPaneVM` lands in `ContentHostVM.current` and is properly
  disposed on subsequent service / connection swaps.

## [0.4.0] - 2026-06-14

### Added

- **VM shell layer (M3).** Full application shell under `src/aws_tui/vm/`,
  all VMx-backed and free of Textual / boto3 imports:
  - `vm/messages.py` — six immutable hub message envelopes
    (`ConnectionChangedMessage`, `ThemeChangedMessage`,
    `AuthExpiredMessage`, `TransferProgressMessage`,
    `KeymapChangedMessage`, `FocusChangedMessage`) that satisfy VMx's
    `Message` protocol via `sender_name` + `sender_object`.
  - `vm/chrome/toast_vm.py` + `toast_stack_vm.py` — single-toast facade
    + stack with asyncio auto-dismiss timers for non-sticky toasts;
    dispose cancels all pending timers.
  - `vm/chrome/status_bar_vm.py` — reactive top-row status strip with
    derived `connection_label`, `region`, `auth_indicator`, and
    humanized `transfers_summary`; subscribes to
    `ConnectionChangedMessage` + `TransferProgressMessage` on the hub.
  - `vm/chrome/hint_legend_vm.py` — context-sensitive bottom chip row;
    swaps action chips on `FocusChangedMessage`, re-resolves through
    `KeymapStore` on `KeymapChangedMessage`, surfaces always-visible
    `: cmd` and `? help` fallbacks.
  - `vm/chrome/command_palette_vm.py` — fuzzy-filterable palette with
    a subsequence-span scorer (label substring > tight subsequence >
    keyword), Open/Close/Move/ExecuteSelected commands, and async
    palette-action support.
  - `vm/chrome/confirm_vm.py` — async `ask(request) -> bool` shim
    backed by an `asyncio.Future` (deliberately not
    `vmx.notifications` — the latter's notification-hub indirection is
    overkill for a single-modal use case).
  - `vm/chrome/quick_look_vm.py` — modal preview with Open/Close,
    bounded `scroll_offset`, and `find_query`; the body stream
    (`AsyncIterator[bytes]`) lives on `QuickLookContent` so file-I/O
    stays in the view layer.
  - `vm/services_protocol.py` — `Service` Protocol +
    `ServiceDescriptor` + `ServiceRegistry` + `ServiceNotFound`.
    Lives in `vm/` (not `services/`) so the VM layer can reach the
    protocol without violating the layer-rule check.
  - `vm/services_menu_vm.py` — left-rail service picker; filters the
    registry by `Service.supports(connection)` and reactively
    rebuilds on `ConnectionChangedMessage`.
  - `vm/content_host_vm.py` — child-swap host; `set_content(vm,
    service_id)` synchronously disposes the previous content via
    VMx's depth-first cascade and constructs the new one. Re-setting
    the same `service_id` is a no-op per spec §5.4.
  - `vm/chrome/chrome_vm.py` — facade aggregate of HintLegendVM +
    StatusBarVM + ToastStackVM.
  - `vm/root_vm.py` — top of the tree. Owns the `MessageHub` for the
    session, the three child aggregates, and the orchestration
    surface (`switch_connection_with`, `switch_service`,
    `switch_theme`, `focus`, `shutdown`).
- **VMx familiarization cheatsheet** at
  `docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md` captures
  the actual VMx Python API, the builder-pattern requirement, and the
  facade pattern aws-tui adopts for every VM.
- **Unit tier (121 new vm/ tests).** Each Task ships its own
  TDD-first test file plus a full M3 integration test
  (`test_m3_integration.py`) that exercises a 2-service registry,
  asserts dispose / build counts during switches, and verifies hub
  propagation end-to-end.

### Changed

- `services/__init__.py` re-exports `Service` / `ServiceRegistry` /
  `ServiceDescriptor` / `ServiceNotFound` from
  `aws_tui.vm.services_protocol` so the `services/` subtree can write
  `from aws_tui.services import ServiceRegistry` without breaking the
  vm/ → services/ layer-rule.
- M3 plan revised in-flight to document the actual VMx API
  (builder-pattern instantiation, no static `.builder()` on
  `AggregateVM3`, `.children(factory)` on composites, etc.).

## [0.3.0] - 2026-06-14

### Added

- **Domain layer (M2).** Norton-Commander unifier landed under
  `src/aws_tui/domain/`:
  - `filesystem.py` — `FileSystemProvider` Protocol + `PathRef`
    (immutable posix-segment path), `FileEntry` / `EntryKind`,
    `TransferProgress`, and the `ProviderError` taxonomy
    (`NotFoundError`, `PermissionDeniedError`, `ConflictError`,
    `ProviderUnreachableError`) per spec §7.2.
  - `local_fs.py` — `LocalFS` on `anyio.Path` + `aiofiles`. Symlinks
    surface as `EntryKind.SYMLINK`; `shutil.rmtree` runs on the
    threadpool; OSError → ProviderError mapping covers
    ENOENT/EACCES/EPERM/EEXIST/ENOTEMPTY/EISDIR/ENOTDIR.
  - `s3_fs.py` — `S3FS` on `aioboto3`. `bucket=None` lists buckets at
    root; `list_objects_v2` with `Delimiter="/"` + continuation-token
    pagination; `mkdir` writes a `/`-suffixed marker object;
    `delete` enumerates and `DeleteObjects`-batches up to 1000 keys
    per call; `rename` is server-side copy + delete; `write_stream`
    adapts the async source iterator into an awaited
    `upload_fileobj` so multipart works end-to-end. Botocore
    `ClientError` codes map to the ProviderError taxonomy;
    `EndpointConnectionError` → `ProviderUnreachableError`.
  - `cross_fs.py` — `CrossFsCopy` + `CrossFsMove` stream between any
    pair of providers, recurse into directories, and honour four
    `ConflictResolution` modes (`ERROR` / `OVERWRITE` / `SKIP` /
    `RENAME` — the last appends `" (1)"`, `" (2)"`, ... preserving
    the file extension). `move` only deletes the source after the
    destination write fully completes.
  - `transfer_journal.py` — append-only JSONL journal at
    `~/.cache/aws-tui/transfers/<id>.jsonl` with
    `begin / record_part / mark_finished / mark_aborted /
    find_unfinished / purge` — the persistence layer for M6's
    crash-resume modal.
- **Unit tier (79 new tests).** PathRef algebra, `InMemoryFS` provider
  contract, LocalFS (incl. 16 MiB round-trip + chmod-0 permission
  denial), S3FS against `moto.server.ThreadedMotoServer` (an HTTP
  mock so aiobotocore's awaited response body works), CrossFsCopy
  across all four provider pairs, and the journal replay.
- **Integration tier.** New `tests/integration/` runs against a real
  MinIO container via `testcontainers[minio]>=4`. Marked
  `@pytest.mark.integration`, opt-in via `uv run pytest -m
  integration`; skips cleanly if Docker is unavailable. Nine tests
  cover S3FS roundtrip (small + 16 MiB multipart), list/delete,
  bucket enumeration, and CrossFsCopy/Move across LocalFS↔MinIO and
  MinIO↔MinIO.
- **CI.** New `integration` job in `.github/workflows/ci.yml` runs on
  `ubuntu-22.04` (Docker available on GitHub-hosted runners) and
  executes the integration tier in parallel with the existing
  `unit` matrix.

### Changed

- Dev deps now include `moto[server,s3]>=5`, `testcontainers[minio]>=4`,
  and `types-aiofiles>=23`. Strict mypy stays clean.

## [0.2.0] - 2026-06-14

### Added

- **Infrastructure layer (M1).** Six independent boundary-layer modules
  under `src/aws_tui/infra/`, each unit-tested against tmp dirs:
  - `LogSink` — JSON-lines log writer with `RotatingFileHandler`
    rotation (5 MiB × 5 backups by default) at `~/.cache/aws-tui/log/`.
  - `ConfigStore` — TOML read/write of `~/.config/aws-tui/config.toml`
    via stdlib `tomllib` + `tomli-w`; atomic save via tempfile +
    `Path.replace`; `ConnectionEntry` / `Defaults` / `Keybindings` /
    `Config` frozen dataclasses; `kind` validation.
  - `KeychainBackend` protocol with `Keyring` (delegates to the
    `keyring` library) and `InMemoryKeychain` (test fake).
  - `ConnectionResolver` — unions explicit `[connections.*]` entries
    with AWS profiles auto-discovered from `~/.aws/{config,credentials}`
    via stdlib `configparser`; dispatches s3-compatible `credentials`
    against `keychain:` / `env:` / `aws-profile:` / `static` sources;
    `materialize()` promotes auto entries into the config file.
  - `AwsSession` — offline SSO cache probe (locates the cache file via
    `sha1(sso_session)` or `sha1(sso_start_url)`, reads `expiresAt`,
    compares against now-UTC with a 60-second skew buffer) and
    `aioboto3` client factory with botocore retries (adaptive, 6
    attempts), 10/60 s timeouts, force-path-style addressing, and
    `aclose_all_clients()` for graceful shutdown.
  - `ThemeStore` — layered `.tcss` loading (built-in via
    `importlib.resources` < user theme < user overlay); ships
    placeholder files for the four built-in themes (carbon, voidline,
    lattice, amber) to be filled in M5.
  - `KeymapStore` — action - keystroke indirection baked with the
    spec §4.2 defaults; overlay replaces per-action keys wholesale and
    refuses to introduce unknown actions.
- Integration sanity test composes all six components against tmp dirs
  to guard against circular imports and verify end-to-end probe success.
- Per-module strict-mypy + ruff + layer-rule clean.

### Changed

- Mypy config now ignores missing imports for `aioboto3` and `botocore`
  (no upstream stubs).

## [0.0.1] - 2026-06-14

### Added

- Initial project scaffold (M0): public GitHub repo, MIT license, VMx submodule, uv-managed dependencies, src-layout, hello-world Textual `AwsTuiApp` with `q`-to-quit, CI matrix on macos-14 / ubuntu-22.04 across Python 3.11–3.13.
- Full design spec at `docs/superpowers/specs/2026-06-13-aws-tui-design.md`.

[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/thekaveh/aws-tui/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/thekaveh/aws-tui/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/thekaveh/aws-tui/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/thekaveh/aws-tui/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/thekaveh/aws-tui/compare/v0.0.1...v0.2.0
[0.0.1]: https://github.com/thekaveh/aws-tui/releases/tag/v0.0.1
