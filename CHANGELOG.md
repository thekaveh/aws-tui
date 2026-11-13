# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The Unreleased section is the promotion queue for the next package release. Entries may reside on ``develop`` before promotion to ``main``; inclusion here does not by itself claim that every entry has landed on ``main``.

## [0.9.0] - 2026-11-12

Source prepared locally; package publication remains pending. Install from the Git repository until a published channel is confirmed.

Glue, Athena, and Iceberg integration are minor-version feature work in 0.9.0.
This release combines development since 0.7.0, including the unpublished 0.8 staging period (`cd2c9e8`).
The PyPI project-name request is tracked in [pypi/support#11264](https://github.com/pypi/support/issues/11264); account control and publisher setup still require verification.

### Added

- **EMR Serverless browser and job forms.** Browse applications and run details, read bounded S3 logs, and review a new Spark job or clone an existing run. Submission is explicit and requires AWS permissions.

- **Settings, themes and file selection.** Manage S3-compatible connections, preview themes before committing, and customize handled keybindings. Multi-select, local filtering, loaded-entry find, reviewed moves and folder creation are available from the file manager.

- **CloudWatch logs and follow.** EMR Serverless can discover and read the selected run's CloudWatch streams alongside its S3 logs. Explicit follow polls bounded intervals; stop, source changes and failures stop following. Reader permissions and retained-data limits are documented in the [EMR guide](https://github.com/thekaveh/aws-tui/blob/main/docs/services/emr-serverless.md#21-cloudwatch-source-and-loaded-data). Log writer permissions remain separate.

- **Local SQL drafts.** Opt-in Athena draft persistence stores plaintext SQL locally; it defaults off and is unavailable in demo mode. Recovery validates the exact original context, confirms replacement of modified SQL and never executes it. Pending or failed saves are not guaranteed recoverable. Verify the AWS account behind a same-name profile before running recovered SQL.

- **Athena result-cell controls.** Inspect complete values, copy cells or rows as JSON, filter loaded rows and sort one column. These transient controls neither rerun SQL nor fetch missing pages. Sorting is lexical, with null last; the 10,000-row ceiling can leave unfetched results.

- **Reviewed Athena result export.** Export the loaded view or complete execution as normalized CSV/JSON without rerunning SQL. Copy eligible original customer-S3 CSV artifacts with the required S3/KMS access. Review scope and destination first; byte, row, request and time limits bound export. Changed results or exceeded limits prevent incomplete publication; see the [export rules](https://github.com/thekaveh/aws-tui/blob/main/docs/services/athena.md#51-export-results-to-a-local-file).

- **Glue table comparison.** Choose two tables independently across configured AWS sources and refresh either side. Reports show field differences and observation provenance, redact parameter values and preserve failure states. Differences do not establish runtime compatibility or grant access.

- **Session launch selectors.** `--connection` or `--profile` selects an exact source; the two flags are mutually exclusive. `--region`, `--service` and `--location` select session context without saving configuration. A location implies S3. Startup validates source/service compatibility and never copies, deletes, uploads, executes SQL or starts jobs. Selectors conflict with demo mode and `doctor`; see the [launch recipe](https://github.com/thekaveh/aws-tui/blob/main/docs/cookbook.md#18-open-a-source-service-or-directory-from-the-cli).

- **Structured Quick Look.** Preview CSV, JSON, JSONL/NDJSON and Parquet files alongside raw text. The raw prefix is limited to 64 KiB; structured reads share a 32-request, 8 MiB budget. Samples do not establish full-file coverage. Unicode JSON strings retain their values; malformed or unsupported content receives bounded fallback or failure guidance.

- **Athena query console.** An AWS-only service provides Query, History, Results and Saved views with workgroup/catalog/database context. Single-statement read-only validation fails closed before submission. Cancellation remains app-owned; history and saved-query inspection do not execute SQL.

- **Athena SQL authoring.** Context-scoped completion, formatting, local SQL files and plaintext snippets preserve execution as an explicit separate action. Opening or replacing modified SQL requires confirmation. Local-file and collection limits are documented in the [Athena guide](https://github.com/thekaveh/aws-tui/blob/main/docs/services/athena.md#2-author-sql-and-use-local-files).

- **Scoped work overview.** Explicit session watches show bounded, manually refreshed read-only AWS status and exact existing-detail navigation. Per-source failures remain visible. Session transfers retain the existing 100-finished-entry limit; opening a detail never starts its operation.

- **Navigation workspaces and bookmarks.** Named local records save allowlisted navigation intent and revalidate current sources/resources before opening. They exclude SQL, credentials, results and running operations.

- **Reviewed S3 uploads and sync.** Recursive local-folder uploads and one-way local-to-S3 sync offer explicit per-object decisions, bounded concurrency, conditional publication and fresh retry reviews. Sync compares full-byte SHA-256 values and never prunes remote objects. Cancellation preserves successfully published objects; ambiguous outcomes require inspection. Batches are not atomic; see the [upload and sync limits](https://github.com/thekaveh/aws-tui/blob/main/docs/services/s3.md#5-file-operations).

- **S3 object properties and versions.** Inspect current metadata, tags, encryption and reported checksums on demand. Browse an exact key's retained versions, including deleted current objects. Download a selected version or recover supported content, metadata and tags to a different new key. Existing destinations are refused; published recovery keys survive cancellation.

- **Transfer History / Recovery.** The 100 newest safe summaries survive restart. Interrupted records preserve unknown outcomes; explicit retries revalidate endpoints and require fresh conflict decisions. Clearing history removes owned metadata only. Automatic replay and multipart resume remain unavailable.

- **Integrated Glue, Athena and Iceberg.** Read-only Glue browsing covers Catalog, Jobs and Crawlers; inspect bounded Iceberg metadata and compare snapshots. Explain reports identify observations and unknown coverage without predicting retention or compatibility. Exact-source table/snapshot handoffs prefill quoted Athena `SELECT … LIMIT 5` SQL without execution. Successful customer-S3 query artifacts can open in S3; Athena managed results have no such artifact.

- **Local Iceberg row preview with DuckDB (Peek).** One optional-extra path reads the selected Iceberg snapshot directly from S3 without Athena execution or workgroup/output setup. It requires an AWS profile, source permissions, applicable KMS access and reachable credential endpoints. Extensions need network access unless cached. Row limits do not bound S3 requests, bytes or charges; retries and load-more rescan. Metadata tabs can still execute Athena. Use the [source-install instructions](https://github.com/thekaveh/aws-tui/blob/main/docs/install.md#2-optional-extras); Peek is unavailable for S3-compatible connections.

- **EMR run cancellation.** Confirm cancellation of one selected active run. Acknowledgement means requested; existing polling observes the actual state. Clone retries reuse one form-intent token to avoid duplicate submissions.

- **Diagnostics and connection setup.** `aws-tui doctor`, JSON output and an explicit named-source probe explain local setup failures. In-session connection setup (#243) supports Save and open and Retry discovery. Setup leaves AWS config and credentials files unchanged. Unavailable keychains preserve actionable recovery; late saves do not switch the screen.

- **Demo mode.** Seeded in-memory S3, EMR, Glue and Athena providers offer isolated synthetic journeys without AWS calls. Explicit local SQL-file and snippet operations still use the real local filesystem.

### Changed

- **License and native paths.** The current project uses Apache License 2.0 with NOTICE attribution. Configuration and cache locations follow the OS; existing legacy XDG directories retain precedence.

- **Contextual commands and keybindings.** The command palette includes current service actions and dynamic connection/service entries. Per-theme dynamic entries and consolidation with Textual's built-in palette remain deferred. Handled keybinding overrides apply to the live map; handlerless actions stay unbound.

- **Navigation and selectors.** Service-context and EMR application pickers overlay without resizing adjacent panes. The compact command row exposes overflow and complete tooltips. Table handoffs share one action path and preserve exact source identity. Failed source changes retain outgoing SQL, draft provenance and context; stale replies cannot overwrite newer choices.

- **File browsing.** Loaded-entry filtering and sorting stay local to the bounded listing. `p` and `P` copy entry and pane paths; truncated names and values remain inspectable. File operations require a focused, ready pane; changed confirmations expire visibly.

- **Runtime compatibility.** Require VMx 3.23 or later below 4 and pin Textual to 8.2.8 for the isolated compatibility adapters. Python support remains 3.11–3.13; ReactiveX 4.x and 5.x are supported. See [Supported Platforms](https://github.com/thekaveh/aws-tui/blob/main/docs/platforms.md) for actual coverage.

### Fixed

- **Glue-to-Athena navigation.** The bottom Athena command preserves the selected table during normal application startup. Database and catalog changes select an available table and update its starter query. Replacing modified SQL still requires confirmation; selection never executes SQL automatically.

- **Athena execution and recovery.** Exact catalog/database context accompanies generated SQL. Run stays disabled during resolution. Queries remain stoppable after polling errors; failed Stop preserves ownership and prevents replacement. Metadata refresh retains valid choices and scoped failures.

- **Storage integrity.** Overwrite and deletion clean up owned staging safely, protect concurrent replacements and report uncertain publication outcomes. Reviewed moves refuse source/destination aliases before mutation. For S3 directories, existing-prefix merges remain distinct from refused whole-tree replacement and absent-tree publication; partial outcomes are explicit.

- **Lifecycle and bounded reads.** Service changes and shutdown drain owned transfers, queries, preview workers and clients before disposal. One shared 90-second budget bounds automatic startup attempts. Pagination rejects malformed/repeated tokens; gzip and preview reads enforce byte/line bounds. Refresh and cancellation cannot publish retired results into a new target.

- **Credentials and diagnostics.** AWS profile precedence follows botocore. Non-SSO credential chains reach validation. Keychain updates preserve the active configuration on failure. Provider errors, endpoints and diagnostics redact secrets; recovered failures reach durable logs without raw service data.

- **Terminal interaction.** Partial bracketed-paste recovery, resize handling, tooltip/mouse cleanup and modal focus routing prevent hidden-page input. Clipboard status distinguishes native helper success, failure and unacknowledged terminal delivery. Helpers run outside the UI thread.

- **Keyboard access and legibility.** EMR selected rows and theme choices remain visible while scrolling. Compact run details and transfer controls remain reachable. Settings and service focus rings support reverse traversal. Theme striping, muted text and documentation accents meet their checked contrast bounds.

### Documentation

- Task-ordered S3 and Athena guides preserve safety conditions and old fragments. Architecture figures include CloudWatch and optional DuckDB routes, with source permissions and network boundaries. Setup, S3Mock, directory-copy, preview-cost and future Homebrew recipes describe their actual prerequisites.

### Build

- Dependency and tooling updates include patched uv/pip trust stores and security fixes for locked libraries. The S3-compatible harness uses digest-pinned Adobe S3Mock. VMx, Textual, package-payload and dependency-floor checks retain their explicit compatibility contracts.

- Release tooling checks wheel/sdist contents and locked dependency inputs. Homebrew publication remains gated until its tap is enabled. No package, registry, website or workflow publication is implied by these local changes.

### Current limits and deferrals

- Automatic transfer replay and multipart resume remain unavailable. Use bucket lifecycle policy for abandoned multipart uploads.
- Per-theme dynamic palette entries and DuckDB 2 support remain deferred. The optional DuckDB extra retains its 1.x contract.
- Native Windows directory publication remains unresolved ([#234](https://github.com/thekaveh/aws-tui/issues/234)); required Windows settings stress evidence is still missing ([#274](https://github.com/thekaveh/aws-tui/issues/274)).
- Unhandled fatal errors write a crash report and exit; they do not open the optional in-app crash-choice dialog.

The complete [unpublished 0.8 development history](docs/superpowers/notes/2026-11-12-unpublished-0.8-development-history.md) preserves the original Pending section, including superseded claims.

Detailed pre-curation implementation notes are retained in the [dated maintainer record](https://github.com/thekaveh/aws-tui/blob/main/docs/superpowers/notes/2026-10-22-unreleased-implementation-history.md).

## [0.7.0] - 2026-06-14

### Added

- **Crash modal + unhandled-exception capture (M6 T1).** Top-level
  Textual `App._handle_exception` override writes a dump to
  `~/.cache/aws-tui/crash/<ts>.txt` containing the full traceback,
  up to the last 1000 lines of the JSON log, and up to the last 100
  user actions (ring buffer on the `App`). The composition root
  hands the resulting `CrashReport` to a `CrashVM` facade whose async
  `ask()` resolves to `CrashChoice.CONTINUE / VIEW_TRACE / QUIT`.
  `CrashModal` widget renders the short trace + dump path + three
  buttons; the `continue` button is disabled when the last user
  action was not in the read-only allowlist
  (`SAFE_CONTINUE_ACTIONS`) per spec §7.10. `main()` re-raises the
  original exception after the app has torn down so stack traces
  aren't swallowed silently. Snapshot tests across all four themes.
- **Transfer-journal resume modal (M6 T2).** `ResumeVM` holds the
  unfinished `TransferJournalEntry` rows surfaced by
  `TransferJournal.find_unfinished()` on startup; async `ask()`
  returns `ResumeAction.RESUME_ALL / ABORT_ALL / DECIDE_EACH /
  KEEP_FOR_LATER`. `ResumeModal` renders one row per entry with
  the spec §7.6 summary format. `composition.apply_resume_decision`
  routes `ABORT_ALL` through `AwsSession.client("s3")` to call
  `AbortMultipartUpload` per `upload_id`, marks the journal
  aborted, and purges the file. `DECIDE_EACH` folds to
  `KEEP_FOR_LATER` per plan §M6 T2. Snapshot tests per theme.
- **First-run flow (M6 T3).** `FirstRunVM` async facade with three
  actions (`ADD_AWS / ADD_S3_COMPAT / SKIP`). `FirstRunModal`
  welcome screen with three buttons; companion `S3CompatFormModal`
  collects the five fields needed for an s3-compatible connection
  (secret rendered with `password=True`). `composition`-level
  helpers: `needs_first_run` (true when both config + AWS
  auto-discovery are empty per spec §6.4 Flow 5),
  `run_aws_configure_sso` (blocking subprocess to `aws configure
  sso`), `add_s3_compat_connection` (writes a `static`-credentials
  entry to `ConfigStore`). Snapshot tests per theme.

### Documentation (M6 T4)

- README polished with the full features list, install +
  development-workflow recipes, quickstart (including the first-run
  modal walkthrough), and a file-locations table.
- Every `docs/*.md` page rewritten or substantially expanded:
  - `architecture.md` — five-layer breakdown, composition-root
    responsibilities, messaging + lifecycle, testing-pyramid totals,
    where-to-start reading order.
  - `keybindings.md` — full action-id table, customization via
    `[keybindings]` with fallback lists and disable-by-empty-list.
  - `theming.md` — built-in matrix, loader stacking rules,
    Carbon palette tokens, snapshot-test note.
  - `connections.md` — TOML schema with three real connections,
    credential-source preference order, auto-discovery + SSO cache
    probe semantics, vendor-quirk checklist, MPU lifecycle rule.
  - `adding-a-service.md` — Service protocol template,
    EC2Service pattern, layer-rule cheat sheet for service modules.
- `docs/cookbook.md` (new) — four end-to-end recipes: connect to a
  local MinIO (with macOS Keychain), switch theme on the fly,
  customize a keybinding, resume after a crash.
- `docs/recording-todo.md` (new) — explicit list of six
  asciinema / PNG artifacts the maintainer needs to record manually
  (a subagent cannot drive a real terminal), with copy-pasteable
  recipes and embed locations.

### Testing

- **Unit tier (+49 tests).** `tests/unit/vm/chrome/test_crash.py`,
  `test_resume.py`, `test_first_run.py` (VMs);
  `tests/unit/ui/test_crash_modal.py`, `test_resume_modal.py`,
  `test_first_run_modal.py` (widgets);
  `tests/unit/infra/test_crash_dump.py` (infra);
  `tests/unit/test_composition_resume.py`,
  `test_composition_first_run.py` (composition helpers).
- **Snapshot tier (+12 goldens).** 3 new modals (crash, resume,
  first-run) × 4 themes, all pinned to (120, 40).

### Watch-outs captured

- Textual's `App._handle_exception` is sync + fatal, so we write
  the dump there and re-raise from `main()` rather than try to push
  the crash modal from inside the failing render path. The CrashVM
  / CrashModal pair is still in the public composition for tests and
  future recovery flows; an explicit `App.show_crash_modal(report)`
  method drives the modal when the runtime is still healthy.
- `DECIDE_EACH` and `RESUME_ALL` on the resume modal are recorded
  as no-ops in v0.7.0: per-entry sub-modal + TransferVM resume
  scaffolding lands in a follow-up. Journals stay on disk so users
  don't lose state.

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

[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/thekaveh/aws-tui/compare/v0.7.0...v0.9.0
[0.7.0]: https://github.com/thekaveh/aws-tui/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/thekaveh/aws-tui/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/thekaveh/aws-tui/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/thekaveh/aws-tui/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/thekaveh/aws-tui/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/thekaveh/aws-tui/compare/v0.0.1...v0.2.0
[0.0.1]: https://github.com/thekaveh/aws-tui/releases/tag/v0.0.1
