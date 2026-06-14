# aws-tui M4 (VM file manager + S3 service) Implementation Plan

> **For agentic workers:** Compact-plan format. Spec is the source of truth — read §4 (UI), §5 (MVVM), §6 (lifecycle), §7 (errors → pane states). VMx Python API: read `docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md` (M3 spike output) and use the facade pattern documented there.

**Goal:** Land `vm/file_manager/` (DualPaneVM, PaneVM, EntryVM, TransfersVM, TransferVM) and the first concrete service composition (`services/s3/`). All VMs use the facade-over-VMx pattern from M3.

**Architecture:** PaneVM owns a `FileSystemProvider` (from M2) and a current `PathRef`. Holds an observable collection of `EntryVM` children. DualPaneVM is an AggregateVM2 of two PaneVMs (left/right). Each pane has commands for navigation (open/ascend/refresh), selection (multi-select state), and operations (copy/move/delete/new — `copy` routes through `CrossFsCopy` from M2 to the OTHER pane's provider). S3Service composes DualPaneVM with an `S3FS(connection)` + `LocalFS()` pair.

**Tech Stack:** Same as M3 (VMx + Python). For S3Service, also uses M1's `AwsSession` for the aioboto3 session and M2's `S3FS` / `LocalFS` / `CrossFsCopy` / `TransferJournal`.

**Reference:** the M3 cheatsheet + the revised M3 plan are mandatory reading before writing any code.

---

## Task 1: `vm/file_manager/entry_vm.py` — single entry facade

**Files:**
- Create: `src/aws_tui/vm/file_manager/entry_vm.py`
- Create: `tests/unit/vm/file_manager/__init__.py`
- Create: `tests/unit/vm/file_manager/test_entry_vm.py`

```python
@dataclass(frozen=True, slots=True)
class EntryState:
    entry: FileEntry              # from domain.filesystem
    is_selected: bool = False
    is_marked: bool = False       # for batch ops (the multi-select highlight)

class EntryVM:
    """Facade over a VMx ComponentVMOf[EntryState].

    Exposes:
      - state: EntryState (reactive)
      - toggle_select_cmd: RelayCommand
      - toggle_mark_cmd: RelayCommand
    """

    def __init__(self, *, entry: FileEntry) -> None: ...
    @property
    def state(self) -> EntryState: ...
    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> EntryKind: ...
    def toggle_select(self) -> None: ...
    def toggle_mark(self) -> None: ...
    @property
    def inner(self) -> ComponentVMOf[EntryState]: ...   # for parent CompositeVM
```

**Acceptance:**
- `EntryVM(entry=fake_file_entry)` constructs, exposes `.name` and `.kind`.
- `toggle_select()` flips `is_selected` and publishes `PropertyChangedMessage`.
- Strict mypy + layer rules clean.

---

## Task 2: `vm/file_manager/pane_vm.py` — single pane

**Files:**
- Create: `src/aws_tui/vm/file_manager/pane_vm.py`
- Create: `tests/unit/vm/file_manager/test_pane_vm.py`

```python
class PaneState(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    EMPTY = "empty"
    AUTH_REQUIRED = "auth_required"
    FORBIDDEN = "forbidden"
    UNREACHABLE = "unreachable"
    ERROR = "error"

@dataclass(frozen=True, slots=True)
class PaneViewModel:
    breadcrumb: tuple[str, ...]     # e.g. ("S3", "bucket", "prefix")
    state: PaneState
    cursor_index: int               # which entry the cursor is on (within current filtered view)
    selection_count: int
    filter_text: str
    error_text: str | None
    summary: str                    # "5 obj . 1 selected . 4.2 M" derived from entries+selection

class PaneVM:
    """Facade over CompositeVM[EntryVM-inner]. Drives navigation + ops.

    Holds:
      - provider: FileSystemProvider
      - path: PathRef
      - entries: CompositeVM<EntryVM-inner>

    Commands:
      - open_cmd       (descend into entry under cursor, or open file → publishes ContentPreviewRequested)
      - ascend_cmd     (.. — parent path)
      - refresh_cmd    (re-runs provider.list)
      - move_cursor_cmd(delta)
      - toggle_select_cmd  (current row; enters multi-select mode if not already)
      - enter_multiselect_cmd (the 'v' key)
      - exit_multiselect_cmd
      - select_all_cmd
      - delete_cmd
      - new_folder_cmd
      - rename_cmd

    Properties (all derived):
      - viewmodel: PaneViewModel
      - selected_entries: tuple[EntryVM, ...]
      - is_multiselect_mode: bool
      - state: PaneState
      - hint_actions: tuple[HintAction, ...]   # consumed by HintLegendVM via FocusChangedMessage
    """

    def __init__(self, *, provider: FileSystemProvider, initial_path: PathRef = PathRef(()), id_prefix: str = "pane") -> None: ...
    async def setup(self) -> None: ...
    async def navigate_to(self, path: PathRef) -> None: ...
    async def refresh(self) -> None: ...
```

**Acceptance:**
- Construct with `InMemoryFS` containing `/a.txt`, `/b/`, `/c.json`; `setup()` populates 3 entries.
- `move_cursor_cmd(delta=+1)` advances cursor; bounds-checked.
- `toggle_select_cmd` flips selection on cursor row.
- `enter_multiselect_cmd` sets `is_multiselect_mode=True`.
- `delete_cmd` requires non-empty selection; calls `provider.delete(...)` for each; updates entries.
- `refresh_cmd` triggers a re-list.
- `navigate_to(PathRef("/b"))` updates `breadcrumb`, `path`, re-runs list, resets cursor.
- On `provider.list()` raising `PermissionDeniedError`, `state` becomes `FORBIDDEN`.
- On `ProviderUnreachableError`, `state` becomes `UNREACHABLE`.
- Filter text `/`: `move_cursor_cmd` only moves through filtered subset.
- Strict mypy + layer rules clean.

---

## Task 3: `vm/file_manager/dual_pane_vm.py`

**Files:**
- Create: `src/aws_tui/vm/file_manager/dual_pane_vm.py`
- Create: `tests/unit/vm/file_manager/test_dual_pane_vm.py`

```python
class FocusedPane(StrEnum):
    LEFT = "left"
    RIGHT = "right"

class DualPaneVM:
    """AggregateVM2 facade of two PaneVMs.

    Commands:
      - switch_focus_cmd
      - copy_across_cmd     (source-pane selection → dest-pane current path)
      - move_across_cmd
      - delete_in_focused_cmd

    Properties:
      - focused: FocusedPane
      - left: PaneVM
      - right: PaneVM
      - hint_actions: derived from focused pane

    copy/move use CrossFsCopy / CrossFsMove (M2) with source.provider + dest.provider.
    Operations publish TransferProgressMessage(transfer_id=..., ...) to the hub.
    """

    def __init__(self, *, left: PaneVM, right: PaneVM, message_hub: MessageHub, transfer_journal: TransferJournal) -> None: ...
    async def setup(self) -> None: ...
```

**Acceptance:**
- Construct with two InMemoryFS-backed PaneVMs.
- `copy_across_cmd` copies selected files from focused pane to other pane's current path.
- After copy, dest pane refresh reveals new entries.
- `switch_focus_cmd` flips focused; `focused == LEFT` initially.
- TransferProgressMessage published to a fake hub during ops.
- Strict mypy + layer rules clean.

---

## Task 4: `vm/file_manager/transfer_vm.py` + `transfers_vm.py`

**Files:**
- Create: `src/aws_tui/vm/file_manager/transfer_vm.py`
- Create: `src/aws_tui/vm/file_manager/transfers_vm.py`
- Create: `tests/unit/vm/file_manager/test_transfers.py`

```python
class TransferState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass(frozen=True, slots=True)
class TransferModel:
    id: str
    direction: str        # "upload" | "download" | "local-copy" | "s3-copy"
    source_label: str     # e.g. "local:///foo.json" or "s3://bucket/prefix/key"
    destination_label: str
    bytes_done: int
    bytes_total: int | None
    state: TransferState
    error: str | None

class TransferVM:
    """Single transfer facade."""

class TransfersVM:
    """CompositeVM<TransferVM-inner>.

    Properties:
      - active_count: DerivedProperty (state ∈ {running, paused})
      - total_bytes_done / total_bytes_total
      - throughput_label: str  (last 5-second avg)

    Commands:
      - cancel_cmd(transfer_id)
      - cancel_all_cmd
      - retry_cmd(transfer_id)
    """

    def __init__(self, *, message_hub: MessageHub, max_concurrent: int = 8) -> None: ...
    def register(self, model: TransferModel) -> TransferVM: ...
    def update(self, transfer_id: str, *, bytes_done: int, bytes_total: int | None, state: TransferState, error: str | None = None) -> None: ...
```

Subscribes to `TransferProgressMessage` from the hub and updates the matching TransferVM.

**Acceptance:**
- Register 3 transfers; 2 mark RUNNING, 1 PENDING — `active_count == 2`.
- Update transfer #1 to COMPLETED — `active_count == 1`.
- `cancel_all_cmd` flips all running/pending to CANCELLED.
- Strict mypy + layer rules clean.

---

## Task 5: `services/s3/service.py` — first concrete service

**Files:**
- Create: `src/aws_tui/services/s3/service.py`
- Create: `tests/unit/services/s3/__init__.py`
- Create: `tests/unit/services/s3/test_s3_service.py`

```python
class S3Service:
    """Implements the Service protocol from vm.services_protocol.

    Composes DualPaneVM:
      - left  = PaneVM(provider=S3FS(connection))
      - right = PaneVM(provider=LocalFS())
    """

    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="s3",
        label="S3",
        icon="S3",
    )

    def __init__(self, *, aws_session: AwsSession, transfer_journal: TransferJournal, message_hub: MessageHub) -> None: ...

    def supports(self, connection: Connection) -> bool:
        return True   # works for both AWS S3 and S3-compatible

    async def build_vm(self, connection: Connection) -> Any:
        """Returns a DualPaneVM with PaneVM-on-S3FS + PaneVM-on-LocalFS, setup'd."""
        ...
```

Register at `services/__init__.py` import time via `ServiceRegistry`.

**Acceptance:**
- `S3Service(...)` constructs.
- `supports(aws_connection)` and `supports(minio_connection)` both True.
- `build_vm(...)` returns a DualPaneVM whose left pane's provider is an `S3FS` and right pane's provider is a `LocalFS`.
- The setup correctly defers initial `list` until `setup()` is awaited.
- Strict mypy + layer rules clean (services/ may import from infra, domain, vm).

---

## Task 6: VMx contract / capability tests for PaneVM

**Files:**
- Create: `tests/unit/vm/file_manager/test_pane_vm_contracts.py`

Tests that verify PaneVM satisfies the capability contracts (selection / filter / paging behavior) — patterned after VMx's conformance fixtures (if they exist for the Python flavor; if not, hand-roll the tests).

If VMx ships `vmx.testing.conformance` with `selectable_contract` / `filterable_contract` / `pageable_contract`, use them. Otherwise hand-roll equivalents that exercise:
- Selection: set/unset/toggle, count, equality
- Filter: apply text, results match, clearing restores all
- Paging: page_size, current_page, total_pages, navigate

**Acceptance:** Contract tests pass.

---

## Task 7: M4 integration test

**Files:**
- Create: `tests/unit/vm/file_manager/test_m4_integration.py`

Compose: ServiceRegistry → S3Service registered → RootVM with the registry → `switch_service("s3")` → DualPaneVM appears as `ContentHostVM.current`. Use InMemoryFS for the S3 provider (mock the s3 path) so no AWS/moto calls — the integration is about VM wiring, not provider behavior.

**Acceptance:**
- switch_service("s3") replaces ContentHostVM.current with a DualPaneVM.
- Switching connection (aws → s3-compat) refilters ServicesMenuVM (still shows S3) and reconstructs the DualPaneVM with new providers.
- Switching service to a different one (mocked EC2Service) disposes the DualPaneVM.

---

## Task 8: commit, push, tag v0.5.0

- One commit per task (1-7), CHANGELOG bump, push, watch CI green, tag `v0.5.0` ("v0.5.0 — vm file manager + s3 service (M4)"), gh release.

**Acceptance:** all CI green; M4 deliverable shipped.

---

## Watch-outs

- **Apply the M3 facade pattern.** All VMs wrap a VMx primitive as `_inner`; do not subclass `ComponentVM` directly. Reference `docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md`.
- **PaneVM does work asynchronously.** `provider.list()` is async; spawn an asyncio task during `setup()` / `navigate_to()`. Set `state = LOADING` synchronously, then transition to `IDLE` on completion. Use the spec's 200ms delay before showing the loading spinner (caller's concern; VM just exposes the state).
- **DualPaneVM destruct/dispose** cancels its in-flight transfers. If any `TransferProgressMessage` arrives after the VM is disposed, the subscriber must safely no-op.
- **`copy_across_cmd` runs `CrossFsCopy.copy()`** which yields `TransferProgress` events. Bridge them to `TransferProgressMessage` and publish to the hub.
- **Layer rules**: `services/s3/` may import from `infra`, `domain`, `vm`, and `vm.services_protocol`. `vm/file_manager/` may import from `domain` (`FileSystemProvider`, `FileEntry`, etc.) but NOT from `services/` and NOT from `infra/aws_session` directly (it gets infra via DI in the ctor signature of higher-level VMs).
- **Test discipline**: don't construct an `S3Service` in PaneVM tests. PaneVM consumes a generic `FileSystemProvider`; use `InMemoryFS` everywhere.
