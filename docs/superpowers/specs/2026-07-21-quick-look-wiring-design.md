# Quick Look Wiring — Design Spec

**Date:** 2026-07-21
**Status:** Approved (design brief) — pending implementation
**Depends on:** BindingResolver keystone (PR #135) — `pane.quick_look` is a
handlerless action the resolver skips until this increment registers it.

## Goal

Wire the built-but-unreached Quick Look modal so pressing `Space` on a file
opens a scrollable 64 KB preview. Registers a `pane.quick_look` handler,
builds `QuickLookContent` from the focused file via the FS provider's
`read_stream`, and pushes the existing `QuickLook` modal driven by the
existing `QuickLookVM`.

## Background (verified in code)

- `QuickLookVM` (`vm/chrome/quick_look_vm.py`) — `open_command:
  RelayCommandOf[QuickLookContent]`, `close_command`, `scroll_command`,
  `find_command`; already `construct()`-ed (`app.py:345`) and `dispose()`-d
  (`app.py:2722`) on `ctx.quick_look_vm`.
- `QuickLook` modal (`ui/widgets/quick_look.py`) — `QuickLook(vm, *, hub)`.
- `QuickLookContent` — `title: str`, `mime: str`,
  `chunks: AsyncIterator[bytes] | None`, `line_count_estimate: int | None`.
- FS providers expose `read_stream(path, *, chunk_size) -> AsyncIterator[bytes]`
  (protocol + LocalFS + S3FS).
- Focused file accessor (same as `action_copy`): `dual = self._dual_pane()`;
  `pane = dual.focused_pane`; `entry_vm = pane.selected_entry` (`EntryVM |
  None`); `entry_vm.kind` / `entry_vm.entry` (`FileEntry`); `pane.provider`;
  `pane.path` (current dir `PathRef`).
- `PaneVM` emits `preview_requested` (payload-less) at two sites; no
  subscriber today.

## Design

### 1. `action_quick_look` (bound to `Space` via the keystone)

Register `pane.quick_look` → `self.action_quick_look` in `AwsTuiApp.__init__`
(alongside the other keystone registrations). Behavior:

```python
def action_quick_look(self) -> None:
    self.record_action("pane.quick_look")
    pane = self._focused_file_pane()          # None if no dual pane / not focused
    if pane is None:
        return
    entry_vm = pane.selected_entry
    if entry_vm is None or entry_vm.kind is not EntryKind.FILE:
        return                                 # dirs / parent-link / empty -> ignore
    entry = entry_vm.entry
    path = pane.path.join(entry.name)
    content = _build_quick_look_content(entry, pane.provider, path)
    self._app_ctx.quick_look_vm.open_command.execute(content)
    self.push_screen(QuickLook(self._app_ctx.quick_look_vm, hub=self._app_ctx.hub))
```

`_focused_file_pane()` is a small helper mirroring `action_copy`'s
`self._dual_pane()` + `focused_pane` lookup (returns `PaneVM | None`).

### 2. `_build_quick_look_content` (module-level helper)

```python
_QUICK_LOOK_PREVIEW_BYTES = 64 * 1024

def _build_quick_look_content(entry, provider, path) -> QuickLookContent:
    mime, _ = mimetypes.guess_type(entry.name)
    return QuickLookContent(
        title=entry.name,
        mime=mime or "application/octet-stream",
        chunks=_first_bytes(
            provider.read_stream(path, chunk_size=_QUICK_LOOK_PREVIEW_BYTES),
            _QUICK_LOOK_PREVIEW_BYTES,
        ),
        line_count_estimate=None,
    )

async def _first_bytes(source, limit) -> AsyncIterator[bytes]:
    remaining = limit
    async for chunk in source:
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            yield chunk[:remaining]
            break
        yield chunk
        remaining -= len(chunk)
```

`read_stream` with `chunk_size=64 KB` already yields ≤64 KB chunks; the
`_first_bytes` cap makes the 64 KB bound explicit and provider-independent.

### 3. `preview_requested` orphan

Investigate whether `preview_requested` has a live runtime trigger (the
keystone's priority bindings may grab the keys before the pane emits). If a
live path exists, subscribe it to the same open helper (clearing the orphan);
if it is currently dead, leave the emitter untouched (do NOT modify the
`Enter`/`action_descend` path) and note it. The `Space` binding is the
primary, reliable trigger regardless.

## Scope

**In:** `Space` opens a 64 KB streaming preview modal for the cursor file;
open/scroll/find/close via the existing `QuickLookVM` commands and modal.

**Out (separate deferred item):** the full-file `$PAGER` shell-out. Shelling
out of a Textual app requires `App.suspend()` + terminal save/restore and its
own error handling — a distinct follow-on increment.

## Testing (TDD)

1. **Content builder** — `_build_quick_look_content` sets `title`=filename,
   `mime` from extension (e.g. `.txt` → `text/plain`, unknown →
   `application/octet-stream`), and `_first_bytes` caps at 64 KB (feed a
   >64 KB fake stream, assert total yielded == 64 KB).
2. **Dir guard** — `action_quick_look` with a directory/parent-link/None
   cursor does not open the modal.
3. **Integration** — `Space` on a seeded file opens the `QuickLook` modal
   (`app.screen` is `QuickLook`) with the file's content; the priority
   binding fires (no "Space does nothing").
4. Reuse existing `tests/unit/vm/chrome/test_quick_look.py` and the QuickLook
   snapshot tests (unchanged).

## Files touched

- `src/aws_tui/app.py` — `action_quick_look`, `_focused_file_pane`,
  `_build_quick_look_content`, `_first_bytes`, register `pane.quick_look`,
  import `QuickLook`, `QuickLookContent`, `mimetypes`.
- `tests/unit/…` — content-builder + dir-guard unit tests.
- `tests/integration/test_quick_look_wiring.py` — Space-opens-modal test.
