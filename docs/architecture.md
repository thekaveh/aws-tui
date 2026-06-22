# Architecture

> Human-readable mirror of §2 of [the design spec](superpowers/specs/2026-06-13-aws-tui-design.md).
> For the deep dive (VM tree, lifecycle invariants, capability matrix,
> end-to-end flows), read the spec.

aws-tui follows a **strict five-layer architecture** with one-way
dependencies:

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

Each layer imports only from layers beneath it. `scripts/check-layers.sh`
greps for any forbidden imports across the five subtrees and is run by
CI on every push; it's the active enforcement today. The
`flake8-tidy-imports` ruff plugin is planned as a follow-up but is not
yet wired (the empty config block would be a no-op).

## 1. Layers
- **View** — Textual widgets and `.tcss` themes
  (`src/aws_tui/ui/`). Never touches `boto3`, `aioboto3`, or
  `botocore`. Talks to VMs via property reads + relay-command
  ``execute(...)``; subscribes to ``MessageHub`` for change
  notifications.
- **ViewModel** — VMx-based viewmodels with reactive commands and
  property-changed messages (`src/aws_tui/vm/`). Never imports
  Textual; tests run headless. Subtrees:
  - `vm/chrome/` — persistent shell (status bar, hint legend, toasts,
    overlays like command palette / confirm / quick look / crash /
    resume / first-run).
  - `vm/file_manager/` — pane / dual-pane / entry / transfer VMs.
  - `vm/settings/` — `SettingsVM` (built per-mount when the user
    selects the Settings nav peer) and `S3ConnectionsVM` (singleton
    on `AppContext`, drives the in-app Connections CRUD).
  - Top-level `vm/nav_menu_vm.py` — `NavMenuVM` (renamed from
    `ServicesMenuVM`; `RootVM.services_menu` is a legacy alias),
    `vm/content_host_vm.py`, `vm/root_vm.py`.
- **Service plugins** — One folder per top-level service
  (`src/aws_tui/services/`). v0.7.0 ships `s3`. Each service implements
  the `Service` protocol (declared in `vm/services_protocol.py`,
  re-exported from `services/__init__.py`).
- **Domain** — `FileSystemProvider` protocol with `LocalFS` and `S3FS`
  implementations + the cross-FS copy/move engine + the transfer
  journal (`src/aws_tui/domain/`). The Norton-Commander unifier; the
  pane VMs treat both sides as the same protocol.
- **Infrastructure** — `AwsSession`, `ConnectionResolver`,
  `ConfigStore`, `ThemeStore`, `KeymapStore`, `LogSink`, `CrashDump`,
  `KeychainBackend`. The only layer that touches the OS, AWS APIs,
  the file system, or the macOS keychain.

## 2. Composition root
The two top-level files `src/aws_tui/composition.py` and
`src/aws_tui/app.py` are the only modules permitted to import from
every layer. `composition.py` builds the dependency graph; `app.py`
is the Textual `App` subclass that mounts widgets and wires action
handlers.

`composition.py` also owns three startup-time helpers:

- `needs_first_run(...)` — true when neither config nor `~/.aws/`
  knows any connection (spec §6.4 Flow 5).
- `apply_resume_decision(...)` — applies the user's choice from the
  transfer-resume modal (calls `AbortMultipartUpload` per
  `upload_id` on ``ABORT_ALL``; purges journal files).
- `add_s3_compat_connection(form)` — materializes the in-TUI
  S3-compatible form into a config-store entry.

## 3. Lifecycle
VMs implement `construct → run → destruct → dispose` (VMx convention).
The `RootVM` constructs the chrome and content-host children
depth-first; `ContentHostVM.set_content(new)` disposes the previous
content via the same cascade. App shutdown awaits the in-flight
transfers cancel + closes every aioboto3 client before disposing the
VM tree (spec §5.4).

## 4. Messaging
All cross-VM communication goes through the session's single
`MessageHub`. Custom envelopes (defined in
`src/aws_tui/vm/messages.py`):

- `ConnectionChangedMessage`, `ThemeChangedMessage`,
  `AuthExpiredMessage`, `TransferProgressMessage`,
  `KeymapChangedMessage`, `FocusChangedMessage`.

VMs subscribe via `hub.messages.subscribe(on_next=callback)` (an
`reactivex.Observable` under the hood); filtering happens inside the
callback (typically `isinstance(msg, FooMessage)`). The view layer
subscribes via `HubSubscriberMixin` on a per-widget basis, which wraps
the same observable plus dispose-on-unmount.

## 5. Testing pyramid
| Tier | Count | What it proves |
|---|---|---|
| Unit | 537 | VM, domain, infra behavior; no I/O |
| Snapshot | 234 | View rendering against golden SVGs per theme × screen-state combination, plus paired content-presence guards (per PR #53 lesson) |
| Integration (in-process) | 40 | Full-app smoke + regression flows (app pilot, modal forwarding, multi-select, source swap, settings nav-page toggle, expired-SSO probe, etc.) |
| E2E | 5 | Pilot-driven user journeys |
| Integration (MinIO) | 9 | MinIO via testcontainers (opt-in, `-m integration`) |

Default tier total: **816** (`uv run pytest`). Opt-in MinIO tier:
**9** (`uv run pytest -m integration`).

Run the default tiers (unit + snapshot + e2e + in-process integration)
with `uv run pytest`. Opt into the MinIO tier with
`uv run pytest -m integration` — it spins up a container, which the
default `addopts` filter excludes (`-m 'not integration'`).

## 6. Layer-rule check
`scripts/check-layers.sh` shells out to `grep -RnE` across the five
layer subtrees with the banned-import patterns inlined. The
composition root and `app.py` are deliberately excluded — they live at
`src/aws_tui/` top-level so the check never inspects them.

## 7. Where to start reading the code
1. `src/aws_tui/composition.py` — see how everything wires.
2. `src/aws_tui/vm/root_vm.py` — top of the VM tree.
3. `src/aws_tui/services/s3/service.py` — the only concrete service in
   v0.7.0; pattern for future ones.
4. `src/aws_tui/domain/cross_fs.py` — the engine that moves bytes
   between any pair of `FileSystemProvider`s.
5. `src/aws_tui/ui/widgets/` — pure Textual widgets; per-VM smoke
   tests in `tests/unit/ui/`.
6. `src/aws_tui/vm/nav_menu_vm.py` + `src/aws_tui/ui/widgets/nav_menu.py` —
   the left-rail nav: services list on top, Settings docked at the
   bottom (split into two `OptionList`s in the widget).
7. `src/aws_tui/vm/settings/settings_vm.py` +
   `src/aws_tui/ui/widgets/settings_view.py` — the in-app Settings
   page (built per-mount, not as an `AppContext` singleton — see the
   PR #56 post-ship amendment in the
   [Settings-as-nav-page design spec](superpowers/specs/2026-06-20-settings-as-first-class-nav-page-design.md)).
