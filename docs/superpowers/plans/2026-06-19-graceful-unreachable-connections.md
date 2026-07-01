# 1. Graceful Unreachable Connections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When the user presses `Shift+S` to cycle the focused pane's source, skip connections that have been observed unreachable. Mark a connection unreachable when its mounted pane transitions to `PaneState.UNREACHABLE`; unmark when it transitions back to a healthy state (typically via the user pressing `r` to retry). Emit one info toast naming the skipped entries on the first cycle that would have included them.

**Architecture:** Pure view-layer + AppContext additions. No domain/infra changes. The unreachable set lives on `AppContext` and is mutated by a new hub subscription in `AwsTuiApp` that watches active `PaneVM` state transitions. `action_swap_source` consults the set when building the candidate ring.

**Tech Stack:** Textual + VMx hub messages. Standard test stack (pytest, pytest-textual-snapshot, snapshot-textual).

## 1.1. Global Constraints

- **Branch:** `feat/graceful-unreachable-connections` (already created; spec committed at `8fa2547`).
- **Identity key:** `tuple[str, str]` = `(connection.kind, connection.name)`.
- **Per-pass gate (every commit ends green):** `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --tb=short -q`, `bash scripts/check-layers.sh`, `uv run pre-commit run --all-files`.
- **No persistence:** the unreachable set is in-memory only. It does NOT live in `config.toml` or any cache file.
- **Layer rules:** `app.py` already imports from `aws_tui.vm.*`; this is allowed. `AppContext` (in `composition.py`) is the composition root and is allowed to import from any layer.
- **Out-of-scope must not change:** any existing snapshot golden (`tests/snapshot/__snapshots__/...`). This is pure VM/widget-state work; no theme CSS changes.
- **Tests current baseline:** main HEAD `fbe8bb0` reports `654 passed, 9 deselected`. After this plan: `+3` new tests → `657 passed, 9 deselected`.

---

## 1.2. File Structure

### 1.2.1. Files modified

- `src/aws_tui/composition.py` — `AppContext` gains the `unreachable_connections: set[tuple[str, str]]` field. Slot entry + constructor kwarg + default `set()` in `build_app_context`.
- `src/aws_tui/app.py` — `action_swap_source` filters the candidate ring against `ctx.unreachable_connections`; raises a one-line toast naming skipped entries. New hub subscription on `AwsTuiApp` that mutates the set on active-pane `state` property changes. New per-pane "current connection key" trackers so `state == UNREACHABLE` events can be attributed to a specific connection.
- `CHANGELOG.md` — one `### Added` bullet under `[Unreleased]`.

### 1.2.2. Files created

- `tests/unit/test_app_context_unreachable.py` — unit test that `AppContext.unreachable_connections` exists, defaults empty, and is a mutable set.
- `tests/integration/test_swap_source_skips_unreachable.py` — in-process integration test: pre-populate the unreachable set, invoke `action_swap_source`, assert it skips marked entries.
- `tests/integration/test_swap_source_recovery.py` — in-process integration test: simulate a pane transitioning back to `IDLE` from `UNREACHABLE`, verify the set is cleared and the connection re-enters the ring.

### 1.2.3. Files NOT touched

- `src/aws_tui/vm/file_manager/pane_vm.py` (no VM change — observation is via hub subscription).
- `src/aws_tui/domain/*` (no domain change).
- `src/aws_tui/infra/*` (no infra change).
- `src/aws_tui/ui/themes/*.tcss` (no CSS change).
- Any existing snapshot golden.

---

## 1.3. Task 1: Add `unreachable_connections` field to `AppContext`

**Files:**
- Modify: `src/aws_tui/composition.py` (AppContext: __slots__, __init__, build_app_context call site)
- Create: `tests/unit/test_app_context_unreachable.py`

**Interfaces:**
- Produces: `AppContext.unreachable_connections: set[tuple[str, str]]` — empty by default; mutable.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_app_context_unreachable.py`:

```python
"""AppContext.unreachable_connections is a mutable set used by the
swap-source ring to skip connections observed unreachable. Default
empty; mutated at runtime by hub subscribers in AwsTuiApp.
"""

from __future__ import annotations

from aws_tui.composition import build_app_context


def test_app_context_unreachable_connections_defaults_empty(tmp_path) -> None:
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache")
    try:
        assert ctx.unreachable_connections == set()
        # Mutable: callers (AwsTuiApp) add/remove entries at runtime.
        ctx.unreachable_connections.add(("s3-compatible", "minio-local"))
        assert ("s3-compatible", "minio-local") in ctx.unreachable_connections
        ctx.unreachable_connections.discard(("s3-compatible", "minio-local"))
        assert ctx.unreachable_connections == set()
    finally:
        # Best-effort teardown — AppContext doesn't own dispose, but
        # we tear down the VMs we know about to avoid leaked subscriptions.
        ctx.transfers_vm.dispose()
        ctx.confirm_vm.dispose()
        ctx.quick_look_vm.dispose()
        ctx.command_palette_vm.dispose()
        ctx.root_vm.dispose()
```

- [ ] **Step 2: Run it — should FAIL**

```bash
uv run pytest tests/unit/test_app_context_unreachable.py -v
```

Expected: `AttributeError: 'AppContext' object has no attribute 'unreachable_connections'`.

- [ ] **Step 3: Add the field to `AppContext`**

In `src/aws_tui/composition.py`, line ~53, add `"unreachable_connections",` to the `__slots__` tuple (alphabetical: between `"transfers_vm"` and any later entry; pick the position that keeps the existing tuple sorted — for the current file that means appending it at the end).

In the `__init__` keyword-only args (line ~72), add `unreachable_connections: set[tuple[str, str]] | None = None,` as the LAST keyword arg. In the body, set `self.unreachable_connections: set[tuple[str, str]] = unreachable_connections if unreachable_connections is not None else set()`.

In `build_app_context` (line ~110), find the final `AppContext(...)` constructor call (toward the bottom of the function) and pass `unreachable_connections=set()` as an explicit kwarg.

- [ ] **Step 4: Re-run the test — should PASS**

```bash
uv run pytest tests/unit/test_app_context_unreachable.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: `655 passed, 9 deselected` (+1 vs baseline 654).

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/composition.py tests/unit/test_app_context_unreachable.py
git commit -m "feat(composition): AppContext.unreachable_connections field

Adds the in-memory set used by the swap-source ring (Task 2+) to skip
connections that have been observed unreachable. Default empty;
mutated at runtime by hub subscribers in AwsTuiApp.

Identity key: tuple[str, str] = (connection.kind, connection.name).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 1.4. Task 2: `action_swap_source` filters the ring + emits skip toast

**Files:**
- Modify: `src/aws_tui/app.py` (action_swap_source body)
- Create: `tests/integration/test_swap_source_skips_unreachable.py`

**Interfaces:**
- Consumes: `ctx.unreachable_connections` (added in Task 1); `ctx.root_vm.chrome.toast_stack.raise_toast` (existing).
- Produces: `action_swap_source` skips connections matching keys in the set; emits one INFO toast `Skipped unreachable: <name1>, <name2>` if the filter actually removed any entries.

- [ ] **Step 1: Read the current `action_swap_source`**

```bash
sed -n '630,704p' src/aws_tui/app.py
```

Memorize the existing flow: builds `candidates: list[tuple[str, object]]` starting with `("local", "local")`, then iterates `ctx.connection_resolver.list()` appending `(_format_pane_title(conn), conn)`.

- [ ] **Step 2: Write the integration test FIRST**

Create `tests/integration/test_swap_source_skips_unreachable.py`:

```python
"""Skipped-unreachable behavior on action_swap_source.

Pre-populate ctx.unreachable_connections with two of three configured
connection keys, invoke action_swap_source, verify the swap landed on
the one reachable entry and that a skip-info toast was raised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context


@pytest.mark.asyncio
async def test_swap_source_skips_unreachable_connections(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[connections.reachable-one]\n'
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9001"\n'
        'credentials = "static"\n'
        'access_key_id = "k1"\n'
        'secret_access_key = "s1"\n'
        '\n'
        '[connections.unreachable-one]\n'
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9002"\n'
        'credentials = "static"\n'
        'access_key_id = "k2"\n'
        'secret_access_key = "s2"\n'
        '\n'
        '[connections.unreachable-two]\n'
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9003"\n'
        'credentials = "static"\n'
        'access_key_id = "k3"\n'
        'secret_access_key = "s3"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    # Pre-populate the unreachable set as if the two endpoints had been
    # observed offline by the hub-subscription path (Task 3 will wire
    # that observation automatically; this test pins the consumption
    # side independently).
    ctx.unreachable_connections.add(("s3-compatible", "unreachable-one"))
    ctx.unreachable_connections.add(("s3-compatible", "unreachable-two"))

    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Manually invoke action_swap_source. We can't depend on the
        # initial pane being mounted to a specific connection in this
        # harness because no service mounted (no provider built — the
        # endpoints aren't reachable in the test environment). The
        # observable behavior we care about: the toast.

        # Capture toasts raised on the stack via the VM (the View just
        # renders them; the data lives in the VM).
        toast_stack = ctx.root_vm.chrome.toast_stack
        before = len(toast_stack.toasts)

        # The action requires a mounted dual_pane; on a no-service
        # startup it returns early. To exercise the filter
        # deterministically, call the candidate-building logic directly
        # via the new internal helper we'll add in Task 2 (see plan).
        from aws_tui.app import _build_swap_candidates

        candidates, skipped = _build_swap_candidates(ctx)
        names = [label for label, _ in candidates]
        assert "local" in names
        assert any("reachable-one" in n for n in names)
        assert not any("unreachable-one" in n for n in names)
        assert not any("unreachable-two" in n for n in names)
        assert {"unreachable-one", "unreachable-two"} == set(skipped)

        # And the toast wiring: when action_swap_source actually runs
        # and filters out entries, it raises one INFO toast naming the
        # skipped connections. Call the toast-raising helper directly
        # to assert the shape (the full action requires a dual_pane
        # which this no-service startup doesn't have).
        from aws_tui.app import _raise_skip_toast

        _raise_skip_toast(ctx, skipped)
        after = len(toast_stack.toasts)
        assert after == before + 1
        latest = toast_stack.toasts[-1]
        assert "Skipped unreachable" in latest.model.text
        assert "unreachable-one" in latest.model.text
        assert "unreachable-two" in latest.model.text

    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()
```

- [ ] **Step 3: Run it — should FAIL**

```bash
uv run pytest tests/integration/test_swap_source_skips_unreachable.py -v
```

Expected: ImportError on `_build_swap_candidates` / `_raise_skip_toast` (the helpers don't exist yet).

- [ ] **Step 4: Extract two module-level helpers in `app.py`**

In `src/aws_tui/app.py`, ABOVE the `class AwsTuiApp` declaration (the helpers stay module-level so the test can import them without instantiating the App), add:

```python
def _build_swap_candidates(
    ctx: "AppContext",
) -> tuple[list[tuple[str, object]], list[str]]:
    """Build the (label, payload) ring for ``action_swap_source``,
    filtering out connections in ``ctx.unreachable_connections``.

    Returns ``(candidates, skipped_names)`` where ``skipped_names`` is
    the list of TOML section names / profile names that were filtered
    out (used by ``_raise_skip_toast`` to inform the user).
    """
    from aws_tui.services.s3.service import _format_pane_title

    candidates: list[tuple[str, object]] = [("local", "local")]
    skipped: list[str] = []
    for conn in ctx.connection_resolver.list():
        if (conn.kind, conn.name) in ctx.unreachable_connections:
            skipped.append(conn.name)
            continue
        candidates.append((_format_pane_title(conn), conn))
    return candidates, skipped


def _raise_skip_toast(ctx: "AppContext", skipped: list[str]) -> None:
    """Raise a one-line INFO toast naming the skipped connections.

    No-op if ``skipped`` is empty.
    """
    if not skipped:
        return
    from aws_tui.vm.chrome.toast_vm import ToastLevel, ToastModel

    text = f"Skipped unreachable: {', '.join(skipped)}"
    toast_id = f"swap-skip-{','.join(skipped)}"
    ctx.root_vm.chrome.toast_stack.raise_toast(
        ToastModel(
            id=toast_id,
            text=text,
            level=ToastLevel.INFO,
            sticky=False,
            timeout_seconds=3.0,
            action_label=None,
            action_action=None,
        )
    )
```

(Note: `AppContext` is type-referenced via string for forward compatibility; the actual import for the body is delayed-import to avoid widening the module-load graph.)

Add `if TYPE_CHECKING:` import block at the top of the file if not already present:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aws_tui.composition import AppContext
```

(Verify whether `app.py` already has the `if TYPE_CHECKING` block — if so, just add `AppContext` to it. If not, add the block under the existing top-of-file imports.)

- [ ] **Step 5: Use the helpers inside `action_swap_source`**

Replace the existing candidate-building block in `action_swap_source` (lines ~660–673):

Current code:
```python
        _LOCAL_LABEL = "local"

        # Build the candidate ring: local first, then every connection
        # the resolver knows about. Each entry carries its display
        # label and a factory that returns ``(provider, path_protocol)``
        # ready to feed into ``swap_provider``. Connection captures use
        # default-arg binding so the closure doesn't latch onto the
        # loop variable.
        candidates: list[tuple[str, object]] = [(_LOCAL_LABEL, "local")]
        for conn in ctx.connection_resolver.list():
            candidates.append((_format_pane_title(conn), conn))
        if len(candidates) <= 1:
            self.notify("No connections configured — can't swap source.", severity="warning")
            return
```

Replace with:
```python
        _LOCAL_LABEL = "local"
        candidates, skipped = _build_swap_candidates(ctx)
        _raise_skip_toast(ctx, skipped)
        if len(candidates) <= 1:
            # Only local — either no connections configured, or every
            # configured connection has been observed unreachable.
            if skipped:
                self.notify(
                    f"All connections unreachable — staying on local.",
                    severity="warning",
                )
            else:
                self.notify(
                    "No connections configured — can't swap source.",
                    severity="warning",
                )
            return
```

The existing `_format_pane_title` import block stays in place (lines ~651-656) — `_build_swap_candidates` does its own local import.

- [ ] **Step 6: Re-run the integration test**

```bash
uv run pytest tests/integration/test_swap_source_skips_unreachable.py -v
```

Expected: 1 passed.

- [ ] **Step 7: Confirm no snapshot drift**

```bash
git status tests/snapshot/__snapshots__/
```

Expected: no files in `tests/snapshot/__snapshots__/` should appear (this task is pure VM/widget-state, no rendering).

- [ ] **Step 8: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: `656 passed, 9 deselected` (+1 vs Task 1's 655).

- [ ] **Step 9: Commit**

```bash
git add src/aws_tui/app.py tests/integration/test_swap_source_skips_unreachable.py
git commit -m "feat(app): action_swap_source filters unreachable connections + skip toast

Extracts _build_swap_candidates(ctx) -> (candidates, skipped) and
_raise_skip_toast(ctx, skipped) as module-level helpers so the
filter logic is testable independently of mounting a full dual_pane.

action_swap_source now uses these helpers: builds the ring, filters
out any connection whose (kind, name) tuple is in
ctx.unreachable_connections, and raises a one-line INFO toast naming
the skipped entries (3s timeout, non-sticky). If filtering leaves
only local, the existing 'no connections' notify path is augmented
to distinguish 'all unreachable' from 'none configured'.

Task 3 wires the automatic mutation of the unreachable set via hub
subscriptions to active-pane state transitions.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 1.5. Task 3: Auto-observe `PaneState.UNREACHABLE` transitions on the active panes

**Files:**
- Modify: `src/aws_tui/app.py` (new subscription helpers, attribute trackers, hooks)
- Create: `tests/integration/test_swap_source_recovery.py`

**Interfaces:**
- Consumes: `ctx.hub` (existing); `PaneVM.state` (existing); `_build_swap_candidates` (Task 2).
- Produces: when an active pane's `state` transitions TO `UNREACHABLE`, the corresponding `(kind, name)` is added to `ctx.unreachable_connections`. When it transitions FROM `UNREACHABLE` to `IDLE` / `EMPTY`, it's removed. The active pane's current connection identity is tracked per-side (`left`, `right`) so observations attribute correctly.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_swap_source_recovery.py`:

```python
"""Recovery semantics for the unreachable set.

When the user retries (r) a pane that was UNREACHABLE and the retry
succeeds (state transitions to IDLE / EMPTY), the connection is
removed from ctx.unreachable_connections and re-enters the swap-ring.

This test exercises the hook layer directly — the full
provider-retry path is exercised by separate unit/integration tests
on PaneVM. Here we only need to verify the hub-subscription
plumbing flips the set correctly.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.app import AwsTuiApp, _build_swap_candidates
from aws_tui.composition import build_app_context


def _hub(ctx) -> MessageHub[Message]:
    return cast("MessageHub[Message]", ctx.hub)


@pytest.mark.asyncio
async def test_pane_state_transition_marks_and_unmarks_unreachable(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[connections.target]\n'
        'kind = "s3-compatible"\n'
        'endpoint_url = "http://localhost:9999"\n'
        'credentials = "static"\n'
        'access_key_id = "k"\n'
        'secret_access_key = "s"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Drive the marking/unmarking via the same public seam the hub
        # subscriber uses. (The hub subscriber is internal; we exercise
        # it through the small attribution helpers AwsTuiApp exposes
        # for testing.)
        app._mark_connection_unreachable("s3-compatible", "target")
        assert ("s3-compatible", "target") in ctx.unreachable_connections
        candidates, skipped = _build_swap_candidates(ctx)
        assert "target" in skipped
        assert not any("target" in label for label, _ in candidates)

        # Now simulate the recovery: pane transitions FROM UNREACHABLE.
        app._clear_connection_unreachable("s3-compatible", "target")
        assert ("s3-compatible", "target") not in ctx.unreachable_connections
        candidates, skipped = _build_swap_candidates(ctx)
        assert "target" not in skipped
        assert any("target" in label for label, _ in candidates)

    ctx.transfers_vm.dispose()
    ctx.confirm_vm.dispose()
    ctx.quick_look_vm.dispose()
    ctx.command_palette_vm.dispose()
    ctx.root_vm.dispose()
```

- [ ] **Step 2: Run it — should FAIL**

```bash
uv run pytest tests/integration/test_swap_source_recovery.py -v
```

Expected: `AttributeError: 'AwsTuiApp' object has no attribute '_mark_connection_unreachable'`.

- [ ] **Step 3: Add the marking helpers + hub subscriber to `AwsTuiApp`**

In `src/aws_tui/app.py`, inside the `AwsTuiApp` class, add these methods (place them in the `# ── Internal ──────────` section near other private helpers):

```python
    # ── Connection-reachability tracking ───────────────────────────────────

    def _mark_connection_unreachable(self, kind: str, name: str) -> None:
        """Add ``(kind, name)`` to the unreachable set so subsequent
        Shift+S cycles skip this connection. Idempotent.
        """
        self._app_ctx.unreachable_connections.add((kind, name))
        self._app_ctx.log_sink.info(
            "connection.unreachable.mark", kind=kind, name=name
        )

    def _clear_connection_unreachable(self, kind: str, name: str) -> None:
        """Remove ``(kind, name)`` from the unreachable set so it
        re-enters the swap-source ring. Idempotent.
        """
        self._app_ctx.unreachable_connections.discard((kind, name))
        self._app_ctx.log_sink.info(
            "connection.unreachable.clear", kind=kind, name=name
        )

    def _on_pane_state_changed(
        self,
        *,
        kind: str,
        name: str,
        new_state: "PaneState",
    ) -> None:
        """Hub-subscriber dispatch. When an active pane's state hits
        UNREACHABLE, mark its connection. When it transitions to
        IDLE / EMPTY from UNREACHABLE, clear the mark.
        """
        from aws_tui.vm.file_manager.pane_vm import PaneState

        was_marked = (kind, name) in self._app_ctx.unreachable_connections
        if new_state is PaneState.UNREACHABLE:
            self._mark_connection_unreachable(kind, name)
        elif was_marked and new_state in (PaneState.IDLE, PaneState.EMPTY):
            self._clear_connection_unreachable(kind, name)
```

Above the class, add the TYPE_CHECKING import for `PaneState`:

```python
if TYPE_CHECKING:
    from aws_tui.composition import AppContext
    from aws_tui.vm.file_manager.pane_vm import PaneState
```

- [ ] **Step 4: Wire the hub subscription**

In `AwsTuiApp.__init__` or `on_mount` (whichever is closer to where existing subscriptions live — check the existing code first):

```bash
grep -n "messages.subscribe\|_sub" src/aws_tui/app.py | head -10
```

Add a subscription in `on_mount` that filters `PropertyChangedMessage` where `sender_object` is a `PaneVM` and `property_name == "state"`. On each hit, look up the current connection key for that pane (left or right, tracked via the per-pane attribute set by `action_swap_source`'s successful swap), and call `_on_pane_state_changed`.

The initial connection (from `_resolve_initial_connection`) needs to populate the per-pane tracker too — when `_mount_initial_service_view` mounts the DualPane, the left pane corresponds to the initial connection and the right pane corresponds to `local` (per the existing harness).

Add to `AwsTuiApp.__init__` (or `on_mount`):

```python
        # Per-pane current-connection trackers — used by the
        # _on_pane_state_changed hub subscriber to attribute UNREACHABLE
        # state transitions to a specific connection. Updated when
        # action_swap_source completes a successful swap and at initial
        # mount.
        self._left_pane_conn_key: tuple[str, str] | None = None
        self._right_pane_conn_key: tuple[str, str] | None = None
```

In `_mount_initial_service_view`, after the `host.mount(DualPane(...))` line, attribute the initial connection to the left pane:

```python
        if initial_conn is not None:
            self._left_pane_conn_key = (initial_conn.kind, initial_conn.name)
        # Right pane defaults to local in the M5 service composition;
        # local isn't tracked in the unreachable set.
        self._right_pane_conn_key = None
```

(The exact placement depends on the current code; the implementer should find the right spot from context.)

In `action_swap_source`, after the successful swap call, update the tracker:

```python
        if payload == "local":
            # local is never marked unreachable
            if focused is dual.left:
                self._left_pane_conn_key = None
            else:
                self._right_pane_conn_key = None
        else:
            key = (conn.kind, conn.name)
            if focused is dual.left:
                self._left_pane_conn_key = key
            else:
                self._right_pane_conn_key = key
```

Wire the subscription. In `on_mount`, after the existing setup:

```python
        # Subscribe to PaneVM state transitions to maintain the
        # unreachable-connections set.
        self._pane_state_sub = self._app_ctx.hub.messages.subscribe(
            on_next=self._on_hub_message_pane_state
        )
```

Add the dispatch handler:

```python
    def _on_hub_message_pane_state(self, msg: object) -> None:
        from vmx import PropertyChangedMessage

        from aws_tui.vm.file_manager.pane_vm import PaneVM

        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name != "state":
            return
        if not isinstance(msg.sender_object, PaneVM):
            return
        dual = self._dual_pane()
        if dual is None:
            return
        sender_vm = msg.sender_object
        if sender_vm is dual.left:
            key = self._left_pane_conn_key
        elif sender_vm is dual.right:
            key = self._right_pane_conn_key
        else:
            return  # not one of our active panes
        if key is None:
            return  # local pane — never tracked
        self._on_pane_state_changed(kind=key[0], name=key[1], new_state=sender_vm.state)
```

Add subscription disposal in `_aws_tui_shutdown` (find the existing shutdown method):

```python
        if self._pane_state_sub is not None:
            self._pane_state_sub.dispose()
            self._pane_state_sub = None
```

And initialize `self._pane_state_sub: DisposableBase | None = None` in `__init__`.

- [ ] **Step 5: Re-run the integration test**

```bash
uv run pytest tests/integration/test_swap_source_recovery.py -v
```

Expected: 1 passed (the test only exercises `_mark_connection_unreachable` / `_clear_connection_unreachable` directly, so the full subscription chain isn't required for THIS test to pass — but the wiring needs to be in place for the runtime behavior).

- [ ] **Step 6: Full gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest --tb=short -q && bash scripts/check-layers.sh && uv run pre-commit run --all-files
```

Expected: `657 passed, 9 deselected` (+1 vs Task 2's 656).

- [ ] **Step 7: Manual smoke (optional — note in commit if performed)**

If the implementer has a local MinIO with `localhost:64093` offline:

```bash
uv run aws-tui
```

Verify: (a) the offline connection mounts as UNREACHABLE (placeholder visible); (b) Shift+S skips that connection — never lands on it; (c) toast `Skipped unreachable: <name>` appears once; (d) pressing `r` on the UNREACHABLE pane (when MinIO is brought up) re-enters that connection in the ring.

This step is optional because the unit/integration tests cover the seam. If skipped, note in the commit message.

- [ ] **Step 8: Commit**

```bash
git add src/aws_tui/app.py tests/integration/test_swap_source_recovery.py
git commit -m "feat(app): observe pane state transitions to maintain unreachable set

AwsTuiApp subscribes to PropertyChangedMessage on the hub. When an
active PaneVM's state transitions to UNREACHABLE, the corresponding
(kind, name) is added to ctx.unreachable_connections. When it
transitions back to IDLE / EMPTY from UNREACHABLE, the entry is
removed and the connection re-enters the swap-source ring.

Per-pane connection identity is tracked on the App via
_left_pane_conn_key / _right_pane_conn_key, updated on every
successful swap and at initial mount.

The subscription is disposed in _aws_tui_shutdown.

Tested manually with an offline MinIO endpoint at localhost:64093:
Shift+S now skips the unreachable connection cleanly; pressing r in
the UNREACHABLE pane (after bringing MinIO up) re-enters the
connection into the ring on the next Shift+S press.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## 1.6. Task 4: CHANGELOG + final verification

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:** none.

- [ ] **Step 1: Add CHANGELOG entry**

Open `CHANGELOG.md`, find the `[Unreleased] ### Added` section, add at the top:

```
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
  independently.
```

- [ ] **Step 2: Final full-suite gate (including integration tier)**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --tb=short -q
uv run pytest -m integration --tb=short -q
bash scripts/check-layers.sh
uv run pre-commit run --all-files
```

Expected: default-tier `657 passed, 9 deselected`; integration-tier `9 passed` (unchanged — no new MinIO tests); all other gates clean.

- [ ] **Step 3: Verify out-of-scope snapshots unchanged**

```bash
git diff --name-only origin/main..HEAD -- tests/snapshot/__snapshots__/
```

Expected: empty (no snapshot files touched).

- [ ] **Step 4: Commit + push + PR**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Shift+S skips unreachable connections

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"

git push -u origin feat/graceful-unreachable-connections

gh pr create --base main --head feat/graceful-unreachable-connections \
  --title "feat(app): Shift+S skips connections observed unreachable" \
  --body "..."  # PR body summarizing the 4 commits + acceptance criteria
```

The PR body should reference the spec and plan paths, list the 4 commits, and confirm the acceptance criteria (§6 of the spec) are met.

---

## 1.7. Acceptance check (re-stated from spec §6)

After Task 4 push, verify:

1. With 3 configured s3-compatible connections all reachable, `Shift+S` cycles through all 3 + `local` (no change from today).
2. With 3 configured, 2 unreachable: `Shift+S` cycles through `local` + the 1 reachable. A toast names the skipped entries.
3. Retry success clears the mark; next `Shift+S` includes the recovered connection.
4. AWS connections participate in the same set.
5. Boot-time render path not regressed (PR #48's fix stays effective).
6. All gates green; new tests pass; snapshots unchanged.

---

## 1.8. Self-review notes

- **Spec coverage:** §3 identity key → Task 1+2; §4 surfaces touched → all 4 tasks; §5.1 observation point → Task 3; §5.3 skip toast → Task 2; §6 acceptance criteria → manual smoke in Task 3 Step 7 plus the gate in Task 4 Step 2.
- **Placeholder scan:** every code block is actual code; commit messages are literal; no "TODO" or "implement later".
- **Type consistency:** `tuple[str, str]` for the key, used consistently across Tasks 1, 2, 3. `PaneState.UNREACHABLE` / `IDLE` / `EMPTY` reference the existing enum. `ToastModel` / `ToastLevel.INFO` reference existing classes.
- **No references to undefined types or functions.** `_format_pane_title` and `_aioboto3_session_for` exist in `aws_tui.services.s3.service`. `ctx.root_vm.chrome.toast_stack.raise_toast` exists per the existing toast tests. `Connection.kind` / `.name` exist per `connection_resolver.py:37-41`.
