# Quick Look Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** `Space` on a file opens a 64 KB Quick Look preview modal, driven by the existing `QuickLookVM` + `QuickLook` modal.

**Spec:** `docs/superpowers/specs/2026-07-21-quick-look-wiring-design.md` (exact code lives there).

## Global Constraints

- 64 KB preview cap enforced by `_first_bytes` (provider-independent).
- Files only: directories / parent-link / empty cursor → no-op.
- Full-file `$PAGER` shell-out is OUT of scope (separate increment).
- `pane.quick_look` handler registration goes in the keystone's registration block in `AwsTuiApp.__init__`.
- `uv run pytest`, `uv run ruff check`, `uv run mypy src` per repo config.

## File Structure

- Modify `src/aws_tui/app.py` — content builder helpers + `action_quick_look` + `_focused_file_pane` + registration + imports.
- Test `tests/unit/test_quick_look_content.py` (new) + `tests/integration/test_quick_look_wiring.py` (new).

---

### Task 1: Content builder (`_first_bytes` + `_build_quick_look_content`)

**Files:** Modify `src/aws_tui/app.py`; Test `tests/unit/test_quick_look_content.py`

**Interfaces:**
- Produces: `_build_quick_look_content(entry: FileEntry, provider: FileSystemProvider, path: PathRef) -> QuickLookContent`; `_first_bytes(source: AsyncIterator[bytes], limit: int) -> AsyncIterator[bytes]`.

- [ ] **Step 1: Failing tests**

```python
import mimetypes
import pytest
from aws_tui.app import _build_quick_look_content, _first_bytes


async def _gen(*chunks: bytes):
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_first_bytes_caps_at_limit() -> None:
    out = b"".join([c async for c in _first_bytes(_gen(b"a" * 40000, b"b" * 40000), 64 * 1024)])
    assert len(out) == 64 * 1024


@pytest.mark.asyncio
async def test_first_bytes_passes_through_when_under_limit() -> None:
    out = b"".join([c async for c in _first_bytes(_gen(b"hello"), 64 * 1024)])
    assert out == b"hello"


def test_build_content_sets_title_and_mime(tmp_path) -> None:
    from aws_tui.domain.filesystem import EntryKind, FileEntry
    entry = FileEntry(name="notes.txt", kind=EntryKind.FILE, size=10)

    class _P:
        def read_stream(self, path, *, chunk_size):
            return _gen(b"x")

    content = _build_quick_look_content(entry, _P(), path="notes.txt")
    assert content.title == "notes.txt"
    assert content.mime == "text/plain"


def test_build_content_unknown_mime_defaults_octet_stream() -> None:
    from aws_tui.domain.filesystem import EntryKind, FileEntry
    entry = FileEntry(name="blob.zzz", kind=EntryKind.FILE, size=1)

    class _P:
        def read_stream(self, path, *, chunk_size):
            return _gen(b"x")

    content = _build_quick_look_content(entry, _P(), path="blob.zzz")
    assert content.mime == "application/octet-stream"
```

- [ ] **Step 2:** `uv run pytest tests/unit/test_quick_look_content.py -v` → FAIL (import error).

- [ ] **Step 3:** Implement `_first_bytes` and `_build_quick_look_content` in `app.py` per spec §2 (+ `import mimetypes`, `_QUICK_LOOK_PREVIEW_BYTES = 64 * 1024`, imports of `QuickLookContent`).

- [ ] **Step 4:** Re-run → PASS.

- [ ] **Step 5: Commit** `feat(quick-look): 64 KB content builder + cap helper`.

---

### Task 2: Wire `action_quick_look` + register `pane.quick_look`

**Files:** Modify `src/aws_tui/app.py`; Test `tests/integration/test_quick_look_wiring.py`

**Interfaces:**
- Consumes: Task 1 builder; `ctx.quick_look_vm.open_command`; `QuickLook` modal; `dual.focused_pane.selected_entry`.

- [ ] **Step 1: Failing tests**

```python
import pytest
from aws_tui.app import AwsTuiApp
from aws_tui.ui.widgets.quick_look import QuickLook


@pytest.mark.asyncio
async def test_space_on_file_opens_quick_look(app_context_factory) -> None:
    # app_context_factory seeds an InMemoryFS; ensure a file is present + focused.
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # (helper) navigate/focus a pane with a file at the cursor, then:
        opened = app.action_quick_look()  # direct call: no cursor file -> no modal OR modal
        await pilot.pause()
    # Asserted in the concrete test after wiring the seeded-file fixture.


@pytest.mark.asyncio
async def test_quick_look_noop_without_file(app_context_factory) -> None:
    app = AwsTuiApp(app_context_factory())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_quick_look()  # empty pane / no cursor -> must not raise, no modal
        await pilot.pause()
        assert not isinstance(app.screen, QuickLook)
```

Refine the "opens" test to seed a file into the pane's InMemoryFS and set the cursor before pressing `Space`; assert `isinstance(app.screen, QuickLook)` and that the VM's content title matches the file. Use the pattern from existing `tests/integration/` pane tests for seeding + cursor placement.

- [ ] **Step 2:** run → FAIL (`action_quick_look` missing).

- [ ] **Step 3:** Implement in `app.py`:
  - `_focused_file_pane(self) -> PaneVM | None` (mirror `action_copy`'s `_dual_pane()` + `focused_pane`).
  - `action_quick_look(self)` per spec §1 (record_action, file guard, build content, `open_command.execute`, `push_screen(QuickLook(...))`).
  - Register `self._actions.register("pane.quick_look", self.action_quick_look)` in the keystone registration block.
  - Import `QuickLook`, `EntryKind`.

- [ ] **Step 4:** run → PASS.

- [ ] **Step 5: `preview_requested`** — grep for a live trigger of `_open_cursor_sync`/`activate_at`. If reachable at runtime, subscribe it to the same open path + add a test; if dead, log a one-line note in the ledger and leave the emitter untouched.

- [ ] **Step 6: Regression** — `uv run pytest tests/unit tests/integration/test_quick_look_wiring.py -q`; `ruff` + `mypy src`.

- [ ] **Step 7: Commit** `feat(app): Space opens Quick Look preview modal`.

- [ ] **Step 8: CHANGELOG** — add an `[Unreleased] Added` entry; commit.
