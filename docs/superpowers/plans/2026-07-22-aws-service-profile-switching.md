# 1. Shared AWS Service Profile Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Shift+S` rebuild any single-context AWS service under the next configured AWS connection while preserving S3's independent per-pane source behavior.

**Architecture:** `RootVM` remains the canonical owner of the active connection and gains one atomic connection-and-service switch operation. `AwsTuiApp` chooses candidates from `ConnectionResolver`, probes authentication, invokes the root operation, and remounts the same service view. A small shared source context gives EMR, Glue, and Athena one display contract, while a UI view factory removes the app shell's growing service-specific branch.

**Tech Stack:** Python 3.11-3.13, Textual 8.x, VMx 3.1.x, aioboto3/botocore, pytest, pytest-textual-snapshot.

## 1.1. Global Constraints

- Branch from `develop`; do not implement on `main` or `develop` directly.
- S3 keeps one independent source per pane.
- EMR Serverless, Glue, and Athena use the one active AWS connection held by `RootVM`.
- `Shift+S` remains action ID `app.swap_source` and means "switch source" everywhere.
- EMR next-application moves to action ID `emr.next_application` with default key `Shift+A`.
- Service-specific `AccessDenied` must not add a connection to `unreachable_connections`.
- Clear outgoing content before loading the replacement source.
- Do not add Glue, Athena, Iceberg, or SQL dependencies in this foundation plan.
- Preserve the enforced View -> ViewModel -> Service -> Domain -> Infrastructure dependency direction.

---

### 1.1.1. Task 1: Add the Shared Service Source Context

**Files:**
- Create: `src/aws_tui/vm/service_source_vm.py`
- Modify: `src/aws_tui/vm/emr_serverless/page_vm.py`
- Modify: `src/aws_tui/services/emr_serverless/service.py`
- Test: `tests/unit/vm/test_service_source_vm.py`
- Test: `tests/unit/vm/emr_serverless/test_page_vm.py`

**Interfaces:**
- Consumes: `aws_tui.infra.connection_resolver.Connection`
- Produces: `ServiceSourceContext.from_connection(connection) -> ServiceSourceContext`
- Produces: `ServiceSourceContext.connection_key -> tuple[str, str]`
- Produces: `ServiceSourceContext.label -> str`
- Produces: `SelectionScope(service_id, connection_name, region)`
- Produces: `ServiceSelectionStore.get(scope, key) -> str | None`
- Produces: `ServiceSelectionStore.set(scope, key, value) -> None`
- Produces: `EmrServerlessPageVM.source -> ServiceSourceContext`

- [ ] **Step 1: Write failing source-context tests**

```python
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.service_source_vm import ServiceSourceContext


def test_source_context_formats_profile_and_region() -> None:
    connection = Connection(
        name="analytics-prod",
        kind="aws",
        profile="prod-sso",
        region="us-west-2",
        source="config",
    )
    source = ServiceSourceContext.from_connection(connection)
    assert source.connection_key == ("analytics-prod", "us-west-2")
    assert source.label == "analytics-prod · prod-sso · us-west-2"


def test_source_context_does_not_repeat_matching_profile_name() -> None:
    connection = Connection(
        name="dev",
        kind="aws",
        profile="dev",
        region="us-east-1",
        source="auto-aws-profile",
    )
    assert ServiceSourceContext.from_connection(connection).label == "dev · us-east-1"


def test_selection_store_is_scoped_by_service_connection_and_region() -> None:
    store = ServiceSelectionStore()
    dev = SelectionScope("emr-serverless", "dev", "us-east-1")
    prod = SelectionScope("emr-serverless", "prod", "us-east-1")
    store.set(dev, "application_id", "dev-app")
    store.set(prod, "application_id", "prod-app")
    assert store.get(dev, "application_id") == "dev-app"
    assert store.get(prod, "application_id") == "prod-app"
```

Add this assertion to the existing EMR page-VM fixture:

```python
assert page.source.connection_key == ("dev", "us-east-1")
assert page.source.label == "dev · us-east-1"
```

- [ ] **Step 2: Run the tests and confirm the missing module/property failures**

Run:

```bash
uv run pytest tests/unit/vm/test_service_source_vm.py tests/unit/vm/emr_serverless/test_page_vm.py -q
```

Expected: collection fails because `aws_tui.vm.service_source_vm` and `EmrServerlessPageVM.source` do not exist.

- [ ] **Step 3: Implement the immutable source context and expose it from EMR**

Create `service_source_vm.py` with this public surface:

```python
from __future__ import annotations

from dataclasses import dataclass

from aws_tui.infra.connection_resolver import Connection


@dataclass(frozen=True, slots=True)
class ServiceSourceContext:
    connection_name: str
    profile: str | None
    region: str

    @classmethod
    def from_connection(cls, connection: Connection) -> ServiceSourceContext:
        return cls(
            connection_name=connection.name,
            profile=connection.profile,
            region=connection.region,
        )

    @property
    def connection_key(self) -> tuple[str, str]:
        return self.connection_name, self.region

    @property
    def label(self) -> str:
        parts = [self.connection_name]
        if self.profile and self.profile != self.connection_name:
            parts.append(self.profile)
        parts.append(self.region)
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class SelectionScope:
    service_id: str
    connection_name: str
    region: str


class ServiceSelectionStore:
    def __init__(self) -> None:
        self._values: dict[tuple[SelectionScope, str], str] = {}

    def get(self, scope: SelectionScope, key: str) -> str | None:
        return self._values.get((scope, key))

    def set(self, scope: SelectionScope, key: str, value: str) -> None:
        self._values[(scope, key)] = value

    def discard(self, scope: SelectionScope, key: str) -> None:
        self._values.pop((scope, key), None)


__all__ = ["SelectionScope", "ServiceSelectionStore", "ServiceSourceContext"]
```

In `EmrServerlessPageVM.__init__`, construct `self._source` from the existing `connection` argument and expose:

```python
@property
def source(self) -> ServiceSourceContext:
    return self._source
```

Give `EmrServerlessService` one long-lived `ServiceSelectionStore` and inject it into every page VM. `EmrServerlessPageVM` writes `application_id` after a successful selection and, after applications load, restores the stored ID only when it still exists. The store belongs to the service plugin so disposing a page does not erase per-profile memory.

- [ ] **Step 4: Run focused VM tests**

Run:

```bash
uv run pytest tests/unit/vm/test_service_source_vm.py tests/unit/vm/emr_serverless/test_page_vm.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the source context**

```bash
git add src/aws_tui/vm/service_source_vm.py src/aws_tui/vm/emr_serverless/page_vm.py src/aws_tui/services/emr_serverless/service.py tests/unit/vm/test_service_source_vm.py tests/unit/vm/emr_serverless/test_page_vm.py
git commit -m "feat: add shared AWS service source context"
```

### 1.1.2. Task 2: Make Connection and Service Switching Atomic in RootVM

**Files:**
- Modify: `src/aws_tui/vm/root_vm.py`
- Modify: `src/aws_tui/vm/content_host_vm.py`
- Test: `tests/unit/vm/test_root_vm.py`
- Test: `tests/unit/vm/test_content_host_vm.py`

**Interfaces:**
- Consumes: `RootVM.switch_connection_with(connection, auth_state)`
- Produces: `RootVM.active_connection -> Connection | None`
- Produces: `RootVM.active_auth_state -> TokenState | None`
- Produces: `RootVM.switch_connection_and_service(connection, auth_state, service_id) -> None`
- Produces: optional hosted-VM `async shutdown() -> None` lifecycle hook

- [ ] **Step 1: Add failing atomic-switch tests**

```python
async def test_switch_connection_and_service_rebuilds_same_service() -> None:
    emr = _FakeService("emr-serverless", accepts_s3=False)
    root = _build_root(emr)
    dev = _aws_conn("dev")
    prod = _aws_conn("prod")

    await root.switch_connection_with(dev, TokenState.CONNECTED)
    await root.switch_service("emr-serverless")
    old_vm = root.content_host.current

    await root.switch_connection_and_service(
        prod,
        TokenState.CONNECTED,
        "emr-serverless",
    )

    assert old_vm is not None
    assert old_vm.status == ConstructionStatus.DISPOSED
    assert root.active_connection == prod
    assert root.active_auth_state is TokenState.CONNECTED
    assert root.content_host.current_id == "emr-serverless"
    assert len(emr.constructed) == 2
    root.dispose()


async def test_atomic_switch_rejects_unsupported_connection_before_disposal() -> None:
    emr = _FakeService("emr-serverless", accepts_s3=False)
    root = _build_root(emr)
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("emr-serverless")
    old_vm = root.content_host.current

    with pytest.raises(RuntimeError, match="does not support"):
        await root.switch_connection_and_service(
            _minio_conn(),
            TokenState.CONNECTED,
            "emr-serverless",
        )

    assert root.content_host.current is old_vm
    root.dispose()


async def test_content_host_awaits_shutdown_before_dispose() -> None:
    events: list[str] = []
    old = _AsyncShutdownVM(events)
    new = _LifecycleVM(events)
    host = _build_host()
    await host.set_content(old, service_id="old")
    await host.set_content(new, service_id="new")
    assert events.index("old.shutdown") < events.index("old.dispose")
```

- [ ] **Step 2: Run the root tests to verify failure**

Run:

```bash
uv run pytest tests/unit/vm/test_root_vm.py -q
```

Expected: failures report missing `active_connection`, `active_auth_state`, and `switch_connection_and_service`.

- [ ] **Step 3: Implement properties and the atomic operation**

Add read-only properties:

```python
@property
def active_connection(self) -> Connection | None:
    return self._connection

@property
def active_auth_state(self) -> TokenState | None:
    return self._auth_state
```

Add the operation beside `switch_connection_with`:

```python
async def switch_connection_and_service(
    self,
    connection: Connection,
    auth_state: TokenState,
    service_id: str,
) -> None:
    service = self._registry.get(service_id)
    if not service.supports(connection):
        raise RuntimeError(
            f"service {service_id!r} does not support connection {connection.name!r}"
        )
    await self.switch_connection_with(connection, auth_state)
    await self.switch_service(service_id)
```

Do not catch construction failures here. Existing `switch_service` menu rollback and app-level logging remain authoritative.

In `ContentHostVM.set_content`, after cancelling setup and before disposing the outgoing VM, await an optional shutdown hook:

```python
async def _shutdown_current(self) -> None:
    if self._current is None:
        return
    shutdown = getattr(self._current, "shutdown", None)
    if not callable(shutdown):
        return
    result = shutdown()
    if inspect.isawaitable(result):
        await result
```

Call `_shutdown_current()` only from async replacement paths. Synchronous `dispose()` remains a final local cleanup path; app shutdown must await the current page's hook before invoking the root's synchronous dispose cascade.

- [ ] **Step 4: Run root and content-host tests**

Run:

```bash
uv run pytest tests/unit/vm/test_root_vm.py tests/unit/vm/test_content_host_vm.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit atomic orchestration**

```bash
git add src/aws_tui/vm/root_vm.py src/aws_tui/vm/content_host_vm.py tests/unit/vm/test_root_vm.py tests/unit/vm/test_content_host_vm.py
git commit -m "feat: switch AWS connection and service atomically"
```

### 1.1.3. Task 3: Route Service Widgets Through One View Factory

**Files:**
- Create: `src/aws_tui/ui/widgets/service_view_factory.py`
- Modify: `src/aws_tui/app.py`
- Test: `tests/unit/ui/test_service_view_factory.py`
- Test: `tests/integration/test_emr_page.py`

**Interfaces:**
- Produces: `build_service_view(service_id, vm, hub, focus_coordinator) -> Widget`
- Consumes: existing `DualPane` and `EmrServerlessPage` constructors

- [ ] **Step 1: Write failing factory tests**

```python
def test_factory_builds_dual_pane_for_s3() -> None:
    view = build_service_view(
        "s3",
        dual_pane_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
    )
    assert isinstance(view, DualPane)
    assert view.id == "content-dual-pane"


def test_factory_builds_emr_page() -> None:
    view = build_service_view(
        "emr-serverless",
        emr_page_vm,
        hub=hub,
        focus_coordinator=focus_coordinator,
    )
    assert isinstance(view, EmrServerlessPage)
    assert view.id == "content-emr-page"


def test_factory_rejects_unknown_service() -> None:
    with pytest.raises(ValueError, match="unknown service view"):
        build_service_view("unknown", object(), hub=hub, focus_coordinator=focus_coordinator)
```

Reuse the existing VM builders from UI unit tests rather than constructing boto clients.

- [ ] **Step 2: Run the factory test and verify the import failure**

Run:

```bash
uv run pytest tests/unit/ui/test_service_view_factory.py -q
```

Expected: collection fails because `service_view_factory` does not exist.

- [ ] **Step 3: Implement the typed factory and simplify both mount paths**

Create:

```python
from __future__ import annotations

from typing import Any

from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui.widgets.dual_pane import DualPane
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM


def build_service_view(
    service_id: str,
    vm: Any,
    *,
    hub: MessageHub[Message],
    focus_coordinator: FocusCoordinatorVM,
) -> Widget:
    if service_id == "s3":
        return DualPane(
            vm,
            hub=hub,
            focus_coordinator=focus_coordinator,
            id="content-dual-pane",
        )
    if service_id == "emr-serverless":
        return EmrServerlessPage(
            vm,
            hub=hub,
            focus_coordinator=focus_coordinator,
            id="content-emr-page",
        )
    raise ValueError(f"unknown service view: {service_id}")


__all__ = ["build_service_view"]
```

Replace the duplicated S3/EMR branches in `_mount_initial_service_view` and `_mount_service_view` with `build_service_view(...)`. Preserve settings mounting and all existing host teardown/error logging.

- [ ] **Step 4: Run factory and service-mount tests**

Run:

```bash
uv run pytest tests/unit/ui/test_service_view_factory.py tests/integration/test_emr_page.py tests/integration/test_settings_flow.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the view factory**

```bash
git add src/aws_tui/ui/widgets/service_view_factory.py src/aws_tui/app.py tests/unit/ui/test_service_view_factory.py tests/integration/test_emr_page.py
git commit -m "refactor: centralize service view construction"
```

### 1.1.4. Task 4: Make Shift+S Cycle Single-Context AWS Connections

**Files:**
- Modify: `src/aws_tui/app.py`
- Test: `tests/integration/test_service_source_swap.py`
- Test: `tests/integration/test_pane_source_swap.py`
- Test: `tests/integration/test_swap_source_skips_unreachable.py`

**Interfaces:**
- Produces: `_service_source_candidates(ctx, service_id) -> tuple[Connection, ...]`
- Produces: `_next_service_source(candidates, active) -> Connection | None`
- Produces: `AwsTuiApp._swap_single_context_source(service_id) -> None`
- Consumes: `RootVM.switch_connection_and_service(...)`

- [ ] **Step 1: Write candidate and integration tests**

```python
def test_service_candidates_include_only_supported_aws_connections(tmp_path: Path) -> None:
    ctx = build_app_context(config_dir=_three_source_config(tmp_path), cache_dir=tmp_path / "cache")
    candidates = _service_source_candidates(ctx, "emr-serverless")
    assert [(c.name, c.region) for c in candidates] == [
        ("dev", "us-east-1"),
        ("prod-west", "us-west-2"),
    ]


def test_next_service_source_wraps_by_connection_name_and_region() -> None:
    dev, prod = _aws_connections()
    assert _next_service_source((dev, prod), dev) == prod
    assert _next_service_source((dev, prod), prod) == dev


@pytest.mark.asyncio
async def test_shift_s_rebuilds_emr_under_next_profile(tmp_path: Path) -> None:
    ctx, factories = _multi_profile_emr_context(tmp_path)
    app = AwsTuiApp(ctx)
    async with app.run_test() as pilot:
        ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
        await _await_service_mount(pilot, app)
        before = ctx.root_vm.content_host.current
        await app.action_swap_source()
        await _await_service_mount(pilot, app)
        after = ctx.root_vm.content_host.current
        assert before is not after
        assert ctx.root_vm.active_connection is not None
        assert ctx.root_vm.active_connection.name == "prod-west"
        assert after.source.connection_key == ("prod-west", "us-west-2")
        assert factories.calls == ["dev", "prod-west"]
```

Add an assertion to the existing S3 source-swap integration test that `root_vm.active_connection` is unchanged when only one pane cycles.

- [ ] **Step 2: Run source-switch tests and verify failure**

Run:

```bash
uv run pytest tests/integration/test_service_source_swap.py tests/integration/test_pane_source_swap.py tests/integration/test_swap_source_skips_unreachable.py -q
```

Expected: new tests fail because the helpers and single-context branch do not exist.

- [ ] **Step 3: Implement candidate selection and app orchestration**

Add pure helpers near `_build_swap_candidates`:

```python
def _service_source_candidates(ctx: AppContext, service_id: str) -> tuple[Connection, ...]:
    service = ctx.registry.get(service_id)
    return tuple(
        connection
        for connection in ctx.connection_resolver.list()
        if connection.kind == "aws" and service.supports(connection)
    )


def _next_service_source(
    candidates: tuple[Connection, ...],
    active: Connection | None,
) -> Connection | None:
    if not candidates:
        return None
    if active is None:
        return candidates[0]
    active_key = active.name, active.region
    for index, connection in enumerate(candidates):
        if (connection.name, connection.region) == active_key:
            return candidates[(index + 1) % len(candidates)]
    return candidates[0]
```

At the start of `action_swap_source`, preserve the existing S3 path when `_dual_pane()` is non-`None`. Otherwise, for the selected non-settings service, call:

```python
async def _swap_single_context_source(self, service_id: str) -> None:
    ctx = self._app_ctx
    target = _next_service_source(
        _service_source_candidates(ctx, service_id),
        ctx.root_vm.active_connection,
    )
    if target is None:
        notifications.advise(
            ctx.root_vm.chrome.toast_stack,
            subject="Source",
            message="no AWS profiles configured",
        )
        return
    try:
        auth_state = ctx.aws_session.probe_token(target).state
    except Exception as exc:
        ctx.log_sink.warning(
            "service_source.probe_failed",
            service_id=service_id,
            connection=target.name,
            error_type=type(exc).__name__,
        )
        auth_state = TokenState.MISSING
    await ctx.root_vm.switch_connection_and_service(target, auth_state, service_id)
    await self._mount_service_view(service_id)
```

Do not consult or mutate `unreachable_connections` in this path. Service-scoped failures remain visible after mount.

- [ ] **Step 4: Run all source-switch tests**

Run:

```bash
uv run pytest tests/integration/test_service_source_swap.py tests/integration/test_pane_source_swap.py tests/integration/test_swap_source_recovery.py tests/integration/test_swap_source_skips_unreachable.py -q
```

Expected: all tests pass and S3 behavior remains unchanged.

- [ ] **Step 5: Commit service source switching**

```bash
git add src/aws_tui/app.py tests/integration/test_service_source_swap.py tests/integration/test_pane_source_swap.py tests/integration/test_swap_source_skips_unreachable.py
git commit -m "feat: switch profiles within AWS service pages"
```

### 1.1.5. Task 5: Separate EMR Application Cycling From Source Switching

**Files:**
- Modify: `src/aws_tui/infra/keymap_store.py`
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Modify: `src/aws_tui/ui/bindings.py`
- Modify: `src/aws_tui/app.py`
- Modify: `src/aws_tui/ui/widgets/emr_serverless/page.py`
- Modify: `docs/keybindings.md`
- Test: `tests/unit/infra/test_keymap_store.py`
- Test: `tests/unit/vm/chrome/test_hint_legend_vm.py`
- Test: `tests/integration/test_keybinding_wiring.py`
- Test: `tests/integration/test_emr_page.py`

**Interfaces:**
- Produces: action ID `emr.next_application`
- Produces: default key tuple `("A",)`
- Consumes: `EmrServerlessPageVM.cycle_application(1)`

- [ ] **Step 1: Write failing keymap and action-routing assertions**

```python
def test_emr_next_application_has_dedicated_binding() -> None:
    keymap = KeymapStore()
    assert keymap.resolve("app.swap_source") == ("S",)
    assert keymap.resolve("emr.next_application") == ("A",)


async def test_emr_shift_s_switches_profile_and_shift_a_cycles_application(...) -> None:
    initial_source = page.vm.source.connection_key
    initial_application = page.vm.applications.selected_id
    await pilot.press("S")
    assert page.vm.source.connection_key != initial_source
    assert page.vm.applications.selected_id != initial_application or page.vm.applications.items
    selected_after_source_switch = page.vm.applications.selected_id
    await pilot.press("A")
    assert page.vm.applications.selected_id != selected_after_source_switch
```

Use profile-specific EMR fake data so the source-switch assertion does not depend on identical application IDs.

- [ ] **Step 2: Run keybinding tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/infra/test_keymap_store.py tests/unit/vm/chrome/test_hint_legend_vm.py tests/integration/test_keybinding_wiring.py tests/integration/test_emr_page.py -q
```

Expected: failures report unknown `emr.next_application` and the old EMR `Shift+S` behavior.

- [ ] **Step 3: Register and route the dedicated action**

Add to `KeymapStore.DEFAULT_BINDINGS`:

```python
"emr.next_application": ("A",),
```

Add the action to EMR's hint actions and labels:

```python
"emr.next_application": "switch app",
```

Remove the `app.swap_source` special-label branch for EMR so it always renders `switch source`. Register an app handler:

```python
self._actions.register("emr.next_application", self.action_next_emr_application)

async def action_next_emr_application(self) -> None:
    page = self._emr_page()
    if page is not None:
        await page.vm.cycle_application(1)
```

Delete the old EMR application branch from `action_swap_source`. Update Textual action descriptions and `docs/keybindings.md` to show `Shift+S` for profile source and `Shift+A` for next application.

- [ ] **Step 4: Run keymap, legend, and EMR integration tests**

Run:

```bash
uv run pytest tests/unit/infra/test_keymap_store.py tests/unit/vm/chrome/test_hint_legend_vm.py tests/integration/test_keybinding_wiring.py tests/integration/test_emr_page.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the binding split**

```bash
git add src/aws_tui/infra/keymap_store.py src/aws_tui/vm/chrome/hint_legend_vm.py src/aws_tui/ui/bindings.py src/aws_tui/app.py src/aws_tui/ui/widgets/emr_serverless/page.py docs/keybindings.md tests/unit/infra/test_keymap_store.py tests/unit/vm/chrome/test_hint_legend_vm.py tests/integration/test_keybinding_wiring.py tests/integration/test_emr_page.py
git commit -m "feat: separate EMR app and profile switching"
```

### 1.1.6. Task 6: Render the EMR Source Header and Complete Foundation Verification

**Files:**
- Create: `src/aws_tui/ui/widgets/service_source_header.py`
- Modify: `src/aws_tui/ui/widgets/emr_serverless/page.py`
- Modify: `src/aws_tui/ui/themes/amber.tcss`
- Modify: `src/aws_tui/ui/themes/carbon.tcss`
- Modify: `src/aws_tui/ui/themes/dracula.tcss`
- Modify: `src/aws_tui/ui/themes/github-light.tcss`
- Modify: `src/aws_tui/ui/themes/gruvbox-dark.tcss`
- Modify: `src/aws_tui/ui/themes/lattice.tcss`
- Modify: `src/aws_tui/ui/themes/nord.tcss`
- Modify: `src/aws_tui/ui/themes/one-light.tcss`
- Modify: `src/aws_tui/ui/themes/solarized-light.tcss`
- Modify: `src/aws_tui/ui/themes/voidline.tcss`
- Modify: `docs/architecture.md`
- Modify: `docs/connections.md`
- Modify: `docs/contract-ledger.md`
- Test: `tests/unit/ui/test_service_source_header.py`
- Test: `tests/snapshot/test_emr.py`
- Test: `tests/e2e/test_journeys.py`

**Interfaces:**
- Produces: `ServiceSourceHeader(source: ServiceSourceContext, id: str | None = None)`
- Consumes: `EmrServerlessPageVM.source`

- [ ] **Step 1: Add header unit and snapshot content assertions**

```python
def test_source_header_renders_connection_profile_and_region() -> None:
    header = ServiceSourceHeader(
        ServiceSourceContext("analytics-prod", "prod-sso", "us-west-2")
    )
    assert header.render().plain == "analytics-prod · prod-sso · us-west-2"
```

In each EMR populated snapshot test, add:

```python
assert "demo-prod · us-east-1" in svg
```

Add an E2E journey that opens EMR, presses `S`, waits for mount completion, and asserts the visible source label changed while the selected nav service remains `emr-serverless`.

- [ ] **Step 2: Run focused UI tests and verify failure**

Run:

```bash
uv run pytest tests/unit/ui/test_service_source_header.py tests/snapshot/test_emr.py tests/e2e/test_journeys.py -q
```

Expected: missing header module and absent source text failures.

- [ ] **Step 3: Implement the compact reusable header**

Create a `Static` subclass that renders `source.label`, has stable one-row height, and uses only theme tokens. Mount it above EMR's existing application strip:

```python
yield ServiceSourceHeader(self.vm.source, id="emr-source-header")
yield ApplicationPicker(...)
```

Add one shared TCSS rule to each shipped theme using that theme's existing `$bg-elev`, `$rule-dim`, and `$text-muted` tokens. Do not add hex colors or a framed card.

Document the two source scopes, the new EMR key, service-scoped access failures, and connection/region identity in the listed docs.

- [ ] **Step 4: Regenerate snapshots and run the foundation verification matrix**

Run:

```bash
uv run pytest tests/snapshot/test_emr.py --snapshot-update
uv run pytest tests/unit tests/integration tests/e2e -q
uv run pytest tests/snapshot -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
./scripts/check-layers.sh
uv run pytest tests/docs -q
```

Expected: all required checks pass; only documented optional Cairo skips are allowed.

- [ ] **Step 5: Commit the source header and foundation docs**

```bash
git add src/aws_tui/ui/widgets/service_source_header.py src/aws_tui/ui/widgets/emr_serverless/page.py src/aws_tui/ui/themes docs/architecture.md docs/connections.md docs/contract-ledger.md tests/unit/ui/test_service_source_header.py tests/snapshot/test_emr.py tests/snapshot/__snapshots__/test_emr tests/e2e/test_journeys.py
git commit -m "feat: surface active AWS profile on service pages"
```
