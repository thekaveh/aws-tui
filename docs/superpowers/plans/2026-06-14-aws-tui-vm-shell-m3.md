# aws-tui M3 (VM shell) Implementation Plan

> **For agentic workers:** Compact-plan format. Spec is the source of truth — read §4 (UI), §5 (MVVM wiring), §6 (auth+lifecycle). VMx Python source at `vendor/vmx/langs/python/src/vmx/` is the second source of truth.

**Goal:** Land the entire `vm/` layer except `vm/file_manager/` (that's M4). Build the application shell — root, services menu, content host, chrome (hint legend + status bar + toast stack), and overlays (command palette, confirmation, quick look). All VMx-based, pure viewmodels, NO Textual imports allowed (enforced by `./scripts/check-layers.sh`).

**Architecture:** All VMs **wrap** VMx primitives (`ComponentVM`, `CompositeVM`, `AggregateVM2..6`) as `_inner` facades — VMx VMs are not subclassable; they are built via immutable fluent builders (see Task 1 cheatsheet for the facade pattern). Commands via `RelayCommand`. Reactive properties published via `MessageHub.send(PropertyChangedMessage.create(self, name, "prop"))`. Derived state via `DerivedProperty` from `BehaviorSubject` sources. Custom messages for `ConnectionChanged`, `ThemeChanged`, `AuthExpired`, `TransferProgress`, `KeymapChanged`, `FocusChanged` — implemented as `@dataclass(frozen=True, slots=True)` with `sender_name: str` + `sender_object` property to satisfy the `Message` protocol. Lifecycle: `RootVM.construct()` cascades depth-first (synchronously); service switch swaps `ContentHostVM`'s child via `dispose` + `construct`; `RootVM.dispose()` on app exit (preceded by async drain — but the drain itself is owned by infra in M1, not us).

**Tech Stack:** VMx (already installed via submodule + path dep). Pure Python — no Textual, no boto3.

**Reference:** Open `vendor/vmx/langs/python/src/vmx/` to understand the actual APIs you'll subclass. Look at `vendor/vmx/langs/python/tests/` for usage patterns.

---

## Task 1: VMx familiarization spike

**Output:** A short markdown note at `docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md` (gitignored under `notes/`? actually keep it tracked under `docs/`) capturing:

- The exact import paths for `ComponentVM`, `CompositeVM`, `GroupVM`, `AggregateVM1..6`, `RelayCommand`, `MessageHub`, `ConstructionStatus`, `PropertyChangedMessage`, `ConstructionStatusChangedMessage`, `CollectionChangedEvent`.
- The exact import paths for capabilities (`ISelectable`, `IFilterable`, `IPageable`, `IExpandable`, etc.) and state helpers (`SearchableState`, `ExpandableState`, `DerivedProperty`).
- The constructor signatures you'll actually call.
- Any opt-in subpackages we need (notifications: `NotificationVM`, `ConfirmationVM`, `ConfirmHelper`).
- The `dispose()` / `destruct()` / `construct()` lifecycle method signatures.
- The pattern for declaring a reactive property (subscribed via `MessageHub.subscribe<PropertyChangedMessage<T>>` or similar).
- A 30-line example VM you've sanity-tested against the installed VMx (run `uv run python -c "..."` to confirm it constructs).

Read `vendor/vmx/langs/python/src/vmx/__init__.py`, `tests/`, `examples/python/console/hello_vmx/`, `examples/python/textual/inspector/` to extract these.

This becomes the contract reference for Tasks 2-9. **No source code in `src/aws_tui/vm/` yet.**

**Acceptance:** Cheatsheet committed; a sanity ComponentVM in `tests/unit/vm/test_vmx_smoke.py` constructs and disposes cleanly via VMx.

---

## Task 2: `vm/messages.py` — custom message envelopes

**Files:**
- Create: `src/aws_tui/vm/messages.py`
- Create: `tests/unit/vm/__init__.py`
- Create: `tests/unit/vm/test_messages.py`

```python
@dataclass(frozen=True, slots=True)
class ConnectionChangedMessage:
    connection: Connection      # from infra.connection_resolver
    auth_state: TokenState      # from infra.aws_session

@dataclass(frozen=True, slots=True)
class ThemeChangedMessage:
    name: str

@dataclass(frozen=True, slots=True)
class AuthExpiredMessage:
    connection_name: str
    reason: str                 # "expired" | "missing" | "load_error"

@dataclass(frozen=True, slots=True)
class TransferProgressMessage:
    transfer_id: str
    bytes_transferred: int
    bytes_total: int | None
    state: str                  # pending | running | paused | completed | failed | cancelled

@dataclass(frozen=True, slots=True)
class KeymapChangedMessage:
    action: str
    new_keys: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class FocusChangedMessage:
    focused_vm_id: str          # the VM id that gained focus; HintLegendVM listens to this
```

Layer rule reminder: `vm/messages.py` may import from `aws_tui.infra` (for `Connection` and `TokenState` dataclass types only). No textual, no boto3.

**Acceptance:** Each message is a frozen slots-dataclass, importable from `aws_tui.vm.messages`. Round-trip tested. Strict mypy clean.

---

## Task 3: `vm/chrome/toast_vm.py` + `vm/chrome/toast_stack_vm.py`

**Files:**
- Create: `src/aws_tui/vm/chrome/__init__.py` (already exists as stub; leave docstring)
- Create: `src/aws_tui/vm/chrome/toast_vm.py`
- Create: `src/aws_tui/vm/chrome/toast_stack_vm.py`
- Create: `tests/unit/vm/chrome/__init__.py`
- Create: `tests/unit/vm/chrome/test_toast.py`

**Contract:**

```python
class ToastLevel(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

@dataclass(frozen=True, slots=True)
class ToastModel:
    id: str
    text: str
    level: ToastLevel
    sticky: bool                # True = stay until dismissed; False = auto-dismiss after timeout
    timeout_seconds: float | None
    action_label: str | None    # for "press a to authenticate" style
    action_action: str | None   # action id resolved by KeymapStore

class ToastVM(ComponentVM[ToastModel]):                  # VMx generic
    """Single toast. Exposes `dismiss` command."""

class ToastStackVM(CompositeVM[ToastVM]):
    """Owns the toast collection. Exposes `raise_toast(model) -> ToastVM` and `dismiss(toast_id)`."""
```

**Acceptance:**
- `raise_toast(...)` adds a `ToastVM` to the collection and fires `CollectionChangedEvent`.
- `dismiss(id)` removes it.
- Non-sticky toasts auto-dismiss after `timeout_seconds` (use a VMx-friendly timer — likely an asyncio task spawned at toast construction, cancelled on dismiss/dispose).
- Strict mypy + layer rules clean.

---

## Task 4: `vm/chrome/status_bar_vm.py`

**Files:**
- Create: `src/aws_tui/vm/chrome/status_bar_vm.py`
- Create: `tests/unit/vm/chrome/test_status_bar.py`

**Contract:**

```python
class StatusBarVM(ComponentVM[None]):
    """Reactive status bar.

    Properties (all DerivedProperty over inputs):
      - connection_label: str       e.g. "kaveh-dev (aws)"
      - region: str
      - auth_indicator: str         e.g. "sso ok" / "login needed" / "keys"
      - transfers_summary: str      e.g. "transfers idle" / "2 active . 12.4 M / 18.0 M"
    """

    def __init__(self, *, message_hub: MessageHub) -> None: ...
    def update_connection(self, conn: Connection, auth_state: TokenState) -> None: ...
    def update_transfers(self, active_count: int, bytes_done: int, bytes_total: int | None) -> None: ...
```

Listens for `ConnectionChangedMessage` and `TransferProgressMessage` from the hub.

**Acceptance:**
- Setting a connection updates `connection_label` and `region`.
- 0 transfers → `"transfers idle"`.
- N transfers → `"N active . done / total"` with humanized bytes.
- Strict mypy clean.

---

## Task 5: `vm/chrome/hint_legend_vm.py`

**Files:**
- Create: `src/aws_tui/vm/chrome/hint_legend_vm.py`
- Create: `tests/unit/vm/chrome/test_hint_legend.py`

**Contract:**

```python
@dataclass(frozen=True, slots=True)
class HintAction:
    key_label: str    # e.g. "Enter" or "c"
    action_label: str # e.g. "open" or "copy"

class HintLegendVM(ComponentVM[None]):
    """Re-derives `actions: tuple[HintAction, ...]` from `FocusChangedMessage`.

    Each focusable VM declares a `hint_actions` property the legend can read.
    """

    def __init__(self, *, message_hub: MessageHub, keymap: KeymapStore) -> None: ...
```

**Acceptance:**
- Default (no focus) → empty actions or app-level fallbacks (`: cmd  ? help`).
- After `FocusChangedMessage{vm_id="pane.left"}` for a pane that exposes `["open", "preview", "copy", "move", "delete"]` → those 5 actions appear with their resolved keys.
- Strict mypy clean.

---

## Task 6: `vm/chrome/command_palette_vm.py`

**Files:**
- Create: `src/aws_tui/vm/chrome/command_palette_vm.py`
- Create: `tests/unit/vm/chrome/test_command_palette.py`

**Contract:**

```python
@dataclass(frozen=True, slots=True)
class PaletteEntry:
    id: str           # e.g. "connection.switch.minio-local"
    label: str        # what user sees
    category: str     # "connection" | "theme" | "bucket" | ...
    keywords: tuple[str, ...] = ()    # for fuzzy match

class CommandPaletteVM(ComponentVM[None]):
    """Fuzzy-filterable command palette.

    Uses VMx's SearchableState for filter state. Exposes:
      - filter_text: str                (reactive)
      - filtered_entries: tuple[PaletteEntry, ...]   (DerivedProperty)
      - selected_index: int             (reactive)
      - is_open: bool                   (reactive)

    Commands:
      - OpenCmd / CloseCmd / ExecuteSelectedCmd / MoveSelectionCmd(delta)
    """

    def __init__(self) -> None: ...
    def register_entry(self, entry: PaletteEntry, action: Callable[[], None | Awaitable[None]]) -> None: ...
    def unregister_entry(self, entry_id: str) -> None: ...
```

Fuzzy filter: substring-match on `label`, fall back to keywords. Don't pull in `rapidfuzz` unless really needed — a simple substring + leading-char-match scorer is enough for v0.

**Acceptance:**
- Register 10 entries, filter on "buc" → returns only `empty bucket`, `delete bucket`, `create bucket`, `bulk delete selected`.
- Selection moves; Enter on selected entry invokes the registered callable.
- `is_open` toggles on Open/Close.
- Strict mypy clean.

---

## Task 7: `vm/chrome/confirm_vm.py` + `vm/chrome/quick_look_vm.py`

**Files:**
- Create: `src/aws_tui/vm/chrome/confirm_vm.py`
- Create: `src/aws_tui/vm/chrome/quick_look_vm.py`
- Create: `tests/unit/vm/chrome/test_confirm.py`
- Create: `tests/unit/vm/chrome/test_quick_look.py`

**Contract:**

```python
@dataclass(frozen=True, slots=True)
class ConfirmRequest:
    title: str
    body_lines: tuple[str, ...]
    confirm_label: str = "OK"
    cancel_label: str = "Cancel"
    danger: bool = False

class ConfirmationVM(ComponentVM[ConfirmRequest | None]):
    """Wraps VMx's notifications-subpackage ConfirmationVM if available, else a thin shim.

    Commands: ConfirmCmd, CancelCmd.
    Properties: is_open, request.
    """

    async def ask(self, request: ConfirmRequest) -> bool: ...   # returns True if confirmed

@dataclass(frozen=True, slots=True)
class QuickLookContent:
    title: str             # e.g. "api-2026-06-13.json  4.2M  application/json"
    mime: str
    chunks: AsyncIterator[bytes] | None  # streamed body (first 64KB only)
    line_count_estimate: int | None

class QuickLookVM(ComponentVM[QuickLookContent | None]):
    """Modal preview.

    Properties: is_open, content, scroll_offset, find_query.
    Commands: OpenCmd(content), CloseCmd, ScrollCmd(delta), FindCmd(query).
    """
```

**Acceptance:**
- `ConfirmationVM.ask(...)` returns True after `ConfirmCmd`, False after `CancelCmd`.
- `QuickLookVM.open(...)` sets `is_open=True`; `CloseCmd` clears.
- Strict mypy clean.

---

## Task 8: `vm/services_menu_vm.py`

**Files:**
- Create: `src/aws_tui/services/base.py` — the `Service` protocol (defined here, used by both `services/` and `vm/`)
- Create: `src/aws_tui/vm/services_menu_vm.py`
- Create: `tests/unit/vm/test_services_menu.py`

`services/base.py`:

```python
@dataclass(frozen=True, slots=True)
class ServiceDescriptor:
    id: str
    label: str
    icon: str       # single Unicode glyph; can be ASCII fallback

class Service(Protocol):
    descriptor: ServiceDescriptor
    def supports(self, connection: Connection) -> bool: ...
    def build_vm(self, connection: Connection) -> Any: ...    # ComponentVM at runtime; Any to avoid VMx import here
    # build_view is implemented in M5 (UI layer)
```

`vm/services_menu_vm.py`:

```python
class ServiceItemVM(ComponentVM[ServiceDescriptor]):
    """One row in the services menu. Selected/focused/enabled flags."""

class ServicesMenuVM(CompositeVM[ServiceItemVM]):
    """Menu of registered services filtered by current connection.

    Implements ISelectable (single).
    Commands: SwitchServiceCmd(id).
    """

    def __init__(self, *, registry: ServiceRegistry, message_hub: MessageHub) -> None: ...
    def update_connection(self, connection: Connection) -> None: ...    # re-filters via service.supports()
```

ServiceRegistry lives at `src/aws_tui/services/__init__.py` (already a stub). Add:

```python
class ServiceRegistry:
    def __init__(self) -> None: ...
    def register(self, service: Service) -> None: ...
    def all(self) -> tuple[Service, ...]: ...
    def get(self, service_id: str) -> Service: ...    # raises ServiceNotFound
```

**Acceptance:**
- Register 3 services (mock: 1 supports="aws-only", 2 supports="any").
- With an AWS connection, menu shows all 3.
- With an s3-compatible connection, menu collapses to 2.
- `SwitchServiceCmd(id)` fires `FocusChangedMessage` / appropriate events.
- Strict mypy + layer rules clean.

---

## Task 9: `vm/root_vm.py` + integration

**Files:**
- Create: `src/aws_tui/vm/content_host_vm.py`
- Create: `src/aws_tui/vm/chrome/chrome_vm.py`
- Create: `src/aws_tui/vm/root_vm.py`
- Create: `tests/unit/vm/test_content_host.py`
- Create: `tests/unit/vm/test_chrome.py`
- Create: `tests/unit/vm/test_root_vm.py`
- Create: `tests/unit/vm/test_m3_integration.py`

```python
class ContentHostVM(ComponentVM[Any | None]):
    """Holds the active service's VM tree.

    set_content(new_vm) disposes the old one (synchronously via VMx) and constructs the new one.
    Re-setting with the same id is a no-op.
    """

    def __init__(self) -> None: ...
    @property
    def current(self) -> Any | None: ...
    @property
    def current_id(self) -> str | None: ...
    async def set_content(self, vm: Any, *, service_id: str) -> None: ...

class ChromeVM(AggregateVM3[HintLegendVM, StatusBarVM, ToastStackVM]):
    def __init__(self, *, message_hub: MessageHub, keymap: KeymapStore) -> None: ...

class RootVM(AggregateVM3[ServicesMenuVM, ContentHostVM, ChromeVM]):
    """Top of the VM tree. Owns the message hub and orchestrates connection/service/theme switches."""

    def __init__(self, *, registry: ServiceRegistry, keymap: KeymapStore, theme: ThemeStore, log: LogSink) -> None: ...

    @property
    def message_hub(self) -> MessageHub: ...
    @property
    def focused_vm_id(self) -> str | None: ...

    # Top-level commands
    async def switch_connection(self, name: str) -> None: ...
    async def switch_service(self, service_id: str) -> None: ...
    async def switch_theme(self, name: str) -> None: ...
    async def shutdown(self) -> None: ...
```

`tests/unit/vm/test_m3_integration.py`: compose everything against fakes — register 2 dummy services, switch between them, verify the right disposes happen, verify the menu filters by connection kind, verify the message hub propagates `ConnectionChangedMessage` to the status bar.

**Acceptance:**
- `RootVM.construct()` cascades depth-first.
- `switch_service("ec2")` on a dummy registry disposes the old content (assert via counter), constructs the new.
- Re-calling `switch_service("ec2")` while EC2 is active is a no-op.
- `switch_connection(...)` updates services menu filter, status bar, and disposes the active service content.
- `RootVM.shutdown()` cascades dispose to the whole tree.
- `./scripts/check-layers.sh` clean (vm/ does NOT import textual or boto3).
- Strict mypy clean.

---

## Task 10: commit per task + push + tag v0.4.0

Per-task commits: `feat(vm): <component>`. Final commit bumps CHANGELOG.

Push, watch CI (all jobs green: unit matrix + integration MinIO + lint+type + pkg). Tag `v0.4.0` with title `v0.4.0 — vm shell (M3)`, push tag, gh release create.

**Acceptance:** all CI green; vm/file_manager/ left empty as stubs (M4 fills); the shell is composable.

---

## Watch-outs (M3-specific) — UPDATED 2026-06-14 after VMx spike

- **VMx VMs are NOT subclassable.** They are built via immutable fluent builders. Every "ViewModel" in our M3 plan is a facade class that holds a VMx instance as `self._inner` and forwards lifecycle. See `docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md` §10 for the pattern.
- **`AggregateVM3.builder()` does NOT exist as a static method.** Use `AggregateVMBuilder3()` constructor directly (or its alias `AggregateVM3Builder()`). All AggregateVMN builders are instantiated, not obtained via a static factory.
- **`CompositeVM` builder method is `.children(factory)`** — NOT `.children_factory(factory)` and the factory is REQUIRED (pass `lambda: ()` for empty composites).
- **`CompositeVM.builder()` has no `.with_null_services()` shortcut** — only `ComponentVMBuilder`/`ComponentVMOfBuilder`/`ReadonlyComponentVMOfBuilder` do. Pass `NULL_MESSAGE_HUB, NULL_DISPATCHER` explicitly for composites.
- **`DerivedProperty.value` raises `RuntimeError` until a source emits.** Use `reactivex.subject.BehaviorSubject` (carries initial value) as sources, NOT plain `Subject`.
- **Custom messages** satisfy the `Message` protocol via `sender_name: str` (default field) and a `sender_object: object` property. They are NOT subclassed from a VMx message base.
- **Service protocol moves to `vm/services_protocol.py`** (not `services/base.py`) so `vm/` can stay free of `aws_tui.services` imports. `ServiceRegistry` also lives in `vm/services_protocol.py`. The `services/` layer imports the protocol from `aws_tui.vm.services_protocol`. This is the cleaner direction; documented in the M3 plan revision commit.
- **No Textual imports in `vm/`.** Layer rules grep will fail CI.
- **Async `ConfirmationVM.ask`**: simplest implementation is an `asyncio.Future[bool]` that the confirm/cancel commands resolve. Skip `vmx.notifications` for this — that subpackage's `NotificationHub` is overkill for our single-modal use case.
- **`StatusBarVM` test fakes**: pass in a `MessageHub()` and call its `send()` directly; don't construct `AwsSession`.
- **`ComponentVM[T]` is NOT a generic.** Use plain `ComponentVM` and store our own state on the facade. `ComponentVMOf[M]` is the generic-modeled variant; we use it only where a single canonical model fits (e.g. `ToastModel` for an individual toast).

This plan deliberately leaves `vm/file_manager/` (DualPaneVM, PaneVM, EntryVM) and the S3 service composition to M4.
