# 1. Graceful degradation for unreachable connections — Design Spec

**Date:** 2026-06-19
**Status:** Approved for implementation
**Tracks:** `app.py` swap-source ring, `AppContext`, `Pane`/`PaneVM` state observation.

## 1.1. Motivation

Users configure multiple `[connections.X]` entries in `~/.config/aws-tui/config.toml`. Some (notably local-dev MinIO at `localhost:64093`, or transient R2 endpoints) may be offline at any given launch. Today, pressing `Shift+S` cycles the source ring blindly: it lands on an offline connection and the user has to press `Shift+S` again to skip past it (and again, and again, if multiple are offline). The user request: skip unreachable entries automatically.

PR #48 fixed the boot-time crash on offline endpoints. This spec adds the polish: don't make the user cycle through known-broken connections.

## 1.2. Behavior contract

1. **Startup:** unchanged. The configured initial connection (per `[defaults].connection` → `$AWS_PROFILE` → first-auto-discovered) still mounts regardless of reachability. If offline, the pane renders the existing `endpoint unreachable - press r to retry` placeholder.
2. **First observation:** the moment any pane mounted on connection `X` transitions to `PaneState.UNREACHABLE`, `X` is marked as unreachable in an in-memory set on `AppContext`.
3. **`Shift+S` cycling:** the candidate ring built in `AwsTuiApp.action_swap_source` filters out any connection whose identity key is in the unreachable set. `local` always remains. If every connection is unreachable, the ring degrades to `local` only.
4. **Recovery:** when the user presses `r` (`pane.refresh`) on a pane currently in `UNREACHABLE` state and the retry succeeds (`state` transitions to `IDLE` or `EMPTY`), remove that connection from the unreachable set. The next `Shift+S` press will include it again.
5. **No background probing**, no persistence across runs, no startup latency.
6. **User feedback:** when `Shift+S` would have included one or more connections that are skipped due to unreachability, emit a one-line info toast naming them (e.g. `Skipped unreachable: minio-local, r2-prod`). Emitted at most once per skip event; not per Shift+S that has nothing to skip.

## 1.3. Identity key

A stable tuple `(kind, name)`:
- `kind`: `connection.kind` (`"aws"` or `"s3-compatible"`).
- `name`: `connection.name` — the TOML section name for `[connections.<name>]`, or the AWS profile name for auto-discovered entries.

The pair disambiguates an AWS profile literally named `minio-local` from an `s3-compatible` connection also named `minio-local`.

## 1.4. Surfaces touched

| File | Change |
|---|---|
| `src/aws_tui/composition.py` | `AppContext` gains a new `unreachable_connections: set[tuple[str, str]]` field, default empty. Add it to `__slots__`. |
| `src/aws_tui/app.py` | `action_swap_source` filters the ring against `ctx.unreachable_connections`. Subscribe to `PropertyChangedMessage` on the hub; when an active `PaneVM`'s `state` transitions to/from `UNREACHABLE`, mutate the set + emit the skip toast on the next swap. |
| `tests/unit/test_app_context_unreachable.py` *(new)* | Unit test for the set field defaulting empty + mutation. |
| `tests/integration/test_swap_source_skips_unreachable.py` *(new)* | In-process integration test: configure 3 connections, mark 2 as unreachable, verify `action_swap_source` skips them. |
| `tests/integration/test_swap_source_recovery.py` *(new)* | In-process integration test: simulate a retry success that removes a connection from the unreachable set, verify it re-enters the ring. |
| `CHANGELOG.md` `[Unreleased]` `### Added` | One bullet documenting the new behavior. |

## 1.5. Implementation notes

### 1.5.1. Where to mark unreachable

Two reasonable observation points:

**Option A (chosen)** — subscribe to hub `PropertyChangedMessage` for any active `PaneVM`. When a pane's `state` field transitions to `UNREACHABLE`, mark its connection. When it transitions from `UNREACHABLE` to `IDLE`/`EMPTY`, unmark.

**Option B (rejected)** — poll PaneVM state in `action_swap_source` immediately after a swap completes. Race-prone (the swap is async; the new state isn't established by the time the action returns) and creates an awkward two-step "swap then check then maybe skip-again" loop.

### 1.5.2. Connection identity from the pane

The pane has access to `pane._connection` *or* the `DualPaneVM` exposes it. Specifically, `_format_pane_title` in `services/s3/service.py` already accepts a `Connection`; the `PaneVM` holds an `identity_label`. For the unreachable set, we need the canonical `(kind, name)`. The simplest source: when `action_swap_source` performs a swap, record the connection it just swapped TO. If a subsequent state transition to `UNREACHABLE` fires on the active pane, attribute it to that recorded connection.

A small per-pane "current connection key" tracker lives on `AwsTuiApp` (one for left, one for right; updated on every successful swap).

### 1.5.3. The skip toast

Emitted via the existing `ToastStackVM.raise_toast(ToastModel(level=INFO, ...))`. Trigger: inside `action_swap_source`, BEFORE building the ring, compute `would_have_included = {keys of all configured connections}` and `actual = would_have_included - unreachable`. If `would_have_included != actual`, raise a single toast naming the skipped entries. Emit at most once per `action_swap_source` invocation.

Default toast lifetime: short (e.g. 3 seconds); not sticky.

## 1.6. Acceptance criteria

1. With 3 configured s3-compatible connections all reachable, `Shift+S` cycles through all 3 + `local` (no change from today).
2. With 3 configured, 2 unreachable: `Shift+S` cycles through `local` + the 1 reachable. A toast `Skipped unreachable: <name1>, <name2>` shows on the first cycle that would have included a skipped entry.
3. When the user presses `r` in an UNREACHABLE pane and the connection recovers, the next `Shift+S` reintroduces that connection to the ring.
4. AWS connections (`kind="aws"`) participate in the same set — if a sso-dev profile fails connectivity check it's also skipped.
5. The change does not regress the boot-time render path (PR #48's fix stays effective).
6. All gates green; default-tier pytest grows by the new tests; out-of-scope snapshots unchanged.

## 1.7. Out of scope

- Background reachability probing.
- Persistence of the unreachable set across runs.
- UI to manually clear an unreachable mark (Esc + `r` on the pane is the path).
- Probing reachability at startup before mounting (would add latency; rejected by user).
- Showing which connections are marked unreachable in a status panel (deferred — could be a `:connections list` palette command later).

## 1.8. Risks

- **Hub subscription lifecycle:** the new subscription on `AwsTuiApp` needs `dispose()` discipline on shutdown. Already covered by `_aws_tui_shutdown`'s dispose cascade — verify in the implementation.
- **Toast spam:** if `Shift+S` is held down (auto-repeat), the toast could fire repeatedly. Mitigation: per-invocation guard + the toast's natural 3s lifetime means at most a few stacked toasts in normal use. Not worth a debounce.
- **AWS-profile attribution:** auto-discovered AWS profiles don't have a unique endpoint, so marking one as `unreachable` is unusual semantically — what got rejected was the boto3 chain's SDK call, which could fail for many reasons (creds expired, network down, IAM denied). Treating it as "skip until retry" is still useful; the user can always reset with `r`. We accept the imprecision.
