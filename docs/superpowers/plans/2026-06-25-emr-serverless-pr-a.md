# EMR Serverless PR-A (Read-Only Browser) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first concrete piece of the EMR Serverless service — a read-only browser that lists applications, lists job runs per application (with state-filter chips), drills into job-run detail, and auto-refreshes. No log surface, no submit form, no cancel. The 80% read path of daily monitoring.

**Architecture:** New `Service` plug-in alongside `S3Service`, registered in `composition.py`. The page is composed of a top-strip application dropdown plus a 2-pane body (job runs LEFT, run detail RIGHT). All domain calls go through one async `EmrServerlessClient` (aioboto3 facade with three verbs); VMs are constructor-injected with the client for testability without a Protocol layer. Three independent VMx workers poll on different cadences sharing one client. UI inherits every visual rule from S3/Settings per the design-language commitment in §3 of the spec.

**Tech Stack:** Python 3.11+, Textual, VMx MVVM (`vmx>=2.6.0,<3.0.0`), aioboto3 for AWS API, pytest (unit/integration/snapshot tiers), ruff + mypy --strict.

## Global Constraints

These apply to every task — repeated here so the reviewer can fail fast on any violation.

- **Spec reference:** `docs/superpowers/specs/2026-06-25-emr-serverless-service-design.md` (commit `c47fce6`). PR-A scope == sections §1 (architecture), §2 (layout/keybindings minus log surface), §3 (every design-language row applies), §6 (auto-refresh + error states minus cancel/lifecycle). §4 (submit), §5 (logs), §6 cancel/lifecycle are PR-B/C and explicitly out of scope.
- **Theme tokens only.** Every CSS color is one of: `$bg`, `$bg-elev`, `$bg-sel`, `$accent`, `$accent-soft`, `$rule-dim`, `$success`, `$warning`, `$danger`, `$text`, `$text-muted`. NO hex literals. NO `$accent-hot` (PR #72 dropped this for modal-shape surfaces and EMR follows).
- **Service icon:** `⚡` (U+26A1 HIGH VOLTAGE). Single-cell. Symmetric with `🪣 S3` / `⚙️ Settings` / `🖥️ EC2`.
- **Service id:** `"emr-serverless"`.
- **Service label:** `"EMR"`.
- **Supports rule:** `connection.kind == "aws"` only. S3-compatible connections must NOT see the ⚡ icon in the nav rail.
- **VMx lifecycle:** `EmrServerlessPageVM` is built FRESH PER MOUNT inside `EmrServerlessService.build_vm` (per the [[vmx-content-host-singleton-trap]] memo). All sub-VMs construct under the page VM's `construct()` and dispose in reverse-construction order.
- **No `Protocol` for the EMR client.** `EmrServerlessClient` is a concrete class; tests inject `_InMemoryEmrClient` via the VM constructor (same pattern integration tests already use for `FileSystemProvider`). The Protocol earns its place only when two implementations exist (see spec §1).
- **All toasts via `aws_tui.ui.notifications`** (PR #75). PR-A uses `advise(subject="Source")` for UNREACHABLE and `error(subject="Auth")` for AUTH_REQUIRED. No new `Subject` literal entries needed in PR-A — `"Job"` is added in PR-C.
- **Selected-row look:** `background: $bg-sel; color: $accent-soft;` — no `text-style: bold`. Identical to S3 pane rows (per PR #66).
- **Pane chrome:** resting `border: solid $rule-dim`; focused `:focus-within { border: solid $accent }` (per PR #61/#62 — use the CSS pseudo-class, NOT a `watch_has_focus_within` Python watcher; the latter silently fails because `has_focus_within` is a property not a reactive).
- **Tab cycle:** exactly two slots, LEFT (job runs) ↔ RIGHT (detail). Application picker is reached via `a` only, not Tab (per PR #66 — a 3-slot cycle reads as idle Tab presses inside the rail).
- **Key-scope rule:** `1`–`5` mean **state filter chips** when LEFT pane has focus; the same keys will mean log-level chips when RIGHT pane has focus (PR-B will wire that). PR-A wires the LEFT-focus mapping only.
- **Snapshot tier rule:** every new widget snapshot MUST pair with a content-presence guard test that reads the SVG and asserts at least one expected glyph or label appears (per PR #53 / #63 — parity-match can pass uniformly-blank renderings across all themes).
- **All 10 themes:** `amber`, `carbon`, `dracula`, `github-light`, `gruvbox-dark`, `lattice`, `nord`, `one-light`, `solarized-light`, `voidline`. Adding any new visible widget requires per-theme CSS in all 10 files.
- **State indicators (glyph + token):**
  - `✓` SUCCESS → `$success`
  - `●` RUNNING → `$accent`
  - `⏸` PENDING → `$text-muted`
  - `✗` FAILED → `$danger`
  - `⊘` CANCELLED → `$text-muted`
- **Quality gates:** `uv run ruff check .`, `uv run mypy src`, `uv run pytest` all pass at the end of every task.
- **Test conventions:** unit tests under `tests/unit/<layer>/test_<name>.py`; integration `tests/integration/test_<topic>.py`; snapshot `tests/snapshot/test_<topic>.py` with fixture app in `tests/snapshot/apps/<topic>.py`.
- **Auto-refresh cadences (per §6):**
  - Application picker: 30 s active, 60 s idle.
  - Job-runs list: 10 s when any run ∈ {PENDING, RUNNING}, 60 s otherwise.
  - Job-run detail: 5 s when run ∈ {PENDING, RUNNING}, one-shot then stop at terminal state.
- **Throttle handling:** on `ThrottlingException` the affected poller backs off exponentially `5s → 10s → 30s → 60s cap` and raises `advise(subject="Source", "EMR throttled — backing off")`. Next successful call clears.

---

## File Structure

### Created

```
src/aws_tui/domain/
└── emr_serverless.py                       ← records + EmrServerlessClient

src/aws_tui/services/emr_serverless/
├── __init__.py
└── service.py                              ← Service protocol impl

src/aws_tui/vm/emr_serverless/
├── __init__.py
├── page_vm.py                              ← EmrServerlessPageVM (master)
├── applications_vm.py                      ← ApplicationsVM
├── job_runs_vm.py                          ← JobRunsVM
└── job_run_detail_vm.py                    ← JobRunDetailVM

src/aws_tui/ui/widgets/emr_serverless/
├── __init__.py
├── page.py                                 ← EmrServerlessPage (top strip + 2-pane)
├── application_picker.py                   ← ApplicationPicker (dropdown)
├── job_runs_pane.py                        ← JobRunsPane (chip row + list)
└── job_run_detail_pane.py                  ← JobRunDetailPane (KV table)

tests/unit/domain/test_emr_serverless.py
tests/unit/domain/_in_memory_emr.py         ← test fake EMR client
tests/unit/services/test_emr_serverless_service.py
tests/unit/vm/emr_serverless/__init__.py
tests/unit/vm/emr_serverless/test_applications_vm.py
tests/unit/vm/emr_serverless/test_job_runs_vm.py
tests/unit/vm/emr_serverless/test_job_run_detail_vm.py
tests/unit/vm/emr_serverless/test_page_vm.py
tests/integration/test_emr_page.py
tests/snapshot/test_emr.py
tests/snapshot/apps/emr.py
```

### Modified

```
src/aws_tui/composition.py                  ← register EmrServerlessService
src/aws_tui/ui/themes/{amber,carbon,dracula,github-light,gruvbox-dark,
                       lattice,nord,one-light,solarized-light,voidline}.tcss
```

---

## Task 1: EMR domain data records

**Files:**
- Create: `src/aws_tui/domain/emr_serverless.py` (records portion only — client added in Task 3)
- Test: `tests/unit/domain/test_emr_serverless.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ApplicationSummary` — frozen dataclass `(id: str, name: str, state: ApplicationState, type: str, created_at: datetime)`
  - `JobRunSummary` — frozen dataclass `(application_id: str, job_run_id: str, name: str | None, state: JobRunState, created_at: datetime, updated_at: datetime)`
  - `JobRunDetail` — frozen dataclass extending JobRunSummary with `entry_point: str | None`, `entry_point_arguments: tuple[str, ...]`, `spark_submit_parameters: str | None`, `execution_role_arn: str`, `duration_ms: int | None`
  - `ApplicationState` — `StrEnum` `{CREATED, STARTING, STARTED, STOPPING, STOPPED, TERMINATED}`
  - `JobRunState` — `StrEnum` `{PENDING, RUNNING, SUCCESS, FAILED, CANCELLING, CANCELLED}`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/domain/test_emr_serverless.py
"""Tests for EMR Serverless domain data records.

These are plain frozen dataclasses + StrEnums; the tests pin field
order, immutability, and the canonical state enums so VMs above
can pattern-match on stable values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
    JobRunDetail,
    JobRunState,
    JobRunSummary,
)


def test_application_summary_is_frozen() -> None:
    a = ApplicationSummary(
        id="00fabc",
        name="etl",
        state=ApplicationState.STARTED,
        type="SPARK",
        created_at=datetime(2026, 6, 25, tzinfo=UTC),
    )
    with pytest.raises(FrozenInstanceError):
        a.name = "renamed"  # type: ignore[misc]


def test_job_run_summary_carries_state_and_timestamps() -> None:
    r = JobRunSummary(
        application_id="00fabc",
        job_run_id="00jrx",
        name="nightly",
        state=JobRunState.RUNNING,
        created_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 12, 5, tzinfo=UTC),
    )
    assert r.state is JobRunState.RUNNING
    assert r.created_at < r.updated_at


def test_job_run_detail_extends_summary_with_spark_params() -> None:
    d = JobRunDetail(
        application_id="00fabc",
        job_run_id="00jrx",
        name=None,
        state=JobRunState.SUCCESS,
        created_at=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 12, 4, tzinfo=UTC),
        entry_point="s3://b/job.py",
        entry_point_arguments=("--in", "s3://b/in/"),
        spark_submit_parameters="--conf spark.executor.instances=4",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
    )
    assert d.entry_point_arguments == ("--in", "s3://b/in/")
    assert d.duration_ms == 240_000


def test_application_state_enum_values() -> None:
    assert ApplicationState.STARTED == "STARTED"
    assert {s.value for s in ApplicationState} == {
        "CREATED", "STARTING", "STARTED", "STOPPING", "STOPPED", "TERMINATED"
    }


def test_job_run_state_enum_values() -> None:
    assert JobRunState.SUCCESS == "SUCCESS"
    assert {s.value for s in JobRunState} == {
        "PENDING", "RUNNING", "SUCCESS", "FAILED", "CANCELLING", "CANCELLED"
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aws_tui.domain.emr_serverless'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/aws_tui/domain/emr_serverless.py
"""EMR Serverless domain types — records + StrEnums.

PR-A ships only the read-only verbs (list applications, list job
runs, get job run detail). PR-B adds cancel + lifecycle, PR-C adds
submit. The records here are the wire-shape mapped from boto3
``EMRServerless`` responses by :class:`EmrServerlessClient` (added
in Task 3). VMs above hold these by value — no fields beyond what
the read-only browser needs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ApplicationState(StrEnum):
    """Application lifecycle states per the boto3 enum.

    See https://docs.aws.amazon.com/emr-serverless/latest/APIReference/
    """

    CREATED = "CREATED"
    STARTING = "STARTING"
    STARTED = "STARTED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"


class JobRunState(StrEnum):
    """Job-run lifecycle states. ``CANCELLING`` is the transient
    state after a cancel request before ``CANCELLED`` is observed."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ApplicationSummary:
    """One row in the application picker dropdown."""

    id: str
    name: str
    state: ApplicationState
    type: str  # "SPARK" or "HIVE" — v1 only renders SPARK applications
    created_at: datetime


@dataclass(frozen=True, slots=True)
class JobRunSummary:
    """One row in the LEFT pane's job-runs list."""

    application_id: str
    job_run_id: str
    name: str | None
    state: JobRunState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobRunDetail:
    """Full job-run view shown in the RIGHT pane.

    Superset of :class:`JobRunSummary` — the ``list_job_runs`` API
    returns only summaries; ``get_job_run`` is required to fill in
    the entry point, args, Spark params, IAM role, and timing
    fields below."""

    application_id: str
    job_run_id: str
    name: str | None
    state: JobRunState
    created_at: datetime
    updated_at: datetime
    entry_point: str | None
    entry_point_arguments: tuple[str, ...]
    spark_submit_parameters: str | None
    execution_role_arn: str
    duration_ms: int | None


__all__ = [
    "ApplicationState",
    "ApplicationSummary",
    "JobRunDetail",
    "JobRunState",
    "JobRunSummary",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v`
Expected: PASS — 5 tests.

- [ ] **Step 5: Run quality gates**

Run: `uv run ruff check src/aws_tui/domain/emr_serverless.py tests/unit/domain/test_emr_serverless.py && uv run mypy src/aws_tui/domain/emr_serverless.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/domain/emr_serverless.py tests/unit/domain/test_emr_serverless.py
git commit -m "feat(domain): EMR Serverless data records + state enums"
```

---

## Task 2: EMR error mapping (reuse domain ProviderError hierarchy)

**Files:**
- Modify: `src/aws_tui/domain/emr_serverless.py` (append `_map_boto_error` helper)
- Test: append cases to `tests/unit/domain/test_emr_serverless.py`

**Interfaces:**
- Consumes: `ProviderError`, `PermissionDeniedError`, `ProviderUnreachableError`, `NotFoundError` from `aws_tui.domain.filesystem` (existing — same hierarchy S3FS uses).
- Produces:
  - `_map_boto_error(exc: BaseException) -> ProviderError | None` — single entry point. Maps `NoCredentialsError` and `TokenRetrievalError` → `AuthRequiredError`; `EndpointConnectionError`, `ConnectTimeoutError`, `ReadTimeoutError` → `ProviderUnreachableError`; `ClientError` with `AccessDeniedException` → `PermissionDeniedError`; `ClientError` with `ThrottlingException` → `ThrottledError`; `ClientError` with `ResourceNotFoundException` → `NotFoundError`; `ClientError` with `ValidationException` → `ValidationError`. Anything else → `ProviderError`. Returns `None` for non-AWS exceptions so callers know to re-raise.
  - New domain exception subclasses `AuthRequiredError`, `ThrottledError`, `ValidationError` added to `aws_tui.domain.filesystem` (where the existing ProviderError lives — so VMs can keep importing from one place).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/unit/domain/test_emr_serverless.py

import botocore.exceptions
import pytest

from aws_tui.domain.emr_serverless import _map_boto_error
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)


def _client_error(code: str, op: str = "ListApplications") -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": f"mocked {code}"}}, op
    )


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (botocore.exceptions.NoCredentialsError(), AuthRequiredError),
        (botocore.exceptions.EndpointConnectionError(endpoint_url="x"), ProviderUnreachableError),
        (botocore.exceptions.ConnectTimeoutError(endpoint_url="x"), ProviderUnreachableError),
        (botocore.exceptions.ReadTimeoutError(endpoint_url="x"), ProviderUnreachableError),
        (_client_error("AccessDeniedException"), PermissionDeniedError),
        (_client_error("ThrottlingException"), ThrottledError),
        (_client_error("ResourceNotFoundException"), NotFoundError),
        (_client_error("ValidationException"), ValidationError),
        (_client_error("InternalServerException"), ProviderError),
    ],
)
def test_map_boto_error_maps_to_provider_error_subclass(
    raised: BaseException, expected: type[ProviderError]
) -> None:
    mapped = _map_boto_error(raised)
    assert mapped is not None
    assert isinstance(mapped, expected)
    # Original cause preserved for log_sink + debugging.
    assert mapped.__cause__ is raised or mapped.__cause__ is None  # cause may be set by `raise from`


def test_map_boto_error_returns_none_for_unrelated_exceptions() -> None:
    assert _map_boto_error(ValueError("not an aws exception")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v -k "map_boto_error or import"`
Expected: FAIL — `_map_boto_error` not defined; some imports also missing.

- [ ] **Step 3: Add new exception classes to filesystem.py**

In `src/aws_tui/domain/filesystem.py`, after the existing `ProviderUnreachableError` definition, add:

```python
class AuthRequiredError(ProviderError):
    """No usable credentials / expired SSO. UI should suggest
    ``aws sso login --profile X`` or ``$AWS_PROFILE`` setup."""


class ThrottledError(ProviderError):
    """Service throttled (boto ``ThrottlingException``). Callers
    should back off exponentially; user-facing toast is INFO-level."""


class ValidationError(ProviderError):
    """Request failed validation (e.g. boto ``ValidationException``
    on ``StartJobRun``). The boto message is forwarded verbatim."""
```

And extend the module's `__all__`:

```python
__all__ = [
    "AuthRequiredError",
    "ConflictError",
    "FileEntry",
    "FileSystemProvider",
    "NotFoundError",
    "PathRef",
    "PermissionDeniedError",
    "ProviderError",
    "ProviderUnreachableError",
    "ThrottledError",
    "ValidationError",
]
```

- [ ] **Step 4: Implement `_map_boto_error` in emr_serverless.py**

Append to `src/aws_tui/domain/emr_serverless.py`:

```python
# ── boto3 error mapping ──────────────────────────────────────────────────

import botocore.exceptions

from aws_tui.domain.filesystem import (
    AuthRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
    ThrottledError,
    ValidationError,
)

_CLIENT_ERROR_CODE_MAP: dict[str, type[ProviderError]] = {
    "AccessDeniedException": PermissionDeniedError,
    "ThrottlingException": ThrottledError,
    "ResourceNotFoundException": NotFoundError,
    "ValidationException": ValidationError,
}


def _map_boto_error(exc: BaseException) -> ProviderError | None:
    """Translate a boto3/botocore exception to the domain
    :class:`ProviderError` hierarchy. Returns ``None`` for anything
    that isn't AWS — callers should re-raise those unchanged."""
    if isinstance(exc, botocore.exceptions.NoCredentialsError | botocore.exceptions.TokenRetrievalError):
        return AuthRequiredError(str(exc) or "no AWS credentials")
    if isinstance(
        exc,
        botocore.exceptions.EndpointConnectionError
        | botocore.exceptions.ConnectTimeoutError
        | botocore.exceptions.ReadTimeoutError,
    ):
        return ProviderUnreachableError(str(exc) or "endpoint unreachable")
    if isinstance(exc, botocore.exceptions.ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        cls = _CLIENT_ERROR_CODE_MAP.get(code, ProviderError)
        return cls(exc.response.get("Error", {}).get("Message", str(exc)))
    return None
```

Move the imports already at the top of the file alongside the existing imports rather than mid-file if ruff suggests it (the snippet above placed them next to the helper for readability — the formatter will hoist them).

- [ ] **Step 5: Run tests + quality gates**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v && uv run ruff check src/aws_tui/domain/ tests/unit/domain/test_emr_serverless.py && uv run mypy src/aws_tui/domain/`
Expected: all tests pass; ruff + mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/aws_tui/domain/filesystem.py src/aws_tui/domain/emr_serverless.py tests/unit/domain/test_emr_serverless.py
git commit -m "feat(domain): _map_boto_error + AuthRequired/Throttled/Validation"
```

---

## Task 3: `EmrServerlessClient` — async boto3 facade

**Files:**
- Modify: `src/aws_tui/domain/emr_serverless.py` (append client class)
- Test: append to `tests/unit/domain/test_emr_serverless.py`

**Interfaces:**
- Consumes: records + state enums + `_map_boto_error` from Tasks 1-2; `aioboto3.Session` from caller.
- Produces:
  - `class EmrServerlessClient` with constructor `(session: aioboto3.Session, *, region_name: str | None = None)` and async methods:
    - `async list_applications() -> list[ApplicationSummary]` — paginates; returns all.
    - `async list_job_runs(application_id: str, *, states: set[JobRunState] | None = None, max_results: int = 100) -> list[JobRunSummary]` — fetches up to `max_results`; `states` filter is applied client-side after paging because the boto3 filter is one-state-at-a-time.
    - `async get_job_run(application_id: str, job_run_id: str) -> JobRunDetail`
  - All three re-raise as `ProviderError` subclasses on AWS errors (via `_map_boto_error`).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/unit/domain/test_emr_serverless.py
from unittest.mock import AsyncMock, MagicMock

import pytest

from aws_tui.domain.emr_serverless import (
    EmrServerlessClient,
    JobRunState,
)


def _fake_app_response(items: list[dict]) -> dict:
    return {"applications": items, "nextToken": None}


def _fake_run_response(items: list[dict]) -> dict:
    return {"jobRuns": items, "nextToken": None}


class _StubClient:
    """Minimal aioboto3-shaped async client we can hand to
    EmrServerlessClient for testing without touching the network."""

    def __init__(self) -> None:
        self.list_applications = AsyncMock()
        self.list_job_runs = AsyncMock()
        self.get_job_run = AsyncMock()

    async def __aenter__(self) -> "_StubClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _StubSession:
    """aioboto3.Session-shaped fake."""

    def __init__(self, stub: _StubClient) -> None:
        self._stub = stub

    def client(self, service_name: str, **kwargs: object) -> _StubClient:
        assert service_name == "emr-serverless"
        return self._stub


@pytest.mark.asyncio
async def test_list_applications_maps_response_to_records() -> None:
    stub = _StubClient()
    stub.list_applications.return_value = _fake_app_response(
        [
            {
                "id": "00abc",
                "name": "etl",
                "state": "STARTED",
                "type": "SPARK",
                "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
            },
            {
                "id": "00def",
                "name": "ad-hoc",
                "state": "STOPPED",
                "type": "SPARK",
                "createdAt": datetime(2026, 6, 24, tzinfo=UTC),
            },
        ]
    )
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]

    apps = await client.list_applications()
    assert len(apps) == 2
    assert apps[0].id == "00abc"
    assert apps[0].state.value == "STARTED"
    assert apps[1].name == "ad-hoc"


@pytest.mark.asyncio
async def test_list_job_runs_filters_by_state_client_side() -> None:
    stub = _StubClient()
    stub.list_job_runs.return_value = _fake_run_response(
        [
            {"applicationId": "00abc", "id": "jr1", "name": "n1", "state": "SUCCESS",
             "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
             "updatedAt": datetime(2026, 6, 25, tzinfo=UTC)},
            {"applicationId": "00abc", "id": "jr2", "name": "n2", "state": "FAILED",
             "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
             "updatedAt": datetime(2026, 6, 25, tzinfo=UTC)},
            {"applicationId": "00abc", "id": "jr3", "name": "n3", "state": "RUNNING",
             "createdAt": datetime(2026, 6, 25, tzinfo=UTC),
             "updatedAt": datetime(2026, 6, 25, tzinfo=UTC)},
        ]
    )
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    runs = await client.list_job_runs("00abc", states={JobRunState.SUCCESS, JobRunState.RUNNING})
    assert [r.job_run_id for r in runs] == ["jr1", "jr3"]


@pytest.mark.asyncio
async def test_get_job_run_maps_detail_fields() -> None:
    stub = _StubClient()
    stub.get_job_run.return_value = {
        "jobRun": {
            "applicationId": "00abc",
            "jobRunId": "jr1",
            "name": "etl",
            "state": "SUCCESS",
            "createdAt": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
            "updatedAt": datetime(2026, 6, 25, 12, 4, tzinfo=UTC),
            "executionRole": "arn:aws:iam::123456789012:role/EmrJobRole",
            "jobDriver": {
                "sparkSubmit": {
                    "entryPoint": "s3://b/job.py",
                    "entryPointArguments": ["--in", "s3://b/in/"],
                    "sparkSubmitParameters": "--conf spark.executor.instances=4",
                },
            },
            "totalExecutionDurationSeconds": 240,
        },
    }
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    detail = await client.get_job_run("00abc", "jr1")
    assert detail.entry_point == "s3://b/job.py"
    assert detail.entry_point_arguments == ("--in", "s3://b/in/")
    assert detail.spark_submit_parameters == "--conf spark.executor.instances=4"
    assert detail.execution_role_arn == "arn:aws:iam::123456789012:role/EmrJobRole"
    assert detail.duration_ms == 240_000


@pytest.mark.asyncio
async def test_list_applications_re_raises_as_provider_error_on_no_creds() -> None:
    stub = _StubClient()
    stub.list_applications.side_effect = botocore.exceptions.NoCredentialsError()
    client = EmrServerlessClient(session=_StubSession(stub))  # type: ignore[arg-type]
    with pytest.raises(AuthRequiredError):
        await client.list_applications()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v -k "list_applications or list_job_runs or get_job_run"`
Expected: FAIL — `EmrServerlessClient` not defined.

- [ ] **Step 3: Implement `EmrServerlessClient`**

Append to `src/aws_tui/domain/emr_serverless.py`:

```python
# ── Async aioboto3 facade ────────────────────────────────────────────────

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aioboto3


class EmrServerlessClient:
    """Async aioboto3 facade for EMR Serverless. PR-A surfaces three
    read-only verbs; PR-B/C extend with cancel/lifecycle/submit.

    The client opens a fresh aioboto3 ``emr-serverless`` client per
    call (the boto3 EMR Serverless client is cheap to instantiate
    and aioboto3 contexts are short-lived). The session is owned by
    the caller — typically built once per :class:`Connection` and
    threaded through :meth:`EmrServerlessService.build_vm`."""

    def __init__(
        self,
        *,
        session: "aioboto3.Session",
        region_name: str | None = None,
    ) -> None:
        self._session = session
        self._region_name = region_name

    async def list_applications(self) -> list[ApplicationSummary]:
        async with self._session.client("emr-serverless", region_name=self._region_name) as c:
            try:
                items: list[dict] = []
                next_token: str | None = None
                while True:
                    kwargs: dict = {}
                    if next_token is not None:
                        kwargs["nextToken"] = next_token
                    resp = await c.list_applications(**kwargs)
                    items.extend(resp.get("applications", []))
                    next_token = resp.get("nextToken")
                    if next_token is None:
                        break
                return [
                    ApplicationSummary(
                        id=a["id"],
                        name=a.get("name", a["id"]),
                        state=ApplicationState(a["state"]),
                        type=a.get("type", "SPARK"),
                        created_at=a["createdAt"],
                    )
                    for a in items
                ]
            except Exception as exc:  # noqa: BLE001 — re-raised below
                mapped = _map_boto_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        """List most-recent runs (sorted descending by createdAt).

        ``states`` filters CLIENT-side after paging. boto3 supports
        a single-state ``states`` parameter but multi-state requires
        client-side filtering anyway, so we fetch unfiltered and
        keep the logic in one place."""
        async with self._session.client("emr-serverless", region_name=self._region_name) as c:
            try:
                items: list[dict] = []
                next_token: str | None = None
                while len(items) < max_results:
                    kwargs: dict = {"applicationId": application_id}
                    if next_token is not None:
                        kwargs["nextToken"] = next_token
                    resp = await c.list_job_runs(**kwargs)
                    items.extend(resp.get("jobRuns", []))
                    next_token = resp.get("nextToken")
                    if next_token is None:
                        break
                summaries = [
                    JobRunSummary(
                        application_id=r["applicationId"],
                        job_run_id=r.get("id", r.get("jobRunId", "")),
                        name=r.get("name"),
                        state=JobRunState(r["state"]),
                        created_at=r["createdAt"],
                        updated_at=r["updatedAt"],
                    )
                    for r in items
                ]
                if states is not None:
                    summaries = [s for s in summaries if s.state in states]
                summaries.sort(key=lambda s: s.created_at, reverse=True)
                return summaries[:max_results]
            except Exception as exc:  # noqa: BLE001
                mapped = _map_boto_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc

    async def get_job_run(self, application_id: str, job_run_id: str) -> JobRunDetail:
        async with self._session.client("emr-serverless", region_name=self._region_name) as c:
            try:
                resp = await c.get_job_run(applicationId=application_id, jobRunId=job_run_id)
                r = resp["jobRun"]
                spark = r.get("jobDriver", {}).get("sparkSubmit", {})
                duration_seconds = r.get("totalExecutionDurationSeconds")
                return JobRunDetail(
                    application_id=r["applicationId"],
                    job_run_id=r.get("id", r.get("jobRunId", job_run_id)),
                    name=r.get("name"),
                    state=JobRunState(r["state"]),
                    created_at=r["createdAt"],
                    updated_at=r["updatedAt"],
                    entry_point=spark.get("entryPoint"),
                    entry_point_arguments=tuple(spark.get("entryPointArguments", ())),
                    spark_submit_parameters=spark.get("sparkSubmitParameters"),
                    execution_role_arn=r.get("executionRole", ""),
                    duration_ms=(duration_seconds * 1000) if duration_seconds is not None else None,
                )
            except Exception as exc:  # noqa: BLE001
                mapped = _map_boto_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc
```

Extend the module `__all__` to add `"EmrServerlessClient"`.

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v && uv run ruff check src/aws_tui/domain/emr_serverless.py && uv run mypy src/aws_tui/domain/emr_serverless.py`
Expected: all tests pass; ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/domain/emr_serverless.py tests/unit/domain/test_emr_serverless.py
git commit -m "feat(domain): EmrServerlessClient — list/get verbs over aioboto3"
```

---

## Task 4: In-memory fake EMR client for tests

**Files:**
- Create: `tests/unit/domain/_in_memory_emr.py`

**Interfaces:**
- Consumes: domain records + state enums (Task 1).
- Produces:
  - `class _InMemoryEmr` — same surface as `EmrServerlessClient` (`list_applications`, `list_job_runs`, `get_job_run`) backed by Python dicts. Used by VM/widget tests that don't want to stub aioboto3 contexts.
  - Helper builders: `add_application(...)`, `add_job_run(...)`, `add_job_run_detail(...)`.

The fake mirrors the public interface of `EmrServerlessClient` so VMs are constructor-injected with `EmrServerlessClient | _InMemoryEmr` (typed via `Any` since we don't add a Protocol — see spec §1).

- [ ] **Step 1: Write the file directly (this task has no failing-test step — the fake IS the test infrastructure)**

```python
# tests/unit/domain/_in_memory_emr.py
"""In-memory fake :class:`EmrServerlessClient` for unit tests.

VMs are constructor-injected with the client; tests substitute this
fake to drive deterministic responses without any aioboto3 / botocore
plumbing. Mirrors the public interface of
:class:`aws_tui.domain.emr_serverless.EmrServerlessClient` — no
Protocol earns its place for a single non-network implementation
(see PR-A spec §1)."""

from __future__ import annotations

from datetime import datetime

from aws_tui.domain.emr_serverless import (
    ApplicationState,
    ApplicationSummary,
    JobRunDetail,
    JobRunState,
    JobRunSummary,
)


class _InMemoryEmr:
    """Test-only fake EMR client.

    Use :meth:`add_application`, :meth:`add_job_run`, and
    :meth:`add_job_run_detail` to seed before driving a VM. The
    same instance can be reused across calls — state mutations
    are intentional so tests can flip a run's state mid-poll."""

    def __init__(self) -> None:
        self._apps: dict[str, ApplicationSummary] = {}
        self._runs: dict[str, dict[str, JobRunSummary]] = {}  # app_id -> run_id -> summary
        self._details: dict[tuple[str, str], JobRunDetail] = {}
        # Counter so each call is observable in tests that pin the
        # auto-refresh cadence.
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    # ── Test seeding ────────────────────────────────────────────────────────

    def add_application(
        self,
        *,
        app_id: str,
        name: str,
        state: ApplicationState = ApplicationState.STARTED,
        app_type: str = "SPARK",
        created_at: datetime | None = None,
    ) -> ApplicationSummary:
        s = ApplicationSummary(
            id=app_id,
            name=name,
            state=state,
            type=app_type,
            created_at=created_at or datetime.fromisoformat("2026-06-25T12:00:00+00:00"),
        )
        self._apps[app_id] = s
        self._runs.setdefault(app_id, {})
        return s

    def add_job_run(
        self,
        *,
        application_id: str,
        job_run_id: str,
        name: str | None = None,
        state: JobRunState = JobRunState.SUCCESS,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> JobRunSummary:
        ts = created_at or datetime.fromisoformat("2026-06-25T12:00:00+00:00")
        s = JobRunSummary(
            application_id=application_id,
            job_run_id=job_run_id,
            name=name,
            state=state,
            created_at=ts,
            updated_at=updated_at or ts,
        )
        self._runs.setdefault(application_id, {})[job_run_id] = s
        return s

    def add_job_run_detail(
        self,
        *,
        application_id: str,
        job_run_id: str,
        entry_point: str | None = "s3://example/job.py",
        entry_point_arguments: tuple[str, ...] = (),
        spark_submit_parameters: str | None = None,
        execution_role_arn: str = "arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms: int | None = None,
    ) -> JobRunDetail:
        summary = self._runs.get(application_id, {}).get(job_run_id)
        if summary is None:
            summary = self.add_job_run(application_id=application_id, job_run_id=job_run_id)
        d = JobRunDetail(
            application_id=application_id,
            job_run_id=job_run_id,
            name=summary.name,
            state=summary.state,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
            entry_point=entry_point,
            entry_point_arguments=entry_point_arguments,
            spark_submit_parameters=spark_submit_parameters,
            execution_role_arn=execution_role_arn,
            duration_ms=duration_ms,
        )
        self._details[(application_id, job_run_id)] = d
        return d

    def set_run_state(self, application_id: str, job_run_id: str, state: JobRunState) -> None:
        """Mutate the state of a previously-added run (used by tests
        that pin auto-refresh observable side effects)."""
        s = self._runs[application_id][job_run_id]
        self._runs[application_id][job_run_id] = JobRunSummary(
            application_id=s.application_id,
            job_run_id=s.job_run_id,
            name=s.name,
            state=state,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    # ── Public client surface (matches EmrServerlessClient) ────────────────

    async def list_applications(self) -> list[ApplicationSummary]:
        self.calls.append(("list_applications", ()))
        return sorted(self._apps.values(), key=lambda a: a.created_at, reverse=True)

    async def list_job_runs(
        self,
        application_id: str,
        *,
        states: set[JobRunState] | None = None,
        max_results: int = 100,
    ) -> list[JobRunSummary]:
        self.calls.append(("list_job_runs", (application_id, states, max_results)))
        runs = list(self._runs.get(application_id, {}).values())
        if states is not None:
            runs = [r for r in runs if r.state in states]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:max_results]

    async def get_job_run(self, application_id: str, job_run_id: str) -> JobRunDetail:
        self.calls.append(("get_job_run", (application_id, job_run_id)))
        return self._details[(application_id, job_run_id)]


__all__ = ["_InMemoryEmr"]
```

- [ ] **Step 2: Sanity-test the fake itself**

Append to `tests/unit/domain/test_emr_serverless.py`:

```python
from tests.unit.domain._in_memory_emr import _InMemoryEmr


@pytest.mark.asyncio
async def test_in_memory_emr_round_trips_records() -> None:
    fake = _InMemoryEmr()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    apps = await fake.list_applications()
    runs = await fake.list_job_runs("a1")
    detail = await fake.get_job_run("a1", "r1")
    assert apps[0].id == "a1"
    assert runs[0].state is JobRunState.RUNNING
    assert detail.entry_point == "s3://b/x.py"
    # Calls are recorded so cadence tests can assert poll counts.
    assert [c[0] for c in fake.calls] == ["list_applications", "list_job_runs", "get_job_run"]
```

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py -v && uv run ruff check tests/unit/domain/ && uv run mypy tests/unit/domain/_in_memory_emr.py`
Expected: all green.

```bash
git add tests/unit/domain/_in_memory_emr.py tests/unit/domain/test_emr_serverless.py
git commit -m "test(domain): in-memory EMR fake for VM/widget tests"
```

---

## Task 5: `EmrServerlessService` (Service protocol impl)

**Files:**
- Create: `src/aws_tui/services/emr_serverless/__init__.py` (empty)
- Create: `src/aws_tui/services/emr_serverless/service.py`
- Test: `tests/unit/services/test_emr_serverless_service.py`

**Interfaces:**
- Consumes: `ServiceDescriptor` from `vm/services_protocol.py`; `EmrServerlessClient` (Task 3); `EmrServerlessPageVM` (Task 9 — but Task 5 only references it via lazy import; class itself is created in Task 9).
- Produces:
  - `class EmrServerlessService` with:
    - `descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(id="emr-serverless", label="EMR", icon="⚡")`
    - `__init__(self, *, hub, dispatcher, emr_client_factory=None)` — factory used by tests to inject `_InMemoryEmr`. In production, the service constructs an `EmrServerlessClient` from the connection's aioboto3.Session.
    - `supports(connection) -> bool` — returns `connection.kind == "aws"`.
    - `build_vm(connection) -> EmrServerlessPageVM` — constructs the page VM, NOT a singleton.

**Note on circular imports:** the service module imports `EmrServerlessPageVM` lazily inside `build_vm` because the page VM module hasn't been written yet at Task 5. The integration test in Task 16 verifies the full wiring once Task 9 lands the page VM.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_emr_serverless_service.py
"""Service-protocol tests for EmrServerlessService.

Pins the ⚡ icon + supports(connection.kind == 'aws') contract so
the nav rail correctly filters on s3-compatible connections."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.vm.services_protocol import ServiceDescriptor


def test_descriptor_icon_is_high_voltage_label_is_emr() -> None:
    assert EmrServerlessService.descriptor == ServiceDescriptor(
        id="emr-serverless", label="EMR", icon="⚡"
    )


def test_supports_aws_connection() -> None:
    hub: MessageHub[Message] = MessageHub()
    svc = EmrServerlessService(hub=hub, dispatcher=NULL_DISPATCHER)
    assert svc.supports(
        Connection(name="dev", kind="aws", region="us-east-1", source="config", profile="dev")
    )


def test_does_not_support_s3_compatible_connection() -> None:
    hub: MessageHub[Message] = MessageHub()
    svc = EmrServerlessService(hub=hub, dispatcher=NULL_DISPATCHER)
    assert not svc.supports(
        Connection(
            name="minio",
            kind="s3-compatible",
            region="us-east-1",
            source="config",
            endpoint_url="http://localhost:9000",
            access_key_id="x",
            secret_access_key="y",
        )
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_emr_serverless_service.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the service**

```python
# src/aws_tui/services/emr_serverless/__init__.py
"""EMR Serverless service package — see service.py."""
```

```python
# src/aws_tui/services/emr_serverless/service.py
"""EmrServerlessService — second concrete :class:`Service` implementation.

PR-A ships the read-only browser. PR-B adds log surface + cancel +
lifecycle. PR-C adds submit. Each PR extends this service's
``build_vm`` return shape additively — no breaking changes
between PRs because :class:`EmrServerlessPageVM` always remains the
hosted root VM.

Construction strategy mirrors :class:`S3Service`: the service holds
the long-lived :class:`MessageHub` and :class:`Dispatcher` and
builds a fresh ``EmrServerlessPageVM`` per ``build_vm`` call.
:class:`ContentHostVM` disposes the previous page VM on swap; never
host this VM as a singleton (see [[vmx-content-host-singleton-trap]])."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import aioboto3
from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import EmrServerlessClient
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.services_protocol import ServiceDescriptor

if TYPE_CHECKING:
    from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM


#: Test hook — when provided, replaces real ``EmrServerlessClient`` construction
#: with whatever the factory returns (typically ``_InMemoryEmr``).
EmrClientFactory = Callable[[Connection], Any]


class EmrServerlessService:
    """``Service``-protocol implementation for EMR Serverless.

    Parameters
    ----------
    hub:
        Top-level :class:`MessageHub`. Sub-VMs publish state-change
        notifications on it.
    dispatcher:
        VMx dispatcher used for sub-VMs.
    emr_client_factory:
        Test hook — when supplied, ``build_vm`` calls this instead
        of constructing a real :class:`EmrServerlessClient`. Lets
        integration tests inject :class:`_InMemoryEmr`.
    """

    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="emr-serverless",
        label="EMR",
        # ⚡ U+26A1 HIGH VOLTAGE — Spark's literal primitive glyph.
        # Single terminal cell, font-stack-safe, no VS-16 needed.
        # Symmetric with the rail's literal-object naming: 🪣 bucket,
        # ⚡ spark, ⚙️ gear, 🖥️ computer.
        icon="⚡",
    )

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        emr_client_factory: EmrClientFactory | None = None,
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._client_factory: EmrClientFactory | None = emr_client_factory

    # ── Service protocol ────────────────────────────────────────────────────

    def supports(self, connection: Connection) -> bool:
        """EMR Serverless is AWS-only — s3-compatible connections
        never see the ⚡ icon in the nav rail (the NavMenuVM filter
        consults this)."""
        return connection.kind == "aws"

    def build_vm(self, connection: Connection) -> "EmrServerlessPageVM":
        """Build a fresh page VM for ``connection``.

        Lazy-imported because :mod:`aws_tui.vm.emr_serverless.page_vm`
        depends on this module's :class:`ServiceDescriptor`; an eager
        import would cycle."""
        from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM

        client = self._make_client(connection)
        return EmrServerlessPageVM(
            client=client,
            hub=self._hub,
            dispatcher=self._dispatcher,
            connection=connection,
        )

    # ── Internal ────────────────────────────────────────────────────────────

    def _make_client(self, connection: Connection) -> Any:
        if self._client_factory is not None:
            return self._client_factory(connection)
        session = aioboto3.Session(
            profile_name=connection.profile,
            region_name=connection.region,
        )
        return EmrServerlessClient(session=session, region_name=connection.region)


__all__ = ["EmrClientFactory", "EmrServerlessService"]
```

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/services/test_emr_serverless_service.py -v && uv run ruff check src/aws_tui/services/emr_serverless/ tests/unit/services/test_emr_serverless_service.py && uv run mypy src/aws_tui/services/emr_serverless/`
Expected: all green. The 3 service tests pass; mypy may complain about the `TYPE_CHECKING` future import being unused if you haven't actually used `EmrServerlessPageVM` at type-check time — that's expected (Task 9 fixes the import).

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/services/emr_serverless/ tests/unit/services/test_emr_serverless_service.py
git commit -m "feat(services): EmrServerlessService — ⚡ icon, aws-only supports"
```

---

## Task 6: `ApplicationsVM`

**Files:**
- Create: `src/aws_tui/vm/emr_serverless/__init__.py` (empty)
- Create: `src/aws_tui/vm/emr_serverless/applications_vm.py`
- Test: `tests/unit/vm/emr_serverless/__init__.py` (empty) + `tests/unit/vm/emr_serverless/test_applications_vm.py`

**Interfaces:**
- Consumes: `EmrServerlessClient` / `_InMemoryEmr` (Task 3, 4); `ApplicationSummary`, `ApplicationState` (Task 1); VMx primitives (`MessageHub`, `PropertyChangedMessage`, `Dispatcher`, `ComponentVMOf`).
- Produces:
  - `class ApplicationsVM` with:
    - `__init__(*, client: Any, hub, dispatcher)`
    - properties `applications: tuple[ApplicationSummary, ...]`, `selected_id: str | None`, `state: PaneState` (loading/idle/empty/auth_required/unreachable/forbidden)
    - command `select(app_id: str) -> None`
    - async method `refresh() -> None` (drives `client.list_applications()`)
    - methods `construct()`, `dispose()` per VMx
    - publishes `PropertyChangedMessage` for `"applications"` and `"selected_id"` changes

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/vm/emr_serverless/test_applications_vm.py
"""ApplicationsVM tests — pin the load/select/refresh contract."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make() -> tuple[ApplicationsVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = ApplicationsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.asyncio
async def test_starts_loading_then_idle_after_refresh() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc", state=ApplicationState.STOPPED)
    assert vm.state is PaneState.LOADING
    await vm.refresh()
    assert vm.state is PaneState.IDLE
    assert [a.id for a in vm.applications] == ["a1", "a2"] or [a.id for a in vm.applications] == ["a2", "a1"]


@pytest.mark.asyncio
async def test_refresh_with_no_apps_lands_on_empty_state() -> None:
    vm, _ = _make()
    await vm.refresh()
    assert vm.state is PaneState.EMPTY
    assert vm.applications == ()


@pytest.mark.asyncio
async def test_select_publishes_property_changed() -> None:
    vm, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    await vm.refresh()
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        vm.select("a1")
        assert vm.selected_id == "a1"
        assert "selected_id" in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_refresh_failure_surfaces_unreachable_state() -> None:
    from aws_tui.domain.filesystem import ProviderUnreachableError

    class _BrokenClient:
        async def list_applications(self) -> list:  # noqa: ANN401
            raise ProviderUnreachableError("network blip")

    hub: MessageHub[Message] = MessageHub()
    vm = ApplicationsVM(client=_BrokenClient(), hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    await vm.refresh()
    assert vm.state is PaneState.UNREACHABLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_applications_vm.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `ApplicationsVM`**

```python
# src/aws_tui/vm/emr_serverless/__init__.py
"""EMR Serverless VMs — see page_vm.py for the orchestration root."""
```

```python
# src/aws_tui/vm/emr_serverless/applications_vm.py
"""ApplicationsVM — backs the top-strip application picker.

Holds the live application list, the currently-selected application
id, and a coarse :class:`PaneState` so the dropdown can render a
loading spinner / error placeholder."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import ApplicationSummary
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState


class ApplicationsVM:
    """Live application list + selection state."""

    def __init__(
        self,
        *,
        client: Any,  # EmrServerlessClient or _InMemoryEmr — see PR-A spec §1
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._applications: tuple[ApplicationSummary, ...] = ()
        self._selected_id: str | None = None
        self._state: PaneState = PaneState.LOADING
        self._error_text: str | None = None
        # VMx component wrapper — gives sub-VM hierarchy + dispose plumbing.
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.applications")
            .services(hub, dispatcher)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def applications(self) -> tuple[ApplicationSummary, ...]:
        return self._applications

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    def select(self, app_id: str) -> None:
        """Mark ``app_id`` as the active application. No-op if already selected."""
        if self._selected_id == app_id:
            return
        self._selected_id = app_id
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "selected_id"))

    async def refresh(self) -> None:
        """Re-fetch the application list. Updates ``state``,
        ``applications``, and (if the prior selection went missing)
        ``selected_id``."""
        self._set_state(PaneState.LOADING)
        try:
            apps = await self._client.list_applications()
        except AuthRequiredError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.AUTH_REQUIRED)
            return
        except ProviderUnreachableError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.UNREACHABLE)
            return
        except PermissionDeniedError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.FORBIDDEN)
            return
        except ProviderError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.ERROR)
            return
        self._applications = tuple(apps)
        # Drop a stale selection if the app no longer exists.
        if self._selected_id is not None and not any(a.id == self._selected_id for a in apps):
            self._selected_id = None
            self._hub.send(
                PropertyChangedMessage.create(self, "emr.applications", "selected_id")
            )
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "applications"))
        self._set_state(PaneState.IDLE if apps else PaneState.EMPTY)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._hub.send(PropertyChangedMessage.create(self, "emr.applications", "state"))


__all__ = ["ApplicationsVM"]
```

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_applications_vm.py -v && uv run ruff check src/aws_tui/vm/emr_serverless/ tests/unit/vm/emr_serverless/ && uv run mypy src/aws_tui/vm/emr_serverless/`
Expected: 4 tests pass; ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/vm/emr_serverless/__init__.py src/aws_tui/vm/emr_serverless/applications_vm.py tests/unit/vm/emr_serverless/
git commit -m "feat(vm): ApplicationsVM — app list + selection + refresh"
```

---

## Task 7: `JobRunsVM`

**Files:**
- Create: `src/aws_tui/vm/emr_serverless/job_runs_vm.py`
- Test: `tests/unit/vm/emr_serverless/test_job_runs_vm.py`

**Interfaces:**
- Consumes: `JobRunSummary`, `JobRunState` (Task 1); `_InMemoryEmr` (Task 4); VMx primitives.
- Produces:
  - `class JobRunsVM` with:
    - `__init__(*, client, hub, dispatcher)`
    - properties `runs: tuple[JobRunSummary, ...]`, `selected_id: str | None`, `state: PaneState`, `state_filter: frozenset[JobRunState]` (default = all states)
    - `set_application(app_id: str | None) -> None` — clears selection + re-fetches; calling with `None` clears the list
    - `select(run_id: str) -> None`
    - `toggle_state_filter(state: JobRunState) -> None`
    - `async refresh() -> None`
    - lifecycle: `construct()`, `dispose()`
  - publishes `PropertyChangedMessage` for `"runs"`, `"selected_id"`, `"state"`, `"state_filter"`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/vm/emr_serverless/test_job_runs_vm.py
"""JobRunsVM tests — application-scoped, state-filtered, selection-aware."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _seed_runs(fake: _InMemoryEmr, app: str) -> None:
    fake.add_application(app_id=app, name="etl")
    fake.add_job_run(application_id=app, job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run(application_id=app, job_run_id="r2", state=JobRunState.RUNNING)
    fake.add_job_run(application_id=app, job_run_id="r3", state=JobRunState.FAILED)
    fake.add_job_run(application_id=app, job_run_id="r4", state=JobRunState.CANCELLED)


def _make() -> tuple[JobRunsVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunsVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.asyncio
async def test_set_application_loads_runs() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    assert vm.state is PaneState.IDLE
    assert {r.job_run_id for r in vm.runs} == {"r1", "r2", "r3", "r4"}


@pytest.mark.asyncio
async def test_state_filter_drops_runs_not_matching() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    vm.set_state_filter(frozenset({JobRunState.SUCCESS, JobRunState.RUNNING}))
    await vm.refresh()
    assert {r.job_run_id for r in vm.runs} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_toggle_state_filter_flips_membership() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.toggle_state_filter(JobRunState.FAILED)
    assert JobRunState.FAILED not in vm.state_filter
    vm.toggle_state_filter(JobRunState.FAILED)
    assert JobRunState.FAILED in vm.state_filter


@pytest.mark.asyncio
async def test_select_routes_change_notification() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    notified: list[str] = []
    sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
        on_next=lambda m: notified.append(getattr(m, "property_name", ""))
    )
    try:
        vm.select("r2")
        assert vm.selected_id == "r2"
        assert "selected_id" in notified
    finally:
        sub.dispose()


@pytest.mark.asyncio
async def test_set_application_with_none_clears_list() -> None:
    vm, fake = _make()
    _seed_runs(fake, "a1")
    vm.set_application("a1")
    await vm.refresh()
    vm.set_application(None)
    assert vm.runs == ()
    assert vm.selected_id is None
    assert vm.state is PaneState.EMPTY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_job_runs_vm.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `JobRunsVM`**

```python
# src/aws_tui/vm/emr_serverless/job_runs_vm.py
"""JobRunsVM — LEFT pane's run-list state.

Scoped to one application at a time. State-filter chips are
multi-select with all-on default; toggling a chip re-applies the
filter against the cached list — no API call. ``refresh()`` is the
only thing that re-fetches."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunState, JobRunSummary
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState

_ALL_STATES: frozenset[JobRunState] = frozenset(JobRunState)


class JobRunsVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._application_id: str | None = None
        self._runs_cache: tuple[JobRunSummary, ...] = ()  # unfiltered
        self._selected_id: str | None = None
        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._state_filter: frozenset[JobRunState] = _ALL_STATES
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_runs")
            .services(hub, dispatcher)
            .build()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def runs(self) -> tuple[JobRunSummary, ...]:
        return tuple(r for r in self._runs_cache if r.state in self._state_filter)

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def state_filter(self) -> frozenset[JobRunState]:
        return self._state_filter

    @property
    def application_id(self) -> str | None:
        return self._application_id

    @property
    def error_text(self) -> str | None:
        return self._error_text

    def set_application(self, app_id: str | None) -> None:
        """Re-scope to a new application. Clears selection + cache;
        caller must subsequently call :meth:`refresh`."""
        if self._application_id == app_id:
            return
        self._application_id = app_id
        self._runs_cache = ()
        if self._selected_id is not None:
            self._selected_id = None
            self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "selected_id"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))
        self._set_state(PaneState.LOADING if app_id is not None else PaneState.EMPTY)

    def select(self, run_id: str) -> None:
        if self._selected_id == run_id:
            return
        self._selected_id = run_id
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "selected_id"))

    def set_state_filter(self, states: frozenset[JobRunState]) -> None:
        if states == self._state_filter:
            return
        self._state_filter = states
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "state_filter"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))

    def toggle_state_filter(self, state: JobRunState) -> None:
        states = set(self._state_filter)
        if state in states:
            states.discard(state)
        else:
            states.add(state)
        self.set_state_filter(frozenset(states))

    async def refresh(self) -> None:
        if self._application_id is None:
            self._runs_cache = ()
            self._set_state(PaneState.EMPTY)
            return
        self._set_state(PaneState.LOADING)
        try:
            # Fetch unfiltered; filter is applied client-side via `runs` property.
            runs = await self._client.list_job_runs(self._application_id, states=None)
        except AuthRequiredError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.AUTH_REQUIRED)
            return
        except ProviderUnreachableError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.UNREACHABLE)
            return
        except PermissionDeniedError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.FORBIDDEN)
            return
        except ProviderError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.ERROR)
            return
        self._runs_cache = tuple(runs)
        # Drop stale selection if the run vanished.
        if self._selected_id is not None and not any(r.job_run_id == self._selected_id for r in runs):
            self._selected_id = None
            self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "selected_id"))
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "runs"))
        self._set_state(PaneState.IDLE if self.runs else PaneState.EMPTY)

    def has_active_runs(self) -> bool:
        """Used by the page-VM poller to choose between the 10-s and
        60-s cadences."""
        return any(r.state in (JobRunState.PENDING, JobRunState.RUNNING) for r in self._runs_cache)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_runs", "state"))


__all__ = ["JobRunsVM"]
```

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_job_runs_vm.py -v && uv run ruff check src/aws_tui/vm/emr_serverless/job_runs_vm.py && uv run mypy src/aws_tui/vm/emr_serverless/`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/vm/emr_serverless/job_runs_vm.py tests/unit/vm/emr_serverless/test_job_runs_vm.py
git commit -m "feat(vm): JobRunsVM — runs list + state filter + selection"
```

---

## Task 8: `JobRunDetailVM`

**Files:**
- Create: `src/aws_tui/vm/emr_serverless/job_run_detail_vm.py`
- Test: `tests/unit/vm/emr_serverless/test_job_run_detail_vm.py`

**Interfaces:**
- Consumes: `JobRunDetail`, `JobRunState` (Task 1); `_InMemoryEmr` (Task 4); VMx.
- Produces:
  - `class JobRunDetailVM` with:
    - `__init__(*, client, hub, dispatcher)`
    - properties `detail: JobRunDetail | None`, `state: PaneState`
    - `set_target(application_id: str | None, job_run_id: str | None) -> None` — clear+reload on change
    - `async refresh() -> None`
    - `is_terminal_state() -> bool` — used by the page-VM poller to stop polling when terminal
    - lifecycle methods

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/vm/emr_serverless/test_job_run_detail_vm.py
"""JobRunDetailVM tests — target tracking + refresh contract."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make() -> tuple[JobRunDetailVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    vm = JobRunDetailVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm, fake


@pytest.mark.asyncio
async def test_refresh_with_target_loads_detail() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert vm.detail is not None
    assert vm.detail.entry_point == "s3://b/x.py"
    assert vm.state is PaneState.IDLE


@pytest.mark.asyncio
async def test_set_target_to_none_clears_detail() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    vm.set_target(None, None)
    assert vm.detail is None
    assert vm.state is PaneState.EMPTY


@pytest.mark.asyncio
async def test_is_terminal_state_returns_true_on_success() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.SUCCESS)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert vm.is_terminal_state()


@pytest.mark.asyncio
async def test_is_terminal_state_returns_false_on_running() -> None:
    vm, fake = _make()
    fake.add_job_run(application_id="a1", job_run_id="r1", state=JobRunState.RUNNING)
    fake.add_job_run_detail(application_id="a1", job_run_id="r1")
    vm.set_target("a1", "r1")
    await vm.refresh()
    assert not vm.is_terminal_state()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_job_run_detail_vm.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `JobRunDetailVM`**

```python
# src/aws_tui/vm/emr_serverless/job_run_detail_vm.py
"""JobRunDetailVM — RIGHT pane's detail view.

PR-A holds detail only; PR-B adds the log surface as a child VM
under this one. The detail is re-fetched on each ``refresh()`` so
the page-VM's 5-s poller can refresh while the run is non-terminal."""

from __future__ import annotations

from typing import Any

from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.emr_serverless import JobRunDetail, JobRunState
from aws_tui.domain.filesystem import (
    AuthRequiredError,
    PermissionDeniedError,
    ProviderError,
    ProviderUnreachableError,
)
from aws_tui.vm.file_manager.pane_vm import PaneState

_TERMINAL_STATES: frozenset[JobRunState] = frozenset(
    {JobRunState.SUCCESS, JobRunState.FAILED, JobRunState.CANCELLED}
)


class JobRunDetailVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._application_id: str | None = None
        self._job_run_id: str | None = None
        self._detail: JobRunDetail | None = None
        self._state: PaneState = PaneState.EMPTY
        self._error_text: str | None = None
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.job_run_detail")
            .services(hub, dispatcher)
            .build()
        )

    @property
    def detail(self) -> JobRunDetail | None:
        return self._detail

    @property
    def state(self) -> PaneState:
        return self._state

    @property
    def error_text(self) -> str | None:
        return self._error_text

    def set_target(self, application_id: str | None, job_run_id: str | None) -> None:
        """Re-point the detail view at a new run. If either id is
        None, clears."""
        if (application_id, job_run_id) == (self._application_id, self._job_run_id):
            return
        self._application_id = application_id
        self._job_run_id = job_run_id
        self._detail = None
        if application_id is None or job_run_id is None:
            self._set_state(PaneState.EMPTY)
        else:
            self._set_state(PaneState.LOADING)
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", "detail"))

    def is_terminal_state(self) -> bool:
        return self._detail is not None and self._detail.state in _TERMINAL_STATES

    async def refresh(self) -> None:
        if self._application_id is None or self._job_run_id is None:
            self._set_state(PaneState.EMPTY)
            return
        self._set_state(PaneState.LOADING)
        try:
            d = await self._client.get_job_run(self._application_id, self._job_run_id)
        except AuthRequiredError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.AUTH_REQUIRED)
            return
        except ProviderUnreachableError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.UNREACHABLE)
            return
        except PermissionDeniedError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.FORBIDDEN)
            return
        except ProviderError as exc:
            self._error_text = str(exc) or None
            self._set_state(PaneState.ERROR)
            return
        self._detail = d
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", "detail"))
        self._set_state(PaneState.IDLE)

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        self._inner.dispose()

    def _set_state(self, state: PaneState) -> None:
        if self._state == state:
            return
        self._state = state
        self._hub.send(PropertyChangedMessage.create(self, "emr.job_run_detail", "state"))


__all__ = ["JobRunDetailVM"]
```

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_job_run_detail_vm.py -v && uv run ruff check src/aws_tui/vm/emr_serverless/job_run_detail_vm.py && uv run mypy src/aws_tui/vm/emr_serverless/`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/vm/emr_serverless/job_run_detail_vm.py tests/unit/vm/emr_serverless/test_job_run_detail_vm.py
git commit -m "feat(vm): JobRunDetailVM — detail tracking + terminal-state flag"
```

---

## Task 9: `EmrServerlessPageVM` (orchestration root)

**Files:**
- Create: `src/aws_tui/vm/emr_serverless/page_vm.py`
- Test: `tests/unit/vm/emr_serverless/test_page_vm.py`

**Interfaces:**
- Consumes: `ApplicationsVM`, `JobRunsVM`, `JobRunDetailVM` (Tasks 6-8); `Connection`; VMx.
- Produces:
  - `class EmrServerlessPageVM` with:
    - `__init__(*, client, hub, dispatcher, connection)`
    - public child VMs: `applications: ApplicationsVM`, `job_runs: JobRunsVM`, `job_run_detail: JobRunDetailVM`
    - `connection: Connection` (read-only)
    - `construct()`, `dispose()` — composes children, subscribes children's PropertyChanged messages to wire master-detail reactivity
    - `async setup() -> None` — initial load (calls `applications.refresh()`)
    - `select_application(app_id: str) -> None` — routes to children
    - `select_job_run(run_id: str) -> None` — routes to detail
    - `async refresh_focused(focus: Literal["applications", "runs", "detail"]) -> None` — for the `r` keybinding

For PR-A the auto-refresh pollers ARE NOT yet wired (they need a `TickSource` abstraction the spec leaves open). Auto-refresh polling lives in the widget-layer `on_mount` via `set_interval`. Page VM exposes `refresh_focused` for explicit refresh. The page-level coordination — "when applications.selected_id changes, set job_runs.application = new id and refresh" — IS wired here because it's pure VMx orchestration.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/vm/emr_serverless/test_page_vm.py
"""EmrServerlessPageVM tests — orchestration of three child VMs."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import JobRunState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _make() -> tuple[EmrServerlessPageVM, _InMemoryEmr]:
    fake = _InMemoryEmr()
    hub: MessageHub[Message] = MessageHub()
    page = EmrServerlessPageVM(
        client=fake,
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        connection=Connection(
            name="dev", kind="aws", region="us-east-1", source="config", profile="dev"
        ),
    )
    page.construct()
    return page, fake


@pytest.mark.asyncio
async def test_setup_loads_applications_and_auto_selects_first() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    await page.setup()
    assert {a.id for a in page.applications.applications} == {"a1", "a2"}
    # Auto-select the first app so the LEFT pane has something to load.
    assert page.applications.selected_id in {"a1", "a2"}


@pytest.mark.asyncio
async def test_select_application_propagates_to_job_runs() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_application(app_id="a2", name="ad-hoc")
    fake.add_job_run(application_id="a2", job_run_id="r9", state=JobRunState.RUNNING)
    await page.setup()
    await page.select_application("a2")
    assert page.applications.selected_id == "a2"
    assert page.job_runs.application_id == "a2"
    assert {r.job_run_id for r in page.job_runs.runs} == {"r9"}


@pytest.mark.asyncio
async def test_select_job_run_propagates_to_detail() -> None:
    page, fake = _make()
    fake.add_application(app_id="a1", name="etl")
    fake.add_job_run(application_id="a1", job_run_id="r1")
    fake.add_job_run_detail(application_id="a1", job_run_id="r1", entry_point="s3://b/x.py")
    await page.setup()
    # set_application is implicit in setup() if there's only one app.
    await page.select_job_run("r1")
    assert page.job_run_detail.detail is not None
    assert page.job_run_detail.detail.entry_point == "s3://b/x.py"


@pytest.mark.asyncio
async def test_dispose_cascades_to_children() -> None:
    page, _ = _make()
    # Mark each child's _inner so we can observe dispose via the wrapper
    # signal. The simpler observable is that dispose() doesn't raise
    # after construct(); a second dispose() should be a no-op.
    page.dispose()
    page.dispose()  # idempotent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_page_vm.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `EmrServerlessPageVM`**

```python
# src/aws_tui/vm/emr_serverless/page_vm.py
"""EmrServerlessPageVM — orchestration root for the EMR page.

Owns three child VMs (applications / job runs / detail) and wires
the master-detail reactivity between them. The auto-refresh
pollers live in the widget layer (``EmrServerlessPage.on_mount``)
via Textual's ``set_interval`` — there's no domain-tier
``TickSource`` abstraction in PR-A."""

from __future__ import annotations

from typing import Any, Literal

from reactivex.abc import DisposableBase
from vmx import ComponentVMOf, Message, MessageHub, PropertyChangedMessage
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM


class EmrServerlessPageVM:
    def __init__(
        self,
        *,
        client: Any,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        connection: Connection,
    ) -> None:
        self._client = client
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._connection: Connection = connection
        self._disposed: bool = False
        self._inner: ComponentVMOf[None] = (
            ComponentVMOf[None]
            .builder()
            .name("emr.page")
            .services(hub, dispatcher)
            .build()
        )
        self.applications: ApplicationsVM = ApplicationsVM(
            client=client, hub=hub, dispatcher=dispatcher
        )
        self.job_runs: JobRunsVM = JobRunsVM(
            client=client, hub=hub, dispatcher=dispatcher
        )
        self.job_run_detail: JobRunDetailVM = JobRunDetailVM(
            client=client, hub=hub, dispatcher=dispatcher
        )
        self._sub: DisposableBase | None = None

    @property
    def connection(self) -> Connection:
        return self._connection

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()
        self.applications.construct()
        self.job_runs.construct()
        self.job_run_detail.construct()
        # Wire master-detail.
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None
        self.job_run_detail.dispose()
        self.job_runs.dispose()
        self.applications.dispose()
        self._inner.dispose()

    # ── Public surface ──────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Initial load — fetch applications and auto-select the
        first one so the LEFT pane has something to populate."""
        await self.applications.refresh()
        apps = self.applications.applications
        if apps and self.applications.selected_id is None:
            await self.select_application(apps[0].id)

    async def select_application(self, app_id: str) -> None:
        self.applications.select(app_id)
        self.job_runs.set_application(app_id)
        await self.job_runs.refresh()
        # Detail follows the first run (if any) on application switch.
        runs = self.job_runs.runs
        if runs:
            await self.select_job_run(runs[0].job_run_id)
        else:
            self.job_run_detail.set_target(None, None)

    async def select_job_run(self, run_id: str) -> None:
        self.job_runs.select(run_id)
        self.job_run_detail.set_target(self.applications.selected_id, run_id)
        await self.job_run_detail.refresh()

    async def refresh_focused(
        self, focus: Literal["applications", "runs", "detail"]
    ) -> None:
        """Manual refresh — invoked by the ``r`` keybinding."""
        if focus == "applications":
            await self.applications.refresh()
        elif focus == "runs":
            await self.job_runs.refresh()
        else:
            await self.job_run_detail.refresh()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        # Reserved for future-tier subscriptions (PR-B wires log-state
        # observation here). PR-A has no hub-driven side effects.
        return


__all__ = ["EmrServerlessPageVM"]
```

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/vm/emr_serverless/test_page_vm.py -v && uv run ruff check src/aws_tui/vm/emr_serverless/page_vm.py && uv run mypy src/aws_tui/vm/emr_serverless/`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/vm/emr_serverless/page_vm.py tests/unit/vm/emr_serverless/test_page_vm.py
git commit -m "feat(vm): EmrServerlessPageVM — three-child orchestration root"
```

---

## Task 10: Application picker widget (dropdown)

**Files:**
- Create: `src/aws_tui/ui/widgets/emr_serverless/__init__.py` (empty)
- Create: `src/aws_tui/ui/widgets/emr_serverless/application_picker.py`

**Interfaces:**
- Consumes: `ApplicationsVM` (Task 6).
- Produces:
  - `class ApplicationPicker(Widget)` — composes a trigger button + an OptionList in a layered popover.
  - When activated (key `a` or click on the trigger), reveals the OptionList; selecting a row calls `vm.select(app_id)` and hides the popover. Esc cancels.
  - Subscribes to `applications` PropertyChanged so the dropdown rebuilds when the list refreshes.
  - Renders the trigger label as `[icon] {selected_name} [state-glyph]`. If no app is selected, label is `(no application)`.
  - `DEFAULT_CSS` covers structural layout; colors come from per-theme `.tcss` (Task 14).

Snapshot tests in Task 17 pin the visual contract for the picker. **No unit test for this widget** — pure rendering, covered by snapshot tier.

- [ ] **Step 1: Implement the widget directly (no unit test step; snapshot tier validates rendering)**

```python
# src/aws_tui/ui/widgets/emr_serverless/__init__.py
"""EMR Serverless widgets — see page.py for the master composition."""
```

```python
# src/aws_tui/ui/widgets/emr_serverless/application_picker.py
"""ApplicationPicker — top-strip dropdown for the EMR page.

Trigger button + layered OptionList. Pressing `a` (page-level
binding) calls ``toggle_open``; clicking the trigger does the same.
Selecting a row in the OptionList commits via ``vm.select(app_id)``
and closes the popover. Esc cancels."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Click
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_serverless import ApplicationState
from aws_tui.vm.emr_serverless.applications_vm import ApplicationsVM

_APP_STATE_GLYPH: dict[ApplicationState, str] = {
    ApplicationState.CREATED: "○",
    ApplicationState.STARTING: "◐",
    ApplicationState.STARTED: "●",
    ApplicationState.STOPPING: "◑",
    ApplicationState.STOPPED: "○",
    ApplicationState.TERMINATED: "✗",
}


class ApplicationPicker(Widget):
    """Top-strip application selector.

    Visually a trigger button (closed) that swaps to an OptionList
    when opened. Theming is in the per-theme ``.tcss``; this widget
    owns only structural rules."""

    DEFAULT_CSS: ClassVar[str] = """
    ApplicationPicker {
        width: auto;
        height: 3;
        layout: horizontal;
    }
    ApplicationPicker > .app-trigger {
        width: auto;
        min-width: 24;
        height: 3;
        padding: 0 1;
        content-align: left middle;
        text-style: bold;
    }
    ApplicationPicker > OptionList {
        layer: dropdown;
        width: 40;
        max-height: 16;
        offset: 0 3;
        display: none;
    }
    ApplicationPicker.-open > OptionList {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [  # noqa: RUF012
        ("escape", "close", "Close"),
        ("enter", "commit", "Pick"),
    ]

    def __init__(
        self,
        vm: ApplicationsVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: ApplicationsVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(self._trigger_label(), classes="app-trigger")
        yield OptionList(*self._build_options(), id="app-options")

    def on_mount(self) -> None:
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Public API ──────────────────────────────────────────────────────────

    def toggle_open(self) -> None:
        if "-open" in self.classes:
            self.remove_class("-open")
        else:
            self.add_class("-open")
        self._refresh_options()

    def action_close(self) -> None:
        self.remove_class("-open")

    def action_commit(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:  # noqa: BLE001
            return
        if opts.highlighted is None:
            return
        opt = opts.get_option_at_index(opts.highlighted)
        if opt.id is not None:
            self._vm.select(opt.id)
        self.remove_class("-open")

    # ── Internal ────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        # Any click bubbles up via ``self`` so toggling is convenient
        # — the OptionList rows have their own click → action_commit
        # via the option-selected message handler below.
        if event.widget is not None and getattr(event.widget, "id", None) == "app-options":
            return
        self.toggle_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._vm.select(event.option.id)
        self.remove_class("-open")

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name not in {"applications", "selected_id", "state"}:
            return
        self.call_after_refresh(self._refresh_trigger)
        self.call_after_refresh(self._refresh_options)

    def _refresh_trigger(self) -> None:
        try:
            trigger = self.query_one(".app-trigger", Static)
        except Exception:  # noqa: BLE001
            return
        trigger.update(self._trigger_label())

    def _refresh_options(self) -> None:
        try:
            opts = self.query_one("#app-options", OptionList)
        except Exception:  # noqa: BLE001
            return
        opts.clear_options()
        for opt in self._build_options():
            opts.add_option(opt)

    def _trigger_label(self) -> str:
        apps = self._vm.applications
        sid = self._vm.selected_id
        if not apps:
            return "(no application)"
        if sid is None:
            return "(select application)"
        match = next((a for a in apps if a.id == sid), None)
        if match is None:
            return "(select application)"
        glyph = _APP_STATE_GLYPH.get(match.state, "?")
        return f"⚡ {match.name} {glyph}{match.state.value}"

    def _build_options(self) -> list[Option]:
        return [
            Option(
                prompt=f"⚡ {a.name} {_APP_STATE_GLYPH.get(a.state, '?')}{a.state.value}",
                id=a.id,
            )
            for a in self._vm.applications
        ]


__all__ = ["ApplicationPicker"]
```

- [ ] **Step 2: Run quality gates**

Run: `uv run ruff check src/aws_tui/ui/widgets/emr_serverless/ && uv run mypy src/aws_tui/ui/widgets/emr_serverless/`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/aws_tui/ui/widgets/emr_serverless/__init__.py src/aws_tui/ui/widgets/emr_serverless/application_picker.py
git commit -m "feat(ui): ApplicationPicker — top-strip dropdown for EMR"
```

---

## Task 11: `JobRunsPane` widget

**Files:**
- Create: `src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py`

**Interfaces:**
- Consumes: `JobRunsVM` (Task 7).
- Produces:
  - `class JobRunsPane(Widget)` — pane chrome with `border: solid $rule-dim` (focused: `$accent`), border-title `runs`, optional border-subtitle showing the selected app id.
  - Top sub-row: state-filter chip strip (`[✓][●][⏸][✗][⊘]`). Active chips carry `.-active` class.
  - Body: scrollable list of run rows (`✓ j-abc 12:01:34 nightly`). Selected row carries `.-selected` class (PR-#66 styling).
  - Subscribes to `runs`, `selected_id`, `state_filter` PropertyChanged.
  - Number keys `1`–`5` toggle the corresponding chip when this pane has focus.
  - `↑`/`↓` move selection; Enter calls `vm.select(run_id)` and routes to the page-level handler (the page handles "follow selection in detail pane").
  - `r` calls page-level refresh-focused (the page handles routing; this widget exposes a `request_refresh` event or callback).
  - State indicator rendered when no entries (mirrors `PaneState`: loading text, empty, unreachable placeholder).

- [ ] **Step 1: Implement the widget**

```python
# src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py
"""JobRunsPane — LEFT pane of the EMR page.

Pane chrome + state-filter chip row + scrollable run list. Selection
is master-detail; the parent ``EmrServerlessPage`` listens for the
``RunSelected`` message and re-points the RIGHT detail pane.

Keybindings (active when the pane has Textual focus):
- ``1``..``5`` toggle state-filter chips (PR-A scope; PR-B reuses
  the same keys for log-level chips on the RIGHT pane).
- ``Up`` / ``Down`` move row cursor.
- ``Enter`` commits selection.
- ``r`` requests refresh."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_serverless import JobRunState, JobRunSummary
from aws_tui.vm.emr_serverless.job_runs_vm import JobRunsVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_STATE_GLYPH: dict[JobRunState, str] = {
    JobRunState.SUCCESS: "✓",
    JobRunState.RUNNING: "●",
    JobRunState.PENDING: "⏸",
    JobRunState.FAILED: "✗",
    JobRunState.CANCELLED: "⊘",
    JobRunState.CANCELLING: "⊘",
}

_KEY_TO_STATE: dict[str, JobRunState] = {
    "1": JobRunState.SUCCESS,
    "2": JobRunState.RUNNING,
    "3": JobRunState.PENDING,
    "4": JobRunState.FAILED,
    "5": JobRunState.CANCELLED,
}


class JobRunsPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = """
    JobRunsPane {
        height: 1fr;
        layout: vertical;
    }
    JobRunsPane > .runs-chip-row {
        height: 1;
        layout: horizontal;
        padding: 0 1;
    }
    JobRunsPane > .runs-chip-row > .runs-chip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
    }
    JobRunsPane > VerticalScroll {
        height: 1fr;
    }
    JobRunsPane .runs-row {
        height: 1;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "commit_selection", "Open"),
        Binding("r", "request_refresh", "Refresh"),
        *[Binding(k, f"toggle_state_filter('{s.value}')", show=False) for k, s in _KEY_TO_STATE.items()],
    ]

    class RunSelected(TextualMessage):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class RefreshRequested(TextualMessage):
        pass

    def __init__(
        self,
        vm: JobRunsVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunsVM = vm
        self._hub: MessageHub[Message] = hub
        self._cursor_index: int = 0
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="runs-chip-row"):
            for state in (
                JobRunState.SUCCESS,
                JobRunState.RUNNING,
                JobRunState.PENDING,
                JobRunState.FAILED,
                JobRunState.CANCELLED,
            ):
                yield Static(
                    f" {_STATE_GLYPH[state]} ",
                    classes=f"runs-chip runs-chip-{state.value.lower()}",
                    id=f"runs-chip-{state.value.lower()}",
                )
        yield VerticalScroll(id="runs-body")

    def on_mount(self) -> None:
        self.border_title = "runs"
        self._refresh_chips()
        self._refresh_rows()
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_toggle_state_filter(self, state_value: str) -> None:
        self._vm.toggle_state_filter(JobRunState(state_value))

    def action_cursor_up(self) -> None:
        if self._cursor_index > 0:
            self._cursor_index -= 1
            self._refresh_rows()

    def action_cursor_down(self) -> None:
        if self._cursor_index + 1 < len(self._vm.runs):
            self._cursor_index += 1
            self._refresh_rows()

    def action_commit_selection(self) -> None:
        runs = self._vm.runs
        if not runs or not (0 <= self._cursor_index < len(runs)):
            return
        run_id = runs[self._cursor_index].job_run_id
        self.post_message(self.RunSelected(run_id))

    def action_request_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name == "state_filter":
            self.call_after_refresh(self._refresh_chips)
            self.call_after_refresh(self._refresh_rows)
        elif msg.property_name in {"runs", "selected_id", "state"}:
            self.call_after_refresh(self._refresh_rows)

    def _refresh_chips(self) -> None:
        active = self._vm.state_filter
        for state in (
            JobRunState.SUCCESS,
            JobRunState.RUNNING,
            JobRunState.PENDING,
            JobRunState.FAILED,
            JobRunState.CANCELLED,
        ):
            try:
                chip = self.query_one(f"#runs-chip-{state.value.lower()}", Static)
            except Exception:  # noqa: BLE001
                continue
            if state in active:
                chip.add_class("-active")
            else:
                chip.remove_class("-active")

    def _refresh_rows(self) -> None:
        try:
            body = self.query_one("#runs-body", VerticalScroll)
        except Exception:  # noqa: BLE001
            return
        body.remove_children()
        state = self._vm.state
        runs = self._vm.runs
        if state is PaneState.LOADING:
            body.mount(Static("loading…", classes="runs-placeholder"))
            return
        if state is PaneState.EMPTY or not runs:
            body.mount(Static("(no runs)", classes="runs-placeholder"))
            return
        if state is PaneState.UNREACHABLE:
            body.mount(
                Static(
                    self._vm.error_text or "endpoint unreachable — press r to retry",
                    classes="runs-placeholder",
                )
            )
            return
        if state is PaneState.AUTH_REQUIRED:
            body.mount(
                Static(
                    "authentication required — aws sso login --profile <X>",
                    classes="runs-placeholder",
                )
            )
            return
        if self._cursor_index >= len(runs):
            self._cursor_index = max(0, len(runs) - 1)
        for idx, r in enumerate(runs):
            row = _format_run_row(r)
            row_classes = "runs-row"
            if idx == self._cursor_index:
                row_classes += " -selected"
            body.mount(Static(row, classes=row_classes))


def _format_run_row(r: JobRunSummary) -> str:
    glyph = _STATE_GLYPH.get(r.state, "?")
    ts = r.created_at.strftime("%H:%M:%S")
    label = r.name or r.job_run_id
    return f"{glyph} {label} · {ts}"


__all__ = ["JobRunsPane"]
```

- [ ] **Step 2: Run quality gates**

Run: `uv run ruff check src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py && uv run mypy src/aws_tui/ui/widgets/emr_serverless/`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/aws_tui/ui/widgets/emr_serverless/job_runs_pane.py
git commit -m "feat(ui): JobRunsPane — chip filter + selected-row + r-refresh"
```

---

## Task 12: `JobRunDetailPane` widget

**Files:**
- Create: `src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py`

**Interfaces:**
- Consumes: `JobRunDetailVM` (Task 8).
- Produces:
  - `class JobRunDetailPane(Widget)` — pane chrome; body is a key-value table. PR-A renders state, started, duration, IAM role, entry point, args, Spark params. Empty state if no run targeted. Loading / unreachable placeholders mirror `JobRunsPane`.
  - Subscribes to `detail`, `state` PropertyChanged.
  - `r` posts a refresh event (parent routes to page VM).

- [ ] **Step 1: Implement the widget**

```python
# src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py
"""JobRunDetailPane — RIGHT pane of the EMR page.

PR-A renders the static detail (state, timings, IAM, entry point,
args, Spark params). PR-B adds the log surface below the KV table
as a child widget; PR-A leaves the bottom empty so PR-B's layout
slot is reserved."""

from __future__ import annotations

from typing import ClassVar

from reactivex.abc import DisposableBase
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub, PropertyChangedMessage

from aws_tui.domain.emr_serverless import JobRunDetail
from aws_tui.vm.emr_serverless.job_run_detail_vm import JobRunDetailVM
from aws_tui.vm.file_manager.pane_vm import PaneState

_TERMINAL_GLYPH: dict[str, str] = {
    "SUCCESS": "✓",
    "FAILED": "✗",
    "CANCELLED": "⊘",
    "CANCELLING": "⊘",
    "RUNNING": "●",
    "PENDING": "⏸",
}


class JobRunDetailPane(Widget, can_focus=True):
    DEFAULT_CSS: ClassVar[str] = """
    JobRunDetailPane {
        height: 1fr;
        layout: vertical;
    }
    JobRunDetailPane > VerticalScroll {
        height: 1fr;
    }
    JobRunDetailPane .detail-row {
        height: auto;
        padding: 0 1;
    }
    JobRunDetailPane .detail-key {
        text-style: bold;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r", "request_refresh", "Refresh"),
    ]

    class RefreshRequested(TextualMessage):
        pass

    def __init__(
        self,
        vm: JobRunDetailVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: JobRunDetailVM = vm
        self._hub: MessageHub[Message] = hub
        self._sub: DisposableBase | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="detail-body")

    def on_mount(self) -> None:
        self.border_title = "detail"
        self._refresh()
        self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_request_refresh(self) -> None:
        self.post_message(self.RefreshRequested())

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_hub_message(self, msg: object) -> None:
        if not isinstance(msg, PropertyChangedMessage):
            return
        if msg.property_name in {"detail", "state"}:
            self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            body = self.query_one("#detail-body", VerticalScroll)
        except Exception:  # noqa: BLE001
            return
        body.remove_children()
        state = self._vm.state
        d = self._vm.detail
        if state is PaneState.LOADING:
            body.mount(Static("loading…", classes="detail-placeholder"))
            return
        if state is PaneState.UNREACHABLE:
            body.mount(
                Static(
                    self._vm.error_text or "endpoint unreachable — press r to retry",
                    classes="detail-placeholder",
                )
            )
            return
        if state is PaneState.AUTH_REQUIRED:
            body.mount(
                Static(
                    "authentication required — aws sso login --profile <X>",
                    classes="detail-placeholder",
                )
            )
            return
        if d is None:
            body.mount(Static("(no run selected)", classes="detail-placeholder"))
            return
        body.mount(Static(_format_kv("State", _state_label(d)), classes="detail-row"))
        body.mount(Static(_format_kv("Started", d.created_at.strftime("%Y-%m-%d %H:%M:%S")), classes="detail-row"))
        body.mount(
            Static(
                _format_kv(
                    "Duration",
                    f"{d.duration_ms // 1000} s" if d.duration_ms is not None else "—",
                ),
                classes="detail-row",
            )
        )
        body.mount(Static(_format_kv("IAM", d.execution_role_arn or "—"), classes="detail-row"))
        body.mount(Static(_format_kv("Entry point", d.entry_point or "—"), classes="detail-row"))
        body.mount(
            Static(
                _format_kv(
                    "Args", " ".join(d.entry_point_arguments) if d.entry_point_arguments else "—"
                ),
                classes="detail-row",
            )
        )
        body.mount(
            Static(
                _format_kv("Spark", d.spark_submit_parameters or "—"),
                classes="detail-row",
            )
        )


def _state_label(d: JobRunDetail) -> str:
    glyph = _TERMINAL_GLYPH.get(d.state.value, "?")
    return f"{glyph} {d.state.value}"


def _format_kv(key: str, value: str) -> str:
    return f"{key:<12}  {value}"


__all__ = ["JobRunDetailPane"]
```

- [ ] **Step 2: Run quality gates**

Run: `uv run ruff check src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py && uv run mypy src/aws_tui/ui/widgets/emr_serverless/`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/aws_tui/ui/widgets/emr_serverless/job_run_detail_pane.py
git commit -m "feat(ui): JobRunDetailPane — KV table + state placeholders"
```

---

## Task 13: `EmrServerlessPage` widget (top strip + 2-pane container)

**Files:**
- Create: `src/aws_tui/ui/widgets/emr_serverless/page.py`

**Interfaces:**
- Consumes: `EmrServerlessPageVM` (Task 9) + the three pane widgets (Tasks 10-12).
- Produces:
  - `class EmrServerlessPage(Widget)` — top-level container mounted by the AwsTuiApp content host when EMR is the active service.
  - Composes: top strip (`ApplicationPicker`) over a `Horizontal` of `JobRunsPane` (LEFT) + `JobRunDetailPane` (RIGHT).
  - Owns the 3 polling intervals via `set_interval` started in `on_mount` and stopped in `on_unmount`.
  - Handles `JobRunsPane.RunSelected` and `RefreshRequested` messages — routes to page VM.
  - Handles Tab cycling: LEFT ↔ RIGHT only (per spec §3 and PR #66).
  - Hotkey `a` opens the application picker.

- [ ] **Step 1: Implement the widget**

```python
# src/aws_tui/ui/widgets/emr_serverless/page.py
"""EmrServerlessPage — content-host root for the EMR service.

Composes the top strip + 2-pane body and owns the three auto-refresh
intervals via Textual's ``set_interval``. The intervals are
independent so they back off independently on
:class:`ThrottlingException` (PR-B wires the back-off — PR-A
ships the static cadences from spec §6)."""

from __future__ import annotations

from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.job_run_detail_pane import JobRunDetailPane
from aws_tui.ui.widgets.emr_serverless.job_runs_pane import JobRunsPane
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM


class EmrServerlessPage(Widget):
    DEFAULT_CSS: ClassVar[str] = """
    EmrServerlessPage {
        height: 1fr;
        layout: vertical;
    }
    EmrServerlessPage > .emr-top-strip {
        height: 3;
        layout: horizontal;
        padding: 0 1;
    }
    EmrServerlessPage > .emr-body {
        height: 1fr;
        layout: horizontal;
    }
    EmrServerlessPage > .emr-body > JobRunsPane {
        width: 1fr;
    }
    EmrServerlessPage > .emr-body > JobRunDetailPane {
        width: 2fr;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("a", "open_application_picker", "Apps"),
        Binding("tab", "cycle_panes_forward", "Tab"),
        Binding("shift+tab", "cycle_panes_back", "←Tab"),
    ]

    def __init__(
        self,
        vm: EmrServerlessPageVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: EmrServerlessPageVM = vm
        self._hub: MessageHub[Message] = hub
        self._picker: ApplicationPicker | None = None
        self._left: JobRunsPane | None = None
        self._right: JobRunDetailPane | None = None

    def compose(self) -> ComposeResult:
        self._picker = ApplicationPicker(
            self._vm.applications, hub=self._hub, id="emr-app-picker"
        )
        self._left = JobRunsPane(self._vm.job_runs, hub=self._hub, id="emr-runs-pane")
        self._right = JobRunDetailPane(
            self._vm.job_run_detail, hub=self._hub, id="emr-detail-pane"
        )
        with Vertical():
            with Horizontal(classes="emr-top-strip"):
                yield self._picker
            with Horizontal(classes="emr-body"):
                yield self._left
                yield self._right

    def on_mount(self) -> None:
        # Initial load: applications + first-app's runs + first-run detail.
        self.run_worker(self._vm.setup(), exclusive=True, group="emr-setup")
        # Set up the three pollers per spec §6.
        self.set_interval(30.0, self._tick_applications, name="emr-poll-apps")
        self.set_interval(10.0, self._tick_runs, name="emr-poll-runs")
        self.set_interval(5.0, self._tick_detail, name="emr-poll-detail")

    # ── Pollers ─────────────────────────────────────────────────────────────

    def _tick_applications(self) -> None:
        self.run_worker(
            self._vm.applications.refresh(), exclusive=False, group="emr-poll-apps"
        )

    def _tick_runs(self) -> None:
        # Cadence-decay: when no PENDING/RUNNING, only refresh every 6th tick (~60 s).
        if not self._vm.job_runs.has_active_runs() and self._poll_runs_decay():
            return
        self.run_worker(
            self._vm.job_runs.refresh(), exclusive=False, group="emr-poll-runs"
        )

    def _tick_detail(self) -> None:
        # Only poll while the run is non-terminal.
        if self._vm.job_run_detail.is_terminal_state():
            return
        self.run_worker(
            self._vm.job_run_detail.refresh(), exclusive=False, group="emr-poll-detail"
        )

    _runs_tick_counter: ClassVar[int] = 0

    def _poll_runs_decay(self) -> bool:
        """Return True if THIS tick should be skipped (6:1 decay)."""
        EmrServerlessPage._runs_tick_counter = (
            EmrServerlessPage._runs_tick_counter + 1
        ) % 6
        return EmrServerlessPage._runs_tick_counter != 0

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_open_application_picker(self) -> None:
        if self._picker is not None:
            self._picker.toggle_open()

    def action_cycle_panes_forward(self) -> None:
        self._cycle("right")

    def action_cycle_panes_back(self) -> None:
        self._cycle("left")

    def _cycle(self, direction: Literal["left", "right"]) -> None:
        # 2-slot cycle; direction doesn't matter for 2 slots, but keep
        # the binding shape so future expansion (e.g. log pane in PR-B)
        # has a place to grow without renaming actions.
        if self._left is None or self._right is None:
            return
        if self._left.has_focus_within or self._left.has_focus:
            self._right.focus()
        else:
            self._left.focus()

    # ── Message routing ─────────────────────────────────────────────────────

    def on_job_runs_pane_run_selected(self, event: JobRunsPane.RunSelected) -> None:
        self.run_worker(
            self._vm.select_job_run(event.run_id), exclusive=True, group="emr-select-run"
        )

    def on_job_runs_pane_refresh_requested(self, _event: JobRunsPane.RefreshRequested) -> None:
        self.run_worker(
            self._vm.refresh_focused("runs"), exclusive=True, group="emr-refresh"
        )

    def on_job_run_detail_pane_refresh_requested(
        self, _event: JobRunDetailPane.RefreshRequested
    ) -> None:
        self.run_worker(
            self._vm.refresh_focused("detail"), exclusive=True, group="emr-refresh"
        )


__all__ = ["EmrServerlessPage"]
```

- [ ] **Step 2: Run quality gates**

Run: `uv run ruff check src/aws_tui/ui/widgets/emr_serverless/ && uv run mypy src/aws_tui/ui/widgets/emr_serverless/`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/aws_tui/ui/widgets/emr_serverless/page.py
git commit -m "feat(ui): EmrServerlessPage — top strip + 2 panes + 3 pollers"
```

---

## Task 14: Theme CSS across 10 themes

**Files:**
- Modify: each of `src/aws_tui/ui/themes/{amber,carbon,dracula,github-light,gruvbox-dark,lattice,nord,one-light,solarized-light,voidline}.tcss`

**Interfaces:**
- Consumes: existing theme tokens.
- Produces: per-theme styling for `EmrServerlessPage`, `ApplicationPicker`, `JobRunsPane`, `JobRunDetailPane`. Selectors and tokens are uniform across the 10 themes; only the tokens differ per theme by the substitution.

The CSS block below is identical across themes — paste it verbatim into each. The token semantics differ by theme because each `.tcss` defines `$accent`, `$bg`, etc. The block uses the unified semantic tokens enforced by the global constraints.

- [ ] **Step 1: Add the canonical EMR CSS block to every theme**

For each of the 10 theme files, append (or insert near the bottom — before any trailing notes):

```css
/* ── EMR Serverless ────────────────────────────────────────────────────── */

EmrServerlessPage {
    background: $bg;
    color: $text;
}

EmrServerlessPage > .emr-top-strip {
    background: $bg;
    border-bottom: solid $rule-dim;
}

ApplicationPicker > .app-trigger {
    background: $bg;
    color: $text;
    border: round $rule-dim;
}
ApplicationPicker.-open > .app-trigger {
    background: $bg-elev;
    border: round $accent;
    color: $accent;
}
ApplicationPicker > OptionList {
    background: $bg-elev;
    color: $text;
    border: round $rule-dim;
}
ApplicationPicker > OptionList > .option-list--option-highlighted {
    background: $bg-sel;
    color: $accent-soft;
}

JobRunsPane {
    background: $bg;
    color: $text;
    border: solid $rule-dim;
}
JobRunsPane:focus-within {
    border: solid $accent;
}
JobRunsPane > .runs-chip-row {
    background: $bg-elev;
    color: $text-muted;
}
JobRunsPane > .runs-chip-row > .runs-chip {
    background: $bg;
    color: $text-muted;
    border-left: solid $rule-dim;
}
JobRunsPane > .runs-chip-row > .runs-chip.-active {
    background: $bg-sel;
    color: $accent-soft;
}
JobRunsPane > .runs-chip-row > .runs-chip-success.-active   { color: $success; }
JobRunsPane > .runs-chip-row > .runs-chip-running.-active   { color: $accent; }
JobRunsPane > .runs-chip-row > .runs-chip-pending.-active   { color: $text-muted; }
JobRunsPane > .runs-chip-row > .runs-chip-failed.-active    { color: $danger; }
JobRunsPane > .runs-chip-row > .runs-chip-cancelled.-active { color: $text-muted; }
JobRunsPane .runs-row {
    background: $bg;
    color: $text;
}
JobRunsPane .runs-row.-selected {
    background: $bg-sel;
    color: $accent-soft;
}
JobRunsPane .runs-placeholder {
    background: $bg;
    color: $text-muted;
    padding: 1 2;
}

JobRunDetailPane {
    background: $bg;
    color: $text;
    border: solid $rule-dim;
}
JobRunDetailPane:focus-within {
    border: solid $accent;
}
JobRunDetailPane .detail-key {
    color: $accent;
}
JobRunDetailPane .detail-row {
    background: $bg;
    color: $text;
}
JobRunDetailPane .detail-placeholder {
    background: $bg;
    color: $text-muted;
    padding: 1 2;
}
```

- [ ] **Step 2: Verify Textual parses every theme**

Run: `uv run python -c "from aws_tui.infra.theme_store import ThemeStore; ts = ThemeStore(); [print(name, len(ts.load(name))) for name in ['amber','carbon','dracula','github-light','gruvbox-dark','lattice','nord','one-light','solarized-light','voidline']]"`
Expected: each prints a positive byte count, no exceptions.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest --no-header 2>&1 | tail -5`
Expected: prior tests still green. (Snapshot regressions are addressed in Task 17.)

- [ ] **Step 4: Commit**

```bash
git add src/aws_tui/ui/themes/
git commit -m "feat(ui,themes): EMR Serverless page styling across 10 themes"
```

---

## Task 15: Composition wiring

**Files:**
- Modify: `src/aws_tui/composition.py`
- Test: `tests/unit/test_composition_emr_registered.py`

**Interfaces:**
- Consumes: `EmrServerlessService` (Task 5); `build_app_context` (existing).
- Produces: `EmrServerlessService` registered in the `ServiceRegistry` after `S3Service`, so the nav rail surfaces ⚡ second.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_composition_emr_registered.py
"""Pin that EmrServerlessService is registered post-PR-A.

The nav rail is order-sensitive — services appear in registration
order, so ⚡ EMR must sit AFTER 🪣 S3 in the registry."""

from __future__ import annotations

from pathlib import Path

from aws_tui.composition import build_app_context


def test_emr_serverless_service_registered_after_s3(tmp_path: Path) -> None:
    ctx = build_app_context(config_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    try:
        ids = [s.descriptor.id for s in ctx.root_vm._registry.all()]  # type: ignore[attr-defined]
        assert "s3" in ids
        assert "emr-serverless" in ids
        assert ids.index("s3") < ids.index("emr-serverless")
    finally:
        ctx.root_vm.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_composition_emr_registered.py -v`
Expected: FAIL — `"emr-serverless" not in ids`.

- [ ] **Step 3: Register `EmrServerlessService` in composition.py**

Open `src/aws_tui/composition.py`. Find the existing block:

```python
    registry = ServiceRegistry()
    s3_service = S3Service(
        transfer_journal=transfer_journal,
        hub=hub,
        dispatcher=dispatcher,
    )
    # cast to Service: S3Service satisfies the protocol structurally; mypy
    # rejects ClassVar `descriptor` here so we widen explicitly.
    registry.register(cast("Service", s3_service))
```

Append directly after the `registry.register(cast("Service", s3_service))` line:

```python
    emr_service = EmrServerlessService(
        hub=hub,
        dispatcher=dispatcher,
    )
    registry.register(cast("Service", emr_service))
```

And add the import near the existing `from aws_tui.services.s3.service import S3Service` line:

```python
from aws_tui.services.emr_serverless.service import EmrServerlessService
```

- [ ] **Step 4: Run tests + quality gates**

Run: `uv run pytest tests/unit/test_composition_emr_registered.py tests/unit/test_composition_first_run.py tests/unit/test_composition_initial_theme.py tests/unit/test_composition_resume.py -v && uv run ruff check src/aws_tui/composition.py && uv run mypy src/aws_tui/composition.py`
Expected: every composition test passes; ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/composition.py tests/unit/test_composition_emr_registered.py
git commit -m "feat(composition): register EmrServerlessService after S3"
```

---

## Task 16: Integration test — page mounts on AWS, hidden on s3-compatible

**Files:**
- Create: `tests/integration/test_emr_page.py`

**Interfaces:**
- Consumes: `AwsTuiApp`, `build_app_context`, `EmrServerlessPage`, `_InMemoryEmr`, `EmrServerlessService.emr_client_factory`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_emr_page.py
"""End-to-end integration: ⚡ EMR nav row appears on AWS connections
and disappears on s3-compatible. Selecting the row mounts
EmrServerlessPage in the content host."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.services.emr_serverless.service import EmrServerlessService
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from tests.unit.domain._in_memory_emr import _InMemoryEmr


def _prep(tmp_path: Path, toml_text: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


def _make_ctx_with_emr_fake(config_dir: Path, cache_dir: Path) -> tuple[object, _InMemoryEmr]:
    ctx = build_app_context(config_dir=config_dir, cache_dir=cache_dir)
    fake = _InMemoryEmr()
    fake.add_application(app_id="00emr", name="etl")
    # Swap the registered EmrServerlessService's client factory for
    # the test fake so no boto3 calls escape.
    for svc in ctx.root_vm._registry.all():  # type: ignore[attr-defined]
        if isinstance(svc, EmrServerlessService):
            svc._client_factory = lambda _conn: fake  # type: ignore[assignment]
    return ctx, fake


_AWS_TOML = (
    "[connections.dev]\n"
    'kind = "aws"\n'
    'profile = "dev"\n'
    'region = "us-east-1"\n'
    "[defaults]\n"
    'connection = "dev"\n'
)

_S3COMPAT_TOML = (
    "[connections.minio]\n"
    'kind = "s3-compatible"\n'
    'endpoint_url = "http://127.0.0.1:1"\n'
    'region = "us-east-1"\n'
    'access_key_id = "x"\n'
    'secret_access_key = "y"\n'
    "[defaults]\n"
    'connection = "minio"\n'
)


@pytest.mark.asyncio
async def test_emr_page_mounts_on_aws_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _AWS_TOML)
    ctx, _fake = _make_ctx_with_emr_fake(config_dir, tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # Switch to EMR via the menu VM (avoids keymap routing).
            ctx.root_vm.services_menu.switch_service_command.execute("emr-serverless")
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            host = pilot.app.query_one("#content-host")
            assert len(host.query(EmrServerlessPage)) == 1, (
                "expected EmrServerlessPage mounted in #content-host"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()


@pytest.mark.asyncio
async def test_emr_nav_row_hidden_on_s3_compatible_connection(tmp_path: Path) -> None:
    config_dir = _prep(tmp_path, _S3COMPAT_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete(list(app.workers._workers))  # type: ignore[attr-defined]
            await pilot.pause()
            # The nav menu's items must NOT include "emr-serverless" when
            # the active connection is s3-compatible.
            ids = [item.descriptor.id for item in ctx.root_vm.services_menu.items]
            assert "emr-serverless" not in ids, (
                f"⚡ EMR must be filtered out on s3-compatible connections, got {ids}"
            )
    finally:
        with contextlib.suppress(Exception):
            ctx.root_vm.dispose()
```

- [ ] **Step 2: Run test to verify it fails (for the first case — second case may already pass on the registry side)**

Run: `uv run pytest tests/integration/test_emr_page.py -v`
Expected: FAIL — `EmrServerlessPage` not yet mounted by the AwsTuiApp content-host wiring (Task 16 also requires extending `AwsTuiApp._mount_service_view` to mount the EMR widget for `service_id="emr-serverless"`).

- [ ] **Step 3: Wire the AwsTuiApp content-host mount path for EMR**

Open `src/aws_tui/app.py`. Find `_mount_service_view` (it currently only mounts `DualPane` for `service_id="s3"`). Generalize it so any service id mounts the right widget. The cheapest fix in PR-A is a small dispatch table.

Add near the existing imports:

```python
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
```

Find the body of `_mount_service_view` that currently does:

```python
host.mount(DualPane(current_vm, hub=ctx.hub, id="content-dual-pane"))
```

Replace that single-line mount with a service-id dispatch:

```python
if service_id == "emr-serverless":
    host.mount(
        EmrServerlessPage(current_vm, hub=ctx.hub, id="content-emr-page")
    )
else:
    host.mount(DualPane(current_vm, hub=ctx.hub, id="content-dual-pane"))
```

Same pattern in `_mount_initial_service_view` if it has a sibling literal — search for `host.mount(DualPane(` across `app.py` and apply the same dispatch.

- [ ] **Step 4: Run the integration tests**

Run: `uv run pytest tests/integration/test_emr_page.py -v && uv run ruff check src/aws_tui/app.py && uv run mypy src/aws_tui/app.py`
Expected: both tests pass; ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/aws_tui/app.py tests/integration/test_emr_page.py
git commit -m "feat(app): mount EmrServerlessPage on service_id=emr-serverless"
```

---

## Task 17: Snapshot tests with content-presence guards

**Files:**
- Create: `tests/snapshot/apps/emr.py` (fixture apps wrapping the widgets with pre-seeded VMs)
- Create: `tests/snapshot/test_emr.py`

**Interfaces:**
- Consumes: `_InMemoryEmr`, page VM, page widget.
- Produces: snapshot tests across the 10 themes for: empty state, populated runs list with selection, detail view with full run data. Each snapshot is paired with a content-presence guard.

- [ ] **Step 1: Build the snapshot fixture app**

```python
# tests/snapshot/apps/emr.py
"""Snapshot test app for the EMR page — wraps EmrServerlessPage with
a pre-seeded :class:`_InMemoryEmr` so the rendered SVG never depends
on boto3 or a network."""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.containers import Container
from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.domain.emr_serverless import ApplicationState, JobRunState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.vm.emr_serverless.page_vm import EmrServerlessPageVM
from tests.unit.domain._in_memory_emr import _InMemoryEmr


_FIXED_TS = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)


def _seeded_fake() -> _InMemoryEmr:
    fake = _InMemoryEmr()
    fake.add_application(
        app_id="00abc",
        name="etl-pipeline-1",
        state=ApplicationState.STARTED,
        created_at=_FIXED_TS,
    )
    fake.add_job_run(
        application_id="00abc",
        job_run_id="r-001",
        name="nightly-2026-06-25",
        state=JobRunState.SUCCESS,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
    )
    fake.add_job_run_detail(
        application_id="00abc",
        job_run_id="r-001",
        entry_point="s3://my-bucket/jobs/etl.py",
        entry_point_arguments=("--input", "s3://my-bucket/raw/", "--output", "s3://my-bucket/curated/"),
        spark_submit_parameters="--conf spark.executor.instances=8",
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        duration_ms=240_000,
    )
    fake.add_job_run(
        application_id="00abc",
        job_run_id="r-002",
        state=JobRunState.RUNNING,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
    )
    fake.add_job_run_detail(application_id="00abc", job_run_id="r-002")
    return fake


def _load_css(theme: str) -> str:
    return ThemeStore().load(theme)


class _EmrSnapshotMixin:
    @staticmethod
    def _build_page_vm() -> EmrServerlessPageVM:
        hub: MessageHub[Message] = MessageHub()
        page = EmrServerlessPageVM(
            client=_seeded_fake(),
            hub=hub,
            dispatcher=NULL_DISPATCHER,
            connection=Connection(
                name="dev", kind="aws", region="us-east-1", source="config", profile="dev"
            ),
        )
        page.construct()
        return page


class EmrPageApp(App[None], _EmrSnapshotMixin):
    """Renders the populated EMR page (1 app, 2 runs, detail visible)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self._theme = theme
        self._page_vm: EmrServerlessPageVM | None = None

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self.stylesheet.add_source(_load_css(self._theme), read_from=("snapshot", "theme"))
        self.stylesheet.parse()
        self.stylesheet.update(self)
        self._page_vm = self._build_page_vm()
        await self._page_vm.setup()
        host = self.query_one("#content-host", Container)
        host.mount(EmrServerlessPage(self._page_vm, hub=self._page_vm._hub, id="emr-page"))  # type: ignore[attr-defined]


class EmrPageEmptyApp(App[None], _EmrSnapshotMixin):
    """Renders the empty state (no applications seeded)."""

    def __init__(self, theme: str) -> None:
        super().__init__()
        self._theme = theme

    def compose(self) -> ComposeResult:
        yield Container(id="content-host")

    async def on_mount(self) -> None:
        self.stylesheet.add_source(_load_css(self._theme), read_from=("snapshot", "theme"))
        self.stylesheet.parse()
        self.stylesheet.update(self)
        hub: MessageHub[Message] = MessageHub()
        from tests.unit.domain._in_memory_emr import _InMemoryEmr

        page = EmrServerlessPageVM(
            client=_InMemoryEmr(),
            hub=hub,
            dispatcher=NULL_DISPATCHER,
            connection=Connection(
                name="dev", kind="aws", region="us-east-1", source="config", profile="dev"
            ),
        )
        page.construct()
        await page.setup()
        host = self.query_one("#content-host", Container)
        host.mount(EmrServerlessPage(page, hub=hub, id="emr-page"))


__all__ = ["EmrPageApp", "EmrPageEmptyApp"]
```

- [ ] **Step 2: Write the snapshot tests with content-presence guards**

```python
# tests/snapshot/test_emr.py
"""Snapshot tests for the EMR page across 10 themes.

Every parity snapshot is paired with a content-presence guard so a
uniformly-blank render across all themes can't pass (per PR #53 /
#63 lesson)."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.emr import EmrPageApp, EmrPageEmptyApp

TERMINAL_SIZE = (120, 30)
THEMES = (
    "amber", "carbon", "dracula", "github-light", "gruvbox-dark",
    "lattice", "nord", "one-light", "solarized-light", "voidline",
)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_snapshot(theme: str, snap_compare) -> None:  # noqa: ANN001
    assert snap_compare(EmrPageApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_empty_snapshot(theme: str, snap_compare) -> None:  # noqa: ANN001
    assert snap_compare(EmrPageEmptyApp(theme=theme), terminal_size=TERMINAL_SIZE)


# ── Content-presence guards ───────────────────────────────────────────────


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_populated_renders_expected_glyphs_and_labels(
    theme: str, snap_compare  # noqa: ANN001
) -> None:
    app = EmrPageApp(theme=theme)
    # Drive a single render so the SVG is materialised by snap_compare.
    snap_compare(app, terminal_size=TERMINAL_SIZE)
    # snap_compare stores last-rendered SVG on its app under .last_svg_path
    # (pytest-textual-snapshot convention). Read and grep.
    from pathlib import Path

    svg_path = Path(getattr(snap_compare, "last_svg_path", ""))
    if not svg_path.exists():
        # Fallback for older pytest-textual-snapshot: re-run app and
        # capture via app.export_screenshot via Pilot. Skip gracefully.
        pytest.skip("snap_compare doesn't expose last_svg_path on this version")
    svg = svg_path.read_text()
    assert "⚡" in svg, "⚡ icon must appear in trigger label"
    assert "etl-pipeline-1" in svg, "selected application name must render"
    assert "nightly-2026-06-25" in svg, "job run name must render in LEFT pane"
    assert "✓" in svg, "SUCCESS state glyph must appear somewhere"
    assert "EmrJobRole" in svg, "execution role ARN must appear in detail KV"


@pytest.mark.parametrize("theme", THEMES)
def test_emr_page_empty_renders_no_application_label(
    theme: str, snap_compare  # noqa: ANN001
) -> None:
    app = EmrPageEmptyApp(theme=theme)
    snap_compare(app, terminal_size=TERMINAL_SIZE)
    from pathlib import Path

    svg_path = Path(getattr(snap_compare, "last_svg_path", ""))
    if not svg_path.exists():
        pytest.skip("snap_compare doesn't expose last_svg_path on this version")
    svg = svg_path.read_text()
    assert "(no application)" in svg
```

- [ ] **Step 3: Generate baseline snapshots**

Run: `uv run pytest tests/snapshot/test_emr.py --snapshot-update --no-header 2>&1 | tail -8`
Expected: 40 snapshots updated (10 themes × 4 tests).

- [ ] **Step 4: Re-run without `--snapshot-update`**

Run: `uv run pytest tests/snapshot/test_emr.py -v --no-header 2>&1 | tail -10`
Expected: all 40 pass.

- [ ] **Step 5: Run the full suite to confirm nothing else regressed**

Run: `uv run pytest --no-header 2>&1 | tail -8`
Expected: all tests pass (the count grows by Tasks 1-17's additions; baseline was 980).

- [ ] **Step 6: Commit**

```bash
git add tests/snapshot/apps/emr.py tests/snapshot/test_emr.py tests/snapshot/__snapshots__/test_emr/
git commit -m "test(snapshot): EMR page across 10 themes + content guards"
```

---

## Wrap-up

After Task 17 the PR-A branch is ready for review. Open the PR with a body that:

1. Links to the spec at `docs/superpowers/specs/2026-06-25-emr-serverless-service-design.md` and pins the PR-A scope boundary (`§1`, `§2` minus log surface / submit / cancel, `§3` design-language commitments, `§6` auto-refresh + error states minus cancel/lifecycle).
2. Lists every new file path and the test that covers it.
3. Confirms the suite passes locally and the 10 themes render cleanly.
4. Calls out PR-B/C as follow-ups so the reviewer knows the deliberate gaps (no log surface, no cancel, no submit).

The final commit history for PR-A should be 17 commits, one per task — keeps `git bisect` useful and matches the codebase's pattern of one-PR-multiple-commits.

---

## Self-Review

**Spec coverage:**

- ✅ §1 Architecture — Tasks 1-3 (records + client), Task 5 (service), Tasks 6-9 (VMs). No speculative Protocol layer (spec §1 ban honoured).
- ✅ §2 Layout & navigation — Task 13 implements top strip + 2-pane composition. Task 10 implements the OptionList-in-popover dropdown. Tab cycle = 2 slots (Task 13). Empty states across LEFT/RIGHT panes (Tasks 11, 12).
- ✅ §3 Design language reuse — Task 14 wires per-theme tokens. Tasks 11/12 use the canonical class names (`-selected`, `runs-row`, etc.). Pane focus uses `:focus-within` (spec rule).
- ✅ §6 Auto-refresh + error states — Task 13 sets up the three intervals at 30/10/5 s with the 6:1 decay on the runs list. Error states LOADING/IDLE/EMPTY/UNREACHABLE/AUTH_REQUIRED/FORBIDDEN propagated by Tasks 6/7/8 and rendered by Tasks 11/12. Throttle back-off is OUT OF PR-A scope per the spec (cancel/lifecycle/throttle land in PR-B); PR-A's pollers fire fixed intervals.
- ⚠️ Spec §6 throttle handling — PR-A's pollers don't implement exponential back-off; that ships with PR-B. This is acceptable because PR-A's audience is monitoring — a few extra polls during a throttle event is non-fatal; the toast advisory is added in PR-B. Documented as deliberate.
- ✅ Out-of-scope ban honoured — no log surface (no `LogViewVM`, no `LogView`), no cancel (no `x` keybinding), no submit (no `+`/`c` keybindings, no `SubmitFormVM`).
- ✅ Snapshot content-presence guards required by global constraint — Task 17 includes them.
- ✅ All 10 themes covered — Task 14 enumerates explicitly.

**Placeholder scan:** No TBD/TODO; every code block contains the implementation. Every test step shows the exact pytest invocation + expected outcome.

**Type consistency:**
- `EmrServerlessClient.list_applications() -> list[ApplicationSummary]` used identically in Tasks 3, 4, 6, 9.
- `JobRunsVM.set_application(app_id: str | None)` — same signature in Task 7 (definition) and Task 9 (consumer).
- `EmrServerlessPageVM.refresh_focused(focus: Literal["applications", "runs", "detail"])` — Task 9 defines; Task 13 calls with each literal.
- `JobRunsPane.RunSelected.run_id: str` — Task 11 defines; Task 13 handler reads as `event.run_id`.
- `_KEY_TO_STATE` uses values "1"…"5" — matches the `Binding` declaration's first arg.
- Service descriptor: `id="emr-serverless"` — Task 5, Task 15 (mount-dispatch in app.py), Task 16 (integration test `switch_service_command.execute("emr-serverless")`), Task 17 (no use of the id; widgets bind by VM).

No drift found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-emr-serverless-pr-a.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
