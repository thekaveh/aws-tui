# Architecture

> Human-readable mirror of §2 of [the design spec](superpowers/specs/2026-06-13-aws-tui-design.md).
> For the deep dive (VM tree, lifecycle invariants, capability matrix,
> end-to-end flows), read the spec.

aws-tui follows a **strict five-layer architecture** with one-way
dependencies:

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

Each layer imports only from layers beneath it. `ruff`
`flake8-tidy-imports` enforces this at lint time; `scripts/check-layers.sh`
greps for any forbidden imports across the five subtrees as a
belt-and-suspenders.

## Layers

- **View** — Textual widgets and `.tcss` themes
  (`src/aws_tui/ui/`). Never touches `boto3`, `aioboto3`, or
  `botocore`. Talks to VMs via property reads + relay-command
  ``execute(...)``; subscribes to ``MessageHub`` for change
  notifications.
- **ViewModel** — VMx-based viewmodels with reactive commands and
  property-changed messages (`src/aws_tui/vm/`). Never imports
  Textual; tests run headless. Two subtrees:
  - `vm/chrome/` — persistent shell (status bar, hint legend, toasts,
    overlays like command palette / confirm / quick look / crash /
    resume / first-run).
  - `vm/file_manager/` — pane / dual-pane / entry / transfer VMs.
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

## Composition root

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

## Lifecycle

VMs implement `construct → run → destruct → dispose` (VMx convention).
The `RootVM` constructs the chrome and content-host children
depth-first; `ContentHostVM.set_content(new)` disposes the previous
content via the same cascade. App shutdown awaits the in-flight
transfers cancel + closes every aioboto3 client before disposing the
VM tree (spec §5.4).

## Messaging

All cross-VM communication goes through the session's single
`MessageHub`. Custom envelopes (defined in
`src/aws_tui/vm/messages.py`):

- `ConnectionChangedMessage`, `ThemeChangedMessage`,
  `AuthExpiredMessage`, `TransferProgressMessage`,
  `KeymapChangedMessage`, `FocusChangedMessage`.

VMs subscribe via `MessageHub.subscribe(callback, filter=...)`; the
view layer subscribes via `HubSubscriberMixin` on a per-widget basis.

## Testing pyramid

| Tier | Count | What it proves |
|---|---|---|
| Unit | 429 | VM, domain, infra behavior; no I/O |
| Snapshot | 44 | View rendering against golden SVGs per theme |
| E2E | 5 | Pilot-driven user journeys |
| Integration | 9 | MinIO via testcontainers (opt-in) |

Run all tiers with `uv run pytest`; opt into integration with
`uv run pytest -m integration`.

## Layer-rule check

`scripts/check-layers.sh` shells out to `grep -RnE` across the five
layer subtrees with the banned-import patterns inlined. The
composition root and `app.py` are deliberately excluded — they live at
`src/aws_tui/` top-level so the check never inspects them.

## Where to start reading the code

1. `src/aws_tui/composition.py` — see how everything wires.
2. `src/aws_tui/vm/root_vm.py` — top of the VM tree.
3. `src/aws_tui/services/s3/service.py` — the only concrete service in
   v0.7.0; pattern for future ones.
4. `src/aws_tui/domain/cross_fs.py` — the engine that moves bytes
   between any pair of `FileSystemProvider`s.
5. `src/aws_tui/ui/widgets/` — pure Textual widgets; per-VM smoke
   tests in `tests/unit/ui/`.
