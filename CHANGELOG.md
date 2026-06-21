# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

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
- **Relicensed from MIT to Apache License 2.0.** ``LICENSE`` now
  carries the canonical Apache 2.0 text and is paired with a new
  ``NOTICE`` file (Apache convention for attribution). ``pyproject.toml``
  classifier and ``license`` field, the banner widget's pedigree
  subtitle (which renders bottom-right of the brand banner border
  inside the running app), and the README footer all updated.
  Historical changelog entries that refer to the prior MIT licence
  are deliberately preserved — they're records of project state at
  that point in time, not active declarations.

### Added

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
  presence guard per the [snapshot-test-content-guards lesson](docs/superpowers/specs/2026-06-20-settings-as-first-class-nav-page-design.md).
  This is a rework of the PR #52 modal pattern, not an extension —
  ``SettingsModal``, the gear footer band, and ``S3CompatFormModal``
  are all deleted. The two surviving VMs (``SettingsVM`` simplified,
  ``S3ConnectionsVM`` unchanged) plus the ``ConfigStore`` extensions
  plus ``ConnectionListChangedMessage`` all carry over.

### Fixed

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

### Deferred / v0.8 roadmap

These items are spec'd but explicitly not wired in v0.7.x. They are
tracked so the next minor release can pick them up without rediscovery:

- **Quick Look full-file `$PAGER` shell-out** — `Space` currently
  streams the first 64 KB with a syntax tint; full-file pager hand-off
  per the design spec is pending.
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
