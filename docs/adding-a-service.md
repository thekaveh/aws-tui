# Adding a new service

> v0.7.0 ships the `s3` service. This doc is the pattern for the next
> ones (EC2, IAM, Lambda, ...).

aws-tui's service-plugin spine is designed so adding a top-level AWS
(or S3-compatible) service is **additive**: a new folder under
`src/aws_tui/services/<name>/` and one registration call in
`composition.py`. No other layer changes.

## 1. The `Service` protocol
Declared in `src/aws_tui/vm/services_protocol.py`, re-exported from
`src/aws_tui/services/__init__.py`:

```python
from typing import Protocol
from aws_tui.infra.connection_resolver import Connection
from vmx import ComponentVM

class Service(Protocol):
    descriptor: ServiceDescriptor  # id, label, icon

    def supports(self, connection: Connection) -> bool:
        """True if this service can run against the given connection."""

    def build_vm(self, connection: Connection) -> ComponentVM:
        """Construct the service's content VM tree for this connection."""
```

The `descriptor` is a `ClassVar` so the registry can introspect it
without instantiating. `build_vm` returns whatever facade /
`ComponentVM` the service decides to host — `ContentHostVM` only
needs a `construct → destruct → dispose` surface.

## 2. Steps
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

4. **Reuse existing VM families** where possible:
    - For storage-like services (lists with hierarchy): the file-
      manager VMs in `vm/file_manager/` work as-is — write a new
      `FileSystemProvider` (see `src/aws_tui/domain/filesystem.py`
      for the protocol, and §2 / §3 of the design spec for the
      architectural shape) and reuse `PaneVM` + `DualPaneVM`.
    - For flat resource lists (EC2 instances, IAM users): write a new
      `ListPaneVM` under `vm/<service>/` and a corresponding widget
      family under `ui/widgets/<service>/`.

5. **Layer rules.** Services live one layer above domain, so they may
    import from `domain/`, `infra/`, and the public VM surface
    (`vm/services_protocol.py`, `vm/messages.py`, and the file-manager
    VMs in `vm/file_manager/` for storage-like services that reuse
    `PaneVM`/`DualPaneVM`). The only hard ban is `ui/` (no Textual
    widget imports) and Textual itself — enforced by
    `scripts/check-layers.sh`. See §3 below for the full cheat-sheet.

6. **Tests.** Add unit tests under `tests/unit/services/<name>/` and,
    if your service touches AWS, integration tests under
    `tests/integration/services/<name>/` against `moto` or a vendor
    container.

7. **Update docs.** Add any vendor / API quirks to
    `docs/connections.md`. Update the README's features list.

## 3. Layer rules cheat-sheet for services
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

## 4. Future: entry-point discovery
v1.1 promotes the registry to
`importlib.metadata.entry_points(group="aws_tui.services")` so third-
party packages can ship services without forking. The same `Service`
protocol applies.

## 5. Reference: the S3 service
`src/aws_tui/services/s3/service.py` is the only concrete service in
v0.7.0. Read it end-to-end (~80 lines):

- `descriptor` declares `id = "s3"`, label `"S3"`, icon `"⎙"`.
- `supports()` accepts both `aws` and `s3-compatible` connections.
- `build_vm(connection)` composes
  `DualPaneVM(left=PaneVM(S3FS), right=PaneVM(LocalFS))` each call.
- An optional `s3_fs_factory` test hook lets unit tests swap S3FS
  for `InMemoryFS` so no AWS calls leak in CI.
- `bind_hub(hub)` late-wires the hub since the service is registered
  before `RootVM` has its hub.
