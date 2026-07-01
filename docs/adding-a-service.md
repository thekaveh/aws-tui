# 1. Adding a new service

> v0.8.0 ships the `s3` and `emr-serverless` services; EMR includes
> the read-only browser, job-run logs, and clone-job-run modal. This
> doc is the pattern for the
> next ones (EC2, IAM, Lambda, ...). For a richer reference than S3
> (dedicated domain client + per-service VM subtree + per-service
> UI widget tree + service-specific modal), read
> `src/aws_tui/services/emr_serverless/` alongside `s3/`.

aws-tui's service-plugin spine keeps service construction additive: a
new folder under `src/aws_tui/services/<name>/` and one registration
call in `composition.py`. Today the app shell still owns the view
factory, so non-`DualPane` services also need an explicit route in
`AwsTuiApp._mount_initial_service_view` / `_mount_service_view` until
the planned service-owned view-factory contract lands.

## 1.1. The `Service` protocol
Declared in `src/aws_tui/vm/services_protocol.py`, re-exported from
`src/aws_tui/services/__init__.py`:

```python
from typing import Any, Protocol
from aws_tui.infra.connection_resolver import Connection

class Service(Protocol):
    descriptor: ServiceDescriptor  # id, label, icon

    def supports(self, connection: Connection) -> bool:
        """True if this service can run against the given connection."""

    def build_vm(self, connection: Connection) -> Any:
        """Construct the service's content VM tree for this connection."""
```

The `descriptor` is a `ClassVar` so the registry can introspect it
without instantiating. `build_vm` is structurally typed as
`-> Any` on the protocol so the `vm/` layer never has to import
`vmx.ComponentVM` just to spell the bound; concrete services return
whatever VMx VM they actually host (`S3Service.build_vm` returns
`DualPaneVM`, see the §2 template below). `ContentHostVM` only
needs a `construct → destruct → dispose` surface.

## 1.2. Steps
1. **Create the folder.**

    ```
    src/aws_tui/services/<name>/
        __init__.py
        service.py    # implements the Service protocol
    ```

2. **Implement `Service`** in `service.py`.

    ```python
    from typing import ClassVar
    from aws_tui.vm.services_protocol import ServiceDescriptor
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.infra.aws_session import AwsSession

    class EC2Service:
        descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
            id="ec2",
            label="EC2",
            icon="•",
        )

        def __init__(self, *, aws_session: AwsSession, ...) -> None:
            self._aws_session = aws_session

        def supports(self, connection: Connection) -> bool:
            return connection.kind == "aws"  # EC2 isn't S3-compat

        def build_vm(self, connection: Connection) -> ComponentVM:
            return InstancesPaneVM(self._aws_session, connection)
    ```

3. **Register** in `src/aws_tui/composition.py` (near the existing
   `s3_service = S3Service(...)` block):

    ```python
    ec2_service = EC2Service(aws_session=aws_session, ...)
    registry.register(cast("Service", ec2_service))
    ```

4. **Add app-shell view routing** if the service does not return a
   `DualPaneVM`. `AwsTuiApp` currently maps `emr-serverless` to
   `EmrServerlessPage` and wraps every other service VM in `DualPane`.
   Add the matching widget factory branch alongside that EMR route.

5. **Reuse existing VM families** where possible:
    - For storage-like services (lists with hierarchy): the file-
      manager VMs in `vm/file_manager/` work as-is — write a new
      `FileSystemProvider` (see `src/aws_tui/domain/filesystem.py`
      for the protocol, and §2 / §3 of the design spec for the
      architectural shape) and reuse `PaneVM` + `DualPaneVM`.
    - For flat resource lists (EC2 instances, IAM users): write a new
      `ListPaneVM` under `vm/<service>/` and a corresponding widget
      family under `ui/widgets/<service>/`.

6. **Layer rules.** Services live one layer above domain, so they may
    import from `domain/`, `infra/`, and the public VM surface
    (`vm/services_protocol.py`, `vm/messages.py`, and the file-manager
    VMs in `vm/file_manager/` for storage-like services that reuse
    `PaneVM`/`DualPaneVM`). The only hard ban is `ui/` (no Textual
    widget imports) and Textual itself — enforced by
    `scripts/check-layers.sh`. See §3 below for the full cheat-sheet.

7. **Tests.** Add unit tests under `tests/unit/services/<name>/` and,
    if your service touches AWS, integration tests under
    `tests/integration/services/<name>/` against `moto` or a vendor
    container.

8. **Update docs.** Add any vendor / API quirks to
    `docs/connections.md`. Update the README's features list.

## 1.3. Layer rules cheat-sheet for services
A service module **may** import from:

- `aws_tui.infra.*` (aws_session, config_store, log_sink, …)
- `aws_tui.domain.*` (filesystem, journal, …)
- `aws_tui.vm.services_protocol` (Service, ServiceDescriptor,
  ServiceRegistry) — re-exported as `aws_tui.services.*`
- `aws_tui.vm.messages` (for pushing on the hub)
- `aws_tui.vm.file_manager.*` (the public VMs — `PaneVM`,
  `DualPaneVM`, etc. — for storage-like services that reuse the
  file-manager scaffolding; see `services/s3/service.py` for the
  pattern that composes `DualPaneVM(left=PaneVM(S3FS),
  right=PaneVM(LocalFS))`).
- `vmx.*`

A service module **may not** import from:

- `aws_tui.ui.*` (no Textual widget code)
- `textual.*` directly

These bans are enforced by `scripts/check-layers.sh`.

## 1.4. Future: entry-point discovery
v1.1 promotes the registry to
`importlib.metadata.entry_points(group="aws_tui.services")` so third-
party packages can ship services without forking. The same `Service`
protocol applies.

## 1.5. Reference: the shipped services

### 1.5.1. S3
`src/aws_tui/services/s3/service.py` is the first concrete service.
Read it end-to-end (~80 lines):

- `descriptor` declares `id = "s3"`, label `"S3"`, icon `"🪣"`
  (U+1FAA3 BUCKET — true emoji codepoint, renders coloured in any
  terminal with a modern emoji font). The icon literal in the
  template at §2 (``"•"``) is a placeholder — the convention is to
  pick an emoji glyph; see the docstring on
  ``services/s3/service.py::S3Service.descriptor`` for the icon
  rationale.
- `supports()` accepts both `aws` and `s3-compatible` connections.
- `build_vm(connection)` composes
  `DualPaneVM(left=PaneVM(S3FS), right=PaneVM(LocalFS))` each call.
- An optional `s3_fs_factory` test hook lets unit tests swap S3FS
  for `InMemoryFS` so no AWS calls leak in CI.
- `bind_hub(hub)` late-wires the hub since the service is registered
  before `RootVM` has its hub.

### 1.5.2. EMR Serverless
`src/aws_tui/services/emr_serverless/service.py` is the second
shipped service and demonstrates the richer per-service pattern:

- `descriptor` declares `id = "emr-serverless"`, label `"EMR"`, icon
  `"🔥"` — U+1F525 FIRE (SMP single-codepoint, 2 cells, in
  colour reliably across SF Mono / JetBrains Mono / Fira Code). See
  the ``services/emr_serverless/service.py`` module docstring for
  the full icon saga (PR #76 bare ``⚡`` U+26A1 → PR #77 ``⚡️``
  with VS-16 → PR #79 ``🔥`` → PR #81 back to ``⚡️`` → PR #83
  ``💥`` → reverted to ``🔥`` after ``💥`` rendered too small). The
  documented "icon contract" future services should follow up front:
  **SMP single-codepoint, no VS-16 dance** — the glyph must reliably
  occupy 2 cells in monospace terminals
  without a variation-selector trick.
- `supports()` is AWS-only (`connection.kind == "aws"`).
- Domain client lives at `domain/emr_serverless.py` (async
  `EmrServerlessClient` facade over `aioboto3`, with read-only
  verbs `list_applications` / `list_job_runs` / `get_job_run` plus
  the write-side `start_job_run` (added PR #83 for the clone flow),
  dedicated `_map_boto_error` adapter).
- VM subtree at `vm/emr_serverless/` (`EmrServerlessPageVM`
  orchestrates `ApplicationsVM` + `JobRunsVM` + `JobRunDetailVM` +
  `JobRunLogsVM`; `JobRunCloneVM` sits alongside, instantiated per
  modal-mount).
- UI widget tree at `ui/widgets/emr_serverless/`
  (`ApplicationPicker` + `JobRunsPane` + `JobRunDetailPane` +
  `JobRunLogsPane` + `EmrServerlessPage` composer +
  `JobRunCloneModal` + `LogFilterModal`).
- Three independent production `set_interval` pollers (apps 60 s /
  runs 60 s with terminal-state suppression / detail 30 s). Demo mode
  uses shorter 30 s / 30 s / 5 s cadences so sample data feels live.
- **Service-specific modal pattern** (PR #83). `JobRunCloneModal`
  is pushed via `app.push_screen` from the page binding
  (`Binding("c", "clone_selected_run", "Clone")`). The
  ``app.py::action_copy`` priority binding short-circuits to the
  EMR clone path when EMR is mounted (parallel to the dual-pane
  hijack pattern for `Tab` / arrow keys). Service-specific
  keymap + chip wiring: ``KeymapStore.DEFAULT_BINDINGS`` adds
  the action id (``"emr.clone": ("c",)``);
  ``HintLegendVM._SERVICE_ACTIONS["emr-serverless"]`` lists the
  action id; ``_ACTION_LABELS["emr.clone"] = "clone"`` gives it
  a human label in the Commands strip. Future services with
  their own actions follow the same three-touch-point pattern:
  default binding + service-actions tuple + action label.
