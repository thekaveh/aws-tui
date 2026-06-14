# Adding a new service

> Lands in M4 when the `Service` protocol is real. This is a forward reference.

aws-tui's service-plugin spine is designed so adding a top-level AWS (or S3-compatible) service is additive: a new folder under `src/aws_tui/services/<name>/` and one line in `src/aws_tui/services/__init__.py`. No other layer changes.

## The `Service` protocol

```python
from typing import Protocol
from aws_tui.infra.connection_resolver import Connection
from vmx import ComponentVM

class Service(Protocol):
    id: str          # stable identifier, e.g. "s3"
    label: str       # what shows in the services menu, e.g. "S3"
    icon: str        # single-char visual hint

    def supports(self, connection: Connection) -> bool:
        """True if this service can run against the given connection."""

    def build_vm(self, connection: Connection) -> ComponentVM:
        """Construct the service's content VM tree for this connection."""

    def build_view(self, vm: ComponentVM):  # Textual Widget
        """Construct the service's content view bound to the VM."""
```

## Steps

1. **Create the folder**: `src/aws_tui/services/<name>/{__init__.py, service.py, view.py}`.
2. **Implement the `Service` protocol** in `service.py`.
3. **Register**: in `src/aws_tui/services/__init__.py`, add `register("<name>", <Name>Service())`.
4. **Filter by connection kind**: e.g. `supports = lambda c: c.kind == "aws"` for AWS-only services.
5. **Reuse `vm/file_manager/`** for any storage-like service, or add a new VM family under `vm/list_manager/` etc.
6. **Add tests** under `tests/unit/services/<name>/` and integration under `tests/integration/services/<name>/`.
7. **Update `docs/connections.md`** with any vendor quirks.

## Future: entry-point discovery

v1.1 promotes the registry to `importlib.metadata.entry_points(group="aws_tui.services")` so third-party packages can ship services without forking. The same `Service` protocol applies.
