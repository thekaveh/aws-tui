# 1. Architecture

![aws-tui five-layer architecture: Textual S3, EMR Serverless, Glue, and Athena views with ContextPicker and ServiceTabStrip; Glue and Athena VM trees; a typed CopyTableReferenceRequest to TableClipboardVM copy-and-insert route; service plugins; shared TableRef and QueryContext models; IcebergInspector; source selection and connection resolution; immutable Glue, Athena, and S3 navigation messages; and the AWS Glue, Athena, S3, and Lake Formation boundary.](diagrams/img/architecture.png)

![aws-tui operational flows for local and S3 transfer, Glue-to-Athena query handoff, S3 result artifacts, and bounded EMR Serverless log loading.](diagrams/img/operations-flow.png)

![aws-tui deployment boundaries showing the local process, platform config and keychain, multiple profile-scoped AWS accounts and regions, and optional S3-compatible endpoints.](diagrams/img/deployment.png)

![aws-tui content lifecycle showing widget-worker drain before VM disposal, transactional source rollback, and ordered application shutdown.](diagrams/img/lifecycle.png)

aws-tui follows a five-layer architecture with enforced forbidden edges:

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

`scripts/check-layers.sh` parses imports with `ast`, resolves relative
imports, and checks the banned edges in the script. `app.py` and
`composition.py` are trusted composition roots and are not scanned.
`services/` is a service-composition boundary: it can import concrete
VMs to build service pages, but it cannot import Textual widgets.

## 1.1. Layers
- **View** — Textual widgets and `.tcss` themes
  (`src/aws_tui/ui/`). Never touches `boto3`, `aioboto3`, or
  `botocore`. Talks to VMs via property reads + relay-command
  ``execute(...)``; subscribes to ``MessageHub`` for change
  notifications. The S3 root is the code-backed `DualPane` from
  `src/aws_tui/ui/widgets/dual_pane.py`, mounted through
  `src/aws_tui/ui/widgets/service_view_factory.py`; there is no `S3Page` class.
  `ContextPicker` provides bordered keyboard-focusable context selection, and
  `ServiceTabStrip` provides one predictable focus stop for service-local
  views.
- **ViewModel** — VMx-based viewmodels with reactive commands and
  property-changed messages (`src/aws_tui/vm/`). Never imports
  Textual; tests run headless. `ServiceSelectionStore` is a VM-layer type in
  `src/aws_tui/vm/service_source_vm.py`, shared by the single-context service
  VMs rather than owned by Infrastructure. Subtrees:
  - `vm/chrome/` — persistent shell state (hint legend, toasts,
    overlays like command palette / confirm / quick look / crash /
    first-run, plus dormant transfer-recovery scaffolding and a retained
    `StatusBarVM` subscriber for
    legacy status bookkeeping even though no `StatusBar` widget is
    mounted in the production chrome).
  - `vm/file_manager/` — pane / dual-pane / entry / transfer VMs.
  - `vm/table_clipboard_vm.py` — `TableClipboardVM`, an app-lifetime VMx
    component that retains one typed, replaceable table reference.
  - `vm/emr_serverless/` — `EmrServerlessPageVM` plus its
    `ApplicationsVM` / `JobRunsVM` / `JobRunDetailVM` / `JobRunLogsVM` children
    (added post-tag by PR #76 and extended by PR #84; the read-mostly EMR
    Serverless browser with logs streaming and focused clone submission).
    Its immutable `ServiceSourceContext` carries the active connection name,
    optional distinct AWS profile, and region to the service view; the shared
    `ServiceSourceHeader` renders that identity above the EMR application picker.
    `ServiceSelectionStore` scopes remembered service selections by
    `(service_id, connection_name, region)`.
    `JobRunCloneVM` (PR #83) backs the clone-job-run modal — a
    sibling VM under `vm/emr_serverless/clone_vm.py`, instantiated
    per modal-mount with the focused run as the source.
  - `vm/glue/` — `GluePageVM` with independent Catalog, Jobs, and
    Crawlers child VMs. `GlueCatalogVM` owns a `GlueIcebergVM` child that
    appears only for tables classified as Iceberg and loads Snapshots,
    History, Manifests, Files, Partitions, and References independently.
    The page shares `ServiceSourceContext` and
    connection/region-scoped selection memory with the other
    single-context AWS services. `GlueCatalogVM` emits the immutable
    service-neutral `OpenS3LocationRequest` and
    `OpenAthenaTableRequest`; it never mounts a Textual view or constructs a
    destination service itself.
  - `vm/athena/` — `AthenaPageVM` with Query, History, Results, and Saved
    child VMs. Its context and remembered selections are scoped by connection
    name and region; changing workgroup, catalog, or database invalidates the
    query context. Query work stays in the VM layer, while `domain/athena.py`
    owns boto mapping and `domain/sql_policy.py` fails closed before dispatch.
    `AthenaPageVM.open_table(...)` sets exact catalog/database context and
    prefills bounded starter SQL. `open_table_in_glue()` publishes
    `OpenGlueTableRequest` only when the current SQL resolves to one visible
    table.
  - `vm/settings/` — `SettingsVM` (built per-mount when the user
    selects the Settings nav peer) and `S3ConnectionsVM` (singleton
    on `AppContext`, drives the in-app Connections CRUD).
  - Top-level `vm/nav_menu_vm.py` — `NavMenuVM` (renamed from
    `ServicesMenuVM`; `RootVM.services_menu` is a legacy alias),
    `vm/content_host_vm.py`, `vm/root_vm.py`.
- **Service plugins** — One folder per top-level service
  (`src/aws_tui/services/`). The current tree ships `s3`,
  `emr-serverless` (read-only browser + clone-job-run plus job-run
  logs — applications listing, job-runs master-detail, state-filter
  chips, clone-and-edit modal via `c`; cancel / vanilla submit are
  still deferred), `glue` (read-only Catalog, Jobs, and Crawlers), and
  `athena` (read-only query, history, results, and saved-query views).
  `GlueService` composes both `GlueClient` and an Athena-backed
  `IcebergInspector`; `AthenaService` composes the query page.
  Each service implements the `Service` protocol (declared in
  `vm/services_protocol.py`, re-exported from `services/__init__.py`).
- **Domain** — `FileSystemProvider` protocol with `LocalFS` and `S3FS`
  implementations + the cross-FS copy/move engine + the transfer
  journal (`src/aws_tui/domain/`). The Norton-Commander unifier; the
  pane VMs treat both sides as the same protocol. Domain adapters perform the
  runtime AWS and filesystem I/O: `LocalFS` and `TransferJournal` access host
  storage, while `S3FS`, `EmrServerlessClient`, `GlueClient`, and
  `AthenaClient` issue service operations and map external responses/errors
  into domain values. `TableRef` and `QueryContext` carry immutable table and
  execution identity. `IcebergInspector` uses `AthenaQueryRunner` to read
  bounded Iceberg metadata tables; `ReadOnlySqlPolicy` validates both user and
  generated SQL. Raw AWS responses remain below VMs.
- **Infrastructure** — Infrastructure owns sessions, credentials,
  configuration, SDK client construction, and OS-backed stores.
  `AwsSession` and `ConnectionResolver` provide configured AWS identities and
  client contexts to the domain adapters. Resolver order drives `Shift+S`;
  the VM-layer `ServiceSelectionStore` scopes workgroup and resource
  selections by service, connection name, and region. `ConfigStore`, `ThemeStore`,
  `KeymapStore`, `LogSink`, `CrashDump`, and `KeychainBackend` persist
  application and platform state. Infrastructure prepares those boundaries;
  domain adapters perform the provider operations.

## 1.2. Composition root
The two top-level files `src/aws_tui/composition.py` and
`src/aws_tui/app.py` are the only modules permitted to import from
every layer. `composition.py` builds the dependency graph; `app.py`
is the Textual `App` subclass that mounts widgets and wires action
handlers.

`composition.py` also constructs the app-lifetime `TableClipboardVM`. `app.py` owns the
best-effort OS clipboard copy after it receives the typed request; the VM
remains the authoritative in-app clipboard.

## 1.3. Lifecycle
VMs implement `construct → run → destruct → dispose` (VMx convention).
The `RootVM` constructs the chrome and content-host children
depth-first; `ContentHostVM.set_content(new)` disposes the previous
content via the same cascade. When outgoing content exposes `shutdown`,
`ContentHostVM.set_content(...)` awaits it before calling `dispose`; hosted VM
shutdown is awaited before disposal, and every hosted service's owned
operations drain before teardown. App shutdown is task-owned and idempotent. Explicit quit and Textual
unmount (including fatal teardown) await the same sequence: stop navigation
intake, drain transfers, setup, queries, and preview workers, close every
aioboto3 client, flush logs, then dispose subscriptions and the VM tree
(spec §5.4).

## 1.4. Messaging
All cross-VM communication goes through the session's single
`MessageHub`. Custom envelopes (defined in
`src/aws_tui/vm/messages.py`):

- `ConnectionChangedMessage`, `ThemeChangedMessage`,
  `AuthExpiredMessage`, `TransferProgressMessage`,
  `KeymapChangedMessage`, `FocusChangedMessage`, `PaletteActionFailedMessage`,
  `TransferCancelRequestedMessage`, `ConnectionListChangedMessage`,
  `OpenS3LocationRequest`, `OpenAthenaTableRequest`,
  `OpenGlueTableRequest`, `CopyTableReferenceRequest`, and
  `ServiceOperationFailedMessage`. Recovered service exceptions use the last
  envelope to reach `RootVM`'s redacted durable-log boundary without coupling
  service VMs to logging infrastructure.

Cross-service navigation stays service-neutral. `OpenAthenaTableRequest` and
`OpenGlueTableRequest` carry a `TableRef` containing catalog, database, table,
connection name, and region; the Athena request may add a validated snapshot
ID. `app.py` serializes table handoffs, resolves the exact connection, rejects
region drift, snapshots the outgoing Glue/Athena state for rollback, and
mounts the destination through `RootVM`. Athena receives generated SQL in the
editor but does not execute it. For S3, `OpenS3LocationRequest` carries the
exact connection, region, URI, pane, and reveal-object intent. The Results VM
reloads an execution and publishes only when it succeeded, belongs to the
active context, and has a valid `s3://` output location. Missing, malformed,
ambiguous, or stale identities stop at an advisory; VMs never import UI code.
`CopyTableReferenceRequest` carries one exact `TableRef` to the composition
root, which updates `TableClipboardVM` and attempts the OS clipboard copy.
Palette eligibility is VM-owned state: the palette projects global commands
and only commands whose declared service IDs include the active service.

VMs subscribe via `hub.messages.subscribe(on_next=callback)` (an
`reactivex.Observable` under the hood); filtering happens inside the
callback (typically `isinstance(msg, FooMessage)`). The view layer
subscribes via `HubSubscriberMixin` on a per-widget basis, which wraps
the same observable plus dispose-on-unmount.

## 1.5. Testing pyramid
| Tier | Count | What it proves |
|---|---|---|
| Unit | Recount with `uv run pytest tests/unit --collect-only -q | tail -1` | VM, domain, infra behavior; isolated local I/O only, with no external services |
| Snapshot | Recount with `find tests/snapshot/__snapshots__ -name '*.raw' | wc -l` | View rendering against golden SVGs per theme × screen-state combination, plus paired content-presence guards (per PR #53 lesson) |
| Integration (in-process) | Recount with `uv run pytest tests/integration --collect-only -q | tail -1` | Full-app smoke + regression flows (app pilot, modal forwarding, multi-select, source swap, settings nav-page toggle, expired-SSO probe, etc.) |
| E2E | Recount with `uv run pytest tests/e2e --collect-only -q | tail -1` | Pilot-driven user journeys |
| Integration (MinIO) | Recount with `uv run pytest -m integration --collect-only -q | tail -1` | MinIO via testcontainers (opt-in, `-m integration`) |

Default tier total drifts with each post-tag PR. Recount with
`uv run pytest --collect-only -q | tail -1`; recount snapshot goldens
with `find tests/snapshot/__snapshots__ -name '*.raw' | wc -l`.
Opt-in MinIO tier: `uv run pytest -m integration`.

Run the default tiers (unit + snapshot + e2e + in-process integration)
with `uv run pytest`. Opt into the MinIO tier with
`uv run pytest -m integration` — it spins up a container, which the
default `addopts` filter excludes (`-m 'not integration'`).

## 1.6. Layer-rule check
`scripts/check-layers.sh` parses Python imports with `ast` across the
five layer subtrees, resolves relative imports to absolute module names,
and matches them against the banned-import rules inlined in the script.
The composition root and `app.py` are deliberately excluded — they live
at `src/aws_tui/` top-level so the check never inspects them.

## 1.7. Where to start reading the code
1. `src/aws_tui/composition.py` — see how everything wires.
2. `src/aws_tui/vm/root_vm.py` — top of the VM tree.
3. `src/aws_tui/vm/file_manager/dual_pane_vm.py` — the first concrete
   page VM (S3 service hosts it).
   `src/aws_tui/vm/emr_serverless/page_vm.py::EmrServerlessPageVM` —
   another concrete page VM; a richer pattern
   that orchestrates four child VMs (`ApplicationsVM`,
   `JobRunsVM`, `JobRunDetailVM`, `JobRunLogsVM`) and runs three independent
   pollers.
4. `src/aws_tui/services/s3/service.py` — the first concrete service
   in v0.7.0; pattern for future ones.
   `src/aws_tui/services/emr_serverless/service.py` uses the richer per-service
   subtree pattern (dedicated domain client + VM subtree + UI
   widget tree).
   `src/aws_tui/services/glue/service.py` follows the same factory
   lifecycle for an AWS-only, read-only three-view page and retains
   validated selection identifiers per connection name and region.
   S3 owns independent sources for each pane, while single-context AWS services
   use `RootVM`'s active connection and are rebuilt as a whole when their source
   changes.
   `src/aws_tui/services/athena/service.py` is the corresponding query-service
   reference: it composes an Athena client, read-only SQL policy, and a fresh
   `AthenaPageVM` per AWS connection. Glue composes its own contextual Athena
   client for `IcebergInspector`; it does not reuse the mounted Athena VM.
5. `src/aws_tui/domain/cross_fs.py` — the engine that moves bytes
   between any pair of `FileSystemProvider`s.
6. `src/aws_tui/ui/widgets/` — pure Textual widgets; per-VM smoke
   tests in `tests/unit/ui/`.
7. `src/aws_tui/vm/nav_menu_vm.py` + `src/aws_tui/ui/widgets/nav_menu.py` —
   the left-rail nav: services list on top, Settings docked at the
   bottom (split into two `OptionList`s in the widget).
8. `src/aws_tui/vm/settings/settings_vm.py` +
   `src/aws_tui/ui/widgets/settings_view.py` — the in-app Settings
   page (built per-mount, not as an `AppContext` singleton — see the
   PR #56 post-ship amendment in the
   [Settings-as-nav-page design spec](superpowers/specs/2026-06-20-settings-as-first-class-nav-page-design.md)).
