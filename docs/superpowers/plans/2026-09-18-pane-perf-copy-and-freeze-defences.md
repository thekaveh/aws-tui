# Pane Performance, Copy Rebuild and Freeze Defences Implementation Plan

> **For agentic workers:** this plan is executed by orchestrated workflows. Each task below is a self-contained brief. The full design evidence lives in `/private/tmp/claude-501/-Users-kaveh-repos-aws-tui/43dfb6b7-b5b9-4e95-92d4-937fe286f249/scratchpad/DESIGN.json` — read the relevant `fix_*` field before implementing.

**Goal:** make the file-manager panes fast enough that a large listing never looks like a hang, make copy tell the truth and look deliberate, add zebra striping, and close every proven path to an unresponsive or unquittable app.

**Architecture:** the panes stop subscribing per row and become imperatively driven by their owning `Pane`; mounting becomes one batched call; the four redundant re-renders per navigation coalesce into one; the VM's collection rebuild is wrapped in a VMx batch. Copy gains a real `infra` clipboard port with a native fallback and honest reporting. Robustness fixes close the modal focus-projection wedge, the unquittable-modal amplifier, and the terminal-protocol traps.

**Tech Stack:** Python 3.12/3.13, Textual 8.2.8, VMx 3.23.0, pytest, pytest-textual-snapshot, uv.

**Branch:** `fix/ui-copy-freeze-zebra`, worktree `/Users/kaveh/repos/aws-tui/.worktrees/ui-fixes-2026-09-18`, based on `develop` at `b1120acc`.

---

## 1. Global Constraints

Copied verbatim from the investigation's binding constraints. Every task inherits all of them.

1. **Layering** is enforced by `scripts/check-layers.sh` (CI job, release, and pre-commit). `infra/` must NOT import textual, `aws_tui.domain`, `aws_tui.vm`, `aws_tui.ui`, `aws_tui.services`, `aws_tui.demo`.
2. The clipboard Protocol, the native implementation and the in-memory fake live in **one** `infra` module, following `src/aws_tui/infra/keychain.py:32-60` exactly. The port must not call `App.copy_to_clipboard`; the OSC 52 write stays in `app.py`.
3. **Subprocess rules** (there is no subprocess in `src/` today, so this IS the convention): `shutil.which` probing, argv lists only, never `shell=True`, explicit ~2 s timeout, `check=False`, stdout/stderr to `DEVNULL`. Windows `clip` needs **UTF-16LE**. Tests must never invoke a real `pbcopy`/`xclip`/`clip`.
4. **Never log the clipboard payload.** Log event name + mechanism + `error_type=type(exc).__name__` (precedent `app.py:3511-3514`).
5. Offload blocking work with `anyio.to_thread.run_sync`, never `asyncio.to_thread`. Textual workers need `exclusive=True, group=<name>`.
6. Every toast goes through `ui/notifications.py` helpers, never `self.notify()` (`app.py:3225-3227`: it "wrecks the footer"). Removing the four `self.notify` calls (`app.py:2228`, `:2243`; `pane.py:384`, `:395`) is part of the fix.
7. `notifications.Subject` is a closed mypy `Literal` (`ui/notifications.py:57-68`). Reuse `"Source"`.
8. `AppContext` uses `__slots__` (`composition.py:66-89`): a new field goes there AND in `__init__` with a `None` default that self-constructs. Do **not** add it to `close_unstarted` or `_aws_tui_shutdown` — `tests/unit/test_app_sanity.py:430-438` asserts the dispose event list exactly.
9. **Do NOT use `:odd`/`:even`** for zebra. Measured catastrophic: 3,000 rows 0.956 s → 18.303 s. Use an explicit `-alt` class.
10. The zebra rule MUST be declared **before** `Pane .entry-row.-selected` and `.-marked` in every theme. Both score specificity `(0,2,1)`; `Stylesheet.apply` iterates `reversed(self.rules)` then takes `max()`'s first maximal element, so **the later source rule wins**. Nothing but source order protects the cursor row.
11. The zebra rule declares `background:` **only**. No `color:`, no `text-style:`.
12. Scope zebra as `Pane .entry-row.-alt`, never bare `.entry-row.-alt` — `NavRow` merges the literal `entry-row` class (`nav_row.py:75`) and has no `Pane` ancestor.
13. All 10 built-in `.tcss` files get the token and the rule. Put them in the per-theme files, **not** `operational-panes.tcss` (`ThemeStore.load` skips that layer when a user replacement theme exists).
14. Theme tokens must be lowercase 6-digit hex, own line, semicolon-terminated, column-aligned — `tests/unit/ui/test_themes.py:110-116` parses exactly `^\s*(\$[\w-]+):\s*(#[0-9a-fA-F]{6});`.
15. Theme tokens live in `.tcss` overlays, never in `DEFAULT_CSS`. `EntryRow.DEFAULT_CSS` keeps `height: 1` with **no** horizontal padding.
16. **Emoji rule** (PR #76 → #77 → #79, restated `pane.py:472-477`): any emoji in TUI chrome must be an SMP single codepoint rendering 2 cells. Verified `cell_len`: `📋`=2, `⎘`=1, `⧉`=1. Removing U+1F4CB is fine; replacing it with a BMP glyph is not.
17. `border_title` is unconditionally parsed by `Content.from_markup` — every assigned value stays `rich.markup.escape`'d. Placeholder `Static` keeps `markup=False`.
18. `_render_body` must keep mounting `vm.filtered_entries`, never `vm.entries` (`pane.py:245-249`).
19. `Pane.on_mount` must NOT call `_render_body` synchronously — it stays behind `call_after_refresh` (`pane.py:349`); synchronous mounting raises `MountError` on the offline-MinIO boot path.
20. `_replace_entries` ordering is load-bearing (`pane_vm.py:996-1011`): `_recompute_filtered()` MUST run before `self._cursor_index = 0`. Keep that sequence **outside** any batch.
21. `MessageHub.batch()` DEFERS but does not coalesce — it will NOT reduce fan-out. Only reducing the subscriber count fixes the quadratic. `CompositeVM.batch_update()` DOES coalesce.
22. `HubSubscriberMixin` installs no `on_unmount`; every consumer must keep calling `unsubscribe_from_vm()` from its own `on_unmount`.
23. Any Pane test must `await pilot.pause()` **twice** (`tests/unit/ui/test_pane_widgets.py:52-57`).
24. **Never assert wall-clock time.** Assert `len(hub._subject.observers)` is invariant across two row counts, with `assert len(app.query(EntryRow)) == n` as a named precondition.
25. Pair every new/changed widget snapshot with a content-presence guard test that reads the `.raw` and asserts literal substrings.
26. Snapshot goldens go **stale**, not platform-drifted. Re-record deliberately; never hand-edit. **Re-record on Python 3.12** (`uv run --python 3.12 pytest tests/snapshot --snapshot-update`) — CI's snapshot job is 3.12 on ubuntu-24.04 and macos-14.
27. Do NOT restore `pytest-rerunfailures`. Drain with `while app.workers._workers:` (`tests/helpers.py::drain_workers`).
28. Do not settle a snapshot by calling a private render method — `tests/docs/test_snapshot_harness.py:6-13` forbids it.
29. An action id lives in six source places at once: `KeymapStore.DEFAULT_BINDINGS`, the `app.py` registry call, `ui/bindings.py` `_ACTION_DESCRIPTIONS`/`_VISIBLE_ACTIONS`, `hint_legend_vm.py` (page tuple + `_ACTION_LABELS` + **mandatory** `_ACTION_EFFECTS` + priorities), optionally `app.py` `_PALETTE_COMMANDS`, and `docs/keybindings.md` **both** tables.
30. The three hint-legend dicts have byte-exact in-test mirrors (`tests/unit/vm/chrome/test_hint_legend.py:43-125`).
31. `KeymapStore.APPROVED_ALIAS_PAIRS` tuples must be alphabetically sorted within the tuple.
32. Never write `pane.copy = "y"` in docs — the canonical example is `"pane.copy" = "ctrl+y"`.
33. Every ledgered behaviour change lands in the **same** commit series as the code or the `documentation contracts` CI job fails. Edit only the canonical `docs/*.md`.
34. `mypy --strict` covers `src/aws_tui` plus `tests/unit/ui/test_pane_widgets.py` and `tests/unit/vm/file_manager/test_pane_vm.py`.
35. Commit trailer is exactly:
    ```
    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_0116VJJGYni8rv7bY8E2xpw4
    ```
36. **Golden re-recording is deferred to Task 12.** Tasks 1–11 must NOT run `--snapshot-update`. Snapshot failures between tasks are expected and are fixed once, at the end.

---

## 2. What was wrong, and what each task fixes

| # | Defect | Severity | Task |
|---|---|---|---|
| A | `EntryRow` holds 2 hub subscriptions each; hub has no sender routing → `6+2N` observers, `O(N²)` | perf | 1 |
| B | `_render_body` removes and mounts rows one-by-one → `O(N²)` scheduled callbacks | perf (dominant) | 1 |
| C | One `navigate_to` schedules 3–4 full teardown+remount cycles | perf ×4 | 2 |
| D | `PaneVM._replace_entries` emits one `CollectionChangedEvent` per entry | perf | 2 |
| E | `app.py` `_run_copy`/`_run_delete` call `entry.set_marked()` directly, bypassing any notify | **correctness, introduced by A's fix** | 3 |
| F | No zebra striping | cosmetic | 4 |
| G | `_put_on_clipboard` reports "Copied" unconditionally; OSC 52 does nothing on macOS Terminal | **correctness (the lie)** | 5, 6 |
| H | 📋 emoji in border title; tooltip advertises an undiscoverable click | cosmetic | 7 |
| I | Deferred focus projection writes a base-screen widget into a modal screen's `focused` → modal cannot be closed | **hard wedge, ours** | 8 |
| J | `_MODAL_ROUTED_ACTIONS` lacks `app.quit` → any stuck modal makes the app unquittable | **amplifier, ours** | 8 |
| K | `AppBlur` leaves a stale tooltip painted and an orphaned mouse capture | cosmetic | 9 |
| L | Bracketed-paste parser wedge: a >100 ms gap inside `\x1b[201~` strands the parser permanently deaf | **hard wedge, upstream** | 10 |
| M | `textual` unpinned as a runtime dependency while every freeze finding is version-specific | supply chain | 11 |
| N | In-band resize (DEC 2048) disables the SIGWINCH fallback → stale geometry after a macOS Space change | **best fit for the user's report** | 11 |

---

## 3. Task 1: One subscription per pane, batched mount

**Files:** `src/aws_tui/ui/widgets/pane.py`; tests `tests/unit/ui/test_pane_widgets.py`.

Read `DESIGN.json` → `fix_1_fanout` steps A1–A5, B6–B9, B11, B13 and apply them verbatim. Summary:

- `EntryRow` drops `HubSubscriberMixin`, drops the `hub` kwarg, drops `on_mount`'s subscribe block, `on_unmount`, and `_on_entry_changed`. It gains `_state_cache` and a public `sync_state(*, is_selected, is_marked)` that early-returns on no change, calls `set_class` twice and then `refresh()` (the refresh is **not** optional — `render()` draws `cursor_glyph`/`mark_glyph`).
- Keep the `merged = " ".join(...)` classes line at `pane.py:113` verbatim; Task 4 depends on it.
- `Pane` gains `_rows: list[EntryRow]`, `_cursor_row: int | None`, `_body_refresh_pending: bool`.
- `_render_body` uses `body.remove_children()` and a single `body.mount(*rows)`.
- New `_apply_cursor()` is O(1) and replaces `_scroll_to_cursor` entirely; it reads truth back off `entry_vm` rather than assuming `True`/`False`.
- New `_sync_marks()` called from `_refresh_chrome`; no VM change is needed because every mark mutation already notifies `"viewmodel"`.
- `_reflow_columns` iterates `self._rows`, not `self.query(EntryRow)`.
- Wire `_SCROLL_TRACK_PROPS` to `_apply_cursor`.

**Tests:** add to `tests/unit/ui/test_pane_widgets.py` a subscription-invariance test per Constraint 24, plus behaviour tests that cursor movement and mark toggling still flip `-selected`/`-marked` and still repaint the glyphs. Every pane test pauses twice.

**Verify:** `uv run pytest tests/unit/ui/test_pane_widgets.py tests/unit/vm/file_manager -q`, then `uv run mypy`, `uv run ruff check . && uv run ruff format --check .`.

---

## 4. Task 2: Coalesce the redundant re-renders and batch the VM rebuild

**Files:** `src/aws_tui/ui/widgets/pane.py`, `src/aws_tui/vm/file_manager/pane_vm.py`; tests in both tiers.

- `_on_vm_property_changed`: guard `_BODY_REFRESH_PROPS` with `_body_refresh_pending`; clear the flag as the **first** statement of `_refresh_all`.
- `PaneVM._replace_entries`: wrap **only** the remove/dispose loop and the construct/append loop in `with self._inner.batch_update():`. Keep `_recompute_filtered()` and the `_cursor_index = 0` assignment **outside** the batch (Constraint 20).

**Risk to guard:** the coalescing is a real timing change — four callbacks each rendering an intermediate state become one rendering the final state. Add a test asserting a single `navigate_to` triggers exactly one body render (count `_render_body` invocations via a counter attribute, not a mock of a private method used in snapshots).

Also grep every `on_collection_changed` subscriber and confirm none inspects the event `action`, since `batch_update` coalesces to one `action="reset"`.

---

## 5. Task 3: Close the `set_marked` bypass

**Files:** `src/aws_tui/vm/file_manager/pane_vm.py`, `src/aws_tui/app.py`; tests `tests/unit/vm/file_manager/test_pane_vm.py` and an app-level test.

Four existing callers mutate `EntryVM.is_marked` directly with no notify: `app.py:2356`, `:2386` (`_run_copy`) and `:2464`, `:2486` (`_run_delete`). They flash the cursor-fallback target as marked for the duration of a transfer. Today the per-row subscription repaints them; after Task 1, nothing would.

Add `PaneVM.set_marked_entries(entries, *, marked)` exactly as specified in `DESIGN.json` → `fix_1_fanout` step 10 (skip parent links and no-op entries, notify `"viewmodel"` once if anything changed), and thread the already-computed `src_pane` explicitly through `_confirm_copy` → `_run_copy` and `_confirm_delete` → `_run_delete`. Pass it explicitly rather than re-reading `dual.focused_pane` in the worker — focus can change while the confirm modal is open.

**This is the highest risk in the plan: there is no existing test for the flash.** Write the regression test first and prove it fails without `set_marked_entries`.

---

## 6. Task 4: Zebra striping

**Files:** `src/aws_tui/ui/widgets/pane.py` (one line, already part of Task 1 step 7), all 10 `src/aws_tui/ui/themes/*.tcss`; tests `tests/unit/ui/test_themes.py`.

- `_render_body` builds rows with `classes="-alt" if index % 2 else None`.
- `_apply_state_classes` and `sync_state` must **never** touch `-alt`.
- Each theme gains `$bg-alt` on the line immediately after `$bg:`, keeping the ladder `$bg → $bg-alt → $bg-elev → $bg-sel` and the column alignment.

| theme | `$bg-alt` | theme | `$bg-alt` |
|---|---|---|---|
| carbon | `#101215` | solarized-light | `#f6efdc` |
| voidline | `#080c18` | github-light | `#fafcfc` |
| lattice | `#081a1f` | one-light | `#f4f4f4` |
| amber | `#140e07` | nord | `#343b49` |
| dracula | `#2e303e` | gruvbox-dark | `#32302f` |

- The rule goes between the closing `}` of `Pane .entry-row { … }` and the opening of `Pane .entry-row.-selected {`, carrying the comment that explains the source-order tie-break.

**Tests:** `test_builtin_theme_defines_zebra_token` (all 10 define `$bg-alt` — this parity test does not exist today and must be written); extend `test_muted_text_is_readable_on_both_content_backgrounds` to cover `$bg-alt`; and `test_zebra_rule_precedes_the_selected_rule` asserting `text.index("Pane .entry-row.-alt") < text.index("Pane .entry-row.-selected")` in every theme — this is the only thing between a correct stripe and a cursor row that renders as a stripe.

---

## 7. Task 5: The clipboard port

**Files:** new `src/aws_tui/infra/clipboard.py`; `src/aws_tui/composition.py`; tests `tests/unit/infra/test_clipboard.py`.

Implement exactly the module in `DESIGN.json` → `fix_2_copy` section A: `ClipboardResult`, `ClipboardPort` Protocol, `NativeClipboard`, `InMemoryClipboard`. Candidate resolution by `sys.platform`; on Linux return `mechanism="none"` **without spawning** when neither `WAYLAND_DISPLAY` nor `DISPLAY` is set. Map `FileNotFoundError`, `PermissionError`, `TimeoutExpired`, `OSError` and non-zero `returncode` to `ok=False`.

Wire into `AppContext` per Constraint 8.

**Tests** must never spawn a real helper: inject a fake `which`/`run` (monkeypatch), and cover macOS/Windows/Wayland/X11/headless resolution, the UTF-16LE encoding on win32, every error mapping, and that the payload never reaches the log.

---

## 8. Task 6: One honest writer

**Files:** `src/aws_tui/app.py`, `src/aws_tui/ui/widgets/pane.py`; tests across unit and integration.

- `_put_on_clipboard` becomes `async`, writes OSC 52, then awaits the port via `anyio.to_thread.run_sync`, and reports **three** honest outcomes: native ok → `notifications.success`; native present but failed → `notifications.advise` + `log_sink.warning`; no helper → `advise` naming OSC-52-only. **Never** report OSC-52-only delivery as "Copied".
- `action_copy_entry_path` and `action_copy_path` become `async def`; delete both `self.notify` calls and route "Nothing selected to copy" through `notifications.advise`.
- Delete `Pane._copy_to_clipboard`; add a public `AwsTuiApp.copy_value(value, label)` and have the pane reach it by duck typing. Do **not** delegate to `action_copy_path` — that resolves the *focused* pane and would copy the wrong pane's path on a border click.
- The Glue table-ref path (`app.py:3503-3521`) switches to the port too. This flips `tests/integration/test_glue_athena_navigation.py:493-521` and requires rewording `docs/contract-ledger.md:59` and `docs/architecture.md:145-147,238-239` **in the same commit** (Constraint 33).

**Verify** the async conversion does not change the installed `BindingsMap` tuple asserted in `tests/integration/test_keybinding_wiring.py:30-97`.

---

## 9. Task 7: The affordance

**Files:** `src/aws_tui/ui/widgets/pane.py`, `src/aws_tui/app.py`, `docs/keybindings.md`, `docs/cookbook.md`; tests.

- `pane.py:478`: drop the ` \U0001f4cb` suffix from `border_title`. Do not substitute a BMP glyph (Constraint 16).
- Keep the border click target and keep the tooltip, but reword `pane.py:411` to lead with the key: `f"{vm.copy_path}\n\npress P to copy, or click here"`. Reword `EntryRow._sync_tooltip` similarly.
- Add `pane.copy_entry_path` and `pane.copy_path` to `_PALETTE_COMMANDS` (`app.py:174-284`) — **not** to the hint legend. Adding chips would take s3 from 6 to 8, change `_fit_actions` overflow behaviour at 120 cols (it can silently evict an existing chip), re-record 42 legend goldens, and collide with the contract-pinned "fitted one-line Commands legend" at `docs/contract-ledger.md:60`.
- Update the label sets in `tests/integration/test_command_palette_wiring.py:20-49` and the docs tables.

---

## 10. Task 8: The modal focus wedge and the quit escape hatch

**Files:** `src/aws_tui/ui/widgets/glue/page.py`, `src/aws_tui/ui/widgets/athena/page.py`, `src/aws_tui/ui/widgets/emr_serverless/page.py`, `src/aws_tui/app.py`; tests in `tests/integration`.

- `_focus_projection_available()` (`glue/page.py:594` and its siblings) checks only `is_running and is_attached and display`. Add a screen-identity check so a deferred projection cannot write a base-screen widget into a modal screen's `focused`: the page's own `self.screen` must still be `self.app.screen`. Apply the same guard at every deferred `self.app.set_focus(...)` site.
- Add `app.quit` to `_MODAL_ROUTED_ACTIONS` (`app.py:161-173`) so no modal state can ever be unquittable. Keep every other action gated.

**Testing note (Risk 19):** this reproduced 11/12 headless but 0/13 in a real pty, so it can only be validated by the headless regression shape. Write a test that pushes a modal while a deferred projection is pending and asserts the modal screen keeps focus and closes on Escape. Do not conclude from a green pty run that it is fixed.

---

## 11. Task 9: Clean up on blur

**Files:** `src/aws_tui/app.py`; tests.

Add an `on_app_blur` handler to `AwsTuiApp` that clears the stale tooltip (`Screen._clear_tooltip()` already exists and is already called from `_on_screen_suspend`) and releases any orphaned mouse capture with `self.capture_mouse(None)`. Both are one-liners for real, observed defects: switching macOS Spaces currently leaves a tooltip painted over the pane, and a scrollbar drag interrupted by a blur costs the user one swallowed click.

Note the latent trap for the comment: `ScrollBar._on_mouse_capture` calls `_realtime_animation_begin()` which calls `gc.disable()` when `PAUSE_GC_ON_SCROLL` is true. Textual's default is `False` and `AwsTuiApp` does not override it — that is the only reason the orphan is harmless today.

---

## 12. Task 10: Bracketed-paste defence

**Files:** `src/aws_tui/app.py` or a new `src/aws_tui/ui/_paste_guard.py`; tests.

`XTermParser.parse()` sets `bracketed_paste = True` on `\x1b[200~` and clears it only on an exact `\x1b[201~`. The `except ParseTimeout: send_sequence(); break` path fires with no regard for the flag and does not return consumed bytes to `paste_buffer`, so a >100 ms gap inside the closing marker strands the parser permanently deaf — `q` and Ctrl+C are swallowed and only SIGKILL ends it.

**Do NOT mitigate by refusing bracketed paste** (Constraint: the Athena SQL editor and the EMR log-filter modal are `TextArea`s and pasting a multi-line query is a needed feature).

Implement a defensive guard: a driver/parser subclass that bounds how long `bracketed_paste` may stay set and recovers by flushing the buffer as a `Paste` event rather than staying deaf. Prove it with a test that feeds a split closing marker and asserts the app still answers a key afterwards. Also file the upstream defect in the changelog notes.

---

## 13. Task 11: Terminal protocol hardening

**Files:** `pyproject.toml`, `src/aws_tui/app.py`, `docs/platforms.md`, `docs/contract-ledger.md`.

- **Pin textual at runtime.** `pyproject.toml:17` lists `textual` unpinned as a runtime dependency while `:47` pins `textual==8.2.8` only as a dev constraint. Every freeze finding is version-specific. Pin the runtime range to what CI actually tests and record it in the ledger.
- **Restore the resize fallback.** `linux_driver.py:246-248` ignores SIGWINCH once DEC mode 2048 is negotiated, and `_xterm_parser.py:320-326` negotiates it whenever `SMOOTH_SCROLL and not IS_ITERM`, with `SMOOTH_SCROLL` defaulting to 1. On Ghostty/WezTerm/kitty on macOS the app therefore runs with **no** resize fallback, and a Space change can leave it painting stale geometry — the best-supported explanation of the user's report. Default `TEXTUAL_SMOOTH_SCROLL=0` in `main()` **only when the user has not set it**, so reliable resize wins over a rendering nicety and the behaviour stays overridable. Document the knob in `docs/platforms.md`.

Verify empirically that the default actually restores SIGWINCH-driven resize, and that setting the variable to `1` restores the old behaviour.

---

## 14. Task 12: Re-record goldens, then verify everything

**Files:** `tests/snapshot/__snapshots__/**`, plus any content-presence guards.

Run once, after Tasks 1–11 are all committed:

```bash
uv run --python 3.12 pytest tests/snapshot --snapshot-update -p no:cacheprovider
uv run --python 3.12 pytest tests/snapshot -q -p no:cacheprovider
```

Then **review the diff**, not just the pass. Two named checks:

1. `tests/snapshot/__snapshots__/test_pane_states/*.raw` must come back **byte-identical** — those states have no rows, so any diff there means the zebra rule leaked onto a placeholder or a widget outside `Pane`.
2. Extend the content-presence guards (`tests/snapshot/test_pane_states.py:67-78`, `test_main_screen.py:50-54`) so a pane that renders blank in one theme cannot hide inside a 60-file golden diff.

Full local verification:

```bash
uv run pytest -q -p no:cacheprovider
uv run pytest tests/integration -m integration -q -p no:cacheprovider
make docs-check
uv run mypy && uv run ruff check . && uv run ruff format --check .
```

---

## 15. Out of scope, to be filed separately

- **EMR job-runs crash.** `ValueError: Cannot set current to 'emr.job_run.…': it is not a member of this composite` at `src/aws_tui/vm/emr_serverless/job_runs_vm.py:254` via `job_runs_pane.py:354` → `page.py:439` → `app.py:2008`. Repro: on the EMR page, descend then immediately press Up. Crash dump at `~/.cache/aws-tui/crash/2026-09-18T17-09-33-751261.txt`.
- **Paste re-issues ESC sequences as real keys.** With `bracketed_paste` true, an unrecognised escape inside a paste reaches `reissue_sequence_as_keys` and fires real bindings — a pasted payload made the app quit outright. Destructive bindings (`pane.delete`, `pane.copy`) are reachable this way. Data-loss hazard, upstream, independent of the freeze.
- **Textual upstream defects** worth reporting: the bracketed-paste strand, `_node_list.py:203-211` cache-assignment bug, and `_watch_app_focus` not releasing mouse capture.
