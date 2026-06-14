# aws-tui M6 (Polish + release) Implementation Plan

> Final milestone. Smaller than M5. Closes out v0.1-equivalent feature scope.

**Goal:** Polish the remaining UX pieces — crash modal, transfer journal resume modal, first-run flow — and ship v0.7.0 as the "feature-complete pre-PyPI" release. (Spec §9.7's "v0.1.0" was tagged before the per-milestone tagging convention emerged; we keep the milestone-marker scheme and use v0.7.0 here.)

**Architecture:** Crash handling wraps the Textual `App` in a top-level try/except that captures unhandled exceptions, dumps a `~/.cache/aws-tui/crash/<ts>.txt`, and surfaces a `CrashModal` with options to view trace / continue / quit. The transfer-resume modal is wired into `RootVM.startup()` — after Connection picker resolves, scan `TransferJournal.find_unfinished()` and present a modal. First-run flow triggers when `ConnectionResolver.list()` returns empty AND `~/.aws/{config,credentials}` is also empty.

**Tech Stack:** Existing only — Textual, VMx, the M1–M5 stack.

---

## Task 1: Crash modal + unhandled-exception capture

**Files:**
- Create: `src/aws_tui/vm/chrome/crash_vm.py`
- Create: `src/aws_tui/ui/widgets/crash_modal.py`
- Modify: `src/aws_tui/app.py` — wrap the App loop in `_safe_run()` that captures unhandled exceptions and routes to the crash modal.
- Modify: `src/aws_tui/infra/log_sink.py` (or add `src/aws_tui/infra/crash_dump.py`) to write crash dumps to `~/.cache/aws-tui/crash/<ts>.txt` per spec §7.10.
- Create: `tests/unit/vm/chrome/test_crash.py`
- Create: `tests/unit/ui/test_crash_modal.py`

```python
@dataclass(frozen=True, slots=True)
class CrashReport:
    timestamp: datetime
    exception_type: str         # e.g. "TypeError"
    exception_message: str
    traceback_short: str        # first 5 lines for the modal preview
    dump_path: Path             # absolute path to the full dump
    can_continue: bool          # False if the error class is unrecoverable

class CrashVM:
    """Facade. Holds the CrashReport, exposes continue / quit / view-trace commands."""
    ...
```

`crash_dump.write(report) -> Path`: writes traceback + last 1000 log lines + last 100 user actions (from a small in-memory ring buffer in `LogSink`) to a single file under `~/.cache/aws-tui/crash/`.

`CrashModal` widget renders the short trace + actions per spec §7.10.

Snapshot tests for the crash modal in each theme.

**Acceptance:**
- Inducing a `TypeError` during a deferred action triggers the modal.
- Dump file written and verifiable byte-for-byte against a small fixture.
- `continue` restores VM state to pre-action; `quit` exits cleanly.
- Strict mypy + layer rules clean.

---

## Task 2: Transfer journal resume modal

**Files:**
- Create: `src/aws_tui/vm/chrome/resume_vm.py` — wraps a list of `TransferJournalEntry` (from M2) into selectable items.
- Create: `src/aws_tui/ui/widgets/resume_modal.py`
- Modify: `src/aws_tui/composition.py` (M5) — after `RootVM.startup()`, scan `TransferJournal.find_unfinished()` and if non-empty, present the resume modal before returning.
- Tests: `tests/unit/vm/chrome/test_resume.py`, `tests/unit/ui/test_resume_modal.py`.

```python
class ResumeAction(StrEnum):
    RESUME_ALL = "resume_all"
    ABORT_ALL = "abort_all"
    DECIDE_EACH = "decide_each"
    KEEP_FOR_LATER = "keep_for_later"

class ResumeVM:
    """Holds unfinished entries; exposes a single decision command."""
    ...
```

`RESUME_ALL` → bridge journal entries back into in-flight `TransferVM`s. `ABORT_ALL` → call `AbortMultipartUpload` for each `upload_id` (via aws_session.client("s3")) and `journal.purge()`. `DECIDE_EACH` → per-entry sub-modal (deferred — for now, fall back to KEEP_FOR_LATER on per-entry indecision). `KEEP_FOR_LATER` → no journal mutation.

Snapshot test for the resume modal.

**Acceptance:**
- Writing a fake journal with 2 unfinished entries triggers the modal on startup.
- `RESUME_ALL` constructs 2 TransferVMs.
- `ABORT_ALL` calls the mocked `AbortMultipartUpload` twice and purges the journal files.
- Empty journal directory → no modal.
- Strict mypy + layer rules clean.

---

## Task 3: First-run flow

**Files:**
- Create: `src/aws_tui/vm/chrome/first_run_vm.py`
- Create: `src/aws_tui/ui/widgets/first_run_modal.py`
- Modify: `src/aws_tui/composition.py` — detect "no connections found AND no AWS config" and route to first-run modal before launching the main screen.
- Tests: `tests/unit/vm/chrome/test_first_run.py`, `tests/unit/ui/test_first_run_modal.py`.

Per spec §6.4 Flow 5:

```python
class FirstRunVM:
    """Three choices: add aws (shell out to `aws configure sso`), add s3-compatible (in-tui form), skip."""

    async def choose_add_aws(self) -> None: ...       # shell-out: `aws configure sso`
    async def choose_add_s3_compat(self) -> S3CompatForm: ...
    def choose_skip(self) -> None: ...
```

The s3-compatible form: prompt for `name`, `endpoint_url`, `region`, `access_key_id`, `secret_access_key`, `force_path_style`. Write to `ConfigStore`. (Use the M1 ConfigStore.add_connection.)

**Acceptance:**
- Empty config + empty ~/.aws → first-run modal appears.
- `skip` proceeds to main with no connection selected.
- `add s3-compatible` collects fields, writes to config, re-runs ConnectionResolver.list, picks the new entry.
- Strict mypy + layer rules clean.

---

## Task 4: README polish + docs

**Files:**
- Modify: `README.md` — fill out features, install, quickstart, screenshots placeholders.
- Modify: `docs/architecture.md`, `docs/keybindings.md`, `docs/theming.md`, `docs/connections.md`, `docs/adding-a-service.md` — flesh out content; cross-reference the spec sections.
- Create: `docs/cookbook.md` — common recipes: connecting to MinIO, switching themes, customizing keybindings, the resume-after-crash flow.

For screenshots / asciinema: **leave placeholders** (`<!-- screenshot: TODO; the maintainer records via `asciinema rec` from a real terminal -->`) — a subagent cannot produce asciinema recordings or real-terminal screenshots. Document this clearly.

**Acceptance:**
- README has working install + quickstart commands.
- Each doc file is at least 50 lines of useful content cross-referencing the spec.
- Cookbook covers 4 recipes.

---

## Task 5: CHANGELOG, version bump, commit, tag v0.7.0

- Modify: `src/aws_tui/version.py` → `__version__ = "0.7.0"`.
- Modify: `CHANGELOG.md` → add `## [0.7.0] - 2026-06-14` section.
- Modify: `tests/unit/test_app_sanity.py` to assert v0.7.0.

Push, watch CI green, tag `v0.7.0` ("v0.7.0 — polish + release (M6)"), gh release create with notes.

**Acceptance:**
- All CI green (unit matrix + integration + snapshot + e2e + lint+type + pkg).
- `pipx install git+https://github.com/thekaveh/aws-tui.git` works (manual smoke for the maintainer; document in release notes).
- Tag + GH release published.

---

## Watch-outs

- **Asciinema and screenshots cannot be made by a subagent.** Leave placeholder markdown comments and a TODO list in `docs/recording-todo.md` so the maintainer knows what to record.
- **Crash modal's `continue` is sometimes unsafe.** Implement the heuristic from spec §7.10: if the offending action was a navigation, refresh, or filter (read-only), `continue` is safe. If it was a write (delete, copy, rename), disable `continue`.
- **First-run shell-out to `aws configure sso`** spawns a subprocess and returns to the TUI when the user completes the wizard. Use `subprocess.run` (sync, blocking) — the TUI freezes during the wizard but that's OK and expected per spec §6.4 Flow 5.
- **Layer rules**: the new VMs live in `vm/chrome/`, new widgets in `ui/widgets/`. Layer rules unchanged. `composition.py` orchestrates as usual.
