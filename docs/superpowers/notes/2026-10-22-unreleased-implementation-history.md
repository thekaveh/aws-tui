# Unreleased implementation history captured 2026-10-22

## 1. Scope

This maintainer record preserves the original Unreleased notes from
`e79ded26e8e5d9a32cef21f90ad32d4616e7e45d` before editorial curation.
It records prior descriptions and investigations, including superseded claims.
It is not current installation, support or release guidance.
See the [current changelog](../../../CHANGELOG.md) for the curated release summary.

## 2. Original Markdown

Relative paths and statements below are preserved as historical source text.
The protected block is not a generated release body or a published package claim.

`````markdown
## [Unreleased]

The Unreleased section is the promotion queue for the next package release.
Entries may reside on ``develop`` before promotion to ``main``; inclusion here
does not by itself claim that every entry has landed on ``main``. Changes
already promoted to ``main`` are tracked relative to the historical v0.8.0
staging commit (``cd2c9e8``). That staging build was not published.

The PyPI
project-name request remains gated on
[pypi/support#11264](https://github.com/pypi/support/issues/11264)
(name-similarity exception for ``aws-tui`` vs ``awstui``).

Glue, Athena, and
Iceberg integration target v0.9.0: they are new minor-version feature work
under SemVer, not a v0.8.0
headline or a v0.8.1 patch candidate. The package metadata remains ``0.8.0``
until a v0.9.0 release-preparation PR bumps it and cuts a dated changelog
section. The current tree must not be tagged as v0.8.0.

### Added

- Read-only scoped overview from the command palette, with explicit session watches,
  bounded manual refreshes, per-source failure states and exact existing-detail navigation.
  Session transfers retain the existing 100-finished-entry limit.

- Named navigation workspaces and bookmarks with keyboard management,
  versioned local storage and current-source/resource validation before Open.
  Saved navigation excludes SQL, credentials, results and running operations.

- Reviewed one-way local-to-S3 sync with full-byte SHA-256 comparison,
  include/exclude filters, individual operation selection, conditional publication,
  cancellation, and fresh comparisons before retrying failed or unattempted work.

- Reviewed recursive local-folder uploads to S3 from the command palette,
  with explicit conflict policies, per-object results, bounded concurrency,
  cancellation that preserves published objects, and fresh failed/unattempted-only retries.

- S3 object-version browsing from the command palette, including exact-key entry
  for deleted current objects. Inspect and download a selected historical version,
  or recover its content, supported metadata and tags to a different new key.
  Existing destinations are refused; published recovery keys survive cancellation.

- Durable transfer summaries and explicit History / Recovery from `Ctrl+T`,
  `app.transfer_history`, the palette, and the Transfers overlay. The 100 newest
  safe summaries survive restart; background startup scanning preserves unknown
  outcomes and separates publication certainty. User-requested copy retries
  revalidate original connections and both endpoints, ask for a fresh conflict
  decision, and use normal progress/cancellation with a new ID. Clear history
  confirms removal of validated owned metadata only, preserving active operations
  and source/destination files. No automatic replay or multipart resume.


- Read-only current S3 object properties via `Ctrl+O`, `pane.object_details`
  and **S3 object details** in the Commands palette. A literal property table
  and wrapping full-value viewer expose metadata, tags, encryption and reported
  checksums with truthful unavailable states and lossless copying. Reads occur
  only on demand; selection/source changes discard stale results.

- Confirmed cancellation for one selected active EMR Serverless job run via
  `x` / `emr.cancel`, the Commands hint, or the service command palette. The
  danger dialog identifies the exact source, application and run. Requests use
  one service attempt and require `emr-serverless:CancelJobRun`; acknowledgement
  reports cancellation requested while existing polling observes actual state.
  Demo cancellation stops the selected run's state walk without resurrection.

- **Read-only diagnostics (#246).** `aws-tui doctor` reports local setup
  failures and repair steps before the TUI starts; `--json` provides schema
  version 1 and `--probe NAME` explicitly checks one source. Running help
  shows the active log file and crash directory.

- **In-session connection setup (#243).** Empty or invalid local connection
  configuration opens actionable setup with the existing S3-compatible form.
  Save and open persists through the Settings connection owner and opens S3;
  Retry discovery refreshes local AWS profile rows without probing or opening
  them. Explicit selection probes credentials before starting the provider.
  Origin labels, separate recovery instructions, and keyboard controls work
  at 120×40. Setup leaves AWS config and credentials files unchanged.
  Locked or unavailable keychains leave setup actionable for an external
  repair and Retry discovery. Saves completing after navigation report their
  outcome without changing the current screen or opening the saved source.

- **Copyable pane paths and revealed truncated names (#208).** `p` copies the
  path of the entry under the cursor and `P` copies the pane's own directory
  path; the pane's top border is a click target for the same copy. A name or
  path too long for its column is revealed in full rather than silently
  clipped.
- **Glue and Athena interaction polish.** Bordered, keyboard-focusable AWS
  context selectors now expose source, Glue state filters, and Athena
  workgroup/catalog/database choices with named commands and complete
  forward/reverse focus rings. Glue can copy a selected table's canonical,
  fully quoted identifier to an authoritative VMx-backed in-app clipboard
  (`y`) and to the OS clipboard, while Athena can insert that value at
  the editor cursor (`i`) or replace a selection. Cross-source insertion is
  refused without mutating the editor or switching profiles; the existing
  source-preserving **Query table in Athena** workflow remains available.
  The service rail is wider and centers all service names, including Athena.
- **Integrated Glue, Athena, and Iceberg workflow.** Glue tables can prefill
  exact, fully-qualified Athena queries; Athena can return to one unambiguous
  visible Glue table; and Iceberg tables expose bounded, on-demand Snapshots,
  History, Manifests, Files, Partitions, and References views. Snapshot
  selection generates `FOR VERSION AS OF` SQL, but no generated query
  executes automatically. Cross-service messages preserve connection, region,
  catalog, database, and table identity; successful customer-S3 result
  artifacts retain the same identity through the final S3 handoff. Demo mode
  provides disjoint dev, prod, and shared Iceberg datasets and the complete
  Glue → Athena → explicit execution → S3 journey.
- **Amazon Athena service.** A fourth AWS-only nav service with
  Query, History, Results, and Saved views; connection- and region-scoped
  workgroup/catalog/database selections; fail-closed one-statement read-only
  SQL validation; app-owned query cancellation; paginated results with bytes
  scanned/reuse statistics; named and prepared query inspection; and exact
  identity result-artifact handoff to S3. Demo mode supplies profile-isolated
  dev/prod Athena state plus an access-denied shared profile.
- **AWS Glue read-only service.** A third first-class nav service with
  Catalog, Jobs, and Crawlers views; database/table pagination,
  schema/storage detail, partitions and column statistics; job/run
  inspection; crawler detail/metrics; profile- and region-scoped
  selection memory; `1` / `2` / `3` view keys; `r` refresh; and
  `Shift+S` whole-service profile switching. Demo mode supplies
  disjoint `demo-dev` / `demo-prod` Glue resources plus a
  `demo-shared` access-denied state.
- **Connection-preserving Glue-to-S3 handoff.** The command palette's
  **Open table location in S3** action publishes immutable
  `OpenS3LocationRequest` identity, resolves the exact connection
  name, rejects region drift instead of substituting a profile,
  rebuilds S3 through the existing RootVM/service factory lifecycle,
  and navigates/focuses the requested pane. Missing or malformed
  locations remain on Glue with a redacted advisory.
- **Command palette** (`:` / `Ctrl+K`). Opens a fuzzy-filterable palette of
  app commands — Theme picker, Cycle theme, Swap pane source, Open table
  location in S3, Settings, Help, Quit — each dispatching through the same
  `ActionRegistry` path as its key
  binding. `:` moves back from help to the palette (help keeps `?`), per the
  keystone plan. Deferred: dynamic commands (`switch connection/theme <name>`)
  and consolidating with Textual's built-in `Ctrl+P` palette. Spec:
  `docs/superpowers/specs/2026-07-21-command-palette-wiring-design.md`.
- **Quick Look preview** (`Space`). Pressing `Space` on a file opens the
  built-in Quick Look modal streaming the first 64 KB of the cursor file
  (mime guessed from the extension); directories / the `..` link / an empty
  pane are ignored. Registers the `pane.quick_look` handler the keystone
  reserved, driving the existing `QuickLookVM` + `QuickLook` modal via the FS
  provider's `read_stream`. Deferred to follow-ons: the mouse-click
  (`preview_requested`) trigger and the full-file `$PAGER` shell-out. Spec:
  `docs/superpowers/specs/2026-07-21-quick-look-wiring-design.md`.
- **Keybinding overlays now take effect** (`BindingResolver` keystone).
  `AwsTuiApp` installs its key bindings at runtime from
  `BindingResolver.to_textual_bindings()` instead of a hard-coded
  `BINDINGS` ClassVar, so a `[keybindings]` table in `config.toml`
  remaps any handled action on the live keymap (e.g. `pane.copy = "ctrl+y"`).
  Each binding dispatches through a single `action_dispatch(id)` into the
  `ActionRegistry`; the resolver emits a binding only for actions with a
  registered handler, so deferred/unwired actions stay unbound. Default
  behavior is byte-identical to the previous bindings — guarded by a
  fidelity test over every installed binding (key/action/show/priority)
  and a pilot test that the priority `tab` binding still fires (no "Tab
  does nothing" regression). Clears the `[0.8.0]` *"BindingResolver is
  constructed but unwired"* deferred item. Quick Look and the command
  palette now register their handlers; the resolver still leaves other
  handlerless deferred actions unbound. Spec:
  `docs/superpowers/specs/2026-07-21-binding-resolver-keystone-design.md`.
- **Demo mode** (PR #97 + #104 polish). ``AWS_TUI_DEMO=1`` or
  ``--demo`` boots the full UI against seeded in-memory S3, EMR, and Glue
  fakes — no AWS credentials, no real network calls. Persistent
  ``DEMO MODE — no real AWS calls`` chip prepended to the
  BrandBanner subtitle (PR #104 keeps the credit pedigree visible
  after the chip). See ``docs/superpowers/specs/2026-06-28-demo-mode-design.md``.
- **NavMenu ENTER opens the service** (PR #101). Pressing ``Enter``
  on a service row in the rail now shifts focus to that service's
  default pane — S3 → LEFT pane, EMR → Job Runs, Settings → first
  section's first focusable control. ``NavMenu.action_commit`` drops
  rail focus before calling the new ``App.focus_active_service_pane``;
  page-side auto-focus is gated on "is NavMenu still focused?" so
  arrow-walking the rail still keeps focus there (only ``Enter``
  hands focus off). New ``DualPane.focus_left_pane`` +
  ``SettingsView.focus_default``.

- **Local Iceberg row preview with DuckDB (Peek).** A seventh tab in the Glue
  Iceberg view previews rows of the selected table by running one
  `iceberg_scan(...)` statement against the table's S3 root with a local DuckDB
  engine, pinned to the snapshot selected on **Snaps**. It needs no Athena
  workgroup and no query-result location, which is what the existing
  Glue-to-Athena handoff fails on. `↓` re-queries at the next row-limit step
  (100 → 1,000 → 10,000) rather than widening a local window over rows already
  in hand; `↻` retries at the active pin and limit. The engine ships as the
  optional `duckdb` extra (`pip install aws-tui[duckdb]`, ~14 MB per platform);
  without it the tab reports the missing engine and the install command. AWS
  credentials come from a fresh `credential_chain` secret bound to the
  connection's exact profile and region on every query, so an `aws sso login`
  in another terminal is picked up on the next retry. Profile connections only;
  not available on `s3-compatible` connections. See
  [Installation](docs/install.md) and the cookbook's §7.4.

### Changed

- **Service navigation ownership.** A typed collaborator owns navigation epochs,
  task cleanup, S3 fallback and adoption policy while App retains UI operations.

- **Live chrome cleanup.** Removed the unmounted legacy `StatusBar` widget,
  its subscribed viewmodel, obsolete theme selectors, and the unused
  `pytest-xdist` development dependency. Connection identity remains in the
  service pane chrome where it is actually rendered.
- **Independent S3-compatible test harness.** Replaced the vulnerable MinIO
  community test image with the digest-pinned Adobe S3Mock 5.2.0 release while
  preserving the same nine strict provider tests, Compose workflow, seeded
  development data, and product support for user-configured MinIO endpoints.
- **VMx 3.23 adoption.** Raised the VMx floor to 3.23.0, moved modal focus
  restoration to public `DiscriminatorVM` APIs, rebuilt S3 validation through
  immutable `FormVMBuilder` validators, and aligned Athena cancellation and
  shutdown with public `AsyncRelayCommand` execution state. The dated audit
  records substitutions, rejected candidates, production line deltas, and test
  impact.
- **Overlay pickers, compact commands, and unified Athena handoffs.** Inline
  service-context and EMR application dropdowns now overlay without resizing
  adjacent panes. The Commands pane is one tooltip-rich row with deterministic
  `[:] more` overflow, and Glue exposes `Shift+Q` table and `Shift+V` visible
  snapshot handoffs through the same key, palette, and button action paths.
  Both preserve exact source identity, prefill bounded Athena SQL, and never
  execute it.
- Pin Textual to the audited `8.2.8` runtime because aws-tui's isolated
  priority-binding, crash-recovery, and mount-lifecycle adapters consume
  private Textual contracts. Supporting another release now requires an
  explicit adapter review before the dependency pin is broadened.
- Align AWS startup resolution with botocore: `AWS_DEFAULT_PROFILE` precedes
  `AWS_PROFILE`, `AWS_DEFAULT_REGION` supplies the final configured-region
  fallback, and `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE` override
  expanded shared-file paths.

- **The path copies are discoverable without the border glyph.** The pane's
  top border no longer ends in a clipboard emoji. It cost two cells of the
  one piece of chrome that truncates to the pane width, and it advertised a
  mouse-only affordance while saying nothing about `p` / `P`, which work
  with no pointer at all. The border is still a click target, the hover
  tooltip now leads with the key and still names the click, and
  **Copy cursor entry path** / **Copy pane path** are command-palette
  entries on the file-manager page.

- **Interaction-surface parity.** Restored configured keymap overlays at
  startup, filtered contextual palette commands by active service, clarified
  CSS ownership for shared selectors and tabs, and aligned the canonical
  documentation with the implemented Glue and Athena workflows.

- ``docs/`` + ``ui/widgets/settings_view.py``: retargeted the
  ``Deferred / v0.8 roadmap`` references to ``Deferred / v0.9
  roadmap`` (and ``coming in v0.8`` placeholders to
  ``coming in v0.9``) because the v0.8.0 release candidate excludes those items.
  Historical CHANGELOG entries kept their original wording.
- ``assets/screenshots/aws-tui-running.png``: refreshed the README
  hero image to a current EMR + demo-mode capture (PR #106).

### Fixed

- **S3Mock setup guidance.** The cookbook states checkout and Docker prerequisites,
  separates Settings from static configuration, and ties teardown to owned resources.

- **S3 directory-copy guidance.** Existing-prefix merges are documented
  separately from unsupported new-tree publication and whole-tree replacement,
  with per-child conflicts, preflight refusals and partial outcomes explained.

- **Peek access and cost guidance.** Local DuckDB preview avoids Athena query
  charges and workgroup/output setup. Direct S3 reads remain subject to source
  permissions, encryption access, connectivity and applicable remote-read charges.

- **Future Homebrew bootstrap.** The first-release recipe uses the actual
  `poet -f` interface in a disposable environment containing the published target.
  Temporary output and validation protect an existing formula from generator failures.

- **Visible theme selection.** The theme picker scrolls the highlighted row
  into view during keyboard navigation and when opening on a late custom theme.
  Mouse scrolling, live preview and Escape rollback keep their existing behavior.

- **Terminal-safe remap examples.** Object details, log follow and history
  examples use distinct ANSI control bytes. The keybinding guide explains
  protocol-dependent Ctrl+I/Tab and Ctrl+H/Backspace aliases and the limits
  of key-name collision validation.

- **Unicode JSONL previews.** Literal Unicode separators inside JSON strings
  no longer split records or force valid JSONL/NDJSON previews into raw mode.
  Record boundaries use LF, with CRLF supported and existing preview limits
  and display escaping preserved.

- **Readable compact EMR runs.** Narrow run lists reserve name/ID space
  instead of clipping it behind timestamps. The scrollable detail pane
  exposes full identity and keeps loaded list metadata available if a
  detail request fails.

- **Keyboard access to EMR details.** The detail focus slot scrolls overflowing
  arguments and Spark settings with arrows, vi keys, page keys and Home/End.
  Reverse Tab from the navigation rail returns to logs, completing the
  six-slot cycle without trapping focus or changing the selected run.

- **EMR filter recovery.** Restoring a state filter after refresh immediately
  reveals retained runs without another provider request. Hidden retained
  selections keep their details and log targets; genuine provider removals
  still reconcile the selection.

- **Visible EMR run selection.** Keyboard navigation keeps the selected run
  inside its scroll viewport without rebuilding rows. Refresh, pagination and
  terminal resizing reveal the current selection after layout.

- **Responsive connection Settings.** Listing S3-compatible connections does
  not read keychain secrets. Editing loads only the selected connection's
  credentials in a worker. Failed or superseded reads preserve current input
  and show safe recovery guidance without replacing a newer form or page.

- Glue table detail shows scoped partition and statistics loading or failures
  instead of false zero counts. Successful schema and storage remain visible,
  and Refresh replaces recovered statuses with actual counts.
- Glue Peek reloads when the selected Iceberg snapshot changes. Retained rows
  keep their successful snapshot identity through failed or cancelled reloads,
  with Retry available for the selected snapshot.
- Athena Query Refresh reloads workgroup configuration, catalogs and databases
  while retaining valid context, SQL, draft provenance and execution results.
  Failed reads remain local to their metadata, and superseded replies cannot
  replace newer choices.
- Transfer cards scroll within the overlay at compact terminal sizes. Later
  Cancel controls remain reachable, title and history controls stay visible,
  and resizing preserves the focused transfer. Small batches retain a compact
  overlay height.
- Active transfers have a keyboard-accessible palette route. Enter and Space
  activate the chosen cancellation control without activating the file pane;
  Escape returns to the service focus.

- File actions use the same focused, ready-pane checks as command discovery.
  Changed copy/delete confirmations show an expiration warning instead of
  silently discarding the accepted operation.

- Local copies, overwrites, moves and cleanup support valid long filenames.
  Internal stage, backup and quarantine names no longer include the user basename.

- Failed or cancelled Athena source changes restore exact outgoing SQL and its
  context, including when local draft persistence is off. Newer edits and
  navigation take priority; recovery retains draft provenance and save status.

- Update locked aiohttp, certifi and Pillow security releases; allow Pillow 13
  for documentation assets. Require patched uv and development pip versions
  that remove the compromised emSign CA certificate.

- The deferred S3 move API refuses same-object destinations across endpoint
  aliases before any mutation, preventing source deletion after a self-copy.

- Failed Athena navigation and credential recovery preserve local SQL draft
  origins, recovery decisions, and save status. Draft clears keep their original
  database target, and restoring older text does not falsely acknowledge it as
  saved or overwrite a newer draft automatically.

- Athena queries remain stoppable after polling errors. Rerunning first stops
  the prior query; a failed stop preserves its identity and prevents replacement.

- Release removed service widgets from cached queries after navigation.

- Athena Refresh retries failed catalog and database discovery. It preserves
  valid workgroups from later pages and newer context choices.
- Disabled Athena workgroups remain available for History but cannot submit
  queries. Glue handoffs discover an enabled workgroup before priming the table.
- Cancelling transfers drains source deletion and cleans owned staging data
  during directory creation, verification, conflict retries and overwrite setup.
- Local deletion restores quarantined entries after metadata failures and preserves
  concurrent replacements if restoration cannot complete.
- Documentation issue references remain inline instead of becoming headings.
  The contract ledger's complete navigation is restored and current prose clarified.

- **Zebra striping is now actually visible in all ten themes.** The striping
  shipped in #227 derived every theme's `$bg-alt` as the midpoint between its
  `$bg` and `$bg-elev`. That construction guarantees the stripe stays weaker
  than the cursor row but never checks that anyone can see it, and nothing in
  the suite measured it: the ten stripes came out at CIELAB `L*` deltas of
  1.18-3.92 (mean 2.22) against their own backgrounds, at or below the
  just-noticeable difference for two large flat adjacent areas. The user
  reported them as "just barely visible" in every theme. Each `$bg-alt` is
  re-derived to `L* = min(5.0, 0.45 x that theme's own $bg -> $bg-sel delta)`,
  walked along the theme's own `$bg -> $bg-elev` direction so the palette's
  tint survives rather than washing out toward neutral grey; the cap keeps the
  stripe clearly subordinate to the cursor bar. Stripes now measure `L*`
  4.17-5.00. `one-light` stops at 4.17 rather than 4.87 because the full
  target puts `$text-muted` at 4.43:1 on the stripe, under the 4.5:1 floor
  every content surface is held to. A new guard,
  `test_zebra_stripe_is_visible_but_subordinate`, measures both bounds per
  theme against that theme's own tokens -- the old values passed every
  existing test.
- **A half-delivered paste can no longer make the app permanently deaf
  (upstream Textual defect).** Textual 8.2.8's terminal parser
  (`textual/_xterm_parser.py`, `XTermParser.parse`) enters bracketed-paste
  mode on `\x1b[200~` and leaves it only on an exact `\x1b[201~`, but its
  escape-sequence loop abandons a partial sequence after `ESCAPE_DELAY`
  (0.1 s) without putting the bytes it consumed back. If the terminal
  delivers the closing marker split — the `\x1b` in one read and `[201~` in
  the next more than 0.1 s later, which is ordinary over a laggy ssh or tmux
  link — the parser stays in paste mode forever: the screen keeps repainting
  while every keystroke, `q` and `Ctrl+C` included, is swallowed into the
  paste buffer, and only killing the process from another terminal ends the
  session. aws-tui now wraps its terminal driver in a guard that bounds how
  long the parser may stay in paste mode and recovers by closing the paste
  and delivering what was buffered, instead of staying deaf. The bound is on
  input *silence*, not on how long a paste takes, so a slow paste of a large
  file is never truncated, and the second bound — the one that covers a user
  mashing keys at a frozen app, who is never silent — fires only once the
  closing marker has actually been seen in the byte stream, so a paste whose
  own content contains an escape character is delivered whole rather than
  arriving as keystrokes. Bracketed paste itself is still requested, because
  pasting a multi-line query into the Athena editor or the EMR log filter is
  a real feature.

- **A resize that happened on another Space is no longer painted at the old
  size.** A terminal can report a size change two ways: the kernel's
  `SIGWINCH` signal, or the newer in-band window-resize protocol (DEC
  private mode 2048). Textual 8.2.8 stops listening to `SIGWINCH` the moment
  a terminal accepts mode 2048, so the in-band report becomes the only
  channel — and if one is missed, which is what a macOS Space switch
  resizing a window on an inactive desktop can produce, the app keeps
  painting the old geometry with no way back. Keys still answered; the frame
  was simply stale, which reads as a freeze. aws-tui now starts with the
  in-band protocol off and `SIGWINCH` back in charge. The switch is
  Textual's own `TEXTUAL_SMOOTH_SCROLL`, which despite its name gates only
  that one negotiation; exporting `TEXTUAL_SMOOTH_SCROLL=1` restores
  Textual's default. Layout, colours and pane contents are untouched, but
  two pointer niceties ride on the same negotiation and are given up with
  it: a scrollbar drag now animates towards the pointer instead of tracking
  it, and mouse coordinates stay whole cells rather than sub-cell pixels.
  Both
  are already how Textual behaves on Terminal.app and iTerm2, which never
  negotiate mode 2048, and on Windows, whose driver has no in-band resize
  path; only Ghostty, WezTerm, kitty and their kin are affected either way.
  A related trap is closed at the same time: a terminal left with mode 2048
  set by some earlier app used to put the input parser into pixel-coordinate
  mode that was never actually negotiated, which collapsed every click
  towards the top-left corner after the first resize. See
  [Platforms](docs/platforms.md#31-window-resize).
- **Switching away no longer leaves the pointer's leftovers on screen.** When
  the terminal loses focus — changing macOS Spaces, switching tabs, or
  clicking into another window — a tooltip that was open stayed painted over
  the pane for as long as the app was away, and a scrollbar drag interrupted
  by the switch kept the mouse captured, so the first click after coming back
  was swallowed. Both are released as the app loses focus now.
- **A modal can no longer be wedged shut, and no UI state is unquittable.**
  Each service page hands focus to its default widget through a deferred
  callback. When an overlay opened before that callback ran, the callback
  wrote a widget from the page *behind* the overlay into the overlay's own
  focus, and key presses then went to the hidden page: `Esc` stopped closing
  the help, theme, confirm, Quick Look, palette, and clone overlays. The
  projection is now abandoned if the page is no longer the screen the user is
  looking at. As a second line of defence, `q` and `Ctrl+C` are honoured from
  any modal state, so no overlay can leave the app with no way out; overlays
  that are working normally still swallow both keys exactly as before.
- **Copy tells the truth about where the text went.** Copying a path or a Glue
  table reference used to announce `Copied …` unconditionally. The only write
  it made was OSC 52, which macOS Terminal.app ignores outright and iTerm2
  ignores unless the user opted in, and which no terminal acknowledges — so on
  those terminals the confirmation was simply wrong and the clipboard still
  held whatever it held before. Every copy now also goes through a real
  clipboard helper (`pbcopy`, `clip`, `wl-copy`, `xclip`, `xsel`) and reports
  what actually happened: copied, the helper failed, or there was no helper and
  only the terminal was written. An unconfirmable OSC-52-only delivery is never
  called a copy. The helper runs in a worker on every path, so a wedged
  `xclip` or a hung pasteboard server can no longer make the app deaf to
  keystrokes — `ctrl+q` included — while it times out.
- **Glue pagination is reachable.** Databases, tables, partitions, jobs, runs,
  and crawlers reported `more available` but no keyboard, palette, or mouse
  path called the view models' load-more methods, so a filtered runs list
  could hide every older matching run. `l` (`glue.load_more`), the **Load
  more Glue rows** palette command, and clicking a list footer now fetch the
  next page for the focused list.
- **EMR clone retries no longer start a second job.** `StartJobRun` now
  carries an app-owned `clientToken` held by the clone view model for the
  life of one form intent. Previously botocore minted a fresh token per call,
  so retrying after an accepted-but-lost request created a second billable
  run. The token rotates on a field edit or a successful submit.
- **Chrome legibility (#202).** The banner pedigree line is dimmed so it reads
  as secondary to the wordmark, and the EMR source/application row is laid out
  on one line instead of wrapping.
- **Athena query execution and controls.** Glue-generated starter SQL now uses
  the resolved Athena catalog context with a quoted database/table reference,
  avoiding redundant catalog qualification in the query text. Known Athena
  start-time rejections identify an inaccessible result location, rejected
  catalog/database context, or rejected workgroup without exposing raw AWS
  values. Run and Stop retain exact `5x3` terminal geometry across disabled,
  enabled, and focused states so their visible borders render square.
- **Immediate Glue-to-Athena starter query projection.** Glue table handoffs
  now populate the mounted Athena editor before waiting for remote workgroup,
  catalog, and database discovery. The existing VMx Run command remains
  disabled with a resolving status until the exact destination context is
  ready, and generated queries still never execute automatically.
- **Terminal maintenance safety and fidelity.** S3 rate limits now remain
  throttling signals instead of marking a connection unreachable. EMR bounds
  application and bulk-run pagination, classifies log paths relative to the
  configured run prefix, preserves the last retry/worker marker in labels, and
  renders streamed lines through one reusable widget. Crash dumps read at most
  1 MiB while retaining up to 1,000 tail lines. Pane mount now reconciles VM
  state that changes between composition and subscription, so snapshots use the
  production reactive path. CI audits every locked dependency group, validates
  complete wheel and sdist payloads, and smoke-installs both artifact types.
- **Late maintenance contract guards.** Service swaps now await every cancelled
  transfer worker before disposing its panes, EMR log discovery rejects more
  than 100 listing pages or 200 classified files, and package validation checks
  that wheels contain the complete Python, typing-marker, and theme payload.
  Documentation publication, including manual dispatch, is restricted to
  `main`; the generated hero replaces unsupported selector glyphs explicitly
  and rejects any future missing font glyph instead of rendering `.notdef`.
- **Bounded startup, transfers, and service hydration.** Automatic connection
  fallback now shares one 90-second launch budget, copy and move reject batches
  above 1,000 selected entries before creating journals, and Athena history
  hydrates each 50-row page with one `BatchGetQueryExecution` request. Persisted
  S3-compatible connections use the same endpoint and credential validation as
  Settings, explicit credential references survive materialization unchanged,
  cross-filesystem overwrite races clean their staging container, and content
  candidates have one disposal owner across failed service swaps.
- **Atomic source and service changes.** Root service adoption now builds and
  constructs the candidate before committing source identity, and publishes
  the new content, connection, and authentication identity as one
  observer-consistent tuple,
  and retires an outgoing VM if cancellation arrives after its shutdown has
  begun. Failed initial adoption clears the tentative selection without
  disturbing a previously hosted service.
- **Modal keyboard and focus routing.** Editable modal controls receive arrow,
  Backspace, Tab, and Shift+Tab input before app-level navigation, and closing
  a modal restores the VMx focus slot without a delayed widget projection
  overwriting it or a newly opened modal.
- **Keys no longer act on the screen behind an overlay.** An open modal now
  swallows Enter, the arrow keys, Backspace, and Left/Right even when it
  implements no handler for them. Previously they fell through to whatever was
  underneath: with the help overlay open, Enter descended into the highlighted
  directory and the arrows moved a pane cursor the user could not see, and on a
  service page Enter could commit a row activation or a service handoff that was
  never visible. The help overlay and Quick Look also scroll with the arrow keys
  now, so the help text below the fold — the whole App section and the docs
  links — and all but the first screenful of a preview are reachable without a
  mouse.
- **A refused Athena cancel stays on screen.** Stopping a query without
  `athena:StopQueryExecution` reported the refusal and then erased it on the
  next poll tick, leaving no sign the query was still running and billing. The
  message now persists until the execution actually settles.
- **Copying a symlink reports the real reason.** Under rename-on-conflict, a
  source the app refuses to copy was retried as though it were a destination
  name collision — a long stall ending in "no available destination name"
  instead of "refusing symlink". Source refusals now fail immediately and say
  what happened.
- **Athena pagination freshness.** Refresh invalidates in-flight continuation
  loads so stale pages cannot append results after newer query state arrives.
- **Resilient shutdown.** Teardown continues after individual cleanup errors,
  reports every failed step, and flushes/closes structured logging last.
- **Maintenance correctness.** CI now verifies the lowest supported dependency
  set, S3Mock seeding creates only genuinely missing buckets, wiki publication
  distinguishes changes from command failures, installed help uses published
  documentation URLs, dead status-bar references are rejected, and current
  command/contract guidance matches runtime behavior and VMx 3.23.
- **Accessible chrome and docs.** Banner title tokens, Solarized muted text,
  and documentation accent colors now meet their intended contrast thresholds.
- **Runtime lifecycle and interaction hardening.** Athena now drains the real
  provider task behind cancelled query/result commands, Settings has a complete
  forward/reverse Tab ring, command-palette empty-result and repeated-open flows
  remain stable, Help reflects the active keymap, EMR logs retarget immediately,
  source swaps avoid invalid authentication fallbacks, and startup failures are
  redacted before the CLI exits with status 1.
- **Maintenance reliability and data-integrity pass.** Cross-filesystem moves
  retain sources that were skipped, S3 batch deletion reports partial service
  failures, gzip log reads enforce decompressed-size and line bounds, and AWS
  clients plus content-host setup tasks now drain cleanly during teardown.
  Overwrites stage files and complete directory trees before replacing an
  existing destination; S3, EMR log, and EMR Serverless pagination raises a
  typed provider error on missing or repeated continuation tokens instead of
  returning partial data. Keychain updates stage new credentials before
  switching the config reference, preserving the active credentials when a
  write fails.
  Failed source rebuilds restore the prior source, EMR run refreshes clear
  stale detail state, command-palette failures reach the toast/log surfaces,
  modal keyboard activation follows the visible safe default, and release/CI
  gates now enforce documentation contracts alongside the runtime tiers.
- **S3-compatible secrets use the configured keychain.** Settings persistence
  stores credentials by reference, removes superseded entries, and rolls back
  cleanly when config persistence fails.
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
- **Transfer journals describe shipped behavior.** Automatic resume, startup
  prompting, and remote abort controls remain deferred. Unfinished journal
  files are retained as local diagnostic evidence; terminal outcomes remove
  their own journal promptly.
- **Recovered service failures are durably logged.** S3/local, EMR Serverless,
  Glue/Iceberg, and Athena view models publish redacted operation diagnostics
  through the shared VMx message hub while preserving their user-facing error
  states.
- **Settings S3-compatible save failures remain recoverable.** The inline form
  uses the unified toast taxonomy and preserves the active connection when
  persistence fails.
- **Non-SSO AWS profiles reach live boto credential validation.** Shared
  credentials, `credential_process`, env, and role-backed profiles no
  longer fall back to local panes as `no AWS credentials` merely because
  they have no SSO cache key.
- **AWS auth failure surfaces mask credential-process stderr.** S3 and
  EMR Serverless now map ``CredentialRetrievalError`` to generic auth
  messages, preserving the recovery hint without echoing raw helper
  output. Startup also treats unreadable SSO cache files as a failed
  initial probe and falls back to local panes instead of crashing.
- **Visible error and endpoint labels redact URL secrets.** EMR client
  construction and context-entry failures now route through the domain
  error mapper, EMR and S3 pane placeholders plus toasts redact raw
  exception text, S3-compatible pane titles / Settings rows / reprs drop
  endpoint userinfo, query strings, and fragments, and the Settings form
  rejects those unsafe endpoint URL shapes plus malformed ports.
- **S3-compatible credential hardening.** Settings uses normalized
  form-to-config mapping, blank optional session tokens resolve
  as absent across static/env/keychain/profile sources, and
  ``S3CompatForm`` / ``ConnectionEntry`` reprs mask static credentials.
- **Crash reports now log through the configured JSON log sink.** The
  `crash.captured` event records exception type, dump path, and the last
  recorded action; wired app actions now populate the action ring before
  dispatch.
- **EMR Serverless service package facade now matches S3.**
  `from aws_tui.services.emr_serverless import EmrServerlessService`
  works and is pinned by an import-contract test.
- **VMx public-surface adoption cleanup.** Focus coordination no longer
  clears VMx's private modal stack, ContentHost setup tasks are cancelled
  on teardown, and EMR Job Runs pagination resets by replacing the VMx
  pager instead of mutating its private token/item storage.
- **App stdlib warnings now land in the JSON log sink.** The composed app
  opts into an `aws_tui.*` logging bridge so config/keymap fallback,
  resume-abort, and AWS client-shutdown warnings are captured
  with the same structured redaction as direct `LogSink` calls.
- **Settings focus avoids private Textual imports.** SettingsView now
  locates the first public focusable child in the Connections section
  instead of importing Textual's private collapsible title widget.
- **Help modal key coverage caught up with shipped bindings.** The modal
  now lists Settings, theme cycling, delete, extend-selection, and pane
  source-cycling shortcuts that were already present in the app.

### Docs

- **Docs coherence after the audit pass.** The contract ledger's Glue
  job-runs gap row and the release summary flow and rollback notes now match
  the code and the promotion-only policy; the lifecycle recipe is rerunnable
  and no longer `exit`s the reader's shell; the Glue paging note states the
  200-row job-run page and the Iceberg tab; the EMR token note states the
  open-form intent boundary.
- **Source-cycle order.** `docs/connections.md` §4 now describes the order
  the resolver actually produces: `local`, then explicit `[connections.*]`
  entries in config-file order regardless of kind, then auto-discovered AWS
  profiles not shadowed by an explicit entry. The old example grouped all AWS
  profiles ahead of all s3-compatible endpoints.
- **Environment-variable cross-references.** `docs/platforms.md` pointed at
  a README "Environment variables" section that no longer exists, and
  `docs/configuration.md` claimed the README still carries the tables. Both
  now point at the Configuration Reference.
- **Table-handoff diagram names the right key.** The diagram said `y` starts
  "Query table in Athena"; `y` copies the table reference and `Shift+Q`
  queries it. Master corrected, SVG/PNG regenerated, and a contract test now
  pins the label to the registered binding.
- **Release recipe follows the branch policy.** `docs/RELEASING.md` now cuts
  the release branch from `develop`, merges it back to `develop`, and reaches
  `main` through a merge-commit promotion PR, matching CONTRIBUTING §5. The
  old recipe branched from `main` and would have shipped without unpromoted
  `develop` work.
- **TestPyPI rehearsal installs into the environment it creates.** The recipe
  now seeds pip with `uv venv --seed` and invokes the environment's own
  `python -m pip`; the previous bare `pip` had no pip in that venv and fell
  through to whichever interpreter was first on `PATH`.
- **Lifecycle recipe no longer clobbers existing rules.** The MPU-abort recipe
  in `docs/connections.md` now fetches the bucket's current lifecycle
  configuration, appends the rule with `jq`, and puts the merged document,
  because `put-bucket-lifecycle-configuration` replaces every existing rule.
  The JSON is now literal JSON rather than a commented `jsonc` block.
- **Three-surface layout standardized, landing poster repaired (#198).** The
  in-repo docs, the generated site, and the wiki now share one section
  hierarchy, and the landing poster renders on every surface.
- **Project opener.** `assets/aws-tui-poster.png` is embedded above the demo
  screenshot in `README.md` and the documentation landing page.
- Added current S3, EMR Serverless, Glue/Iceberg, and Athena service guides; a
  VMx 3.23 compatibility and line-count audit; refreshed consumed-contract and
  release checklists; and regenerated the four architecture diagrams from the
  runtime's current ownership and lifecycle boundaries.
- Added an installation guide, corrected the documentation inventory and
  implementation-status records, and made `docs-check` reject stale or
  unexpected committed architecture-diagram assets.
- ``docs/superpowers/specs/`` — added the demo-mode and
  cross-platform-readiness design specs (in ``fc55c6a``).
- ``SECURITY.md`` — supported-version table now distinguishes the
  pending 0.8.x line from the latest tagged 0.7.x release;
  ``docs/homebrew-bootstrap.md`` adopts the §3.9 numbered heading
  mandate; ``docs/recording-todo.md`` tracks the v0.9.0 development
  documentation captures.
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
- `docs/superpowers/plans/` now has a numbered index of plan documents,
  and recent VMx adoption plan/spec headings are hierarchically numbered.

### Build

- Pages and wiki publication now also trigger on `CONTRIBUTING.md`,
  `SECURITY.md`, and `CODE_OF_CONDUCT.md`; the manifest publishes all three,
  but the workflow's path filter only watched `docs/**`. A guard test now
  derives the required triggers from `docs/manifest.yaml`.
- Bumped `astral-sh/setup-uv` from v10.0.1 to v10.1.0, `ruff-pre-commit`
  from v0.16.6 to v0.16.8, and the Adobe S3Mock harness image from 5.2.0 to
  5.2.2 across the compose file, the integration conftest, the cookbook, and a
  new dated pass in `docs/contract-ledger.md`. Also moved the unledgered
  development-only `cairosvg`, `fonttools`, and `ruff` lock entries. Every
  new ref was verified against its upstream tag or registry manifest.
- Bumped `actions/deploy-pages` from 5.0.0 to 5.0.1 and recorded the new pin in
  `docs/contract-ledger.md`.
- Recorded the removal of the resume and first-run modal flows.
  `ResumeModal`, `ResumeVM`, `FirstRunModal` and `FirstRunVM` had been
  deleted without a Removed entry, while the deferred roadmap continued
  to describe them as built and merely unwired — presenting work that no
  longer existed as resumable.
- Widened the `reactivex` requirement from `>=4,<5` to `>=4,<6` and moved the
  lock to 5.1.0. This changes what a downstream `pip install aws-tui` may
  resolve, so it is an install-surface change, not just a lock bump. The
  lowest-supported-dependencies tier still exercises the 4.x floor.
- CI, release, Pages, and bootstrap now enforce the lockfile with `--locked`;
  the declared development graph includes `textual-dev`; workflow and
  pre-commit actions use verified immutable refs; and wheel/sdist publication
  rejects repository metadata, local caches, tests, and traversal members
  before upload.
- Removed blanket integration-test reruns so failures remain observable,
  added a stable aggregate `ci gate`, expanded installed-wheel smoke tests,
  and gated Homebrew publication until the tap is explicitly enabled.
- Tracked `platformdirs` and `sqlglot` to their current locked versions (see
  `uv.lock` and the dependency row of `docs/contract-ledger.md`), and aligned
  botocore retry configuration on an explicit total-attempt budget.
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
- TestPyPI release dry-run docs now download only the aws-tui rehearsal
  artifact from TestPyPI and resolve dependencies from real PyPI during
  the local install check.
- Pre-commit now includes ShellCheck for repository shell scripts via
  `shellcheck-py`, matching the maintenance verification path.

`````
