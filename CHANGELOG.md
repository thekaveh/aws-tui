# 1. Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.1. [Unreleased]

These changes have landed on ``main`` since the v0.8.0 cut commit
(``cd2c9e8``) but have not yet been packaged as a release. The v0.8.0
PyPI publish is gated on
[pypi/support#11264](https://github.com/pypi/support/issues/11264)
(name-similarity exception for ``aws-tui`` vs ``awstui``). All work
below will either ship as v0.8.1 (patch — UI polish + bug fixes) or
roll into v0.9.0 if the maintainer chooses to recategorise the new
nav-focus + demo-mode behaviour as feature work; the version label
will be set at cut time.

### 1.1.1. Added

- **Demo mode** (PR #97 + #104 polish). ``AWS_TUI_DEMO=1`` or
  ``--demo`` boots the full UI against seeded in-memory S3 + EMR
  fakes — no AWS credentials, no real network calls. Persistent
  ``DEMO MODE — no real AWS calls`` chip prepended to the
  BrandBanner subtitle (PR #104 keeps the credit pedigree visible
  after the chip). See ``docs/superpowers/specs/2026-06-28-demo-mode-design.md``.
- **NavMenu ENTER opens the service** (PR #101). Pressing ``Enter``
  on a service row in the rail now shifts focus to that service's
  default pane — S3 → LEFT pane, EMR → Job Runs, Settings → first
  section's ``CollapsibleTitle``. ``NavMenu.action_commit`` drops
  rail focus before calling the new ``App.focus_active_service_pane``;
  page-side auto-focus is gated on "is NavMenu still focused?" so
  arrow-walking the rail still keeps focus there (only ``Enter``
  hands focus off). New ``DualPane.focus_left_pane`` +
  ``SettingsView.focus_default``.

### 1.1.2. Changed

- ``docs/`` + ``ui/widgets/settings_view.py``: retargeted the
  ``Deferred / v0.8 roadmap`` references to ``Deferred / v0.9
  roadmap`` (and ``coming in v0.8`` placeholders to
  ``coming in v0.9``) since v0.8.0 shipped without those items.
  Historical CHANGELOG entries kept their original wording.
- ``assets/screenshots/aws-tui-running.png``: refreshed the README
  hero image to a current EMR + demo-mode capture (PR #106).

### 1.1.3. Fixed

- **EMR + UI polish train.** Eight follow-up PRs (#96, #98, #99,
  #100, #102, #103, #104, #105) addressed user-reported issues
  found while exercising the EMR + nav-rail surface:
  - markup crash + polling cadence + NavMenu pane-parity (#96);
  - NavMenu polish + EMR demo cadence (#98) — four items batched;
  - focus-steal + redraw flash + WARN-noise in default log filter
    (#99) — drop WARN from ``DEFAULT_LOG_FILTER``;
  - no row / option re-mount on arrow-walk (#100) — JobRunsPane uses
    a class-flip selection repaint, ApplicationPicker diff-guards
    the option rebuild;
  - NavRow ribbon flush with the pane edge (#102) — drop the
    horizontal padding so column 0 is the ribbon, matching EntryRow;
  - filter EMR hub messages by sender (#103) — kill cross-VM
    redraws on the shared MessageHub when sibling VMs fire ``state``
    or ``selected_id`` echoes;
  - preserve the credit pedigree in the demo banner subtitle
    (#104) — prepend the DEMO chip instead of replacing;
  - Settings NavRow keeps the ``-selected`` highlight (#105) —
    drop the redundant per-theme override that out-specificity'd
    ``NavRow.-selected``.
- **S3 pagination defensive break.** ``S3FS.list()`` and
  ``S3FS.delete()`` no longer infinite-loop when an S3-compatible
  provider returns ``IsTruncated=True`` without a
  ``NextContinuationToken`` (MinIO has historically shipped this
  edge case). Pattern now matches ``emr_logs.py::list_log_files``.
- **Resume modal no longer advertises unwired resume-all.** Automatic
  transfer resume remains deferred, so resume-all is removed; abort all
  and keep-for-later are wired, while decide-each remains a deferred
  no-op placeholder.
- **First-run S3-compatible save failures no longer crash the error
  handler.** The modal now uses supported Textual notification kwargs in
  test harnesses and the unified toast taxonomy in production.
- **S3-compatible credential hardening.** Settings and first-run now share
  normalized form-to-config mapping, blank optional session tokens resolve
  as absent across static/env/keychain/profile sources, and
  ``S3CompatForm`` / ``ConnectionEntry`` reprs mask static credentials.
- **Crash reports now log through the configured JSON log sink.** The
  `crash.captured` event records exception type, dump path, and the last
  recorded action; wired app actions now populate the action ring before
  dispatch.
- **EMR Serverless service package facade now matches S3.**
  `from aws_tui.services.emr_serverless import EmrServerlessService`
  works and is pinned by an import-contract test.

### 1.1.4. Docs

- ``docs/superpowers/specs/`` — added the demo-mode and
  cross-platform-readiness design specs (in ``fc55c6a``).
- ``SECURITY.md`` — supported-version table now distinguishes the
  pending 0.8.x line from the latest tagged 0.7.x release;
  ``docs/homebrew-bootstrap.md`` adopts the §3.9 numbered heading
  mandate; ``docs/recording-todo.md`` retargets to "v0.8.0 docs feel
  done".
- README §4 — indexed three previously-orphaned post-tag specs
  (public-release-pipeline, cross-platform-readiness, demo-mode)
  and the maintainer-facing ``docs/RELEASING.md`` +
  ``docs/homebrew-bootstrap.md``.
- Repository markdown headings are now hierarchically numbered for the
  `NUMBERED_DOCS=yes` maintenance policy; local markdown anchors were
  refreshed to match the new GitHub slugs.
- README now indexes the VMx vNext upstream-asks spec and documents the
  v0.8.x English-only localization policy; the VMx upstream spec uses
  standard Markdown links instead of wiki-style links.
- The consumed-contract ledger now records the EMR Serverless
  botocore contract and immutable GitHub Actions workflow refs.

### 1.1.5. Build

- Dependabot bumps for ``actions/upload-artifact`` (4→7, PR #1) and
  ``astral-sh/setup-uv`` (3→7, PR #2). Follow-up alignment commit
  brought ``release.yml`` to the same versions (``download-artifact``
  bumped to v7 in lockstep) so the CI and publish pipelines share
  action majors.
- CI and release verification now run a pytest-cov coverage floor for
  unit + in-process integration tests.
- GitHub Actions workflow dependencies are pinned to immutable commit
  SHAs, with inline version comments preserving their human-readable
  source refs.
- Workflow guard tests now assert executable pytest command lines and scan
  nested integration tests for marker drift.

## 1.2. [0.8.0] - 2026-06-27

### 1.2.1. Added

- **EMR job-run logs pane** (PR #84, service PR-B — logs surface).
  Lower half of the right column in the EMR page. Streams the selected
  run's ``s3MonitoringConfiguration.logUri`` from S3 (gzip-decompressed
  in 64 KB chunks), filters lines through a configurable regex set
  (default: ``ERROR`` / ``WARN`` / ``FAIL`` / ``Exception`` / 
  ``Caused by`` / ``Traceback`` / ``Killed`` / ``OutOfMemoryError``),
  and shows them with progress feedback. On-demand fetch only — press
  ``Enter`` on the logs pane to load; ``r`` reloads, ``f`` opens the
  filter modal. New ``vm/emr_serverless/job_run_logs_vm.py::JobRunLogsVM``
  + ``ui/widgets/emr_serverless/job_run_logs_pane.py::JobRunLogsPane``.
- **EMR Serverless: clone-job-run modal** (PR #83, item #7 of the
  user-feedback batch; lands ahead of the rest of PR-C). New
  ``vm/emr_serverless/clone_vm.py::JobRunCloneVM`` +
  ``ui/widgets/emr_serverless/clone_modal.py::JobRunCloneModal``;
  ``EmrServerlessClient.start_job_run`` API. Bound to ``c`` on the
  EMR page (``Binding("c", "clone_selected_run", "Clone")``);
  ``app.py::action_copy`` hijacks ``c`` to the EMR clone path when
  EMR is mounted, parallel to the existing dual-pane priority
  short-circuits. ``KeymapStore.DEFAULT_BINDINGS`` adds
  ``"emr.clone": ("c",)``;
  ``HintLegendVM._SERVICE_ACTIONS["emr-serverless"]`` gains
  ``"emr.clone"`` and ``_ACTION_LABELS["emr.clone"] = "clone"``
  so the Commands strip shows a ``[c] clone`` chip when EMR is
  active. ``notifications.Subject`` literal gains ``"Job"`` for
  the success / error toasts.

### 1.2.2. Changed

- **Screen layers gain ``dropdown``** (PR #83, items #1 + #2).
  ``Screen { layers: base dropdown notifications }`` (was
  ``base notifications``) so the EMR ``ApplicationPicker``'s
  ``OptionList`` lands on its own stacking context — the
  "There's no dropdown!" symptom from PR #81 is resolved.
  ``Shift+S`` on the EMR page already forwarded to
  ``EmrServerlessPage.action_open_application_picker``; with the
  popover now actually visible the keystroke produces the
  expected affordance.
- **EMR Serverless icon settled back on ``🔥`` FIRE.** PR #83
  tried ``💥`` after earlier ``⚡`` / ``⚡️`` experiments, but the
  collision glyph rendered too small beside the S3 bucket icon. The
  shipped descriptor uses the PR #79 fire glyph: SMP single-codepoint,
  2-cell colour reliably, and full bounding box. Updated at the
  descriptor in
  ``services/emr_serverless/service.py`` and the dropdown
  labels in ``ui/widgets/emr_serverless/application_picker.py``;
  the documented icon contract in ``nav_menu.py`` is now
  satisfied (SMP single-codepoint, no VS-16 dance).
- **EMR error / advisory paths now route through the unified
  toast helpers** (PR #83, item #4). ``AwsTuiApp.action_copy`` /
  ``action_delete`` / ``action_swap_source`` no-config /
  no-target paths now use ``notifications.error(...)`` /
  ``notifications.advise(...)`` instead of Textual's bare
  ``self.notify`` — the latter paints over the Commands strip
  and disappears with the wrong glyph / wrong colour / wrong
  countdown. Errors get the ✖ glyph + ``$danger`` colour + 30 s
  countdown; advisories get the ⚠ glyph + ``$warning`` colour +
  8 s countdown. Same routing as every other toast in the app.
- **HintLegend chips now reflect selection-state disable rules**
  (PR #83, item #5). ``HintAction`` gains an ``enabled: bool``
  field; ``HintLegendVM`` gains ``set_disabled_actions(frozenset)``;
  an app-level ``_on_hub_message_cursor`` subscriber listens for
  ``PaneVM`` cursor / viewmodel / entries changes and pushes the
  disabled action set. Today's only rule: cursor on the ``..``
  parent-link row disables ``pane.copy`` and ``pane.delete``. The
  widget renders disabled chips with the ``-disabled`` CSS class
  (``text-style: dim``). The existing app-handler short-circuit
  on ``is_parent_link`` stays as the actual no-op gate; the
  disable flag is the visible affordance.
- **Commands strip is now ONE concatenated row** (PR #83, item
  #6). PR #81's left / right dock split is gone; service-specific
  chips come first, globals follow, in a single ordered
  ``#hint-strip`` row. The ``_hint-strip-service`` and
  ``_hint-strip-global`` ids are removed. User feedback: "I want
  their concatenation displayed at the bottom" — same chip set,
  single ordered list. ``test_main_screen`` snapshots
  regenerated across all 10 themes.

### 1.2.3. Added (prior entries)

- **EMR Serverless read-only browser** (PR #76, service PR-A). New
  ``🔥`` nav-rail entry next to S3, gated to AWS-only connections.
  Applications dropdown, master-detail Job Runs pane + Job Run Detail
  pane with multi-select state-filter chips, three independent
  pollers (apps 30 s / runs 10 s with 6:1 decay when no active runs /
  detail 5 s with terminal-state suppression). Hierarchical VM tree
  (`EmrServerlessPageVM` orchestrates `ApplicationsVM` +
  `JobRunsVM` + `JobRunDetailVM`) and a dedicated UI widget tree
  under `ui/widgets/emr_serverless/`. PR-B (cancel + logs), PR-C
  (submit form), PR-D (E2E journey) follow.
- **EMR page arrow-key navigation + LEFT-pane auto-focus on mount**
  (PR #78). Up/Down/Enter/`r` route through the EMR page when it is
  mounted (parallel to the dual-pane hijack pattern); the LEFT pane
  gets the `:focus-within` accent border by default, matching S3's
  default-active-pane behavior.
- **EMR page layout overhaul** (PR #80). Bordered apps box now
  width-matches the LEFT pane (compose restructured into a 2-column
  horizontal split, LEFT = `Vertical(app_box, JobRunsPane 1fr)`,
  RIGHT = `JobRunDetailPane` full height). Columnized run rows
  (STATUS / NAME / TIME with column header), master-detail follows
  the cursor (not just Enter), mouse-click on rows (via
  `_JobRunRow` subclass carrying `run_id`), and multi-line args /
  spark params in the detail pane. Page width split tightened from
  `1fr / 2fr` to `1fr / 1fr`.

### 1.2.4. Changed

- **"Commands" pane renamed from "Shortcuts"** (PR #81). The
  `HintLegend` border title and all user-visible references now read
  "Commands"; the strip is also split into a service-actions row
  (focused-pane block) and a global row, with hamburger margin /
  border alignment tightened in the same PR.
- **EMR Serverless icon — the ⚡️ ↔ 🔥 ↔ ⚡ ↔ 💥 ↔ 🔥 saga** (PR #77 /
  #79 / #81 / #83). Bare `⚡` (PR #76) rendered as a narrow 1-cell
  text-style stroke in SF Mono / JetBrains Mono / Fira Code,
  mis-aligning the nav-rail's 2-cell emoji column. PR #77 forced
  emoji presentation with `⚡️` (BMP U+26A1 + U+FE0F VS-16);
  PR #79 briefly tried `🔥` (SMP, reliable 2-cell colour);
  PR #81 returned to `⚡️` with VS-16 per user ask; PR #83 picked
  `💥` (SMP U+1F4A5 COLLISION), then the shipped descriptor returned
  to `🔥` after collision rendered too small beside the S3 bucket. The
  documented icon contract is codified in
  `nav_menu.py::_format_collapsed_prompt` /
  `_format_expanded_prompt` — SMP single-codepoint, no VS-16.
- **(fourth overnight-maintenance loop, pass 1)** EMR Serverless
  client made production-tight without changing user-facing
  behavior. ``JobRunState`` gains ``SUBMITTED`` / ``SCHEDULED`` /
  ``QUEUED`` and ``ApplicationState`` gains ``CREATING`` to close
  the boto-service-model drift surfaced by the dependency-contract
  pass (a freshly-submitted run no longer crashes the 10-s poller).
  `_map_boto_error` now wraps `ValueError`/`KeyError` as
  `ValidationError` so any future enum drift surfaces as a
  recoverable provider error, not an uncaught exception.
  ``EmrServerlessClient`` adopts a botocore ``Config`` with
  ``connect_timeout=10`` / ``read_timeout=60`` /
  ``retries.max_attempts=6, mode="adaptive"`` matching the S3
  client's posture. ``JobRunsVM.has_active_runs`` extended to
  include `SUBMITTED` / `SCHEDULED` / `QUEUED` / `CANCELLING` so the
  10-s cadence stays sticky through the transient cancel window.
- **(fourth overnight-maintenance loop, pass 1)** EMR page double-
  setup race fixed: ``EmrServerlessPage.on_mount`` no longer launches
  its own ``setup()`` worker — ``ContentHostVM`` already drives it,
  the page-side worker was a double dispatch. Pollers (apps / runs /
  detail) all switched to ``exclusive=True`` so backed-up ticks
  serialize rather than overlap. ``Backspace`` on EMR gains a
  symmetric no-op branch matching ``action_descend``.
  ``action_swap_source`` on the EMR page forwards to
  ``EmrServerlessPage.action_open_application_picker`` so the
  Commands chip advertising "switch app" actually fires (was
  silently no-op'ing through `_dual_pane()`).
- **(fourth overnight-maintenance loop, pass 1)** S3FS hardening.
  ``S3FS.read_stream`` now eagerly probes ``head_object`` so a
  missing source raises ``NotFoundError`` at call time instead of
  mid-stream (cleaner error surface for the transfer engine). The
  auth-error handling block was extracted into a single
  ``_auth_error(exc)`` helper after being duplicated 8x across the
  list / get / put / delete / rename / head call sites.
- **(fourth overnight-maintenance loop, pass 1)** Boot-chain
  outcome map for ``_on_pane_state_changed`` extended with
  ``PaneState.ERROR → "error"`` so an ERROR-terminal pane resolves
  the future explicitly instead of timing out.
  ``_try_connection`` uses ``asyncio.get_running_loop()`` (was
  ``get_event_loop()`` — deprecated in 3.12 when called outside a
  running loop). ``_raise_attempt_toast_after_grace`` now returns
  ``True`` only when the toast actually posted (was returning True
  on the grace-cancelled path too).
- **(fourth overnight-maintenance loop, pass 1)**
  ``HintLegendVM._SERVICE_ACTIONS["settings"]`` is now ``()`` (was
  ``("pane.refresh",)``) — the Settings page has no
  ``action_refresh`` handler, so the chip was an advertised
  affordance with no behavior. Empty tuple drops the chip
  cleanly until the Settings refresh handler ships.
- **(fourth overnight-maintenance loop, pass 1)**
  ``DualPaneVM.delete_marked`` now collects per-target failures
  and reloads the pane in ``finally`` (was silent partial failure
  on mid-batch error — first failing target raised, leaving
  subsequent entries un-attempted and the pane stale).
- **(fourth overnight-maintenance loop, pass 1, Medium-batch)**
  Two user-visible fixes: (1) Nav-rail ribbon no longer "lies"
  about the live service when a content-host adoption raises
  during ``RootVM.switch_service`` — the prior selection is now
  captured before the menu command runs and reverted if
  ``set_content`` raises (was: ribbon advanced to a service that
  never actually mounted). (2) Status-bar concurrent-transfer
  totals are now true aggregates (per-id ``(done, total)`` dict
  summed across active entries), not last-event-wins — two
  parallel transfers no longer toggle the displayed totals back
  and forth based on whichever message landed last. Idle
  aggregate is ``None`` instead of ``0`` so the
  ``humanize_bytes(None) → "?"`` placeholder still renders.

  Internal refactors with no user-visible behavior change:
  ``map_provider_error`` extracted to
  ``vm/emr_serverless/_errors.py`` and shared by all three EMR
  VMs (single source of truth for the ProviderError → PaneState
  ladder). ``ConfigStore._mutate`` Template Method extracted —
  the four mutators (``add_connection`` / ``update_connection`` /
  ``remove_connection`` / ``set_default_connection``) now
  delegate to a single load/save framing helper, preserving each
  public signature. ``CrashModal`` migrated to ``ModalButton`` so
  every modal uses the same button class. ``RootVM.shutdown`` is
  now sync (was async with nothing awaiting it).
  ``nav_menu._split_items`` extracted from ``_rebuild_options``.

- **NavMenu is now always visible** (PR #59). The left rail
  collapses to a minimally-wide icon-only column instead of
  disappearing — Tab focuses the menu, arrow keys navigate, Enter
  selects. Discoverability of the nav peers (S3 + Settings) is
  always present without the user having to remember the
  toggle key.
- **README documentation rewritten for accuracy** (third
  maintenance loop, pass 1). The Features bullets for "Multi-select",
  "Streaming Quick Look", and "Command palette" now match what is
  actually wired in v0.7.x. Previously they claimed `v` + `Space`,
  `Space` for streaming preview, and `:` / `Ctrl+K` for the
  command palette — none of which have BINDINGS or action handlers
  in `AwsTuiApp.BINDINGS`. The CHANGELOG `### Added` and
  `### Deferred / v0.8 roadmap` were corrected in lockstep.
- **`Shift+S` Features bullet expanded in the README** (PR #58) to
  highlight the cycler's full ring — every AWS profile + every
  s3-compatible connection, all reachable with one keystroke per
  step. Reflects the existing behavior from PR #43/#49; no code
  change.

- **(third maintenance loop, pass 1)**
  `[dependency-groups].dev` in `pyproject.toml` now carries an
  explicit rationale comment that next-major caps are intentionally
  omitted (unlike the runtime-deps block above). Dev tooling does
  not reach downstream `pip install aws-tui`, and the lockfile
  already pins each version — capping would have actively rolled
  back working majors (mypy 2.x, pytest 9.x, pre-commit 9.x).
- **(third maintenance loop, pass 1)**
  `.pre-commit-config.yaml` declares
  `default_language_version.python: python3.11` so pre-commit
  picks the same interpreter as `requires-python` /
  `tool.mypy.python_version` regardless of `$PATH`.
- **(third maintenance loop, pass 2)** Six snapshot suites
  (`test_main_screen`, `test_modals`, `test_theme_picker`,
  `test_toast`, `test_transfers`, `test_pane_states`) now each
  have a content-presence guard pair test (per PR #53 lesson).
  Each guard reads the generated SVG off disk and asserts that
  the user-facing text actually rendered — a uniformly-blank
  render across all 10 themes (which `snap_compare` parity-match
  would silently pass) now fails the guard. Adds 134 new tests;
  total default-tier count goes 817 → 951.
- **(third maintenance loop, pass 2)** `docs/architecture.md`
  test-count + message-list updated: default total now 817 (was
  816); `TransferCancelRequestedMessage` and
  `ConnectionListChangedMessage` added to the messages list.
- **(third maintenance loop, pass 2)** v0.1.0 design-spec ASCII
  five-layer diagram updated for the post-ship renames:
  `AppScreen → AwsTuiApp`, `ServicesMenu → NavMenu`,
  `ServicesMenuVM → NavMenuVM`, and `DualPaneFileManager`
  collapsed to the actual widget name `DualPane`. Adds an
  inline rename note next to `NavMenuVM` referencing PR #54.
  The amendments preface already covered the rename in prose;
  this aligns the diagram with the prose.
- **(third maintenance loop, pass 2)** `docs/adding-a-service.md`
  `Service` protocol code-quote now matches
  `services_protocol.py` exactly — `build_vm(...) -> Any` (was
  `-> ComponentVM`); the surrounding prose explains why the
  protocol is structurally typed as `Any` while the §2 template
  uses a concrete VM type. Prevents reader confusion when
  cross-referencing the doc against the actual protocol.
- **(third maintenance loop, pass 2)**
  `S3ConnectionsPanel._surface_error_toast` now reaches the app
  context through the public `app_ctx` property instead of the
  private `_app_ctx` name; the `hasattr` gate is updated in
  lockstep so test harnesses mounting the panel under a vanilla
  Textual `App` (without the property) still no-op cleanly.
- **(third maintenance loop, pass 3)** `PaneState` enum docstring
  now spells out the per-state entry condition (single source of
  truth for the state machine, kept in sync with
  ``PaneVM._reload`` and ``PaneVM.set_auth_required``). Catches the
  one non-uniform branch — ``NotFoundError`` at the root path
  enters ``EMPTY`` without setting ``_error_text``, unlike the
  three sibling handlers — with an inline comment at the call
  site as well.
- **(third maintenance loop, pass 3)** `AwsTuiApp.action_modal_left_or_ascend`
  and `AwsTuiApp.action_modal_right` now carry one-line docstrings
  (the names are non-obvious, the behavior switches on modal
  presence). The other unannotated action_* handlers are
  self-documenting (move/mark/refresh) or carry sufficient inline
  comments already.
- **(third maintenance loop, pass 3)** Fixed a "Wiring lands with
  the BindingResolver work above" → "below" typo in the
  `Quick Look (entire feature)` Deferred entry; the
  `BindingResolver` description sits later in the same section.
- **(third maintenance loop, pass 4)** Fixed a regression
  introduced in pass 2: `docs/architecture.md` test-count footer
  said "817" but should have been "951" once pass 2's 134
  snapshot guards landed. Pass 2 edited the doc and added the
  tests in the same pass but didn't loop back to update the
  footer. Now bumped to 951 with a note pointing at the
  third-loop additions for the source of the drift.
- **(third maintenance loop, pass 4)** `AwsTuiApp.action_copy`
  and `AwsTuiApp.action_delete` error logs now carry
  `error_type` and `file_count` fields, so an operator reading
  the log can tell whether a copy of 1 file vs 50 failed without
  cross-referencing the toast. Event names also normalized to
  the `app.<action>.failed` dotted-hierarchy convention used
  elsewhere in the file: `copy.failed` → `app.copy.failed`,
  `delete.failed` → `app.delete.failed`, and
  `theme.load.failed` → `app.theme.load_failed`. No test
  references existed for the old names.
- **(third maintenance loop, pass 5)** `tests/integration/conftest.py`
  ``app_context_factory`` fixture now yields its builder (was a
  plain `def` returning a callable) and cleans up every temp dir
  the builder created when the test finishes. Previously a raise
  inside an integration test stranded one
  `tempfile.mkdtemp(prefix="aws-tui-ictx-")` directory under
  ``$TMPDIR`` per call. Uses `shutil.rmtree(..., ignore_errors=True)`
  so a partially-cleaned dir on Windows (background worker still
  holding a file open) doesn't fail the teardown.
- **(third maintenance loop, pass 7)** `PaneVM.mark_at` now refuses
  to mark the synthetic ".." parent-link row, and
  `PaneVM.marked_entries` filters it out as a belt-and-braces
  guard. The click path (`EntryRow.on_click`) already filtered
  parent links, but the shift+arrow extend-selection path
  (`AwsTuiApp._extend_selection`) walked through `mark_at` without
  the same check — so `Shift+↑` while the cursor was on ".." would
  flip the parent row's mark flag and a subsequent `c`/`d` would
  include ".." in its target list. The fallback path
  (single-cursor target) already had its own ``is_parent_link``
  filter; this brings the marked path to parity.
- **(third maintenance loop, pass 8)** Crash-dump files
  (``~/.cache/aws-tui/crash/<ts>.txt``) now chmod 0o600 after
  write. Previously the parent directory was 0o700 (from the
  second-loop cache-dir hardening) but the dump file itself
  inherited the process umask — typically 0o644, world-readable.
  Dumps carry the last 1000 log lines + 100 user actions, which
  can include endpoint URLs, request IDs, and partial upload
  identifiers; tightening to owner-only matches the existing
  hardening posture. Best-effort: filesystems without POSIX
  permission bits silently no-op the chmod.
- **(third maintenance loop, pass 9)** ``TransferJournal._append``
  now ``flush()`` + ``os.fsync()`` each line before the file
  handle closes. The module docstring already promised "fsync
  semantics" but the code only relied on a natural close, which
  flushes stdio buffers without forcing the FS journal/metadata
  to disk. On power loss between a ``mark_completed`` write and
  the OS's background flush (~30s), the journal would lose the
  terminal marker and the resume modal would replay the whole
  transfer on relaunch. fsync closes that window for one syscall
  per append — negligible against the network I/O the surrounding
  multipart upload pays per part.
- **(third maintenance loop, pass 9)** ``TransfersOverlay._arm_linger``
  now tracks a ``_pending_linger_ids`` set so successive
  ``_rebuild`` calls for the same finished transfer don't queue
  a fan of independent ``set_timer`` callbacks. The docstring
  claimed "idempotent on repeat calls" but the early-return on
  ``_expired_ids`` only fired AFTER the first timer had expired.
  A rapid sequence of rebuilds (e.g. a transfer + a hub message
  + a focus-change in quick succession) would spawn multiple
  staggered linger timers, each independently re-calling
  ``_rebuild`` when they fired.
- **(third maintenance loop, pass 10)** ``ThemeStore.load`` now
  refuses to follow a symlink at
  ``~/.config/aws-tui/themes/<name>.tcss`` that resolves outside
  the user-themes directory. A malicious (or accidental) symlink
  to a file like ``/etc/passwd`` would previously have its
  contents inlined into the active stylesheet and surfaced on
  screen as Textual tried to parse it. Local-only threat model,
  but a defensive-coding fix that matches the path-confinement
  posture used elsewhere in ``infra/``.
- **(third maintenance loop, pass 10)** ``AwsTuiApp.switch_theme``
  error log now carries ``error`` and ``error_type`` fields,
  matching the same fix applied to ``_apply_initial_theme`` in
  pass 1. Pass 1 missed this second site — a runtime
  ``Esc``-rollback or ``t``-picker theme switch that hit an
  exception still landed in the log with only the theme name
  attached.
- **(third maintenance loop, pass 11)** ``ConnectionResolver``
  now reads ``~/.aws/config`` and ``~/.aws/credentials`` with
  ``encoding="utf-8-sig"`` (was ``"utf-8"``). A UTF-8 file with
  a leading byte-order mark (BOM ``0xEF 0xBB 0xBF``) was causing
  ``configparser`` to raise ``MissingSectionHeaderError`` —
  ``﻿[default]`` is not a recognized section header. BOM
  prefixes are common when files are edited on Windows (Notepad
  saves UTF-8 with BOM by default). ``utf-8-sig`` transparently
  strips the BOM if present and is otherwise identical to
  ``utf-8``. Three call sites updated:
  ``_discover_aws_profiles`` (config + credentials) and
  ``_read_aws_credentials_profile``.
- **(third maintenance loop, pass 15 / corrected in pass 10 of this run)**
  ``composition.build_app_context`` validates
  ``config.keybindings.bindings`` by constructing a temporary
  ``KeymapStore(overlay=...)``. The runtime-visible keymap remains
  the v0.8.x default map so the Commands strip cannot advertise
  keys that ``AwsTuiApp.BINDINGS`` does not dispatch yet. A
  malformed overlay (``UnknownAction``) is caught and logged rather
  than crashing startup.
- **(third maintenance loop, pass 18)** ``CrossFsCopy.copy`` now
  deletes the partial destination on a mid-stream write failure
  (or worker cancellation). Previously a copy that raised after
  any bytes had been written left a truncated junk file on the
  destination — visible to the user on the next pane refresh
  with no way to distinguish a failed copy's leftover from a
  legitimate small file. The source stream is still closed
  first (so it can release its handle/connection before we
  re-enter the destination), then ``destination.delete`` runs
  best-effort with ``contextlib.suppress(Exception)`` so the
  ORIGINAL write error reaches the caller, not a secondary
  cleanup error. The catch is ``BaseException`` so worker
  cancellation (``asyncio.CancelledError``) also triggers
  cleanup. ``CrossFsMove`` inherits the behavior automatically
  (it calls ``copy`` and only deletes the source on success).
- **(third maintenance loop, pass 19)** ``LogSink`` now writes
  ``~/.cache/aws-tui/log/aws-tui.log`` (and its rotated backups
  ``aws-tui.log.1`` … ``aws-tui.log.5``) with ``0o600`` instead
  of the umask-default ``0o644``. The parent directory was
  already ``0o700`` (from the second-loop cache-dir hardening)
  but the rotating-file handler created the files with
  world-readable permissions. Log lines can carry endpoint
  URLs, request IDs, and structured error context that
  shouldn't be ``cat``-able by other local users on shared
  systems — same posture as the crash-dump fix from pass 8.
  Implemented via a ``_PrivateRotatingFileHandler`` subclass
  that overrides ``_open`` (initial + post-rotation) and
  ``doRollover`` (rotated backups) with a best-effort
  ``chmod 0o600``. Filesystems without POSIX permission bits
  silently no-op.
- **(third maintenance loop, pass 20)** ``Keyring.get`` now
  catches ``KeyringError`` and returns ``None`` instead of
  letting the exception escape. A locked keychain, a headless
  Linux system without ``gnome-keyring``/``kwallet`` configured,
  or a transient OS error on credential lookup previously
  crashed startup — ``ConnectionResolver._dispatch_s3_credentials``
  is the only caller, called from ``ConnectionResolver.list()``,
  called from ``AwsTuiApp._resolve_initial_connection`` with no
  try/except guard upstream. Returning ``None`` instead leaves
  the connection in ``AUTH_REQUIRED`` once it's used (the same
  state the resolver lands in when env-var lookups miss the
  expected ``${PREFIX}_ACCESS_KEY_ID``). ``Keyring.set`` does
  NOT suppress: the caller explicitly asked to persist and a
  silent drop would mislead the UI. ``Keyring.delete`` extends
  its existing ``PasswordDeleteError`` suppression to cover the
  general ``KeyringError`` for the same reason as ``get``.
- **(third maintenance loop, pass 20)** ``Connection`` dataclass
  now declares ``repr=False`` and ships a custom ``__repr__``
  that masks ``access_key_id``, ``secret_access_key``, and
  ``session_token`` (``"***"`` if present, ``None`` if not). The
  default dataclass repr inlined every field verbatim — any logger,
  REPL print, or traceback that surfaced a ``Connection`` instance
  leaked plaintext credentials. ``eq``, ``hash``, and the ``slots``
  layout are unchanged.
- **(third maintenance loop, pass 27)** ``S3FS`` now translates
  the full family of botocore transport-layer failures to
  ``ProviderUnreachableError``, not just ``EndpointConnectionError``.
  The original chain of ``except EndpointConnectionError`` blocks
  (8 sites) missed ``ConnectTimeoutError`` and ``ReadTimeoutError``
  — both subclasses of ``HTTPClientError``, NOT subclasses of
  ``EndpointConnectionError`` — so the 10s connect / 60s read
  timeouts configured on the botocore client (matching the
  spec §6.3 + §7.3 policy) propagated as raw botocore exceptions
  whenever they fired. The pane VM's
  ``except ProviderUnreachableError`` then never matched and the
  state machine landed in the generic ``ERROR`` placeholder
  instead of ``UNREACHABLE``. Replaced with a single
  ``_TRANSPORT_FAILURE_EXCEPTIONS`` tuple containing
  ``EndpointConnectionError``, ``ConnectTimeoutError``,
  ``ReadTimeoutError``, and the base ``BotoConnectionError``
  (catches future-introduced sibling shapes).
- **(third maintenance loop, pass 27)** ``_map_client_error``
  now maps S3 service-side transient codes
  (``ServiceUnavailable``, ``RequestTimeout``, ``SlowDown``,
  ``InternalError``, ``503``, ``504``) to
  ``ProviderUnreachableError``. Botocore's adaptive retry
  budget (``max_attempts=6``) usually absorbs these but a
  sustained ``SlowDown`` storm can still exhaust it; from the
  user's perspective the bucket is unreachable — same recovery
  action as a DNS/timeout failure (press ``r``, or wait + try
  again). Previously these surfaced as the generic
  ``ProviderError`` and the pane landed in the ``ERROR``
  placeholder instead of ``UNREACHABLE``.
- **(third maintenance loop, pass 54)** ``HintLegendVM._rebuild_actions``
  now dedupes ``action_id`` across the focused-pane block and the
  ``_FALLBACK_ACTIONS`` block via a ``seen: set[str]``. Without it,
  a focused-pane registration that included a fallback id (e.g.
  ``("pane.descend", "pane.copy", "pane.move", "pane.delete")``)
  produced duplicate chips in the bottom legend — the first three
  ids appeared once in the focused row and again in the fallback
  row. The wiring is currently exercised only by tests
  (``register_focusable`` isn't called at runtime pending the
  deferred ``BindingResolver`` work), but the bug was real enough
  that a future caller hitting it would have landed on a
  visibly-doubled legend. Regenerated the 10 ``test_main_screen``
  snapshots in the same pass — the snapshot fixture
  (``tests/snapshot/apps/main_screen.py``) was already exercising
  the duplicating path, so the goldens carried the duplicate
  chips. The matching guard tests still pass because they assert
  on user-visible labels (``copy``, ``delete``, ``aws.tui``), not
  on chip count.
- **(third maintenance loop, pass 50)** ``.github/dependabot.yml``
  now declares a ``package-ecosystem: pre-commit`` entry tracking
  the pinned hook versions in ``.pre-commit-config.yaml``
  (``pre-commit-hooks``, ``astral-sh/ruff-pre-commit``,
  ``ComPWA/taplo-pre-commit``). The first maintenance loop bumped
  ``ruff`` from ``v0.15.0`` to ``v0.15.17`` by hand to close patch-
  level drift Dependabot wasn't catching; the new entry catches
  the next such drift on a weekly cadence without manual triage.
  Weekly Monday schedule + 2 PR cap + ``["dependencies", "tooling"]``
  labels match the existing ecosystem entries' shape.
- **(third maintenance loop, pass 49)** ``DualPaneVM.copy_across``
  and ``move_across`` no longer leave PENDING journal entries
  behind when a batch transfer raises mid-loop. The previous
  flow was: ``_pre_register_pending`` opened a fresh journal
  file for every target up front (one ``begin`` line per id);
  the loop then ran ``_run_one_transfer`` for each one and
  appended the terminal ``finished`` / ``aborted`` line. If an
  entry's ``_run_one_transfer`` raised (its own journal already
  marked aborted before the re-raise), the for-loop exited and
  any UNREACHED entries had no terminal marker — those journal
  files sat in ``~/.cache/aws-tui/transfers/`` indefinitely and
  would resurface in the deferred resume modal as phantom
  pending transfers on next launch. Tracked entries via a
  ``consumed: set[str]`` that the loop adds to BEFORE awaiting
  ``_run_one_transfer`` (so a raise still counts as consumed —
  ``_run_one_transfer`` already marked that id), and in the
  ``finally`` block any id NOT in consumed gets
  ``mark_aborted``.
- **(third maintenance loop, pass 47)** ``ToastStackVM.raise_toast``
  now dismisses any prior toast carrying the same ``model.id``
  before adding the new one. The intent (per the inline comment
  on ``_schedule_auto_dismiss``) was that re-raising the same
  id should refresh the existing toast — and the timer dict
  was already being keyed on id so the old auto-dismiss got
  cancelled — but the old toast itself stayed in
  ``_toasts`` forever. When the new toast's timer eventually
  fired, ``_on_toast_dismissed`` walked ``_toasts`` via
  ``_find`` (which returns the FIRST match) and removed the
  ORIGINAL toast instead of the new one. The new toast then
  sat on screen sticky. Re-raising twice could leak a card
  indefinitely. The fix is a single
  ``self._on_toast_dismissed(existing)`` call at the top of
  ``raise_toast`` so the duplicate id always replaces, never
  stacks.

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
- **`Shift+S` now cycles through every available source** instead of
  toggling between the *initial* connection's S3 and Local. The ring
  is: `local` → every connection the resolver knows about (TOML +
  auto-discovered AWS profiles) → wrap. Lets you spin through
  `aws s3 · default · us-east-1` → `s3-compatible · minio-local ·
  localhost:64093` → `local` → `aws s3 · …` without opening the
  connection picker.
- **VMx is now consumed from PyPI**, replacing the `vendor/vmx` git
  submodule. Pinned at `vmx>=2.6.0,<3.0.0` — PyPI's lowest published
  version is `2.6.0`, the vendored `python-v2.4.0` tag was never
  released. API parity across the 2.4 → 2.6 minor bump verified by
  the full default-tier suite staying green. Effects for users: fresh clones no
  longer need `--recurse-submodules`; CI checkouts no longer fetch
  the submodule; `scripts/bootstrap.sh` drops the
  `git submodule update --init --recursive` step. Internal source
  organization unchanged — every `from vmx import ...` continues to
  resolve to the same symbols. This also lifts the long-standing
  "aws-tui PyPI release blocked on VMx publishing" gate; aws-tui's
  own first PyPI release is the next scheduled milestone.
- **(first maintenance loop, passes 1–6)** `AwsTuiApp.on_mount`
  (was 63 effective lines) extracted into four helpers:
  `_apply_initial_theme`, `_resolve_initial_connection`,
  `_mount_initial_service_view`, `_mount_no_connection_placeholder`.
  on_mount itself is now 18 lines.
- **(first maintenance loop, passes 1–6)** `pyproject.toml`
  `[tool.pytest.ini_options].addopts` now defaults to
  `-m 'not integration'` so `uv run pytest` runs the default tier
  (unit + snapshot + e2e + in-process integration) without Docker.
  The opt-in MinIO testcontainer tier runs via
  `uv run pytest -m integration`. CI continues to invoke
  `pytest -m integration` for the integration job — the right-most
  `-m` wins.
- **(first maintenance loop, passes 1–6)** `.github/dependabot.yml`
  `python-runtime` group now includes `tomli-w` (was missed);
  `python-dev` group includes `testcontainers*` and `types-*` (also
  missed) so they group instead of opening individual PRs.
- **(first maintenance loop, passes 1–6)** `.pre-commit-config.yaml`
  `astral-sh/ruff-pre-commit` bumped from `v0.15.0` to `v0.15.17`
  to match `uv.lock` (closes the patch-level drift the M1 fix
  `91f6040` left at the minor level).
- **(second maintenance loop, passes 1–4)** Cache subdirectories
  (`<cache_home>/log/`, `<cache_home>/crash/`, `<cache_home>/transfers/`)
  now chmod 0o700 on creation. Matches the config-dir hardening
  applied in PR #54's first maintenance loop — these subdirs carry
  log lines (endpoint URLs, request IDs), crash dumps (last 1000
  log lines + last 100 user actions), and transfer journals
  (S3 source/destination URIs + multipart upload IDs); none should
  be readable by other local users on shared systems. Best-effort
  (filesystems without POSIX permission bits silently fall back).
- **(second maintenance loop, passes 1–4)** All runtime
  dependencies in `pyproject.toml` now carry next-major upper
  bounds (e.g. `textual<9`, `boto3<2`, `aioboto3<16`). Matches the
  long-standing vmx posture (`>=2.6.0,<3.0.0`). Insulates
  downstream `pip install aws-tui` from a transitive breaking
  change between releases. `uv.lock` unchanged — every cap covers
  the currently-resolved version. Build-system requirement
  (`hatchling>=1.21,<2`) also capped.
- **(second maintenance loop, passes 1–4)** All `configparser.read()`
  calls on `~/.aws/{config,credentials}` now pass `encoding="utf-8"`
  explicitly (default was `locale.getencoding()`, platform-
  dependent). Matches the explicit utf-8 used elsewhere in
  `infra/`. Niche but real on non-UTF-8 POSIX locales.
- **(second maintenance loop, passes 1–4)** CI `lint-type` job
  no longer runs `ruff`, `ruff format --check`, and `mypy` twice
  per push (once as dedicated steps, once via `pre-commit run
  --all-files`). The pre-commit step is now the single source of
  truth for those hooks. Net: lint-type wall-clock dropped ~30s.
  All runners moved from `ubuntu-22.04` to `ubuntu-24.04` ahead
  of the upstream image's deprecation window.
- **(second maintenance loop, passes 1–4)** `S3Service` no longer
  takes an `aws_session` constructor parameter — the field was
  held but never read (the S3FS factory uses its own
  `aioboto3.Session` per build_vm). Composition + 4 test
  modules simplified.
- **Pane border-subtitle format is now connection-kind-aware.**
  - `aws`           → `aws s3 · {profile} · {region}` (was `aws · …`)
  - `s3-compatible` → `s3-compatible · {name} · {endpoint}`
    (was `s3-compatible · {profile?} · {region?}`)
  Region is intentionally *not* surfaced for `s3-compatible` — MinIO,
  R2, B2, etc. don't have a meaningful region, and showing the
  internal SigV4 default `us-east-1` was misleading. The user-defined
  connection `name` (TOML section name) and `endpoint_url` carry the
  identity. The endpoint's `http(s)://` scheme is stripped for
  compactness.
- **Multiple `[connections.<name>]` entries are now first-class.**
  Adding another `[connections.r2-prod]` block to `config.toml`
  surfaces it as a candidate in the swap-source ring automatically;
  the section name is what shows up in the pane title.
- **Settings nav peer now docks to the bottom of the left rail**
  (PR #56, user-reported). Previously Settings was the last entry in a
  single ``OptionList`` taking all the vertical space, so it sat
  directly under ``S3`` with empty rows below it. The ``NavMenu``
  widget's compose was split into two ``OptionList`` children —
  ``#menu-services`` (top, ``height: 1fr``) for service items, and
  ``#menu-pinned`` (``dock: bottom``, ``height: auto``) for the
  Settings nav peer. ``NavMenuVM`` is unchanged; the split is purely
  a View concern (filters items by id). Per-theme
  ``border-top: solid $rule-dim`` added to ``#menu-pinned`` across all
  10 themes for a subtle separator. Matches the macOS Settings-app /
  VS Code activity-bar pattern. Content-presence guard asserts
  ``svg.index("S3") < svg.index("Settings")`` to catch a regression
  where the ``dock: bottom`` rule gets dropped.
- **Relicensed from MIT to Apache License 2.0.** ``LICENSE`` now
  carries the canonical Apache 2.0 text and is paired with a new
  ``NOTICE`` file (Apache convention for attribution). ``pyproject.toml``
  classifier and ``license`` field, the banner widget's pedigree
  subtitle (which renders bottom-right of the brand banner border
  inside the running app), and the README footer all updated.
  Historical changelog entries that refer to the prior MIT licence
  are deliberately preserved — they're records of project state at
  that point in time, not active declarations.

### 1.2.5. Added

- **Shift+S now skips connections observed unreachable.** If a pane
  mounted on an s3-compatible (or AWS) connection lands in the
  ``UNREACHABLE`` state — typical case: a local MinIO endpoint that
  isn't running — that connection is marked in an in-memory set and
  silently filtered out of the swap-source ring on every subsequent
  ``Shift+S`` press. A one-line info toast names the skipped
  connections the first time the cycle would have included them.
  Pressing ``r`` to retry the pane, on success, clears the mark and
  re-enters the connection into the ring. No startup probe; no
  persistence across runs. Identity key is
  ``(connection.kind, connection.name)`` so an AWS profile and an
  s3-compatible connection with the same name are tracked
  independently. AWS profiles participate as well, but only when the
  failure is a true network/endpoint error (expired SSO or permission
  denied transitions to ``AUTH_REQUIRED`` / ``FORBIDDEN``, which are
  NOT skipped).
- **Cross-platform support — runs on macOS, Linux, and Windows.**
  Replaced hardcoded `~/.config/aws-tui` and `~/.cache/aws-tui` paths
  with `platformdirs`-resolved per-OS native locations:
  - Windows: `%APPDATA%\aws-tui` + `%LOCALAPPDATA%\aws-tui\Cache`
  - macOS:   `~/Library/Application Support/aws-tui` + `~/Library/Caches/aws-tui`
  - Linux:   `$XDG_CONFIG_HOME/aws-tui` + `$XDG_CACHE_HOME/aws-tui`
    (default `~/.config/aws-tui` + `~/.cache/aws-tui`)
  Legacy XDG paths win when they already exist on disk, so existing
  macOS / Linux installs see zero disruption. Added a `windows-latest`
  runner to the CI unit matrix and a new `docs/platforms.md` covering
  recommended terminal (Windows Terminal 1.18+) + font (Cascadia Code)
  per OS. Project classifiers updated to include
  `Operating System :: Microsoft :: Windows`.
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
- **Multi-select** via `Shift+↑/↓` (extend selection) and
  modifier+click (`Shift`, `Cmd`, or `Ctrl` — `Shift+Click` is
  often consumed by macOS terminals for native text-select, so
  `Cmd+Click` is the reliable path there). Marked-byte total surfaces
  in the pane footer (`N obj · M marked · X selected`). The `v` +
  `Space` mode-entry shortcut is spec'd but deferred to v0.8 (see
  `### Deferred / v0.8 roadmap`).
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
- **App Settings as a first-class nav page** with full CRUD for
  s3-compatible connections. The left rail is now a generic vertical
  nav (Textual ``OptionList``) with peer items ``S3`` and ``Settings``;
  selection-highlight matches the file-pane row cursor (``$bg-sel`` +
  ``$accent``). Selecting Settings swaps the main area to a VS Code-style
  scrollable page of ``Collapsible`` sections. Sub-project A populates
  the ``S3-Compatible Connections`` section; ``Themes (coming in v0.8)``
  and ``Keymap (coming in v0.8)`` are visible-disabled placeholders.
  Add/Edit S3 connection form expands inline within the Connections
  section, below the rows — no more modal-on-modal layering. Save
  commits + reloads any affected pane + collapses the form, all
  immediately. Cancel just collapses. Delete still uses the polished
  ``ConfirmModal`` (destructive ops keep the modal interruption
  pattern). Credentials remain inline in TOML (cross-platform, no
  keychain dependency). Keyboard: ``,`` selects the Settings nav item;
  ``m`` toggles the rail's collapsed/expanded state. Per-theme CSS for
  all 10 themes. Every new snapshot test is paired with a content-
  presence guard (per the PR #53 lesson: a uniformly-blank rendering
  can pass parametric snapshot match across all themes; guards read
  the generated SVG and assert a user-visible glyph/label is present).
  This is a rework of the PR #52 modal pattern, not an extension —
  ``SettingsModal``, the gear footer band, and ``S3CompatFormModal``
  are all deleted. The two surviving VMs (``SettingsVM`` simplified,
  ``S3ConnectionsVM`` unchanged) plus the ``ConfigStore`` extensions
  plus ``ConnectionListChangedMessage`` all carry over.

### 1.2.6. Fixed

- **(fourth overnight-maintenance loop, pass 2)** EMR runs pane now
  renders the actionable placeholder for ``UNREACHABLE`` /
  ``AUTH_REQUIRED`` / ``FORBIDDEN`` / ``ERROR`` pane states instead of
  rendering ``"(no runs)"`` or nothing at all. The branch order in
  ``JobRunsPane._refresh_rows`` checked the empty-list state BEFORE
  the provider-error states, so a pane that failed an API call (and
  therefore had an empty runs cache) silently fell through to the
  generic empty placeholder — leaving the user without the recovery
  hint the sibling ``JobRunDetailPane`` already showed.
- **(fourth overnight-maintenance loop, pass 1, Medium-batch)**
  Resume modal no longer reports a fake percentage on
  single-part transfers. ``ResumeVM.entry_summary`` now renders
  honest ``"<parts> parts / <total>"`` text instead of inventing
  a "50 % done" string when there is no real progress signal —
  the cancelled-then-resumed path was the worst offender.
- **(fourth overnight-maintenance loop, pass 1, Medium-batch)**
  A stale ``RUNNING`` progress event arriving after a transfer
  has already settled to ``CANCELLED`` / ``FAILED`` /
  ``SUCCEEDED`` is now ignored. ``TransferVM.apply_update``
  refuses non-terminal transitions on a finished transfer
  (terminal-state stickiness); ``_apply_update_unchecked`` stays
  as the retry escape hatch for genuine re-attempts. Previously
  a late RUNNING event could "un-cancel" a CANCELLED transfer in
  the UI status bar.

- **EMR icon VS-16 alignment** (PR #77). U+26A1 alone defaults to
  text-style in monospace terminals (1 cell, mis-aligning the
  nav-rail's 2-cell column); appending U+FE0F forces the
  emoji-presentation glyph (2 cells, in colour) on the three call
  sites — the ServiceDescriptor icon, the application-picker
  trigger label, and the dropdown options.
- **EMR page layout user-reported gaps** (PR #80). Six user-reported
  gaps closed in one PR: bordered apps box width-matched to LEFT
  pane, columnized run rows, master-detail follows cursor (not just
  Enter), mouse-click on rows, multi-line args / spark params, page
  width split (`1fr/1fr` from `1fr/2fr`).
- **EMR misc batch** (PR #81). ⚡️ icon refresh, args pairing in the
  job-run detail pane, nav-margin spacer, "Shortcuts" → "Commands"
  rename + service/global strip split, border alignment. Dropdown
  bug deferred to a follow-up.
- **3-slot Tab cycle + visible NavMenu focus border** (PR #62).
  Folded the earlier 4-slot Tab cycle (left → right → nav-services →
  nav-pinned) into a single NAV slot (left → right → NAV → wrap) so
  successive Tab presses don't read as "two idle switches" across
  the narrow rail column. NavMenu now gains a visible focus border
  via Textual-native `:focus-within` rather than a Python
  `watch_has_focus_within` reactive (which doesn't exist on
  `has_focus_within` — it's a property, not a reactive).
- **Tab cycle + SettingsView border + nav polish** (PR #61).
  Hamburger margin, icon centering, and tooltips on the NavMenu;
  SettingsView gets a proper border so it doesn't read as floating
  text inside the content host.
- **AWS+EXPIRED at startup mounts LocalFS-only DualPane + toast,
  not error placeholder** (PR #60). When the initial connection
  resolves to an AWS profile whose SSO token is expired, the app
  now mounts a degraded DualPane (LocalFS on both sides) plus a
  sticky warning toast naming the recovery command, instead of
  rendering the no-connection error placeholder. Mirrors the
  graceful-unreachable design from PR #48/#49.

- **(third maintenance loop, pass 1)** Several bare
  `except Exception:` blocks in `composition.py` and `app.py`
  startup helpers now log the error (with type) before falling
  back. Previously a malformed `config.toml` looked identical to a
  clean install (`needs_first_run` returned False on the swallowed
  load failure), and `_apply_initial_theme` / `_resolve_initial_connection`
  / `_mount_initial_service_view` lost the failure cause. The
  service-view mount now also surfaces a sticky error toast so the
  user gets some explanation instead of a blank pane.
- **(third maintenance loop, pass 1)** `S3FS._list_objects` now
  raises `ProviderError` instead of `assert target_bucket is not None`
  when called bucketless without an explicit `bucket=` arg.
  `assert` is stripped under `python -O`, which would convert a
  runtime invariant breach into a confusing botocore `ParamValidationError`
  much later in the call chain.
- **(third maintenance loop, pass 1)** `PaneVM.marked_entries` now
  snapshots `self._entries` before iterating, matching the
  precaution already in `filtered_entries`. Prevents a race against
  `_reload`'s `_replace_entries` rewrite that could skip or
  duplicate marked entries during a concurrent refresh.
- **(third maintenance loop, pass 1)** `action_copy` and
  `action_delete` workers now use `exclusive=True, group="transfer-ops"`
  so a user pressing `c` then `d` in quick succession can no longer
  interleave the two flows on the focused pane's mark state or the
  shared transfer journal.

- **(second maintenance loop, passes 1–4)** `AwsTuiApp.action_quit`
  is now overridden to await `_aws_tui_shutdown` on `q` / `ctrl+c`.
  Previously every normal exit silently bypassed
  `aws_session.aclose_all_clients`, `transfers_vm.cancel_all_command`,
  and `log_sink.flush()` — Textual's sync `App.action_quit` was
  called instead. Each leaked aioboto3 sockets, abandoned in-flight
  copy tasks (left their journal entries as 'crashed' so the next
  launch's resume modal would surface them), and dropped buffered
  log records.
- **(second maintenance loop, passes 1–4)** `S3FS.delete` and
  `S3FS.rename` now wrap their outer `try` with
  `except ClientError → _map_client_error`. Previously a bucket
  policy that allowed `s3:GetObject` but denied `s3:DeleteObject`
  produced a raw botocore `ClientError` with verbose XML instead
  of a `PermissionDeniedError`; the UI's pretty toast path was
  skipped. The `rename` fix also handles the partial-rename case
  (copy succeeded, source delete denied).
- **(second maintenance loop, passes 1–4)** `DualPaneVM._pre_register_pending`
  no longer leaks partial `_cancel_events` entries when
  `TransferJournal.begin` raises mid-batch (e.g. disk-full on
  entry N of M). The new inner `try/except` reaps the
  registrations from entries 1..N-1 before re-raising.
- **(second maintenance loop, passes 1–4)**
  `AwsTuiApp._mount_settings_view` and `_mount_service_view`
  replaced their bare `contextlib.suppress(Exception)` blocks with
  `try/except + log_sink.error(...)` so a `set_content` /
  `switch_service` / `host.mount` failure on the content-host nav
  path now leaves a triage signal instead of silently rendering
  blank. Mirrors the existing observability in
  `_mount_initial_service_view`.
- **(second maintenance loop, pass 10)** `CrossFsCopy.copy()` now
  wraps the destination's `write_stream` call in `try/finally` that
  explicitly awaits `stream.aclose()` when the source's
  `read_stream` returned an async generator. Previously a
  `write_stream` failure (or normal completion) relied on Python's
  GC-driven `aclose` for source-side cleanup, which can race
  event-loop shutdown on abort paths. Belt-and-suspenders
  deterministic close; GC remains the fallback for non-generator
  iterables.
- **(second maintenance loop, pass 10)** `LocalFS.delete()`'s
  initial `lstat` probe now catches the generic `OSError` branch in
  addition to `FileNotFoundError` / `PermissionError`. Without it,
  `ELOOP` / `ENAMETOOLONG` / `EIO` errors leaked past the
  `FileSystemProvider` error-taxonomy contract; the matching catch
  was already present in the same method's second try-block.
  Same bug-class as the Pass 4 S3FS `delete` / `rename`
  outer-except gap.
- **Blank screen on launch — defensive hardening** (PR #55). After PR
  #54's nav-page rework, some users saw a blank main area at startup.
  Could not be reproduced in any headless test, but three belt-and-
  suspenders changes shipped: (a) explicit ``width: 1fr; height: 1fr``
  on ``#main-area`` and ``#content-host`` in ``AwsTuiApp.CSS`` — the
  legacy ``ServicesMenu``'s per-theme ``width: 16`` rule made the
  Horizontal layout work by accident; the new ``NavMenu`` moved that
  rule into ``DEFAULT_CSS`` and left the content host implicit; (b)
  ``_boot_in_flight`` guard on the new
  ``_on_nav_selection_changed`` subscriber so on_mount's
  ``switch_service("s3")`` does not spawn a mount worker that races
  the direct ``_mount_initial_service_view`` call; (c) drop
  ``height: 1fr`` from the ``NavMenu`` root rule to match the legacy
  rail's shape. Regression: ``test_dual_pane_mounts_at_startup_without_blank_screen``.
- **Expired AWS SSO hang at startup** (PR #55, user-reported). When
  the resolved AWS connection's SSO access token was expired, on_mount
  built the DualPane → ``PaneVM.setup`` → ``S3FS.list`` → boto3 tried
  to refresh the token over the network and blocked on its default
  timeout; the whole launch hung then crashed. Now ``on_mount``
  probes the token offline via
  ``AwsSession.probe_token`` (reads ``~/.aws/sso/cache``) BEFORE
  ``switch_service`` runs; on ``TokenState.EXPIRED`` it mounts an
  ``"aws sso login --profile X"`` placeholder instead of building the
  DualPane. ``MISSING`` is intentionally NOT gated —
  ``probe_token`` returns ``MISSING`` for both "SSO configured but no
  cache" AND "no SSO at all (static creds)"; the latter is legitimate
  and must proceed. Regression:
  ``test_expired_sso_does_not_call_switch_service_at_startup``
  (mutation-tested).
- **Settings → S3 → Settings re-toggle crash** (PR #56,
  user-reported). ``ctx.settings_vm`` was a singleton built once at
  composition and pre-constructed in ``on_mount``. ``ContentHostVM.set_content``
  calls ``vm.dispose()`` on swap-out and ``vm.construct()`` on
  swap-in; after the first Settings → S3 swap the singleton was in
  ``Disposed`` state, and the second Settings click raised
  ``WorkerFailed: StatusTransitionError('Cannot construct from state Disposed.')``.
  Fix: build a fresh ``SettingsVM`` per mount in
  ``_mount_settings_view`` (factory pattern, matching how
  ``S3Service.build_vm`` already returns a fresh ``DualPaneVM`` per
  call). ``SettingsVM.dispose()`` does NOT cascade to its
  ``S3ConnectionsVM`` child, so the shared connection list/selection
  state survives across rebuilds — only the thin ComponentVM wrapper
  is recreated. The ``settings_vm`` field was dropped from
  ``AppContext``. Regression:
  ``test_toggle_settings_s3_settings_does_not_crash``
  (mutation-tested).
- **Windows py3.11 nav-mount worker race** (PR #56 follow-up exposed
  by the new toggle test). ``_mount_service_view`` and
  ``_mount_settings_view`` are workers that both touch
  ``ContentHostVM`` via an ``await``. With back-to-back clicks the
  service worker could resume after the settings worker had replaced
  ``ContentHost.current``, read the now-``SettingsVM``, and try to
  wrap it in ``DualPane(self._vm.left, ...)`` →
  ``AttributeError: 'SettingsVM' object has no attribute 'left'``.
  Fix: scope both ``run_worker`` calls to a shared
  ``group="content-mount"`` with ``exclusive=True`` so Textual cancels
  any in-flight worker in the group before starting the new one.
- **Config directory permission hardened to ``0o700`` on save.** The
  ``config.toml`` file itself was already created mode ``0o600`` via
  ``tempfile.mkstemp``, but the parent directory ``~/.config/aws-tui``
  inherited the user's umask (typically ``0o755``) which leaked the
  directory listing to other local users on shared systems. Credentials
  are still protected at the file level; tightening the parent dir to
  ``0o700`` keeps the existence of the config private too. The chmod
  is best-effort (wrapped in ``contextlib.suppress(OSError,
  NotImplementedError)``) so filesystems without POSIX permission bits
  don't break the save path. Pinned by
  ``test_save_chmods_parent_dir_to_0o700``.
- **NavMenu now subscribes to NavMenuVM property changes.** The
  widget's ``_rebuild_options`` docstring claimed it ran "whenever
  the VM's items change" but no subscription was wired up. As a
  result the rail would silently show stale items after a connection
  switch caused ``NavMenuVM._rebuild_items`` to filter services
  differently. ``on_mount`` now subscribes to the hub and re-renders
  on ``PropertyChangedMessage("items")`` / ``("selected_id")``.
- **S3 connections CRUD error paths hardened.** ``_do_delete`` is now
  ``@work(exclusive=True)`` so rapid double-clicks serialize instead
  of racing on ``vm.remove``; ``vm.remove`` is wrapped in try/except
  so a connection that vanishes between dialog and remove call
  surfaces an error toast instead of crashing the worker. Inline-form
  add path now catches all exceptions (not just ``ValueError``) so
  disk-full / permission errors surface to the user instead of
  silently dismissing the form.
- **Transfers overlay now shows all queued transfers upfront.** When
  the user marked N entries and pressed `c` / `m`, ``DualPaneVM``
  registered each transfer one-at-a-time as the loop reached it —
  so the overlay only ever displayed the currently-running transfer
  plus any lingering completed ones. With multiple marked entries
  the queue depth was invisible (users perceived the overlay as
  "treating them in twos"). Now every marked entry sends a PENDING
  ``TransferProgressMessage`` before the loop starts, so all N rows
  appear immediately and transition RUNNING → COMPLETED in order.
  Pinned by ``test_dual_copy_across_pre_registers_all_pending_before_running``.
- **Transfers overlay no longer shows empty bars or "0 B · streaming…"
  on completed directory copies.** When the source entry was a
  directory, ``LocalFS`` returned ``size=None`` so the COMPLETED
  message carried ``bytes_total=None``; the row rendered ▱×10
  (empty bar) and "0 B · streaming…" even though the copy was
  finished. ``TransferRowWidget._bar_text`` now returns ▰×10 for any
  COMPLETED state regardless of ``bytes_total``; ``_bytes_text``
  shows "✓ done" instead of the misleading streaming text when the
  size is unknown.
- **Transfers overlay destination label was identical to the source
  name on every row** (both rendered as the trailing path segment,
  so users couldn't tell source from destination). The destination
  now renders the FULL ``destination_label`` (with scheme prefix —
  ``s3://bucket/path/file``), and the per-theme CSS truncates with
  ellipsis if the string exceeds the 44-cell overlay width via
  ``text-wrap: nowrap; text-overflow: ellipsis`` on
  ``.transfer-name`` and ``.transfer-dest-row``.
- **Cancel chip in the transfers overlay no longer half-renders.**
  The chip was styled with ``border: round $rule-dim`` and
  ``height: 1`` — rounded borders need three rows to render, so the
  chip clipped to a tiny stub at the right edge of every row. Now
  styled as a flat 1-cell background fill with ``color: $accent;
  background: $bg-elev; text-style: bold`` and ``hover →
  background: $danger; color: $bg``.
- **Cancel chip now actually interrupts the in-flight copy.**
  ``TransferVM._cancel`` flipped the row's VM state to ``CANCELLED``
  but the underlying ``CrossFsCopy.copy(...)`` task kept running —
  the row read ``⊘ cancelled`` while bytes kept transferring (the
  user-reported "cancel doesn't work" bug). Fixed with a two-part
  cancel: ``TransferVM`` still flips state immediately for instant
  UI feedback, AND publishes a new ``TransferCancelRequestedMessage``
  on the hub. ``DualPaneVM`` subscribes, holds a per-transfer
  ``asyncio.Event`` registry populated by ``copy_across`` /
  ``move_across`` during the pre-register pass, and races the copy
  task against the event via ``asyncio.wait(..., return_when=
  FIRST_COMPLETED)``. When the event fires the copy task is
  ``cancel()``-ed (CrossFsCopy bails at its next await point), the
  journal is marked aborted, and the batch loop moves on to the
  next queued transfer. Pre-PENDING cancels are handled too: a
  cancel arriving before the queued transfer's turn skips the work
  entirely. Pinned by
  ``test_dual_copy_across_cancel_event_interrupts_in_flight_copy``.
- **ConfirmModal proportions tightened.** The modal felt visually
  heavy: ``width: 70`` on dark themes (60 elsewhere), container
  ``padding: 1 2``, body ``padding: 0 1 1 1``, footer ``height: 5``
  with a row of top-padding. Dropped to ``width: 64`` (dark themes,
  60 elsewhere unchanged), container ``padding: 0 2``, body
  ``padding: 0 1``, footer ``height: 3`` ``padding: 0``,
  path-label ``margin: 0`` (was top-margin 1). Net: the modal is
  ~5 rows shorter and 6 cols narrower on dark themes. Both copy
  and delete variants benefit.
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
- **Transfers overlay mislabelled every transfer as "local-copy."**
  ``TransfersVM`` auto-registered placeholder ``TransferModel`` rows
  with a hard-coded ``direction="local-copy"`` regardless of whether
  the underlying transfer was an upload, download, or s3-copy.
  Producer (``DualPaneVM._pane_uri``) now emits scheme-prefixed URIs
  (``s3://...`` for S3 panes, plain ``/...`` for local) and consumer
  (``TransfersVM._infer_direction``) classifies from the prefix.
  Locked in by ``test_auto_register_infers_direction_from_uri_schemes``.
- **S3 ``LastModified`` from older MinIO releases was treated as the
  wrong timezone.** ``S3FS._to_aware`` was a no-op return; it now
  coerces naïve datetimes to UTC-aware so downstream sort / format
  code can mix tz-aware and tz-naïve responses safely.
- **``AwsSession.aclose_all_clients`` silently swallowed every
  shutdown exception.** Replaced ``contextlib.suppress(Exception)``
  with a logged warning so crash-dump triage has a signal when a
  client fails to close cleanly. Shutdown stays best-effort.
- **Shift+Arrow semantics simplified to "toggle the row I'm
  leaving."** Previous version had three modes (extend / shrink /
  toggle-isolated) and could touch both the current and target
  rows in a single press, which surprised users walking back
  through a contiguous range. New rule from the user: a Shift+Arrow
  flips the mark on the row the cursor is moving *away from* and
  never touches the target. Walking down through unmarked rows
  marks them as you leave; walking back up through them unmarks
  them the same way. New regression test
  `test_shift_arrow_toggles_only_the_row_being_left` locks it in.
- **Carbon banner now uses the genai-vanilla blue gradient.**
  Direct 6-stop subsample of the upstream 15-stop palette
  (`color(17)` deep navy → `color(195)` pale blue), matching the
  banner the carbon theme was spec'd against. Carbon, amber, and
  lattice are now the three reference palettes the other 7 themes
  follow: 6 stops, one hue family, dark→light, final stop close to
  `$accent`.
- **Services-rail title click was redundant with the hamburger.**
  Removed the title's `on_click` toggle; it's now a passive
  "services" header. Collapse/expand goes through the hamburger
  button (or `m`).
- **Copy / Delete modal buttons were clipped — only one rendered
  fully.** Root cause: `.modal-footer` was `height: 3` with
  `padding-top: 1` (2 usable rows), but each `ModalButton` is 3 rows
  tall (rounded border + 1 content row). The second button was
  pushed out of the footer's clip rect. Footer is now `height: 5`
  and buttons widen to `width: 18` with `padding: 0 3` so labels
  read clearly and both render with room to spare.
- **Shift+Arrow couldn't deselect an isolated marked row.** The
  extend/shrink rule had no way to handle "the cursor is on a
  marked row, the row in the opposite direction is unmarked, and
  the target is unmarked" — that pattern is the user re-pressing
  Shift+Arrow on a solo mark to toggle it off. Added it as a third
  branch in `_extend_selection`: when current is marked and the
  opposite-direction neighbor is unmarked → unmark current + move.
- **Hamburger sat over the brand banner and click toggled twice.**
  Moved the `ServicesHamburger` from a top-of-screen overlay into
  the left edge of `#main-area` (just below the banner) so its
  position now reads as "the control for the column to my right."
  Removed the `ServicesMenu` body-click handler that was double-
  firing on the bubbled click (expand-then-immediately-collapse);
  collapse/expand now goes only through the hamburger or `m`.
- **Services rail styling did not match the file panes.** Reworked
  to mirror `Pane`: full `border: solid $rule-dim`, `color: $text`
  (not muted), and selected-row treatment matches `entry-row.-selected`
  (`$bg-sel` background, `$accent-soft` color, bold) across all 10
  themes.
- **`s` collapse-key was too close to `S` (swap source).** Rebound
  services toggle from `s` to `m` (Menu). `S` still swaps the
  focused pane's provider; the footer chip updates accordingly.
- **Theme-change toast text was too terse.** Was "Theme: X"; now
  "Theme changed to: [X]".
- **Banner gradients were inconsistent — only amber and lattice
  read as deliberate.** Redesigned the gradients for the other 8
  themes using amber and lattice as references: 6 evenly-spaced
  256-color cube stops, single hue family, dark→light, ending
  near each theme's `$accent`.
    - carbon: deep navy → pale ice-blue (was greyscale→azure mix)
    - voidline: deep magenta → pale pink (kin to `$accent-hot`)
    - solarized-light / github-light / one-light: dark navy →
      theme-specific blue (dark stops for contrast on light bg)
    - nord: dark Frost → pale Frost (cyan band)
    - dracula: deep purple → pale purple (drops the pink drift)
    - gruvbox-dark: forest → olive-gold (distinct hue from amber
      so the two warm themes don't read identically)
- **Confirm modal: From / To / Target labels were invisible.** The
  rule `height: 1; padding: 1 1 0 1;` left zero content rows. Fixed
  to `height: 1; padding: 0 1; margin-top: 1` so each label renders
  on its own row above the bordered path block.
- **Confirm modal: Cancel and Confirm buttons were different widths
  and text was clipped.** Buttons now use a fixed `width: 14` (in
  both `ModalButton` DEFAULT_CSS and every theme override) and the
  text no longer gets manual `"  Label  "` padding spaces — the CSS
  `content-align: center middle` handles centering. Identical
  rectangles, no spill.
- **Confirm modal: no arrow-key navigation between footer buttons.**
  `Left` / `Right` (and `Shift+Tab` / `Tab`) now swap the focused
  button; the focused side gets a `-focused` class that themes paint
  with the louder accent. `Enter` commits whichever side has focus
  (`action_commit_focused`). Default focus = Confirm for normal
  modals, Cancel for danger modals (so a reflex Enter on a delete
  doesn't nuke data).
- **Theme-change toast rendered as a blank horizontal line with no
  text.** `ToastStack` was fixed-width 50 but the inner `Vertical`
  was `width: auto`; child `Toast` widgets asked for `width: 100%`
  of an auto parent → the resolver collapsed everything to 0. Inner
  Vertical is now `width: 100%`, so toasts fill the 50-col stack and
  the "Theme: voidline" text is visible again.
- **Transfer overlay showed `?  →  ?` for source and destination.**
  `TransferProgressMessage` didn't carry the source / destination
  URIs, so `TransfersVM._on_message` auto-registered placeholders
  with empty labels. Added optional `source_label` /
  `destination_label` fields to the message and seeded a `PENDING`
  message at the start of `copy_across` / `move_across` so the
  placeholder gets meaningful labels from the very first sighting.
- **Services rail did not actually collapse the column — only the
  list inside.** Reworked the affordance: a new `ServicesHamburger`
  floats at the top-left on the `notifications` layer (always
  clickable), the rail itself is now `display: none; width: 0;` in
  the collapsed state. Clicking the hamburger or pressing `s`
  toggles it. Expanded width unchanged (16).
- **Services list selection used the muted accent without a side
  bar.** Selected row now uses `background: $accent-hot; color: $bg;
  text-style: bold;` across all themes and renders with the same
  heavy `▌` left-bar prefix the file pane uses for its cursor row,
  so the visual language is consistent.
- **Shift+Arrow deselection didn't work** — extending forward marked
  rows, but reversing course did not unmark them. `_extend_selection`
  now infers direction from whether the *target* row is already
  marked: target marked → shrink (unmark the row we're leaving),
  target unmarked → extend (mark current + target). Walking back
  through a previously-selected range now cleanly deselects.
- **Confirm-modal "Confirm" / "Delete" button was invisible after the
  PR-25 `ModalButton` extraction.** The widget class was renamed from
  the private `_ModalButton` to the public `ModalButton`, but every
  theme `.tcss` still selected on `_ModalButton`, so the theme rules
  no longer matched and both footer buttons fell back to the
  background-on-background default. The only visible button was the
  one whose text contrasted by accident. Renamed selectors to
  `ModalButton` in all 10 themes and dropped a leftover
  `.modal-footer > Button` rule from the Textual-Button era.
- **Theme-change toast rendered over the brand-banner top border.**
  `ToastStack` was docked `top` on the notifications layer, so the
  stack landed on the same rows the banner occupies. Re-anchored to
  match the `TransfersOverlay` pattern (`dock: right; offset: 0 8;`)
  so toasts appear at the top-right just below the banner, fully
  inside the visible viewport.
- **Services rail did not collapse horizontally** — the collapsed
  width was `6` (still showing icon glyphs) instead of disappearing
  down to the toggle. Collapsed width is now `3` (just the `+`
  affordance) and `#services-list` is hidden via `display: none` in
  the collapsed state, so the file panes reclaim the full width.
- **Copy / delete confirm dialogs duplicated the source path and
  buried the most important info under unstructured prose.** Replaced
  `body_lines` source/destination duplication with a structured
  `paths: tuple[ConfirmPath, ...]` field on `ConfirmRequest`. The
  modal now renders each path as a bold accent-colored label (`From`,
  `To`, `Target`) followed by the path inside a rounded border block.
  Copy shows From + To; delete shows Target plus the unchanged "This
  cannot be undone." warning. No duplication.
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

### 1.2.7. Removed

- **`SettingsModal`, `ServicesMenuFooter`, `S3CompatFormModal`,
  `_PlaceholderPanel`, `ServicesMenuVM`** (PR #54 rework). The PR #52
  modal-overlay pattern was reworked into the first-class nav-page
  pattern: ``SettingsModal`` → ``SettingsView`` (mounted in the
  content host), ``ServicesMenuFooter`` (gear band) → removed
  (Settings is now a peer nav item docked at the rail's bottom — see
  PR #56), ``S3CompatFormModal`` → ``ConnectionFormInline`` (expands
  inline within the Connections section), ``_PlaceholderPanel`` →
  removed (disabled ``Collapsible`` sections handle the
  "coming-soon" treatment), ``ServicesMenuVM`` → ``NavMenuVM``
  (renamed; legacy ``RootVM.services_menu`` property preserved as an
  alias awaiting a future minor-version cleanup).
- **`AppContext.settings_vm` field** (PR #56). Removed because the
  singleton-VM pattern is fundamentally incompatible with
  ``ContentHostVM.set_content``'s dispose-on-swap-out behavior — see
  the matching entry under Fixed. ``SettingsVM`` is now constructed
  per-mount inside ``AwsTuiApp._mount_settings_view``.
- `StatusBar` widget. Profile / region / auth indicator moved to the
  left pane's `border_subtitle`. The chrome VM stays so hub
  subscribers continue to receive updates.
- Vacuous `if t.id not in existing_ids: pass` dead branch in
  `TransfersOverlay._rebuild` — the linger arm beneath it is already
  idempotent.
- Duplicate `import sys` inside `app.main()` (was imported twice on
  separate exception branches); folded into the module-level import.

### 1.2.8. Testing

- 41 new regression tests across 8 files cover every pass-7–12
  feature and bug-fix (`S3FS` bucketless ops, `PaneVM` border / swap /
  marked-bytes, `BrandBanner` theme palette swap, theme runtime
  propagation, chrome layout, hint-legend chips, modifier-click
  multi-select, ConfirmModal Enter forwarding, `$AWS_PROFILE`
  resolution, pane source swap). Total: 482 → 518 tests (net of 5
  dead-widget tests dropped in the pass-13 maintenance cleanup). The
  second overnight loop's pass-2 theme-default fix adds 3 (total 521);
  pass-10's journal-preservation regression test adds 1 (total 522).

**First maintenance loop (passes 1–6) fixes** — folded into the
unified `### Fixed` section above (per Keep a Changelog one-section
rule); items retained below for provenance until the next release tag
cuts a new `[Unreleased]`.

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
  - `docs/architecture.md` testing-pyramid counts corrected to match
    the live suite (the specific 463 → 429 → … historical figures are
    no longer load-bearing — see the current table in architecture.md
    §5 for today's authoritative numbers); the architecture doc's
    `MessageHub.subscribe(callback, filter=...)` claim replaced with
    the actual `hub.messages.subscribe(on_next=...)` API.
  - `docs/adding-a-service.md` cross-reference to spec §7 corrected
    to §2 (the FileSystemProvider protocol).

**First maintenance loop (passes 1–6) — removed:** dead code: stray
`_ = head` placeholder in `S3FS.delete()`; unused `max_concurrent`
ctor param + field in `TransfersVM`. The function-local `DualPane`
import in `app.py.on_mount` moved to a module-level import. (Items
folded into the unified `### Removed` section above; retained here for
provenance.)

### 1.2.9. Deferred / v0.9 roadmap

These items are spec'd but explicitly not wired in v0.8.x. They are
tracked so the next minor release can pick them up without rediscovery:

- **Quick Look (entire feature)** — `Space` on a file is spec'd to
  stream the first 64 KB with a syntax tint, then offer a full-file
  `$PAGER` shell-out. `QuickLookVM` is built, the `QuickLook` modal
  is built and snapshot-tested, and `PaneVM` emits
  `preview_requested` on file-cursor `Enter`/`Space`, but no
  subscriber consumes the signal and no `Binding("space", …)` lives
  in `AwsTuiApp.BINDINGS`, so end-to-end the feature is unreachable
  at runtime. Wiring lands with the `BindingResolver` work below.
- **`pane.enter_multiselect` action** — `v` is spec'd as the
  mode-entry shortcut for multi-select; the handler is not wired in
  v0.7.x. `Shift+↑/↓` and modifier+click cover the actual
  multi-select paths today.
- **Command palette (entire feature)** — `:` or `Ctrl+K` is spec'd
  as a fuzzy-filterable list of every action (including dynamic
  ones like `connection switch <name>`). `CommandPaletteVM` and the
  modal exist; in v0.7.x `:` opens the help overlay as a
  placeholder and `Ctrl+K` is unbound.
- **`BindingResolver` is constructed but unwired** — `AwsTuiApp`
  builds it from `KeymapStore` and the `ActionRegistry`, but
  `BINDINGS` is still a hard-coded `ClassVar`. User `[keybindings]`
  overlays in `config.toml` parse and validate but do not yet affect
  the live keymap.
- **`*_requested` orphan signals** — `PaneVM`/`DualPaneVM` emit
  `PropertyChangedMessage` envelopes named `open_requested`,
  `ascend_requested`, `refresh_requested`, `preview_requested`,
  `copy_requested`, `move_requested`, `delete_requested` that no
  subscriber consumes. Direct VM-method calls handle the action; the
  signal path is preserved for the MVVM-correct subscriber wiring.
- **Modal-driven flows not yet pushed at runtime:** the `ResumeModal`
  (transfer-journal resume on relaunch), `FirstRunModal` (welcome
  flow when no connections are configured), `QuickLook` modal
  (`preview_requested` consumer), and `CommandPalette` modal
  (no opening binding) are all built, snapshot-tested, and exported
  from `composition.py` but not invoked by `AwsTuiApp` runtime
  wiring. The placeholder `_mount_no_connection_placeholder` text
  panel covers the no-connection case in v0.7.x.
- **`AwsTuiApp._handle_exception` does not push the crash modal** —
  the dump file is written and stderr re-raises through `main()`, but
  the in-app `CrashChoice` modal flow (continue / view trace / quit)
  is only reachable via the public `show_crash_modal(report)` method
  on a healthy app; `record_action()` is also not invoked from any
  binding, so the `_action_ring` is always empty when a dump is
  written.

## 1.3. [0.7.0] - 2026-06-14

### 1.3.1. Added

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

### 1.3.2. Documentation (M6 T4)

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

### 1.3.3. Testing

- **Unit tier (+49 tests).** `tests/unit/vm/chrome/test_crash.py`,
  `test_resume.py`, `test_first_run.py` (VMs);
  `tests/unit/ui/test_crash_modal.py`, `test_resume_modal.py`,
  `test_first_run_modal.py` (widgets);
  `tests/unit/infra/test_crash_dump.py` (infra);
  `tests/unit/test_composition_resume.py`,
  `test_composition_first_run.py` (composition helpers).
- **Snapshot tier (+12 goldens).** 3 new modals (crash, resume,
  first-run) × 4 themes, all pinned to (120, 40).

### 1.3.4. Watch-outs captured

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

## 1.4. [0.6.0] - 2026-06-14

### 1.4.1. Added

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

### 1.4.2. Testing

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

### 1.4.3. Layer rules

- Composition root (`composition.py`) and Textual app (`app.py`)
  live at the top of `src/aws_tui/` (not under any of the five
  layer dirs), so `scripts/check-layers.sh` does not need to be
  exempted — it only walks the five layer folders.

### 1.4.4. Watch-outs captured

- `_context` attribute name on an `App` subclass collides with
  Textual's internal `App._context`; rename to e.g. `_app_ctx`.
- `_shutdown` method name on an `App` subclass collides with
  Textual's `App._shutdown` lifecycle hook; rename to e.g.
  `_aws_tui_shutdown`.
- Snapshot `.raw` files are excluded from the `end-of-file-fixer`
  and `trailing-whitespace` pre-commit hooks since they're
  byte-exact match targets.

### 1.4.5. CI

- New `snapshot` job (ubuntu-22.04 / py3.12) running
  `tests/snapshot`.
- New `e2e` job (ubuntu-22.04 / py3.12) running `tests/e2e`.

## 1.5. [0.5.0] - 2026-06-14

### 1.5.1. Added

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

### 1.5.2. Testing

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

## 1.6. [0.4.0] - 2026-06-14

### 1.6.1. Added

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

### 1.6.2. Changed

- `services/__init__.py` re-exports `Service` / `ServiceRegistry` /
  `ServiceDescriptor` / `ServiceNotFound` from
  `aws_tui.vm.services_protocol` so the `services/` subtree can write
  `from aws_tui.services import ServiceRegistry` without breaking the
  vm/ → services/ layer-rule.
- M3 plan revised in-flight to document the actual VMx API
  (builder-pattern instantiation, no static `.builder()` on
  `AggregateVM3`, `.children(factory)` on composites, etc.).

## 1.7. [0.3.0] - 2026-06-14

### 1.7.1. Added

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

### 1.7.2. Changed

- Dev deps now include `moto[server,s3]>=5`, `testcontainers[minio]>=4`,
  and `types-aiofiles>=23`. Strict mypy stays clean.

## 1.8. [0.2.0] - 2026-06-14

### 1.8.1. Added

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

### 1.8.2. Changed

- Mypy config now ignores missing imports for `aioboto3` and `botocore`
  (no upstream stubs).

## 1.9. [0.0.1] - 2026-06-14

### 1.9.1. Added

- Initial project scaffold (M0): public GitHub repo, MIT license, VMx submodule, uv-managed dependencies, src-layout, hello-world Textual `AwsTuiApp` with `q`-to-quit, CI matrix on macos-14 / ubuntu-22.04 across Python 3.11–3.13.
- Full design spec at `docs/superpowers/specs/2026-06-13-aws-tui-design.md`.

[Unreleased]: https://github.com/thekaveh/aws-tui/compare/cd2c9e8...HEAD
[0.8.0]: https://github.com/thekaveh/aws-tui/compare/v0.7.0...cd2c9e8
[0.7.0]: https://github.com/thekaveh/aws-tui/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/thekaveh/aws-tui/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/thekaveh/aws-tui/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/thekaveh/aws-tui/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/thekaveh/aws-tui/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/thekaveh/aws-tui/compare/v0.0.1...v0.2.0
[0.0.1]: https://github.com/thekaveh/aws-tui/releases/tag/v0.0.1
