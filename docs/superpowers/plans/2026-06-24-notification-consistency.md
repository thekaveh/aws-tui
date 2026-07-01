# 1. Notification consistency plan

**Status:** proposal — awaiting user review before any migration work.
**Author:** assistant, 2026-06-24.
**Trigger:** user feedback after PR #72: "the toast styles are not consistent
either! the toast style for the theme change is not the same as the one for
the source fallback announcement and the ones for the copy progress reports.
… You may even classify them as announcements, questions, confirmations,
errors, and so on…"

The current notification surface is **functionally working but visually and
linguistically inconsistent**. We have toast, modal, transfer-row, and inline
helpers all evolving independently. The goal of this plan is to lock down one
classification + one visual grammar so future work doesn't drift again.

This plan describes the target state and a phased path to get there. **No
code in this plan ships yet** — the user picks which phases to implement.

---

## 1.1. Current state (inventory)

### 1.1.1. Toast call sites (12 distinct call sites across the codebase)

| Call site                                    | Current level | Current text                                                 | Timeout | Sticky |
|----------------------------------------------|---------------|--------------------------------------------------------------|---------|--------|
| `_raise_swap_skip_toast`                     | INFO          | `Skipped unreachable: minio-old`                             | 3s      | no     |
| `_raise_theme_changed_toast`                 | INFO          | `Theme changed to: [carbon]`                                 | 2s      | no     |
| `_raise_attempt_toast` (boot chain)          | INFO          | `▸ Trying dev-sso (AWS) — 1/3…`                              | sticky  | yes    |
| `_raise_success_toast` (boot chain)          | SUCCESS       | `✓ Connected to dev-sso`                                     | 4s      | no     |
| `_raise_failure_toast` (boot chain)          | WARNING       | `✗ dev-sso SSO token expired — trying next…`                 | 6s      | no     |
| `_raise_local_fallback_toast` (boot chain)   | WARNING       | `All configured sources unavailable — both panes…`           | 12s     | no     |
| `_mount_local_only_dual_pane` legacy reasons | WARNING       | `AWS SSO expired for profile. Both panes fell back…`         | 12s     | no     |
| `_mount_initial_service_view` failure        | ERROR         | `Could not mount the S3 view: ExceptionName. See log…`       | n/a     | yes    |
| `_swap_skip_toast` cycle-skip                | INFO          | `Skipped unreachable: minio-old`                             | 3s      | no     |
| `s3_connections_panel._raise_error_toast`    | ERROR         | (caller-supplied)                                            | 4s      | no     |
| `_on_pane_state_changed` ad-hoc              | various       | inline call sites                                            | various | mixed  |

### 1.1.2. Modal call sites

| Modal                | Role                                          | Buttons / shape                              |
|----------------------|-----------------------------------------------|----------------------------------------------|
| `ConfirmModal`       | Yes/no decision (copy, delete)                | Cancel + Confirm/Delete                      |
| `FirstRunModal`      | Choose how to set up first source             | add aws / add s3-compatible / skip           |
| `ResumeModal`        | Choose how to handle unfinished transfers     | resume / abort / decide each / keep          |
| `CrashModal`         | Show the crash report and recover             | continue / quit                              |
| `ThemePickerModal`   | Pick a theme from a list                      | OptionList + apply                           |
| `QuickLook`          | Preview a file                                | (no buttons — Esc closes)                    |

### 1.1.3. Inline / chrome surfaces

- `TransferRowWidget` — live progress bar with state-colored left border
- `Pane` — `endpoint unreachable — press r to retry` error pane
- Boot-time "no connections configured" placeholder

### 1.1.4. Observed inconsistencies

1. **Icons / glyphs** — boot-chain uses `▸ ✓ ✗`; theme-change, skipped-unreachable,
   error-mount use no icon at all. Other call sites use ad-hoc decoration or none.
2. **Subject framing** — sometimes the message leads with the subject ("Theme
   changed to: …"), sometimes with the verb ("Skipped unreachable: …"), sometimes
   with the actor ("Could not mount the S3 view: …").
3. **Bracket conventions** — `[carbon]` for theme name, `[b]r[/]` for inline bold
   keys, `[…]` (literal brackets) for action chips. Three meanings, one syntax.
4. **Action call-outs** — Some toasts embed a recovery hint ("Press [b]r[/] inside
   the pane"), others don't. No rule about where it goes.
5. **Timeouts** — 2s, 3s, 4s, 6s, 12s, sticky. No tier policy.
6. **Voice** — Mixed telegraphic and prose.

---

## 1.2. Proposed taxonomy

Six notification **classes** mapped to existing **levels** + new **surface rules**.

| Class            | Purpose                                                   | Surface              | Lifetime              | Glyph | Level enum            |
|------------------|-----------------------------------------------------------|----------------------|-----------------------|-------|-----------------------|
| **Announcement** | A benign event just happened. No action needed.           | toast                | 3 s                   | `›`   | `INFO`                |
| **Progress**     | Live update on an in-flight operation.                    | toast (sticky) or row| sticky-until-resolved | `…`   | `INFO`                |
| **Success**      | A user-initiated operation completed cleanly.             | toast                | 3 s                   | `✓`   | `SUCCESS`             |
| **Advisory**     | Something is off but we made a reasonable choice. May warrant action later. | toast | 6 s                   | `⚠`   | `WARNING`             |
| **Error**        | A failure happened; user must act or read the log.        | toast (sticky)       | sticky                | `✖`   | `ERROR`               |
| **Confirmation** | Block on a yes/no decision before proceeding.             | `ConfirmModal`       | until user decides    | n/a   | (no toast level)      |
| **Question**     | Multi-option choice (3+ options).                         | dedicated modal      | until user decides    | n/a   | (no toast level)      |

**Rationale.** Six classes is a tractable ceiling — enough to give the visual
grammar room to differentiate, few enough to remember. The mapping to existing
`ToastLevel` enum stays 1:1 for the four toast classes; `Confirmation` and
`Question` are modal-only so they don't need a level.

Notes:

- `Progress` shares the `INFO` level with `Announcement` but differs by lifetime
  and (proposed) glyph. The grammar makes it visually distinct.
- `Success` is its own class so the user gets explicit positive feedback for
  things they did (vs. things that just happened); maps to the existing
  `SUCCESS` level.
- The boot-chain's "pre-attempt" sticky toast is `Progress`; its outcome
  toasts are `Success` (good) or `Advisory` (failed-but-continued) or `Error`
  (failed-and-final).

---

## 1.3. Proposed visual grammar (toasts)

Single-line format:

```
<glyph> <subject>: <message>[ — <action>]
```

Examples by class:

```
›  Theme: switched to carbon
…  Connection: trying dev-sso (1/3)
✓  Connection: dev-sso connected
⚠  Source: dev-sso unavailable — trying next
⚠  Fallback: both panes set to local — press r to retry dev-sso
✖  Mount: S3 view failed — see ~/.cache/aws-tui/log/aws-tui.log
```

Rules:

1. **Always lead with a glyph** (one of `› … ✓ ⚠ ✖`). One space after.
2. **Subject is one word** (or a hyphenated compound) followed by `:`. Acts
   as the "channel": `Theme`, `Connection`, `Source`, `Fallback`, `Mount`,
   `Transfer`, etc.
3. **Message is sentence-case, no terminal period.** Allows append of `— action`.
4. **Action call-out** is optional. Always last. Prefixed by ` — ` (space, em-dash,
   space). Keys / commands use `[b]…[/]` markup.
5. **No brackets around values** (`carbon`, not `[carbon]`); reserve `[]` for
   action chips in the model.

### 1.3.1. Subject vocabulary (canonical)

To prevent drift, define a fixed set of subjects:

- `Theme` — theme switched / failed to load
- `Connection` — single connection attempt status
- `Source` — multi-source fallback narration
- `Fallback` — system-level fallback decision
- `Mount` — view mount/unmount failures
- `Transfer` — copy/move/delete batch outcomes
- `Settings` — config-store CRUD outcomes
- `Auth` — SSO / credentials prompts

New subjects require updating this list (and the typing — see §6).

---

## 1.4. Proposed modal grammar

`ConfirmModal` already has the canonical shape after PR #72:

```
Title?                  (← .modal-title, $accent + margin-bottom 1)

Subject                 (← .modal-path-label, $accent + bold)
  /path/value           (← .modal-path-value, $text inside outlined box)

Body line(s)            (← .modal-body)

         [ Cancel ]  [ Confirm ]   (← .modal-footer, right-aligned)
```

Other modals (`FirstRunModal`, `ResumeModal`) should follow the same
hierarchy — title + body + buttons — but currently differ in:

- `FirstRunModal` has 3 horizontal buttons (one primary, two default)
- `ResumeModal` has 4 horizontal buttons (one primary, one danger, two default)

**Proposed normalization:**

- Use the same `.modal-title` / `.modal-body` / `.modal-footer` slot classes
  across all three modals.
- Standardize button focus behaviour to PR #72's pattern: every button looks
  deselected at rest; the focused one lights up only on hover or after the
  first arrow / Tab press.
- For `Question`-class modals with 3+ buttons (FirstRun, Resume), keep the
  primary-variant suggestion but no auto-focus on mount.

---

## 1.5. Timeout / sticky policy

| Class            | Default timeout    | When to override                                                    |
|------------------|--------------------|---------------------------------------------------------------------|
| Announcement     | 3 s                | shorter for high-frequency cosmetic events (theme: 2 s)             |
| Progress         | sticky-until-done  | always sticky; outcome toast replaces by id                         |
| Success          | 3 s                | longer if accompanied by an actionable suggestion                   |
| Advisory         | 6 s                | longer (12 s) when an action hint is embedded the user must read    |
| Error            | sticky             | always sticky — user dismisses or fixes                             |
| Confirmation     | (modal — n/a)      | —                                                                   |
| Question         | (modal — n/a)      | —                                                                   |

---

## 1.6. Implementation plan (phased)

### 1.6.1. Phase A — taxonomy + helper API (no behavioural change)

Add a thin layer over `ToastStackVM.raise_toast` so call sites pick a class,
not a level + timeout + glyph + format. New helper in `aws_tui.ui.notifications`
(or similar):

```python
def announce(stack, *, subject: str, message: str, action: str | None = None) -> None: ...
def progress(stack, *, key: str, subject: str, message: str) -> ToastVM: ...
def success(stack, *, subject: str, message: str, action: str | None = None) -> None: ...
def advise(stack, *, subject: str, message: str, action: str | None = None,
           timeout: float = 6.0) -> None: ...
def error(stack, *, subject: str, message: str, action: str | None = None) -> None: ...
```

Each helper composes the glyph + subject + message into the canonical
single-line format, picks the right `ToastLevel`, and applies the default
timeout for that class. `progress` returns the `ToastVM` so callers can
dismiss it explicitly on outcome.

Call sites stay on `raise_toast` initially — the helpers wrap and call it.
This phase ships zero user-visible change.

### 1.6.2. Phase B — migrate boot chain + theme change + skip toasts

Rewrite the existing call sites in `app.py` to use the helpers. The boot-chain
narration is the largest single migration (6 of the 12 call sites). User-
visible deltas:

- `Theme changed to: [carbon]` → `›  Theme: switched to carbon`
- `▸ Trying dev-sso (AWS) — 1/3…` → `…  Connection: trying dev-sso (1/3)`
- `✓ Connected to dev-sso` → `✓  Connection: dev-sso connected`
- `✗ dev-sso SSO token expired — trying next…` → `⚠  Source: dev-sso unavailable — trying next`
- `Skipped unreachable: minio-old` → `⚠  Source: skipped unreachable minio-old`

Update snapshot baselines.

### 1.6.3. Phase C — migrate error + mount + settings toasts

Rewrite the remaining call sites + `s3_connections_panel`.

### 1.6.4. Phase D — modal normalization

Apply PR #72's "no auto-focus on mount" pattern to `FirstRunModal` and
`ResumeModal`. Ensure they use the same `.modal-title` / `.modal-body`
slot classes. Update snapshots.

### 1.6.5. Phase E — subject typing

Replace the `subject: str` parameter with a `Literal[…]` so a typo
(`subject="Conection"`) fails at mypy time. Adds the canonical subject list
to a single source of truth (`NotificationSubject` enum).

---

## 1.7. What this plan deliberately does NOT do

- **Touch the transfer overlay.** `TransferRowWidget` is its own surface
  with a different problem shape (long-running, state-machine UI). Worth a
  separate review.
- **Add notification history / "see all" pane.** Toasts are still ephemeral.
- **Change the modal layer or push-screen plumbing.** Only the visual + text
  layer.
- **Localize / i18n.** Strings stay in English in the helper signatures.
- **Add sound / OS notifications.** Out of scope.

---

## 1.8. Open questions for the user

1. **Glyph set.** Proposed `› … ✓ ⚠ ✖`. Alternative: `i … ✓ ! ✖` (no Unicode
   risk on terminals that fall back). Pick one.
2. **Subject style.** Proposed capitalized one-word + colon (`Theme:`).
   Alternative: lowercase, no colon (`theme — switched to carbon`).
3. **Modal focus rule for `FirstRunModal` / `ResumeModal`.** PR #72's
   "no button selected on mount" works for ConfirmModal because Esc is always
   the safe default. For first-run, a user with no keyboard mental-model might
   not realize they need to press Tab. Maybe keep auto-focus there?
4. **Migration scope.** Implement all phases, or stop after Phase B (boot
   chain + theme change)?
5. **Should error toasts auto-dismiss after some long timeout** (e.g. 30 s)
   in addition to allowing manual dismiss?

---

## 1.9. Next step

User reviews this plan. Pick a glyph set, decide on subject style, scope the
migration (phases A–E or a subset), and answer §8 questions 3 + 5. Then we
ship Phase A as a single PR to establish the helper API without any visual
change, followed by per-phase migrations.
