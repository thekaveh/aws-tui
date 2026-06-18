# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Block-art brand banner** atop the chrome — six-row aws-tui logo with
  a per-theme 6-stop vertical gradient (carbon → deep navy/sky-blue;
  amber → mahogany/gold; voidline → deep purple/soft pink; lattice →
  dark teal/pale mint). Banner subscribes to a hub
  `ThemeChangedMessage` so the palette repaints in lockstep with every
  theme swap.
- **Dev `test-services/` harness for local AWS-compatible backends.**
  `scripts/test-services/s3/` ships a MinIO Docker Compose +
  `seed.py` that pre-populates 5 buckets (~90 objects) with a
  realistic mix: nested folder trees, unicode / long / spaced
  filenames, small files + an 8MB+ file that exercises the
  multipart-upload path. `up.sh` brings it up + seeds idempotently;
  `down.sh` stops (or `--purge` wipes). The directory layout is
  designed for extension — sibling subdirs (`ec2/`, `iam/`, …) can
  drop in their own docker-compose + seed using LocalStack as the
  backend for non-S3 services. See `scripts/test-services/README.md`.
- **Six new built-in themes** — three light themes (Solarized Light,
  GitHub Light, One Light) and three popular community palettes
  (Nord, Dracula, Gruvbox Dark). Cycle order (`Shift+T`) is now
  carbon → voidline → lattice → amber → solarized-light →
  github-light → one-light → nord → dracula → gruvbox-dark. All
  follow the structural template lattice + amber set: matching
  border / radius style, `$accent` / `$accent-hot` semantics, and a
  per-theme 6-stop banner gradient picked from the 256-color cube to
  track the theme's signature color family.
- **`+` / `-` toggle glyph in the services-menu title.** Click the
  glyph (or anywhere on the rail) to toggle the rail's collapsed
  state — discoverable affordance alongside the existing `s` key.
- **Live path in the pane border title** — left pane shows
  `s3://my-bucket/folder/`, right pane shows `/Users/.../path`. Identity
  (`kind · profile · region`) moved to the bottom border subtitle so it
  doesn't compete for the strip the old `StatusBar` used to occupy
  (`StatusBar` was removed in pass-7).
- **Adaptive pane columns** — `Pane.on_resize` recomputes the NAME
  column width as `pane_width − 32` (cursor + mark + separators +
  SIZE=10 + MODIFIED=16), clamped to `[12, 64]`. SIZE and MODIFIED
  stay visible on standard ~100-col-pane terminals; NAME grows when
  the user widens the terminal.
- **Mouse + trackpad scrolling**, `#pane-body` swapped from `Vertical`
  to `VerticalScroll`. Cursor moves trigger `scroll_to_widget` so the
  selected row stays in view past the visible window.
- **Theme switching from the keyboard or the chrome.** `t` opens the
  keyboard-navigable theme picker modal (↑/↓/Enter); `Shift+T` cycles
  to the next theme without a modal; both refresh the stylesheet via
  Textual's own `refresh_css(animate=False)` + a stable `read_from`
  key so theme sources don't accumulate.
- **Multi-select** via `v` + `Space`, `Shift+↑/↓` (extend selection),
  and modifier+click (`Shift`, `Cmd`, or `Ctrl` — `Shift+Click` is
  often consumed by macOS terminals for native text-select, so
  `Cmd+Click` is the reliable path there). Marked-byte total surfaces
  in the pane footer (`N obj · M marked · X selected`).
- **Collapsible services rail.** `s` toggles between 6-wide icon-only
  and 16-wide full-label mode; clicking the rail also toggles.
- **Pane source swap** via `Shift+S` and `PaneVM.swap_provider`. Any of
  the four `{S3, local} × {S3, local}` combinations can be live in the
  dual-pane in the same session.
- **Copy / delete via confirm modal**, `c` and `d`. Themable
  `_ModalButton` widgets replace `textual.widgets.Button` (which ships
  its own ANSI palette that fights theme overrides). `Enter` confirms,
  `Esc` cancels — App-level `priority=True` bindings are routed past
  the modal via the new `_forward_to_modal` helper.
- **Transfers overlay** — top-right floating box on the notifications
  layer. Each active transfer renders src → dst label + `ProgressBar`
  + Cancel button. Finished entries linger `AWS_TUI_TRANSFER_LINGER`
  seconds (default 3.0; env-overridable for tests) then fade so new
  transfers take their place.
- **`$AWS_PROFILE` env-var resolution** between `[defaults].connection`
  and the first-auto fallback — fixes the "aws s3 ls works on the CLI
  but the TUI shows access denied" SSO setup where `[default]` has no
  creds and the working profile lives in the env.

### Changed

- **VMx is now consumed from PyPI**, replacing the `vendor/vmx` git
  submodule. Pinned at `vmx>=2.6.0,<3.0.0` — PyPI's lowest published
  version is `2.6.0`, the vendored `python-v2.4.0` tag was never
  released. API parity across the 2.4 → 2.6 minor bump verified by
  522/40 tests staying green. Effects for users: fresh clones no
  longer need `--recurse-submodules`; CI checkouts no longer fetch
  the submodule; `scripts/bootstrap.sh` drops the
  `git submodule update --init --recursive` step. Internal source
  organization unchanged — every `from vmx import ...` continues to
  resolve to the same symbols. This also lifts the long-standing
  "aws-tui PyPI release blocked on VMx publishing" gate; aws-tui's
  own first PyPI release is the next scheduled milestone.

### Fixed

- **`[defaults].theme` from `config.toml` was silently ignored on
  launch.** `build_app_context()` hardcoded `initial_theme="carbon"`
  and never consulted `ConfigStore`, so a user who set `theme =
  "voidline"` got carbon on every launch and had to press `T` to
  reach their configured theme each session. Composition now loads
  the config at startup and falls back to carbon only if the file is
  absent or malformed.
- **Transfer journal silently destroyed on S3
  `AbortMultipartUpload` failure.** When the resume modal's "abort
  all" path hit any S3 error (network, expired creds, throttle, 5xx)
  the code suppressed the exception and unconditionally purged the
  journal anyway — so the MPU continued to live on S3 (consuming
  storage quota) with no local record of it, no recovery path. Now
  the journal is only purged after a successful abort; failures
  leave the journal intact for next-session retry.
- **`.content-placeholder` no-connection screen ignored user theme
  overrides.** All four built-in themes declared the placeholder
  background and foreground as hex literals matching their own
  `$bg`/`$text` instead of referencing the variables. A user who
  overrode `$text` or `$bg` in `~/.config/aws-tui/theme.tcss` would
  see every other widget pick up the override but the placeholder
  would stay on the built-in hex. Rewired the four themes' rules to
  use `$text` / `$bg`.
- **QuickLook leaked file handles / S3 streams on the 64 KiB cap.**
  The preview's `async for chunk in content.chunks: ...; break` left
  the iterator for garbage collection, so the underlying file handle
  (for LocalFS) or botocore stream (for S3FS) stayed open until the
  generator was GC'd. Now wrapped in try/finally + explicit
  `aclose()`.
- **S3 → local copy crashed the app.** `S3FS.stat / read_stream /
  write_stream / delete / mkdir / rename` all raised `ProviderError`
  when `bucket=None` — but the service-level `S3FS` is always
  constructed bucketless. New `_resolve(path) → (bucket, key)` helper
  centralises the "first path segment is the bucket" translation;
  every op now routes through it.
- **Copy / delete escalated to the crash modal.**
  `await push_screen_wait(modal)` requires a Textual worker context
  that binding-fired actions don't have, so the call raised
  `NoActiveWorker`. Switched to `push_screen(modal, callback)` plus
  `run_worker` for the actual transfer.
- **Theme picker arrows / Enter were eaten by App-level priority
  bindings.** Textual dispatches `priority=True` bindings in
  *reversed* order (App fires before the modal), so the dual-pane
  cursor consumed ↑/↓/Enter before the modal could see them. New
  `_forward_to_modal(*action_names)` in `AwsTuiApp` forwards
  `action_descend`/`action_ascend`/`action_move_up`/`action_move_down`
  to the active screen first.
- **Pane content "didn't load"** because rows were wider than the pane
  rectangle — adaptive columns fixed the actual symptom (SIZE +
  MODIFIED were just clipped off the right).
- **Theme switches didn't repaint unfocused widgets.** Replaced the
  hand-rolled tree walk with Textual's own `refresh_css(animate=False)`
  pipeline plus a hub broadcast (`ThemeChangedMessage`) for the
  Python-side palettes (banner).
- **Hint legend used hard-coded Rich styles** (`bold cyan`), so the
  footer never followed the theme. Each chip is now a `Static` with
  `.hint-key` / `.hint-label` / `.hint-sep` classes; coloring comes
  from theme tcss.
- **Path duplicated** inside each pane (in both the breadcrumb Static
  and the border title). Dropped the inline `.breadcrumb` Static — the
  border title is the single source.

### Removed

- `StatusBar` widget. Profile / region / auth indicator moved to the
  left pane's `border_subtitle`. The chrome VM stays so hub
  subscribers continue to receive updates.
- Vacuous `if t.id not in existing_ids: pass` dead branch in
  `TransfersOverlay._rebuild` — the linger arm beneath it is already
  idempotent.
- Duplicate `import sys` inside `app.main()` (was imported twice on
  separate exception branches); folded into the module-level import.

### Testing

- 41 new regression tests across 8 files cover every pass-7–12
  feature and bug-fix (`S3FS` bucketless ops, `PaneVM` border / swap /
  marked-bytes, `BrandBanner` theme palette swap, theme runtime
  propagation, chrome layout, hint-legend chips, modifier-click
  multi-select, ConfirmModal Enter forwarding, `$AWS_PROFILE`
  resolution, pane source swap). Total: 482 → 518 tests (net of 5
  dead-widget tests dropped in the pass-13 maintenance cleanup). The
  second overnight loop's pass-2 theme-default fix adds 3 (total 521);
  pass-10's journal-preservation regression test adds 1 (total 522).

### Fixed (first maintenance loop, passes 1–6)

- **App launch was visually blank** because `app.py.on_mount` never invoked
  `RootVM.switch_connection_with` / `switch_service`; widgets had no
  PropertyChangedMessage to render against. `on_mount` now resolves the
  initial connection (config defaults → first auto-discovered AWS profile),
  probes SSO state, awaits both switches, and mounts a `DualPane` widget
  into the content host. With no connection available, the content host
  shows a clear "configure one and relaunch" message.
- **Main-screen layout repairs**: `StatusBar`'s Statics had no width
  constraint and overflowed off-screen; `ToastStack` was in-flow (no
  layer / dock) and covered the left half of the screen with its empty
  auto-sized box; `DualPane`'s `> Pane` CSS selector didn't match because
  the Panes are children of an inner `Horizontal`; `Container#content-host`
  stayed empty after `switch_service` because the view layer never mounted
  the service's widget. Fixed all four.
- `.gitignore` inline-comment bug — gitignore has no inline-comment
  syntax, so the `snapshot_report.html       # comment` entry was
  matching the literal filename + trailing spaces + comment text and
  never ignoring the file. Moved comment to its own line. Same pass
  removed the no-op `~/.config/aws-tui/` and `~/.cache/aws-tui/`
  patterns (gitignore does not tilde-expand).
- `StatusBar` widget's `query_one(".status-auth-ok, .status-auth-warn,
  .status-auth-err", Static)` used a comma-union selector that
  Textual's query layer does not support — every auth-indicator update
  silently raised and the bar never refreshed. Each Static now gets a
  stable `id` and the refresh path queries `#status-auth`.
- Theme stylesheets used `Pane > Breadcrumb` / `> ColumnHeader` /
  `> PaneFooter` / `> .entry-row` / `> .pane-placeholder` selectors
  that never matched — `Breadcrumb` etc. are not widget types, and
  `EntryRow` / placeholder children live inside `Vertical(id=pane-body)`,
  so the direct-child combinator didn't apply. Switched the four
  built-in themes to descendant selectors + normalized the pane chrome
  class names to kebab-case (`column-header`, `pane-footer`); the
  pane chrome and row styling now actually theme.
- S3FS direct construction (the production path via S3Service) was
  using BotoConfig with no retries / timeouts; spec §6.3 + §7.3 mandate
  adaptive retries (6 attempts) + 10 s connect / 60 s read. Apply.
- `composition.run_aws_configure_sso` had no subprocess timeout — a
  hung `aws configure sso` froze the TUI forever. 10-minute cap
  matches the SSO device-flow grace; returns 124 on expiry.
- `TransferState` was defined twice (Literal alias in `vm/messages.py`
  + StrEnum in `vm/file_manager/transfer_vm.py`). Consolidated as a
  single StrEnum in `vm/messages.py`; `transfer_vm.py` re-exports it.
- Documentation drift across the prose docs:
  - README's Documentation index is now hierarchically numbered, plus
    the six prose docs (architecture, connections, cookbook,
    keybindings, theming, adding-a-service) carry hierarchical
    section/sub-section numbering. Inbound cross-doc anchors updated.
  - `docs/keybindings.md` and `docs/cookbook.md` action IDs now match
    `KeymapStore.DEFAULT_BINDINGS`; `pane.refresh` binding corrected
    from `Ctrl+R` to `r`; duplicate `r`-binding row removed (rename is
    `pane.move` with one entry marked, not a separate action).
  - `docs/architecture.md` testing-pyramid count corrected from 463
    to 429; the architecture doc's
    `MessageHub.subscribe(callback, filter=...)` claim replaced with
    the actual `hub.messages.subscribe(on_next=...)` API.
  - `docs/adding-a-service.md` cross-reference to spec §7 corrected
    to §2 (the FileSystemProvider protocol).

### Changed (first maintenance loop, passes 1–6)

- `AwsTuiApp.on_mount` (was 63 effective lines) extracted into four
  helpers: `_apply_initial_theme`, `_resolve_initial_connection`,
  `_mount_initial_service_view`, `_mount_no_connection_placeholder`.
  on_mount itself is now 18 lines.
- `pyproject.toml` `[tool.pytest.ini_options].addopts` now defaults to
  `-m 'not integration'` so `uv run pytest` runs the default tier
  (unit + snapshot + e2e + in-process integration) without Docker.
  The opt-in MinIO testcontainer tier runs via
  `uv run pytest -m integration`. CI continues to invoke
  `pytest -m integration` for the integration job — the right-most
  `-m` wins.
- `.github/dependabot.yml` `python-runtime` group now includes
  `tomli-w` (was missed); `python-dev` group includes `testcontainers*`
  and `types-*` (also missed) so they group instead of opening
  individual PRs.
- `.pre-commit-config.yaml` `astral-sh/ruff-pre-commit` bumped from
  `v0.15.0` to `v0.15.17` to match `uv.lock` (closes the patch-level
  drift the M1 fix `91f6040` left at the minor level).

### Removed (first maintenance loop, passes 1–6)

- Dead code: stray `_ = head` placeholder in `S3FS.delete()`; unused
  `max_concurrent` ctor param + field in `TransfersVM`. The function-
  local `DualPane` import in `app.py.on_mount` moved to a module-level
  import.

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

[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/thekaveh/aws-tui/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/thekaveh/aws-tui/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/thekaveh/aws-tui/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/thekaveh/aws-tui/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/thekaveh/aws-tui/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/thekaveh/aws-tui/compare/v0.0.1...v0.2.0
[0.0.1]: https://github.com/thekaveh/aws-tui/releases/tag/v0.0.1
