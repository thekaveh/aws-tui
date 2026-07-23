# Foundation Task 2 Report: Atomic RootVM Connection and Service Switching

## Status

Complete.

## Implementation

- Added read-only `RootVM.active_connection` and `RootVM.active_auth_state` accessors.
- Added `RootVM.switch_connection_and_service(connection, auth_state, service_id)`.
  It resolves and validates the target service against the proposed connection before clearing
  current content; then it delegates to the existing connection and service switch paths so
  existing message publication, menu rollback, construction, and disposal behavior remain
  authoritative.
- Added `ContentHostVM.shutdown()` and its private `_shutdown_current()` helper. Replacement
  paths cancel pending setup, await an optional hosted VM `shutdown()` hook, then perform the
  existing synchronous disposal exactly once.
- Updated `AwsTuiApp._aws_tui_shutdown()` to await the content host before its existing root
  disposal cascade, preserving synchronous `RootVM.dispose()` as final local cleanup.

## TDD Evidence

### RED

Added the RootVM atomic-switch, ContentHostVM shutdown-order, and app-shutdown ordering tests
before production edits, then ran:

```text
uv run pytest tests/unit/vm/test_root_vm.py tests/unit/vm/test_content_host.py tests/unit/test_app_sanity.py -q
```

Result:

```text
5 failed, 44 passed in 0.48s
```

The failures were the intended missing behavior:

- `RootVM` had no `switch_connection_and_service`.
- Replacing hosted content never invoked `old.shutdown`.
- `ContentHostVM` had no async `shutdown` boundary for app exit.
- App shutdown disposed the root without awaiting the host first.

### GREEN

After the minimal implementation, the same focused command passed:

```text
49 passed in 0.31s
```

Broader VM regression:

```text
uv run pytest tests/unit/vm -q
457 passed in 28.99s
```

Static and whitespace checks:

```text
uv run ruff check <changed Python files>
All checks passed!

uv run ruff format --check <changed Python files>
6 files already formatted

uv run mypy src/aws_tui/vm/root_vm.py src/aws_tui/vm/content_host_vm.py src/aws_tui/app.py
Success: no issues found in 3 source files

git diff --check
```

## Tests Added

- Atomic switch rebuilds the same service under the new connection, disposes the old VM, and
  exposes the new active connection/auth state.
- Unsupported atomic target is rejected before outgoing content disposal.
- Content replacement awaits an optional hosted `shutdown()` before calling `dispose()`.
- App shutdown awaits the content-host lifecycle boundary before root disposal.

## Files

- `src/aws_tui/vm/root_vm.py`
- `src/aws_tui/vm/content_host_vm.py`
- `src/aws_tui/app.py`
- `tests/unit/vm/test_root_vm.py`
- `tests/unit/vm/test_content_host.py`
- `tests/unit/test_app_sanity.py`
- `.superpowers/sdd/task-2-report.md`

## Self-Review

- Compatibility is checked before the existing connection switch clears hosted content, so an
  unsupported target leaves the active page unchanged.
- Valid switches continue through `switch_connection_with()` and `switch_service()`, retaining
  connection-change publication and existing service-menu rollback behavior without duplicate
  lifecycle ownership.
- The optional hook is called only from async replacement/app-shutdown paths. Synchronous
  `ContentHostVM.dispose()` remains a no-hook final cleanup path, so the app’s awaited handoff
  does not double-dispose or double-shutdown a hosted VM.
- No Glue, Athena, Iceberg, SQL, or S3 behavior was added or changed.

## Concerns

No implementation concerns. The task brief named `tests/unit/vm/test_content_host_vm.py`, but
this repository’s established ContentHostVM suite is `tests/unit/vm/test_content_host.py`; the
new lifecycle coverage was added there rather than creating a duplicate test module.
