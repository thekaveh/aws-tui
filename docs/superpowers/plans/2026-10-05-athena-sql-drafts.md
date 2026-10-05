# Athena SQL Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist opt-in local Athena SQL drafts with explicit, source-safe recovery, truthful save state, private storage, and a bounded draft-flush wait.

**Architecture:** One atomic JSON file per five-field context is coordinated by one application-owned runtime and an owned I/O worker. Short-lived query sessions capture edits; a separate recovery boundary validates the exact live source and Athena context before replacing text or allowing execution. Settings enables persistence, and the Athena Drafts manager appears only when enabled.

**Tech Stack:** Existing Python 3.11–3.13 support, asyncio, threading/concurrent.futures, dataclasses, JSON, Textual, VMx/reactivex, pytest and pytest-textual-snapshot; no new dependency.

## Global Constraints

- Preserve Python support `>=3.11,<3.14`; add no dependency or toolchain installation.
- Persist SQL text, UTC timestamps, a stable draft identifier, the five QueryContext fields, and schema version only; never persist result rows, query execution state, credentials, provider objects, or navigation snapshots.
- Keep AthenaQuerySnapshot and AthenaPageSnapshot fields, representations, and existing export/restore behavior unchanged; no draft read, write, or execution is triggered by snapshot restoration.
- Draft persistence defaults to off; missing configuration and demo mode perform no draft-directory creation, draft reads, or draft writes.
- Use `<config-dir>/athena-drafts/<id>.json`, schema version `1`, at most `50` owned record files, at most `8_388_608` total owned-record bytes, at most `262_144` SQL UTF-8 bytes, and at most `278_528` bytes per record.
- Use a `0.500` second edit debounce and a `2.000` second total draft-flush wait budget per application shutdown; these are constants, not new preferences.
- Existing Athena layouts and behavior remain unchanged when drafts are off; enabled UI must work at `80x24` and with Tab, Shift+Tab, Enter, and Escape.
- Domain code must not import infrastructure; infrastructure must not import domain, VM, services, or UI; preserve all existing layer checks.
- Never log, place in exception text, attach to a crash report, or expose through object repr any draft SQL or untrusted raw draft payload.
- Recovery never submits SQL, loads result rows, creates AWS named queries, or automatically chooses another connection or context.
- Use only applicable local checks on the existing Python 3.12.9 environment; do not claim Windows, multiple-runtime, native-clipboard, live-AWS, or hosted-Actions verification.

---

## Execution boundary and environment

Read the canonical [design](../specs/2026-10-05-athena-sql-drafts-design.md) before implementing any task. Its exact data types, fixed strings, failure outcomes and limit semantics are requirements, not optional examples. Implementers run each task's tests, self-review, commit through normal repository hooks, and report the commit hash. Root performs independent diff review and integration, owns merge/cleanup, issue closure and remote changes. Do not skip hooks, amend unrelated commits, or wait for root to make an implementation commit. The planning worker writes only this document, the design and its own report. Implementation workers receive only their assigned task plus this Global Constraints section and the design.

Use `/Users/kaveh/repos/aws-tui`, `/bin/bash`, `login:false`. Prepare each command environment without installing anything:

```bash
export PATH="/Users/kaveh/repos/aws-tui/.venv/bin:/opt/homebrew/bin:$PATH"
export TMPDIR=/private/tmp
export UV_NO_SYNC=1
export UV_OFFLINE=1
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib
```

Each task has a red/green cycle. Expected red means the new assertion/import fails for the intended missing behavior; infrastructure/import failures are not evidence of a valid red. Expected green means exit 0 and all selected tests pass. Do not run live AWS, native clipboard or hosted workflows. Do not remove unrelated files, run `git clean -fdx`, modify toolchains, or alter a test to bless missing rendered content. Use safe fake SQL in visual fixtures; sentinel privacy tests inspect generated diagnostics directly.

## File map and dependency order

| Task | Independently reviewable output | Depends on |
|---|---|---|
| 1 | Configuration plus bounded private synchronous store | Existing ConfigStore transaction |
| 2 | Owned worker plus deterministic sessions, races and bounded flush | Task 1 interfaces |
| 3 | Exact-context explicit recovery and execution guards | Task 2 session |
| 4 | App/service/staging/settings lifecycle wiring and normal/crash recovery | Tasks 1–3 |
| 5 | Keyboard-accessible UI, verified renders and user documentation | Tasks 2–4 |

No parallel edits to the same query/page/composition files. Task 1 must land its interface before Task 2; Task 3 must land before service wiring. Do not spawn a child agent from an implementation worker. Each task's tests must exercise its observable contract, not only the helpers it just implemented.

### Task 1: Preserve the opt-in setting and implement the private store

**Files:**

- Create: `src/aws_tui/infra/athena_draft_store.py`
- Modify: `src/aws_tui/infra/config_store.py` (`Config`, `_parse`, `_serialize`, four mutators and new setter)
- Create: `tests/athena_drafts_helpers.py` (pure store fixtures in Task 1; runtime helper added in Task 2)
- Test: `tests/unit/infra/test_athena_draft_store.py`
- Test: `tests/unit/infra/test_config_store.py`

**Interfaces:**

- Consumes `ConfigStore.path`, `.read_only`, `.load()`, `.transaction()`, `.set_athena_sql_drafts(enabled)` and `ensure_private_dir`.
- Produces exactly `DraftContext`, `DraftCode`, `SqlDraft`, `DraftStoreResult`, `DraftPermit`, `AthenaDraftStore` and `draft_id` from the design.
- Root task extraction must append the exact section headed `## Privacy evidence belongs to the store/recovery tasks` to this Task 1 brief. It is required Task 1 acceptance, not optional final work.
- The store is synchronous. All successful mutating results return the current valid record listing, effective `enabled`, and skipped count. `code is None` is success; every failure has a fixed code and no raw exception.

- [ ] **1.1 Write config red tests.** Add a default assertion, exact bool parse rejection, serialization round trip, and parameterized preservation tests invoking all four existing mutators and theme/keybinding transactions. Use a real temporary TOML file. Do not merely inspect a `Config` constructor.

```python
from dataclasses import replace

from aws_tui.infra.config_store import ConfigStore, ConnectionEntry


def test_sql_drafts_defaults_off_and_survives_connection_mutations(tmp_path):
    store = ConfigStore(path=tmp_path / "config.toml")
    assert store.load().athena_sql_drafts is False
    store.set_athena_sql_drafts(True)
    store.add_connection(ConnectionEntry(name="a", kind="aws", region="us-west-2"))
    assert store.load().athena_sql_drafts is True
    store.update_connection("a", ConnectionEntry(name="a", kind="aws", region="us-east-1"))
    assert store.load().athena_sql_drafts is True
    store.set_default_connection("a")
    assert store.load().athena_sql_drafts is True
    store.remove_connection("a")
    assert store.load().athena_sql_drafts is True
    store.set_athena_sql_drafts(False)
    assert store.load().athena_sql_drafts is False
    assert "sql_drafts" not in store.path.read_text()
```

Also parameterize `sql_drafts` with `"yes"`, `1`, an array and a table; require the exact fixed ConfigError without the input value. Add a read-only ConfigStore test proving setter does not write and runtime can detect read_only rather than infer success from the no-op.

- [ ] **1.2 Run red.**

```bash
uv run pytest tests/unit/infra/test_config_store.py -k sql_drafts -q
```

Expected: new assertions fail because Config lacks `athena_sql_drafts` or the setter.

- [ ] **1.3 Add the minimal preserving config implementation.** Add the defaulted bool after existing non-default Config fields. Use exact `type(value) is bool` checking. Add the following transformation; convert existing four Config reconstructions to targeted `replace` calls preserving all other fields.

```python
def set_athena_sql_drafts(self, enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ConfigError("[athena].sql_drafts must be a boolean")
    self._mutate(lambda cfg: replace(cfg, athena_sql_drafts=enabled))
```

Parse table type before key; add the bool to the final Config construction. `_serialize` emits `out["athena"] = {"sql_drafts": True}` only when enabled. Preserve load of absent config and existing keybindings/default behavior. Do not change read_only policy.

- [ ] **1.4 Write the real-store red tests.** Put `CTX`, `STAMP`, `record` and `store_at` in `tests/athena_drafts_helpers.py`. The new store test module imports them with `from tests.athena_drafts_helpers import CTX, record, store_at`; production modules never import this file.

```python
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from aws_tui.infra.athena_draft_store import (
    AthenaDraftStore, DraftPermit, SqlDraft, draft_id,
)
from aws_tui.infra.config_store import ConfigStore

CTX = ("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
STAMP = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)


def record(context=CTX, sql="SELECT 1"):
    return SqlDraft(draft_id(context), context, sql, STAMP, STAMP)


def store_at(tmp_path):
    config = ConfigStore(path=tmp_path / "config.toml")
    return AthenaDraftStore(config=config, directory=tmp_path / "athena-drafts")


def test_missing_setting_creates_no_draft_path(tmp_path):
    store = store_at(tmp_path)
    assert store.list().records == ()
    assert store.save(record(), permit=DraftPermit()).code == "disabled"
    assert not (tmp_path / "athena-drafts").exists()


def test_five_fields_round_trip(tmp_path):
    store = store_at(tmp_path)
    assert store.set_enabled(True).code is None
    expected = record()
    assert store.save(expected, permit=DraftPermit()).code is None
    reopened = store_at(tmp_path)
    assert reopened.list().records == (expected,)
    assert "SELECT 1" not in repr(reopened.list())


def test_separate_instances_preserve_overlapping_context_saves(tmp_path):
    first, second = store_at(tmp_path), store_at(tmp_path)
    assert first.set_enabled(True).code is None
    other = ("analytics", "us-east-1", "other", "federated", "events")
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(first.save, record(), permit=DraftPermit())
        b = executor.submit(second.save, record(other, "SELECT 2"), permit=DraftPermit())
        assert a.result(timeout=5).code is None
        assert b.result(timeout=5).code is None
    assert {row.context for row in first.list().records} == {CTX, other}
```

Add a barrier inside the test's serialization/write seam so both threads overlap before transaction acquisition; do not rely on incidental scheduler concurrency. Use separate ConfigStore instances targeting the same path. Cover 50-record limit, per-SQL bytes with multibyte UTF-8, total 8 MiB, 278,528 record bytes, and replacement subtraction. Preserve original bytes on failed writes. Place an unsupported-version file and a corrupt file beside a valid draft and assert the valid draft remains listed; assert corrupt/unknown files are still on disk and counted. POSIX test `stat.S_IMODE` for directory/record/temp modes. A fail-before-replace injection leaves the previous record readable and cleans the owned temp.

- [ ] **1.5 Run store red.**

```bash
uv run pytest tests/unit/infra/test_athena_draft_store.py -q
```

Expected: import failure naming the new module, followed by assertion failures as implementation progresses.

- [ ] **1.6 Implement the schema and safe primitive types.** The exact wire schema is in the design. The following is the canonical identity/encoding core; conversion accepts no domain classes.

```python
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import threading
from typing import Literal

DraftContext = tuple[str, str, str, str, str]
DraftCode = Literal["disabled", "read_only", "invalid", "unsupported", "limit", "io", "cancelled"]
SCHEMA_VERSION = 1
MAX_DRAFTS = 50
MAX_TOTAL_BYTES = 8_388_608
MAX_SQL_BYTES = 262_144
MAX_RECORD_BYTES = 278_528
CONTEXT_KEYS = ("connection_name", "region", "workgroup", "catalog", "database")


def draft_id(context: DraftContext) -> str:
    body = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(body).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_record(record: SqlDraft) -> bytes:
    body = {
        "schema_version": SCHEMA_VERSION,
        "id": record.id,
        "created_at": _timestamp(record.created_at),
        "updated_at": _timestamp(record.updated_at),
        "context": dict(zip(CONTEXT_KEYS, record.context, strict=True)),
        "sql": record.sql,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class DraftPermit:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()
```

Declare `SqlDraft` and `DraftStoreResult` exactly as the design before this core. Parsing must reject duplicate keys using `object_pairs_hook`, reject JSON constants, validate types without implicit string conversion, validate all keys/bytes/timestamps/ID, and return `invalid`/`unsupported` codes. Catch raw decode/Unicode/filesystem errors inside a helper that returns codes; never return the caught object. A malformed record is a skipped record, not a poison pill for the whole list.

Use this complete decoder; all error-return paths discard the caught exception before returning. The exact-key test protects the storage scope as well as validity.

```python
def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Invalid draft record")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError("Invalid draft record")


def _decode_record(payload: bytes, expected_id: str) -> tuple[SqlDraft | None, DraftCode | None]:
    try:
        if len(payload) > MAX_RECORD_BYTES:
            return None, "limit"
        body = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_json_constant)
        if type(body) is not dict or set(body) != {
            "schema_version", "id", "created_at", "updated_at", "context", "sql"
        }:
            return None, "invalid"
        if type(body["schema_version"]) is not int:
            return None, "invalid"
        if body["schema_version"] != SCHEMA_VERSION:
            return None, "unsupported"
        fields = body["context"]
        if type(fields) is not dict or set(fields) != set(CONTEXT_KEYS):
            return None, "invalid"
        values = tuple(fields[key] for key in CONTEXT_KEYS)
        if any(type(value) is not str or not value or len(value.encode("utf-8")) > 1_024
               for value in values):
            return None, "invalid"
        context: DraftContext = (values[0], values[1], values[2], values[3], values[4])
        if type(body["id"]) is not str or body["id"] != expected_id or draft_id(context) != expected_id:
            return None, "invalid"
        sql = body["sql"]
        if type(sql) is not str or not sql.strip():
            return None, "invalid"
        if len(sql.encode("utf-8")) > MAX_SQL_BYTES:
            return None, "limit"
        times: list[datetime] = []
        for key in ("created_at", "updated_at"):
            raw = body[key]
            if type(raw) is not str or not raw.endswith("Z"):
                return None, "invalid"
            parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
            if parsed.tzinfo is None or _timestamp(parsed) != raw:
                return None, "invalid"
            times.append(parsed)
        if times[0] > times[1]:
            return None, "invalid"
        return SqlDraft(expected_id, context, sql, times[0], times[1]), None
    except Exception:
        return None, "invalid"
```

Use typed `cast` only after the exact runtime checks when satisfying strict mypy for the JSON dictionary/tuple; do not replace them with coercion.

- [ ] **1.7 Implement transactions and disk operations.** `list` early-returns disabled/read_only before any directory enumeration or creation. Enabled operations use the shared config transaction. Save locks, reloads enabled, checks permit, scans canonical owned files with lstat/no traversal, reads bounded bytes, validates the input record, preserves existing created_at, computes proposed byte/count budget, creates a private temp, writes/flushes/fsyncs, checks permit again, atomically replaces, then returns the fresh listing. Use `finally` to unlink the owned temp when it still exists. Never log raw payload or exceptions. Clear/delete perform no optimistic listing mutation; results reflect disk success. Disable persists false and deletes while holding that same transaction; it can retry cleanup even when already off. Enable initialization rollback and unknown confirmation state follow the design exactly.

A permit revoked before transaction entry or before replace returns `cancelled`; the old record stays intact. A permit revoked after an already-entered replace cannot roll back the physical call and is handled conservatively by the runtime. Keep this distinction explicit in comments/tests.

The filesystem mechanism for this step is complete below. Put it in `athena_draft_store.py`; its public store methods invoke it only inside `ConfigStore.transaction()`. `_decode_record` is the pure schema decoder from step 1.6. The callbacks accepted by `mutate_records` are explicit arguments, not undefined module helpers. `decode` returns `(record_or_none, code_or_none)`; `encode` is `_encode_record` from step 1.6.

```python
import os
from pathlib import Path
import re
import stat
import tempfile
from collections.abc import Callable

from aws_tui.infra.paths import ensure_private_dir

_OWNED_RECORD = re.compile(r"[0-9a-f]{64}\.json\Z")


class _UnsafeDraftPath(Exception):
    pass


def _private_directory(directory: Path, *, create: bool) -> bool:
    try:
        before = directory.lstat()
    except FileNotFoundError:
        if not create:
            return False
    else:
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise _UnsafeDraftPath("Draft storage path is unavailable")
    # The refusal above happens BEFORE ensure_private_dir can chmod/follow a link.
    ensure_private_dir(directory)
    after = directory.lstat()
    if not stat.S_ISDIR(after.st_mode) or stat.S_ISLNK(after.st_mode):
        raise _UnsafeDraftPath("Draft storage path is unavailable")
    return True


def _read_regular_record(path: Path) -> bytes | None:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RECORD_BYTES:
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            after = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
                or opened.st_size > MAX_RECORD_BYTES
            ):
                return None
            chunks: list[bytes] = []
            remaining = MAX_RECORD_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65_536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            return payload if len(payload) <= MAX_RECORD_BYTES else None
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        return None


def _atomic_record(directory: Path, name: str, payload: bytes, permit: DraftPermit) -> bool:
    if not _OWNED_RECORD.fullmatch(name):
        raise _UnsafeDraftPath("Draft storage path is unavailable")
    descriptor, raw_path = tempfile.mkstemp(prefix=".draft-", suffix=".tmp", dir=directory)
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if permit.cancelled:
            return False
        _private_directory(directory, create=False)
        if permit.cancelled:
            return False
        os.replace(temporary, directory / name)
        if os.name == "posix":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(directory, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def mutate_records(
    *, config: ConfigStore, directory: Path, operation: str,
    record: SqlDraft | None, selected_id: str | None, permit: DraftPermit,
    decode: Callable[[bytes, str], tuple[SqlDraft | None, DraftCode | None]],
    encode: Callable[[SqlDraft], bytes],
) -> DraftStoreResult:
    try:
        if config.read_only:
            return DraftStoreResult(code="read_only", enabled=False)
        if operation == "list" and not config.load().athena_sql_drafts:
            return DraftStoreResult(enabled=False)
        with config.transaction():
            enabled = config.load().athena_sql_drafts
            if operation == "save" and (not enabled or permit.cancelled):
                return DraftStoreResult(code="cancelled" if permit.cancelled else "disabled", enabled=enabled)
            if operation == "enable":
                config.set_athena_sql_drafts(True)
                try:
                    _private_directory(directory, create=True)
                except Exception:
                    confirmed: bool | None = None
                    try:
                        config.set_athena_sql_drafts(False)
                        confirmed = False
                    except Exception:
                        pass
                    return DraftStoreResult(code="io", enabled=confirmed)
                enabled = True
            if operation == "disable":
                config.set_athena_sql_drafts(False)
                enabled = False
            exists = _private_directory(directory, create=operation == "save")
            if not exists:
                return DraftStoreResult(enabled=enabled)
            paths = [path for path in directory.iterdir() if _OWNED_RECORD.fullmatch(path.name)]
            if operation in {"delete", "clear", "disable"}:
                for path in paths:
                    if operation != "delete" or path.stem == selected_id:
                        if not stat.S_ISDIR(path.lstat().st_mode):
                            path.unlink()  # Unlink an owned symlink itself; never its target.
                        else:
                            return DraftStoreResult(code="io", enabled=enabled)
            if operation == "save":
                if record is None:
                    return DraftStoreResult(code="invalid", enabled=enabled)
                payload = encode(record)
                if len(payload) > MAX_RECORD_BYTES or len(record.sql.encode("utf-8")) > MAX_SQL_BYTES:
                    return DraftStoreResult(code="limit", enabled=enabled)
                target = directory / (record.id + ".json")
                replacement = next((path for path in paths if path.name == target.name), None)
                if replacement is not None:
                    old_bytes = _read_regular_record(replacement)
                    if old_bytes is None:
                        return DraftStoreResult(code="invalid", enabled=enabled)
                    old, issue = decode(old_bytes, replacement.stem)
                    if old is None:
                        return DraftStoreResult(code=issue or "invalid", enabled=enabled)
                    record = replace(record, created_at=old.created_at)
                    payload = encode(record)
                    confirmed_record, issue = decode(payload, record.id)
                    if confirmed_record is None:
                        return DraftStoreResult(code=issue or "invalid", enabled=enabled)
                projected_count = len(paths) + (replacement is None)
                projected_bytes = len(payload)
                for path in paths:
                    if path != replacement:
                        info = path.lstat()
                        projected_bytes += info.st_size if stat.S_ISREG(info.st_mode) else MAX_RECORD_BYTES + 1
                if projected_count > MAX_DRAFTS or projected_bytes > MAX_TOTAL_BYTES:
                    return DraftStoreResult(code="limit", enabled=enabled)
                if not _atomic_record(directory, target.name, payload, permit):
                    return DraftStoreResult(code="cancelled", enabled=enabled)
            for temporary in directory.iterdir():
                if temporary.name.startswith(".draft-") and temporary.name.endswith(".tmp"):
                    if not stat.S_ISDIR(temporary.lstat().st_mode):
                        temporary.unlink()
            records: list[SqlDraft] = []
            skipped = 0
            for path in directory.iterdir():
                if not _OWNED_RECORD.fullmatch(path.name):
                    continue
                payload = _read_regular_record(path)
                parsed, issue = decode(payload, path.stem) if payload is not None else (None, "invalid")
                if parsed is None:
                    skipped += 1
                else:
                    records.append(parsed)
                    try:
                        path.chmod(0o600, follow_symlinks=False)
                    except (OSError, NotImplementedError):
                        pass
            if not any(directory.iterdir()):
                directory.rmdir()
            return DraftStoreResult(
                records=tuple(sorted(records, key=lambda row: (row.updated_at, row.id), reverse=True)),
                skipped=skipped, enabled=enabled,
            )
    except Exception:
        return DraftStoreResult(code="io")
```

Validate a new `record` with the same pure decoder after encoding and before `mutate_records` accepts it (round-trip against its ID); map an empty/whitespace-only SQL edit to `delete` in the runtime. Complete wrapper mapping: `list` uses operation `list`; `save` uses `save`; `delete` validates `selected_id` with `[0-9a-f]{64}` then uses `delete`; `clear` uses `clear`; `set_enabled(True/False)` uses `enable/disable`. Every call supplies `record=None`/`selected_id=None` where absent and a fresh permit where no caller permit applies. On generic failure after disabling, reload only the boolean setting inside a separate safe helper and include it as `enabled` if confirmed; do not misreport successful cleanup. The method preserves unknown/invalid owned records except explicitly requested delete/clear/disable.

The public wrapper glue for the transaction mechanism is:

```python
class AthenaDraftStore:
    def __init__(self, *, config: ConfigStore, directory: Path) -> None:
        self._config = config
        self._directory = directory

    def _call(self, operation: str, record: SqlDraft | None = None,
              selected_id: str | None = None, permit: DraftPermit | None = None) -> DraftStoreResult:
        return mutate_records(
            config=self._config, directory=self._directory, operation=operation,
            record=record, selected_id=selected_id, permit=permit or DraftPermit(),
            decode=_decode_record, encode=_encode_record,
        )

    def list(self) -> DraftStoreResult:
        return self._call("list")

    def save(self, record: SqlDraft, *, permit: DraftPermit) -> DraftStoreResult:
        try:
            if len(record.sql.encode("utf-8")) > MAX_SQL_BYTES:
                return DraftStoreResult(code="limit")
            parsed, issue = _decode_record(_encode_record(record), record.id)
        except Exception:
            return DraftStoreResult(code="invalid")
        if parsed is None:
            return DraftStoreResult(code=issue or "invalid")
        return self._call("save", record=parsed, permit=permit)

    def delete(self, draft_id: str) -> DraftStoreResult:
        if not re.fullmatch(r"[0-9a-f]{64}", draft_id):
            return DraftStoreResult(code="invalid")
        return self._call("delete", selected_id=draft_id)

    def clear(self) -> DraftStoreResult:
        return self._call("clear")

    def set_enabled(self, enabled: bool) -> DraftStoreResult:
        result = self._call("enable" if enabled else "disable")
        if result.code is None or result.enabled is not None:
            return result
        try:
            confirmed = self._config.load().athena_sql_drafts
        except Exception:
            confirmed = None
        return replace(result, enabled=confirmed)
```

Add POSIX FIFO and socket fixtures under canonical filenames, a symlink file pointing at a sentinel outside the draft directory, and a symlink draft directory. Listing must finish without opening FIFO/socket targets; the outside sentinel is unchanged, the directory symlink is refused before `ensure_private_dir`, and a valid sibling still lists. Monkeypatch `os.open` to record flags and assert `O_NONBLOCK`/`O_NOFOLLOW` where available. These are narrow draft metadata safeguards, not new generic host hardening.

- [ ] **1.8 Run green and layer checks.**

```bash
uv run pytest tests/unit/infra/test_config_store.py tests/unit/infra/test_athena_draft_store.py -q
bash scripts/check-layers.sh
uv run ruff check src/aws_tui/infra/config_store.py src/aws_tui/infra/athena_draft_store.py tests/unit/infra/test_config_store.py tests/unit/infra/test_athena_draft_store.py
```

Expected: all tests pass, `layer rules clean`, ruff exits 0. The implementer self-reviews the schema, safe result boundary, preservation tests and owned-file deletion, commits assigned files through normal hooks with `feat: add private opt-in Athena draft store`, and reports the hash. Root then independently reviews the committed diff and integrates.

### Task 2: Own asynchronous work, save revisions and bounded shutdown

**Files:**

- Create: `src/aws_tui/infra/draft_worker.py`
- Create: `src/aws_tui/vm/athena/drafts_vm.py`
- Modify: `tests/athena_drafts_helpers.py` (add runtime_at)
- Test: `tests/unit/infra/test_draft_worker.py`
- Test: `tests/unit/vm/athena/test_drafts_vm.py`

**Interfaces:**

- Consumes Task 1's synchronous store/results/permits; no QueryContext import in worker.
- Produces `DraftWorker`, `DraftState`, `DraftFlushReport`, `AthenaDraftsVM`, `AthenaDraftSession` and their exact design methods/properties.
- Runtime owns one worker, every save permit/future, all active sessions and a monotonic revision per draft ID. Individual query sessions never own an untracked background task.
- Use `DEBOUNCE_SECONDS = 0.500` and `SHUTDOWN_SECONDS = 2.000` in `drafts_vm.py`.

- [ ] **2.1 Write worker and revision red tests.** Assert FIFO ordering, lazy thread creation, completion retrieval on success/failure, rejection after close_intake, no default executor use and payload release. For sessions assert pending immediately after edit, only latest successful revision becomes saved, errors preserve SQL, missing context writes nothing, context change never rebases an old payload, and disabled mode does not call the store. Append `runtime_at` below to `tests/athena_drafts_helpers.py`; add its VMx/runtime imports inside that function so Task 1 helper imports do not require the not-yet-created runtime module. The VM test module imports `CTX`, `record`, `runtime_at` and `store_at` from that exact helper file:

```python
def runtime_at(tmp_path, *, enabled=True):
    from vmx import NULL_DISPATCHER, MessageHub
    from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM

    store = store_at(tmp_path)
    if enabled:
        assert store.set_enabled(True).code is None
    vm = AthenaDraftsVM(
        store=store,
        enabled=enabled,
        read_only=False,
        directory=tmp_path / "athena-drafts",
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
    )
    return vm, store


async def test_latest_edit_is_saved_under_its_captured_context(tmp_path):
    runtime, store = runtime_at(tmp_path)
    session = runtime.open_session()
    context = QueryContext(*CTX)
    session.edited("SELECT 1", context)
    assert session.state == "pending"
    session.edited("SELECT 2", context)
    assert session.state == "pending"
    report = await runtime.shutdown()
    assert report.unpersisted == 0
    assert store.list().records[0].sql == "SELECT 2"
```

Use `from tests.athena_drafts_helpers import CTX, record, runtime_at, store_at` and `from tests.helpers import wait_until` and `from aws_tui.domain.query import QueryContext` in `tests/unit/vm/athena/test_drafts_vm.py`. Do not import helpers from a test function module. A settled saved-state test must wait for the worker acknowledgment while the runtime is still active, rather than checking a disposed session after shutdown.

- [ ] **2.2 Write the adversarial shutdown test before implementation.** This stalls the synchronous store using a thread event; it is not a cancellation-friendly async fake.

```python
import asyncio
import threading
import time


async def test_shutdown_deadline_retains_and_observes_stalled_store(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = store.save

    def stalled(record, *, permit):
        started.set()
        release.wait()  # Intentionally ignores permit cancellation until released.
        return original(record, permit=permit)

    monkeypatch.setattr(store, "save", stalled)
    session = runtime.open_session()
    session.edited("SELECT 'FLUSH_SECRET'", QueryContext(*CTX))
    begin = time.monotonic()
    try:
        report = await runtime.shutdown()
        elapsed = time.monotonic() - begin
        assert started.is_set()
        assert report.timed_out is True
        assert report.unpersisted == 1
        assert elapsed < 2.5  # 2.0 second contract plus scheduler tolerance.
        assert runtime._worker.pending_count == 1
        assert session.state != "saved"
    finally:
        release.set()
    await wait_until(lambda: runtime._worker.pending_count == 0, what="owned draft worker drained")
    assert session.state != "saved"
    assert "FLUSH_SECRET" not in repr(report)
```

Add a second test that cancels the awaiting flush coroutine and releases the worker later; the runtime still owns and observes the result. Assert no unexpected pending asyncio tasks remain after cleanup. Add a monkeypatch forbidding `asyncio.to_thread`/default executor in the draft worker test. Test a fake that completes an already-entered write after timeout; the session still never reports saved or touches a disposed observer.

- [ ] **2.3 Run red.**

```bash
uv run pytest tests/unit/infra/test_draft_worker.py tests/unit/vm/athena/test_drafts_vm.py -q
```

Expected: missing modules or missing interface assertions fail.

- [ ] **2.4 Implement the dedicated owned worker.** Use `queue.Queue`, a lazy `threading.Thread(daemon=True)`, `concurrent.futures.Future`, and a lock-protected pending registry. Each submitted callable is retained until completion and then cleared; `Future` gets only `DraftStoreResult`, including `io` for caught operational failures. A done result is always retrieved. Close intake atomically rejects new submissions and enqueues a stop sentinel after accepted operations, never joins on the UI loop. Use a private exception with constant text for submit-after-close, or return a completed cancelled result consistently; select the latter to keep the public result boundary uniform.

```python
# Core worker execution, inside its FIFO loop, after obtaining a job:
try:
    if future.set_running_or_notify_cancel():
        try:
            result = operation()
        except Exception:
            result = DraftStoreResult(code="io")
        future.set_result(result)
finally:
    operation = None
    with self._lock:
        self._pending.discard(future)
    if future.done() and not future.cancelled():
        future.result()
```

Use a repr-suppressed job wrapper if a dataclass stores `operation`; never include arguments/SQL in thread names. Handle race between submit and close under one lock. Do not convert arbitrary exceptions to strings, log them, or attach them to futures.

- [ ] **2.5 Implement runtime/session transitions.** A session stores the latest SQL/context privately in memory even while off. `edited` cancels its debounce handle, increments revision and registers a new permit only when enabled, active and complete context is valid. The runtime serializes IDs across sessions and cancels older permits when a later revision for the same ID becomes current. Capture payload before scheduling. `context_changed` changes availability but does not mutate the captured payload's context. `recovered` sets an exact saved baseline without enqueueing a write. `activate` attaches the current editor baseline without treating snapshots as edits. Staged sessions remain inert until activation.

The save-completion predicate is exact:

```python
current = (
    not self._detached
    and self._runtime.enabled
    and self._runtime._coordinator.is_current(record.id, captured_write_revision, permit)
    and self._editor_revision == captured_editor_revision
    and not permit.cancelled
)
if current and result.code is None:
    self._saved_sql = record.sql
    self._saved_context = context
    self._state = "saved"
elif current and result.code not in {None, "cancelled", "disabled"}:
    self._state = "error"
```

Every private capture carries `captured_editor_revision` and `captured_write_revision` as separate fields. The runtime counter identifies the newest writer for an ID across sessions; the session counter identifies its current editor contents. The `is_current(draft_id: str, captured_write_revision: int, permit: DraftPermit) -> bool` implementation appears in step 2.5 below. Use `loop.call_later` timers; do not create one sleeping task per keystroke. Post completed results to the loop only while it is alive and the owner is active; after disposal, retrieve/release only. Notification payloads contain property names, never SQL. Save retry is next edit or final flush, not an unbounded retry loop.

The focused scheduling mechanism below defines every revision-bearing handoff. Keep it private in `drafts_vm.py`; `AthenaDraftSession` supplies `completed` to update its baseline/state using the predicate above. `on_property_changed` remains the existing value-free VM facade, not part of this worker mechanism.

```python
import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class _EditCapture:
    record: SqlDraft = field(repr=False)
    captured_editor_revision: int
    captured_write_revision: int
    permit: DraftPermit = field(repr=False)


class _WriteCoordinator:
    def __init__(self, store: AthenaDraftStore, worker: DraftWorker) -> None:
        self.store = store
        self.worker = worker
        self.loop = asyncio.get_running_loop()
        self.sequence = 0
        self.current: dict[str, _EditCapture] = {}
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.launches: dict[str, Callable[[], None]] = {}
        self.futures: set[Future[DraftStoreResult]] = set()
        self.waiters: dict[Future[DraftStoreResult], asyncio.Future[DraftStoreResult]] = {}
        self.intake = True
        self.terminal: DraftFlushReport | None = None
        self.shutdown_lock = asyncio.Lock()
        self.mutation_lock = asyncio.Lock()

    def is_current(self, draft_id: str, captured_write_revision: int, permit: DraftPermit) -> bool:
        capture = self.current.get(draft_id)
        return (
            capture is not None
            and capture.captured_write_revision == captured_write_revision
            and capture.permit is permit
            and not permit.cancelled
        )

    def observe(self, future: Future[DraftStoreResult]) -> asyncio.Future[DraftStoreResult]:
        existing = self.waiters.get(future)
        if existing is not None:
            return existing
        waiter: asyncio.Future[DraftStoreResult] = self.loop.create_future()
        self.futures.add(future)
        self.waiters[future] = waiter

        def deliver(result: DraftStoreResult) -> None:
            if not waiter.done():
                waiter.set_result(result)
            self.futures.discard(future)
            self.waiters.pop(future, None)

        def completed(done: Future[DraftStoreResult]) -> None:
            result = done.result()  # Worker returns a sanitized result, never a raw exception.
            if self.loop.is_closed():
                self.futures.discard(done)
                self.waiters.pop(done, None)
                return
            try:
                self.loop.call_soon_threadsafe(deliver, result)
            except RuntimeError:
                self.futures.discard(done)
                self.waiters.pop(done, None)

        future.add_done_callback(completed)
        return waiter

    def schedule(
        self, *, sql: str, context: QueryContext, captured_editor_revision: int,
        completed: Callable[[_EditCapture, DraftStoreResult], None],
    ) -> _EditCapture | None:
        if not self.intake or not all(context.cache_key):
            return None
        identity = draft_id(context.cache_key)
        previous = self.current.get(identity)
        if previous is not None:
            previous.permit.cancel()
        old_timer = self.timers.pop(identity, None)
        if old_timer is not None:
            old_timer.cancel()
        self.sequence += 1
        now = datetime.now(UTC)
        capture = _EditCapture(
            SqlDraft(identity, context.cache_key, sql, now, now),
            captured_editor_revision, self.sequence, DraftPermit(),
        )
        self.current[identity] = capture

        def launch() -> None:
            self.timers.pop(identity, None)
            self.launches.pop(identity, None)
            if not self.is_current(identity, capture.captured_write_revision, capture.permit):
                return
            if sql.strip():
                operation = lambda: self.store.save(capture.record, permit=capture.permit)
            else:
                # Empty edits delete the bound context. The same FIFO orders older writes.
                operation = lambda: (
                    DraftStoreResult(code="cancelled") if capture.permit.cancelled
                    else self.store.delete(identity)
                )
            waiter = self.observe(self.worker.submit(operation))

            def apply(done: asyncio.Future[DraftStoreResult]) -> None:
                if done.cancelled() or self.terminal is not None:
                    return
                completed(capture, done.result())

            waiter.add_done_callback(apply)

        self.launches[identity] = launch
        self.timers[identity] = self.loop.call_later(DEBOUNCE_SECONDS, launch)
        return capture

    def fence(self, ids: set[str] | None) -> None:
        for identity in tuple(self.current):
            if ids is not None and identity not in ids:
                continue
            self.current[identity].permit.cancel()
            timer = self.timers.pop(identity, None)
            if timer is not None:
                timer.cancel()
            self.launches.pop(identity, None)
            self.current.pop(identity, None)
```

Construct this coordinator lazily on the first enabled async operation, so composition and synchronous disabled VMs do not require a running event loop or start a thread. The session's exact edit transition is:

```python
# Initialize these in AthenaDraftSession.__init__, in addition to runtime/subscriptions:
self._editor_revision = 0
self._sql = ""
self._context: QueryContext | None = None
self._bound_context: QueryContext | None = None
self._saved_sql: str | None = None
self._saved_context: QueryContext | None = None
self._deleted_revision: int | None = None
self._detached = False
self._active = active
self._state: DraftState = "off"


def edited(self, sql: str, context: QueryContext) -> None:
    if self._detached:
        return
    self._editor_revision += 1
    self._sql = sql
    self._context = context
    self._deleted_revision = None
    if not sql.strip():
        self._bound_context = None
    elif self._bound_context is None and all(context.cache_key):
        self._bound_context = context
    if not self._active or not self._runtime.enabled:
        self._state = "off"
    elif not all(context.cache_key) or (
        self._bound_context is not None and self._bound_context != context
    ):
        self._state = "context_required"
    else:
        self._state = "pending"
        self._runtime.schedule_session(self, sql, context, self._editor_revision)
    self._notify("state")


def context_changed(self, context: QueryContext) -> None:
    self._context = context
    if self._runtime.enabled and self._sql.strip() and (
        not all(context.cache_key)
        or self._bound_context is not None and self._bound_context != context
    ):
        self._state = "context_required"
    self._notify("state")


def recovered(self, record: SqlDraft) -> None:
    self._editor_revision += 1
    self._sql = record.sql
    self._context = QueryContext(*record.context)
    self._bound_context = self._context
    self._saved_sql = record.sql
    self._saved_context = self._context
    self._deleted_revision = None
    self._state = "saved"
    self._notify("state")


def has_unsaved_text(self, sql: str, context: QueryContext) -> bool:
    return bool(sql.strip()) and (sql != self._saved_sql or context != self._saved_context)
```

`_notify(property_name)` is the same value-free ObserverSafeSubject facade used by the adjacent Athena VMs. Add this runtime bridge (the coordinator is initialized by the runtime's enabled async setup before editing is accepted):

```python
def schedule_session(self, session: AthenaDraftSession, sql: str,
                     context: QueryContext, captured_editor_revision: int) -> None:
    if self._disposed or self._saving_suspended or not self.enabled:
        return
    self._ensure_coordinator()
    def completed(capture: _EditCapture, result: DraftStoreResult) -> None:
        current = (
            not session._detached and self.enabled
            and self._coordinator.is_current(
                capture.record.id, capture.captured_write_revision, capture.permit
            )
            and session._editor_revision == capture.captured_editor_revision
        )
        if not current:
            return
        if result.code is None:
            session._saved_sql = capture.record.sql
            session._saved_context = QueryContext(*capture.record.context)
            session._state = "saved" if capture.record.sql.strip() else "empty"
        elif result.code not in {"cancelled", "disabled"}:
            session._state = "error"
        session._notify("state")
    self._coordinator.schedule(
        sql=sql, context=context, captured_editor_revision=captured_editor_revision,
        completed=completed,
    )
```

The lazy coordinator initializer is a runtime method, called only from an enabled async operation or edit while the application loop is running:

```python
def _ensure_coordinator(self) -> _WriteCoordinator:
    if self._coordinator is None:
        self._coordinator = _WriteCoordinator(self._store, self._worker)
    return self._coordinator
```

The runtime initializes `_sessions: set[AthenaDraftSession]`, `_coordinator=None`, a lazy `_worker=DraftWorker()`, `_store`, `_enabled`, `_read_only`, `_disposed=False`, `_saving_suspended=False`, `_items=()`, `_error_text=None`, `_busy=False` and `_cleanup_required=False`; public properties are read-only projections. When disabled, it retains current session text without creating a coordinator. `activate` assigns text/context/complete bound context and flips `_active=True` without calling `edited`. `deleted` sets `_deleted_revision = _editor_revision`, clears saved baseline, and sets empty. `detach` marks detached and cancels its timer through the coordinator but leaves already-submitted future ownership with the runtime; final eligible capture happens before detach.

- [ ] **2.6 Implement mutation fencing and deadline aggregation.** Before delete/clear/disable, revoke appropriate permits in every session and cancel timers. Queue disk mutations in the same FIFO, then update listings only from successful disk results. Set an in-memory tombstone on matching editor revisions; no untouched text is requeued by shutdown. New edits clear only their own tombstone. Disable also suspends intake before starting I/O; failed cleanup keeps an explicit retry state. Concurrent enable/disable actions serialize through one runtime operation lock; `busy` prevents duplicate UI actions, and generation checks reject late results.

For flush, snapshot accepted concurrent futures. For each, make a runtime-owned `loop.create_future()` waiter and feed its result from a done callback using `loop.call_soon_threadsafe` only if the loop remains open. This is an observation bridge: canceling a waiter never cancels the concurrent future. Do not use bare `asyncio.wrap_future` followed by `.cancel()`, because that propagates cancellation to queued work. Use an absolute deadline:

```python
remaining = max(0.0, deadline - asyncio.get_running_loop().time())
if wrappers:
    done, pending = await asyncio.wait(wrappers, timeout=remaining)
else:
    done, pending = set(), set()
for wrapper in pending:
    wrapper.cancel()  # Observation waiter only; no cancellation chain to the worker.
```

Do not cancel a concurrent future that is still queued and then lose its completion accounting. The runtime owns both waiters and original futures until their completion callbacks have been observed; the worker owns its original future regardless of waiter cancellation; revoked permits keep stale queued operations inert. Close worker intake only after all final eligible saves are submitted. Count current unconfirmed editor revisions, not the number of worker operations. One absolute deadline covers all sessions and pending work. `dispose` must be nonblocking and idempotent.

These coordinator methods implement the destructive fence and single terminal wait. They receive all policy-specific state changes as explicit callbacks, so no session is silently skipped:

```python
async def mutate(
    self: _WriteCoordinator, operation: Callable[[], DraftStoreResult],
    *, ids: set[str] | None, tombstone: Callable[[], None],
) -> DraftStoreResult:
    async with self.mutation_lock:
        if self.terminal is not None:
            return DraftStoreResult(code="cancelled")
        self.fence(ids)
        tombstone()
        return await asyncio.shield(self.observe(self.worker.submit(operation)))


async def finish(
    self: _WriteCoordinator, *, deadline: float,
    unconfirmed: Callable[[], int],
) -> DraftFlushReport:
    async with self.shutdown_lock:
        if self.terminal is not None:
            return self.terminal
        self.intake = False
        for timer in self.timers.values():
            timer.cancel()
        self.timers.clear()
        for launch in tuple(self.launches.values()):
            launch()
        self.launches.clear()
        self.worker.close_intake()
        waiters = tuple(self.waiters.values())
        cancelled = False
        if waiters:
            try:
                _, pending = await asyncio.wait(
                    waiters, timeout=max(0.0, deadline - self.loop.time())
                )
            except asyncio.CancelledError:
                cancelled = True
                pending = {waiter for waiter in waiters if not waiter.done()}
        else:
            pending = set()
        # A success callback scheduled by waiter completion must settle before counting.
        await asyncio.sleep(0)
        count = unconfirmed()
        timed_out = bool(pending)
        if pending:
            self.fence(None)
            for waiter in pending:
                waiter.cancel()  # No chain to the original concurrent Future.
        self.terminal = DraftFlushReport(count, timed_out)
        if cancelled:
            # Ownership remains here even when the caller leaves; report is cached.
            raise asyncio.CancelledError
        return self.terminal
```

Place these as methods on `_WriteCoordinator` (remove the explicit `self: _WriteCoordinator` annotation). Runtime `shutdown` computes `deadline = loop.time() + SHUTDOWN_SECONDS` once and passes a callback counting each active session with `has_unsaved_text` and no matching `_deleted_revision`. On cancellation, retain the terminal report for a later shutdown caller; do not close the coordinator twice. The ordinary page-navigation flush uses the same observation wait pattern without `close_intake` or setting `terminal`; app-terminal flush immediately returns the cached report. Define `_session_is_unconfirmed` as `session._active and session._sql.strip() and session._deleted_revision != session._editor_revision and session.has_unsaved_text(session._sql, session._context)` when context exists, with incomplete-context nonempty text also counted. Never count two sessions' identical superseded revisions as two current edits; select only the runtime's current writer per ID.

Runtime delete passes `ids={selected_id}` and a tombstone callback iterating all matching bound contexts. Clear passes `ids=None` and tombstones all sessions. Disable first sets local saving suspension and then passes `store.set_enabled(False)` with all-session tombstoning; enabled/error state is taken from the returned `enabled` field, and local suspension remains until a successful enable. Enable uses the same mutation lock without cancelling new edits until its initialized success; only then schedules the current nonempty text of active sessions once. All mutators notify `items`, `enabled`, `busy`, `error_text` with property names only.

- [ ] **2.7 Run green and review races.**

```bash
uv run pytest tests/unit/infra/test_draft_worker.py tests/unit/vm/athena/test_drafts_vm.py tests/unit/infra/test_athena_draft_store.py -q
bash scripts/check-layers.sh
```

Required race matrix: older-save/newer-edit, context-change/debounce, old-page/new-page same ID, pending-save/delete, in-flight-save/clear, enable/disable overlap, disable/save, shutdown/stalled-write, canceled-waiter/completion. The implementer self-reviews ownership and the true deadline test, commits through normal hooks with `feat: coordinate Athena draft saves and bounded flush`, and reports the hash. Root then independently reviews the committed diff and integrates.

### Task 3: Add explicit recovery and source/context execution guards

**Files:**

- Create: `src/aws_tui/vm/athena/draft_recovery.py`
- Modify: `src/aws_tui/vm/athena/query_vm.py` (optional draft session/validation callback; set_sql/set_context/execute/shutdown hooks)
- Modify: `src/aws_tui/vm/athena/page_vm.py` (optional runtime/source callback; explicit restore flow)
- Test: `tests/unit/vm/athena/test_draft_recovery.py`
- Consumed helper: `tests/athena_drafts_helpers.py`
- Test: `tests/unit/vm/athena/test_query_vm.py`
- Test: `tests/unit/vm/athena/test_page_vm.py`

**Interfaces:**

- Consumes `AthenaDraftsVM`, `AthenaDraftSession`, `SqlDraft`, exact `QueryContext` and domain validators. Test imports: `from tests.athena_drafts_helpers import record, runtime_at`; `from tests.unit.vm.athena.test_page_vm import PageClient, make_page_vm`; `from tests.helpers import wait_until`; `from aws_tui.infra.athena_draft_store import DraftPermit, DraftStoreResult`. The existing page test module is the canonical PageClient/make_page_vm fixture owner; only its optional keyword signature is extended.
- Root task extraction must append the exact section headed `## Privacy evidence belongs to the store/recovery tasks` to this Task 3 brief; complete its recovery/snapshot/diagnostic cases before the task commit.
- Produces `validate_draft_context`, `AthenaPageVM.restore_draft(draft_id, confirm_replace)`, `.keep_current_editor()`.
- Add optional keyword parameters `drafts: AthenaDraftsVM | None = None`, `drafts_active: bool = True`, `source_is_current: Callable[[], Awaitable[bool]] | None = None` to PageVM; optional `draft_session: AthenaDraftSession | None = None`, `validate_draft_execution: Callable[[QueryContext], Awaitable[bool]] | None = None`, `drafts_enabled: Callable[[], bool] | None = None` to QueryVM. Existing constructors remain valid.
- QueryVM exposes `draft_state: DraftState`, `draft_error_text: str | None`, and `draft_execution_blocked: bool`. PageVM exposes `drafts: AthenaDraftsVM | None` and `draft_recovery_error: str | None`. These are value-free observable property changes.

- [ ] **3.1 Write recovery tests seeded through the real VM setter.** Extend `make_page_vm` in its existing test module with the optional runtime/source callback parameters, preserving defaults. Build a real enabled store/runtime and the existing PageClient. The accepted restore test explicitly selects the record's context before calling restore. Assert no start calls on every restore path.

```python
async def test_restore_prompts_for_vm_seeded_unsaved_text(tmp_path, monkeypatch):
    runtime, store = runtime_at(tmp_path)
    client = PageClient()

    async def source_current():
        return True

    page = make_page_vm(client, drafts=runtime, source_is_current=source_current)
    await page.setup()
    saved = record(context=page.context.cache_key, sql="SELECT 'RECOVERED'")
    assert store.save(saved, permit=DraftPermit()).code is None
    monkeypatch.setattr(store, "save", lambda record, *, permit: DraftStoreResult(code="io"))
    page.query.set_sql("SELECT 'UNSAVED'")
    asked = 0

    async def decline():
        nonlocal asked
        asked += 1
        return False

    starts = len(client.start_calls)
    assert await page.restore_draft(saved.id, decline) is False
    assert asked == 1
    assert page.query.sql == "SELECT 'UNSAVED'"
    assert len(client.start_calls) == starts
    await page.shutdown()
    await runtime.shutdown()
```

Add accept, editor-changes-during-confirmation, context-changes-during-confirmation, delete-during-confirmation, disable-during-confirmation, provider-removes-database-after-first-check, same-cached-context-now-invalid, source callback false, missing context, busy query and shutdown-during-validation. Use events to stop at confirmation/provider boundaries, not timing guesses. The unsaved example deliberately makes autosave fail after the old good record is committed, so the full journey can exceed the production 500 ms debounce without overwriting the recovery target or blocking the FIFO listing read. Keep a separate test that changes the stored record during confirmation and requires refusal; never relax the fresh reread comparison. For stale cases assert the exact warning and `execute_command.can_execute() is False`; direct `execute_command.execute_async()` never starts a query.

- [ ] **3.2 Run red.**

```bash
uv run pytest tests/unit/vm/athena/test_draft_recovery.py -q
```

Expected: missing API/constructor parameter failures, then recovery assertions fail.

- [ ] **3.3 Implement fresh exact validation without fallback.** Extract a focused bounded page-discovery helper with the same page/row/token limits as existing `_snapshot_identity_is_available`, validating list shapes, token loops, duplicate identities and database source references. `validate_draft_context` must:

```python
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def _contains_exact(
    fetch: Callable[[str | None], Awaitable[tuple[list[T], str | None]]],
    *,
    valid: Callable[[T], bool],
    matches: Callable[[T], bool],
    identity: Callable[[T], object],
) -> bool:
    token: str | None = None
    seen_tokens: set[str] = set()
    seen_rows: set[object] = set()
    empty_pages = 0
    for _ in range(64):
        rows, next_token = await fetch(token)
        if type(rows) is not list or not all(valid(row) for row in rows):
            return False
        if next_token is not None and (type(next_token) is not str or not next_token):
            return False
        for row in rows:
            key = identity(row)
            if key in seen_rows:
                return False
            seen_rows.add(key)
        if len(seen_rows) > 1_000:
            return False
        if any(matches(row) for row in rows):
            return True
        empty_pages = empty_pages + 1 if not rows else 0
        if empty_pages > 3 or next_token is None or next_token in seen_tokens:
            return False
        seen_tokens.add(next_token)
        token = next_token
    return False


async def validate_draft_context(
    *,
    context: QueryContext,
    client: Any,
    source_is_current: Callable[[], Awaitable[bool]],
) -> bool:
    try:
        if not all(type(part) is str and bool(part) for part in context.cache_key):
            return False
        if not await source_is_current():
            return False
        if not await _contains_exact(
            lambda token: client.list_workgroups_page(start_token=token),
            valid=valid_athena_workgroup_summary,
            matches=lambda row: row.name == context.workgroup,
            identity=lambda row: row.name,
        ):
            return False
        detail = await client.get_workgroup(context.workgroup)
        if (
            not valid_athena_workgroup_detail(detail)
            or detail.summary.name != context.workgroup
            or detail.summary.state != "ENABLED"
        ):
            return False
        if not await _contains_exact(
            lambda token: client.list_catalogs_page(
                workgroup=context.workgroup, start_token=token
            ),
            valid=valid_athena_catalog_summary,
            matches=lambda row: row.name == context.catalog,
            identity=lambda row: row.name,
        ):
            return False
        if not await _contains_exact(
            lambda token: client.list_databases_page(
                context.catalog, workgroup=context.workgroup, start_token=token
            ),
            valid=lambda row: (
                valid_database_summary(row)
                and row.ref.connection_name == context.connection_name
                and row.ref.region == context.region
                and row.ref.catalog_name == context.catalog
            ),
            matches=lambda row: row.ref.database_name == context.database,
            identity=lambda row: row.ref,
        ):
            return False
        return await source_is_current()
    except Exception:
        return False
```

Import `QueryContext` from `domain.query`, the three Athena validators from `vm.athena._domain_validation`, and `valid_database_summary` from `vm.athena._domain_validation`. `CancelledError` is not caught by this `Exception` boundary in supported Python versions. The function transports no raw provider exception.

Use `valid_athena_workgroup_summary`, `valid_athena_workgroup_detail`, `valid_athena_catalog_summary` and `valid_database_summary`. Require workgroup state `ENABLED`. Never call `_select_workgroup`, `_select_catalog`, `refresh_query_context` or the equal-context shortcut in `_preflight_snapshot_context` to validate recovery. Operational exceptions become false with a fixed warning; cancellation propagates normally without attaching SQL. Existing snapshot methods stay untouched.

- [ ] **3.4 Implement the restore transaction.** Use a fresh listing lookup for the selected ID and refuse unavailable records. Set a temporary execution guard, capture current SQL/context/editor/lifecycle generations, check exact equality of all five fields, and fresh-validate. If session reports unsaved text, await the injected confirmation callback. On decline release only the temporary guard; keep SQL/save state unchanged. After approval reread listing, compare candidate ID+updated_at and payload equality privately, recheck enabled/idle/lifecycle/editor/context, and fresh-validate again. Acquire the existing lifecycle guard, recheck revisions synchronously, then install only SQL with draft edit scheduling suppressed and call `session.recovered(record)`. Select query view synchronously if needed; never call execute or restore_snapshot.

A mismatch leaves `draft_recovery_error = "Draft context is unavailable or changed. Select the exact original context and retry."` and blocks execution. `keep_current_editor` clears this failed-recovery guard only; it must not clear a successful recovered session's different-context guard. To release that latter guard the user returns to its original context or clears SQL. Guard cleanup on cancellation must respect newer recovery/context generations.

The restore control flow below is the complete page mechanism. Initialize `_draft_restore_running=False` and `_draft_recovery_error=None` in PageVM; `_draft_session` is the session passed to its query. No provider selection function is called.

```python
async def restore_draft(self, draft_id: str,
                        confirm_replace: Callable[[], Awaitable[bool]]) -> bool:
    drafts = self._drafts
    session = self._draft_session
    if (
        drafts is None or session is None or not drafts.enabled
        or self._draft_restore_running or not self._is_alive()
        or self.query.is_executing or self.query.is_submitting
        or self.query.is_context_resolving
    ):
        return False
    self._draft_restore_running = True
    prior_guard = self.query.draft_recovery_guard
    self.query.set_draft_recovery_guard(True)
    self._draft_recovery_error = None
    before_context = self.context
    before_page = self._snapshot_restore_token()
    before_query = self.query.snapshot_generation
    before_editor = session.editor_revision
    accepted = False
    declined = False
    cancelled = False

    def unchanged() -> bool:
        return (
            self._is_alive() and drafts.enabled
            and self.context == before_context
            and self._snapshot_restore_token() == before_page
            and self.query.snapshot_generation == before_query
            and session.editor_revision == before_editor
            and not self.query.is_executing and not self.query.is_submitting
            and not self.query.is_context_resolving
        )

    async def read_selected() -> SqlDraft | None:
        await drafts.refresh()
        if drafts.error_text is not None:
            return None
        return next((row for row in drafts.items if row.id == draft_id), None)

    async def current_context_valid() -> bool:
        return await validate_draft_context(
            context=before_context, client=self._client,
            source_is_current=self._source_is_current,
        )

    try:
        selected = await read_selected()
        if selected is None or selected.context != before_context.cache_key:
            return False
        if not unchanged() or not await current_context_valid() or not unchanged():
            return False
        if session.has_unsaved_text(self.query.sql, before_context):
            if not await confirm_replace():
                declined = True
                return False
        latest = await read_selected()
        if latest != selected or not unchanged():
            return False
        if not await current_context_valid() or not unchanged():
            return False
        async with self.query.snapshot_restore_guard(before_query):
            if not unchanged():
                return False
            self.query.install_draft_sql(selected)
            self._select_view_state("query")
            self._draft_recovery_error = None
            self.query.set_draft_recovery_guard(False)
            accepted = True
            return True
    except asyncio.CancelledError:
        cancelled = True
        self.query.set_draft_recovery_guard(prior_guard)
        raise
    finally:
        self._draft_restore_running = False
        if declined:
            self.query.set_draft_recovery_guard(prior_guard)
        elif not accepted and self._is_alive() and not declined and not cancelled:
            self._draft_recovery_error = (
                "Draft context is unavailable or changed. Select the exact original context and retry."
            )
            self.query.set_draft_recovery_guard(True)
        self._notify("draft_recovery_error")


def keep_current_editor(self) -> None:
    if self._draft_restore_running:
        return
    self._draft_recovery_error = None
    self.query.set_draft_recovery_guard(False)
    self._notify("draft_recovery_error")
```

`keep_current_editor` affects only `_draft_recovery_guard`; the query's separate context-origin predicate remains unchanged. Cancellation restores the prior guard and does not enter the stale-context failure finalizer.

- [ ] **3.5 Hook editing, context and explicit execution.** `set_sql` remains sync; after assigning SQL call session.edited unless scoped recovery suppression is active. `set_context` calls session.context_changed only for the actual committed query context; transient discovery cannot relabel a captured edit. Clearing SQL removes the bound-origin guard. Add `_can_execute` checks for draft guards and context_required while enabled. At `_run_execution` before start/submitting, await `validate_draft_execution(self._context)` when enabled, then compare the captured query/editor/context generations and return with fixed validation text on failure/change. This catches direct command execution and a configured source changing while a control looked enabled. Preserve all existing SQL-policy and AWS query lifecycle logic.

Query shutdown invokes session final capture/flush using the query's context, before it is disposed. Do not infer query context from the page's already cleared context. Off-state query control flow adds only a no-op optional hook.

Add these query methods/projections. Initialize `_draft_recovery_guard=False`, `_draft_session=draft_session`, `_validate_draft_execution=validate_draft_execution` and `_drafts_enabled: Callable[[], bool]` supplied by PageVM as `lambda: drafts is not None and drafts.enabled`. The independent context-origin predicate cannot be cleared by dismissing a failed recovery.

```python
@property
def draft_recovery_guard(self) -> bool:
    return self._draft_recovery_guard


def set_draft_recovery_guard(self, blocked: bool) -> None:
    self._draft_recovery_guard = blocked
    self._notify("draft_execution_blocked")


@property
def draft_execution_blocked(self) -> bool:
    session = self._draft_session
    if not self._drafts_enabled():
        return False
    return self._draft_recovery_guard or (
        session is not None and bool(self._sql.strip()) and (
            session.state == "context_required"
            or session.bound_context is not None and session.bound_context != self._context
        )
    )


def install_draft_sql(self, record: SqlDraft) -> None:
    # Caller owns the query lifecycle guard; no await or execution here.
    if self._draft_session is None or record.context != self._context.cache_key:
        raise ValueError("Draft context is unavailable")
    self._sql = record.sql
    self._validation_error = None
    self._draft_session.recovered(record)
    self._notify("sql")
    self._notify("validation_error")
    self._notify("draft_state")


def set_sql(self, sql: str) -> None:
    if self._disposed or sql == self._sql:
        return
    self._sql = sql
    self._validation_error = None
    if self._draft_session is not None:
        self._draft_session.edited(sql, self._context)
    self._notify("sql")
    self._notify("validation_error")


async def _draft_preflight(self, generation: int) -> bool:
    if not self._drafts_enabled():
        return True
    session = self._draft_session
    if session is None or self.draft_execution_blocked or self._validate_draft_execution is None:
        return False
    captured_context = self._context
    captured_editor_revision = session.editor_revision
    allowed = await self._validate_draft_execution(captured_context)
    return (
        allowed and generation == self._generation
        and not self._disposed and not self._shutdown_started
        and captured_context == self._context
        and captured_editor_revision == session.editor_revision
        and not self.draft_execution_blocked
    )
```

Extend QueryVM's optional constructor contract with `drafts_enabled: Callable[[], bool] | None = None`; assign `_drafts_enabled = drafts_enabled or (lambda: False)`. Inside the existing `_can_execute` early-return condition add `or self.draft_execution_blocked`. Inside `set_context`, immediately after `self._context = context`, call `self._draft_session.context_changed(context)` when the session exists. Inside `_run_execution`'s existing outer `try` immediately after calculating `generation`, before SQL validation/start, insert:

```python
if not await self._draft_preflight(generation):
    if generation == self._generation:
        self._validation_error = "Draft context is unavailable or changed."
        self._notify("validation_error")
    return
```

The outer existing `finally` still resets busy/task state. Before query shutdown's existing `_shutdown_complete = True`, flush the session against `loop.time() + SHUTDOWN_SECONDS` only when shared runtime is nonterminal; `AthenaDraftSession.flush` returns the cached terminal report when app shutdown already ran. Query dispose calls `session.detach()` once. Subscribe to session property changes with a callback that notifies only `draft_state`, `draft_error_text` and `draft_execution_blocked`, and dispose that subscription alongside existing query subscriptions. No snapshot method is edited.

- [ ] **3.6 Run green including exact binding regression.**

```bash
uv run pytest tests/unit/vm/athena/test_draft_recovery.py tests/unit/vm/athena/test_query_vm.py tests/unit/vm/athena/test_page_vm.py -q
uv run pytest tests/unit/vm/athena/test_page_vm.py::test_page_snapshot_round_trip_restores_query_results_and_selections_without_execution -q
bash scripts/check-layers.sh
```

Expected: all pass. The implementer self-reviews the no-fallback path, direct-command protection, confirmation races and unchanged snapshot diff, commits through normal hooks with `feat: recover Athena drafts only in validated source context`, and reports the hash. Root then independently reviews the committed diff and integrates.

### Task 4: Wire app/service lifetimes and prove normal-exit and crash-file recovery

**Files:**

- Modify: `src/aws_tui/services/athena/service.py`
- Modify: `src/aws_tui/composition.py`
- Modify: `src/aws_tui/app.py` (Settings construction, shared shutdown and fixed terminal warning)
- Modify: `src/aws_tui/vm/settings/settings_vm.py`
- Modify: `src/aws_tui/vm/athena/drafts_vm.py` (idempotent terminal report if needed)
- Test: `tests/unit/services/athena/test_service.py`
- Create: `tests/unit/test_composition_athena_drafts.py`
- Extend: `tests/unit/vm/athena/test_draft_recovery.py`
- Extend: `tests/unit/vm/settings/test_settings_vm.py`
- Modify: `tests/unit/test_app_sanity.py` (all three constructor-bypassing shutdown contexts and lifecycle assertions)

**Interfaces:**

- Consumes Tasks 1–3 and `ConnectionResolver.resolve_selected(name)` exact lookup, captured `Connection`, `RecoveryServiceVM.commit_selection`. The Task 4 normal/crash tests extend `tests/unit/vm/athena/test_draft_recovery.py` using its exact Task 3 imports from `tests.athena_drafts_helpers`, `tests.unit.vm.athena.test_page_vm` and `tests.helpers`; define `always_current` and `accept_replace` locally as shown below.
- Add `AppContext.athena_drafts_vm: AthenaDraftsVM` and `AppContext.athena_drafts_shutdown_warning: str | None`.
- Add optional `drafts: AthenaDraftsVM | None = None` and `source_check_factory: Callable[[Connection], Callable[[], Awaitable[bool]]] | None = None` to `AthenaService`. Each page gets its own check closure; PageVM setup calls it before accepting drafts-enabled editor actions to establish a credential-material-free baseline.
- Add optional `athena_drafts: AthenaDraftsVM | None = None` and `.athena_drafts` property to `SettingsVM`; SettingsVM does not dispose the app-owned runtime.
- Ordinary build opens an active query session; recovery build opens an inactive session. `commit_selection` commits selection state then invokes `page.activate_drafts()`, a new sync PageVM helper forwarding `session.activate(query.sql, query.context)`. Discarded candidates only detach.

- [ ] **4.1 Write composition/lifecycle red tests.** Use temporary config/cache, fake keychain/clients and the existing composition fixtures. Assert missing setting and `demo=True` create no draft directory even when a real config has true; read-only demo never lists draft files. Assert service pages and Settings share one runtime; no per-page store. A staged recovery page edit and shutdown must not write; activation does not autosave the snapshot but a subsequent user edit does. Old page flushing after replacement's newer edit cannot overwrite it. Build failure/close_unstarted must close worker intake without starting a worker just for cleanup.

- [ ] **4.2 Add distinct exit/crash recovery tests.** These tests use a fresh store/runtime and page for the destination and never copy an in-memory snapshot.

```python
async def test_normal_exit_flush_restores_into_fresh_page(tmp_path):
    runtime, store = runtime_at(tmp_path)
    client = PageClient()
    page = make_page_vm(client, drafts=runtime, source_is_current=always_current)
    await page.setup()
    page.query.set_sql("SELECT 'NORMAL_EXIT'")
    report = await runtime.shutdown()
    await page.shutdown()
    assert report.unpersisted == 0
    fresh_runtime, fresh_store = runtime_at(tmp_path)
    fresh_page = make_page_vm(client, drafts=fresh_runtime, source_is_current=always_current)
    await fresh_page.setup()
    assert fresh_page.query.sql == ""
    saved = fresh_store.list().records[0]
    before = len(client.start_calls)
    assert await fresh_page.restore_draft(saved.id, accept_replace)
    assert fresh_page.query.sql == "SELECT 'NORMAL_EXIT'"
    assert len(client.start_calls) == before
    await fresh_runtime.shutdown()
    await fresh_page.shutdown()


async def test_abrupt_exit_file_restores_without_source_shutdown(tmp_path):
    runtime, store = runtime_at(tmp_path)
    client = PageClient()
    page = make_page_vm(client, drafts=runtime, source_is_current=always_current)
    await page.setup()
    page.query.set_sql("SELECT 'ABRUPT_EXIT'")
    await wait_until(lambda: page.query.draft_state == "saved", what="debounced draft committed")
    saved_bytes = next((tmp_path / "athena-drafts").glob("*.json")).read_bytes()
    # Destination is created from committed disk data while source has not shut down.
    fresh_runtime, fresh_store = runtime_at(tmp_path)
    fresh_page = make_page_vm(client, drafts=fresh_runtime, source_is_current=always_current)
    await fresh_page.setup()
    saved = fresh_store.list().records[0]
    before = len(client.start_calls)
    assert await fresh_page.restore_draft(saved.id, accept_replace)
    assert fresh_page.query.sql == "SELECT 'ABRUPT_EXIT'"
    assert len(client.start_calls) == before
    assert saved_bytes == next((tmp_path / "athena-drafts").glob("*.json")).read_bytes()
    await fresh_runtime.shutdown()
    await fresh_page.shutdown()
    await runtime.shutdown()  # Test cleanup only, after recovery assertions.
    await page.shutdown()
```

Define the callbacks in that test module:

```python
async def always_current() -> bool:
    return True

async def accept_replace() -> bool:
    return True
```

The second case represents an abrupt-exit store left on disk; do not claim it injects a real kernel/power failure. Add failed temp-write recovery in Task 1 for atomicity. Do not substitute exporting/restoring a page snapshot for either case.

- [ ] **4.3 Run red.**

```bash
uv run pytest tests/unit/test_composition_athena_drafts.py tests/unit/services/athena/test_service.py tests/unit/vm/settings/test_settings_vm.py tests/unit/vm/athena/test_draft_recovery.py -q
```

Expected: missing composition properties/signatures or lifetime assertions fail.

- [ ] **4.4 Wire composition and source validation.** Construct the store with `directory=config_store.path.parent / "athena-drafts"`. Construct runtime once with effective enabled `_cfg.athena_sql_drafts and not demo`; if config load failed, enabled false and fixed preference error state. Build a source verifier with `await asyncio.to_thread(read_current_source)`; this bridge performs only source/configuration reads and does not handle SQL-bearing draft jobs. `read_current_source` calls `connection_resolver.resolve_selected(captured.name)` and compares `(kind, name, region, source, profile, endpoint_url, force_path_style, verify_tls)` plus the credential-material-free `entry_source_identity` tuple defined in step 4.4. Never compare a ConnectionEntry wholesale; keep only the identity tuple in memory. Reject newly shadowed, removed or remapped sources; do not compare rotating credential values as routing identity. No default connection/environment fallback and no unrelated secret resolution. Never persist or log credentials. Catch failures to false without interpolating raw errors. Pass the callback through AthenaService and each page.

Place the shared runtime into `AppContext.__slots__`, constructor and composition return. `close_unstarted` invokes nonblocking dispose/close intake. Register rollback in the same ExitStack used for other composition owners. Inject the runtime into `_mount_settings_view`'s SettingsVM. Because Settings content changes often, its dispose must release only its subscriptions, never disable persistence or terminate runtime work.

Use the following source identity functions in composition (or a private infrastructure helper consumed only by composition). They compare no credential material and make the baseline timing explicit: one factory closure per page; its first check occurs during page setup before draft recovery/edit execution is available. Subsequent checks refuse routing changes relative to that initialized page.

```python
import asyncio
from collections.abc import Awaitable, Callable


def entry_source_identity(entry: ConnectionEntry | None) -> tuple[object, ...]:
    if entry is None:
        return (False, None, None, None, None, None, None, None)
    return (
        True, entry.kind, entry.profile, entry.region, entry.endpoint_url,
        entry.credentials, entry.force_path_style, entry.verify_tls,
    )


def connection_route(connection: Connection) -> tuple[object, ...]:
    return (
        connection.kind, connection.name, connection.region, connection.source,
        connection.profile, connection.endpoint_url,
        connection.force_path_style, connection.verify_tls,
    )


def make_source_check_factory(
    config: ConfigStore, resolver: ConnectionResolver,
) -> Callable[[Connection], Callable[[], Awaitable[bool]]]:
    def factory(captured: Connection) -> Callable[[], Awaitable[bool]]:
        expected_route = connection_route(captured)
        baseline: tuple[object, ...] | None = None
        check_lock = asyncio.Lock()

        def read_identity() -> tuple[object, ...] | None:
            try:
                before = entry_source_identity(config.load().connections.get(captured.name))
                current = resolver.resolve_selected(captured.name)
                after = entry_source_identity(config.load().connections.get(captured.name))
                if before != after or connection_route(current) != expected_route:
                    return None
                return after
            except Exception:
                return None

        async def check() -> bool:
            nonlocal baseline
            async with check_lock:
                current = await asyncio.to_thread(read_identity)
                if current is None:
                    return False
                if baseline is None:
                    baseline = current
                return current == baseline

        return check
    return factory
```

Import `Connection`, `ConnectionResolver` from `infra.connection_resolver` and `ConnectionEntry`, `ConfigStore` from `infra.config_store`. None of `access_key_id`, `secret_access_key`, `session_token`, their hashes, or credential values enters either tuple. The credentials selector is the literal `entry.credentials` routing string (for example `env:TEAM_`), never its resolved value. The first-setup baseline cannot attest changes before that check; the captured connection route is still compared, and the five-field/account limitation remains explicit.

Composition construction and AppContext storage are:

```python
athena_draft_store = AthenaDraftStore(
    config=config_store,
    directory=config_store.path.parent / "athena-drafts",
)
athena_drafts_vm = AthenaDraftsVM(
    store=athena_draft_store,
    enabled=(not demo and _cfg.athena_sql_drafts),
    read_only=demo,
    directory=config_store.path.parent / "athena-drafts",
    hub=hub,
    dispatcher=dispatcher,
)
source_check_factory = make_source_check_factory(config_store, connection_resolver)
# Add to the existing AthenaService constructor call:
# drafts=athena_drafts_vm, source_check_factory=source_check_factory
# Add these names to AppContext.__slots__ and constructor assignment:
self.athena_drafts_vm = athena_drafts_vm
self.athena_drafts_shutdown_warning: str | None = None
```

In the config-load exception branch initialize `_cfg = Config(connections={}, defaults=Defaults(), keybindings=Keybindings())` so this code never references an unbound value; preserve the existing fixed configuration failure report. The runtime's constructor performs no I/O. `rollback.callback(athena_drafts_vm.dispose)` belongs in the existing composition ExitStack, while `AppContext.close_unstarted` calls `.dispose()` directly. In `_mount_settings_view`, replace the existing SettingsVM construction with:

```python
settings_vm = SettingsVM(
    s3=ctx.s3_connections_vm, athena_drafts=ctx.athena_drafts_vm,
    hub=ctx.hub, dispatcher=ctx.dispatcher,
)
```

SettingsVM stores the optional argument as `_athena_drafts`; its property returns that reference, and its dispose never disposes it.

- [ ] **4.5 Wire ordinary versus speculative lifetimes.** `build_vm` passes active sessions; `build_recovery_vm` passes `drafts_active=False`. Its existing `commit_selection` closure becomes a real function that performs the old selection commit and then `page.activate_drafts()`. A snapshot restore by itself remains inert. On normal source switching a newly active session owns only its editor; original context SQL remains under the original record ID. The runtime's per-ID permit supersession prevents delayed old-page writes from overwriting newer active-session edits.

The service construction changes are these complete methods plus two stored optional constructor arguments. `_build_vm` keeps its existing client/policy construction and adds the final three PageVM arguments shown below:

```python
# AthenaService.__init__ assignments:
self._drafts = drafts
self._source_check_factory = source_check_factory


def build_vm(self, connection: Connection) -> AthenaPageVM:
    return self._build_vm(connection, self._selections, drafts_active=True)


def build_recovery_vm(self, connection: Connection) -> RecoveryServiceVM:
    selections = self._selections.clone()
    scope = SelectionScope(self.descriptor.id, connection.name, connection.region)
    page = self._build_vm(connection, selections, drafts_active=False)

    def commit_selection() -> None:
        self._selections.replace_scope_from(selections, scope)
        page.activate_drafts()

    return RecoveryServiceVM(vm=page, commit_selection=commit_selection)


# In _build_vm(connection, selections, *, drafts_active: bool):
source_check = (
    self._source_check_factory(connection)
    if self._source_check_factory is not None else None
)
# Existing AthenaPageVM(...) call receives these exact keywords:
# drafts=self._drafts, drafts_active=drafts_active, source_is_current=source_check

# AthenaPageVM.__init__, before constructing self.query:
self._drafts = drafts
self._draft_session = drafts.open_session(active=drafts_active) if drafts is not None else None
self._source_is_current = source_is_current or _unavailable_draft_source


async def _unavailable_draft_source() -> bool:
    return False


# AthenaPageVM.setup: before admitting drafts-enabled edit/recovery execution:
if self._drafts is not None and self._drafts.enabled:
    if not await self._source_is_current():
        self._draft_recovery_error = "Draft context is unavailable or changed."
        self.query.set_draft_recovery_guard(True)


def activate_drafts(self) -> None:
    if self._draft_session is not None:
        self._draft_session.activate(self.query.sql, self.query.context)
```

The QueryVM construction receives `draft_session=self._draft_session`, `drafts_enabled=lambda: self._drafts is not None and self._drafts.enabled`, and `validate_draft_execution=self._validate_draft_execution`. Add this complete page method:

```python
async def _validate_draft_execution(self, context: QueryContext) -> bool:
    return await validate_draft_context(
        context=context, client=self._client, source_is_current=self._source_is_current,
    )
```

The PageVM constructor remains synchronous; all source lookup and baseline establishment occur during async setup. Both ordinary and staged pages validate the route, but staged sessions perform no draft disk work. Activating a staged snapshot does not call `set_sql` or `edited`.

- [ ] **4.6 Wire one bounded application shutdown.** Call `ctx.athena_drafts_vm.shutdown()` before `content_host.shutdown`, while the log sink is alive, and record its terminal report. All later session flush/detach calls immediately reuse this terminal outcome; they cannot reopen intake, enqueue saves, or wait for another two seconds. Runtime shutdown is idempotent under simultaneous quit/unmount calls. The report counts distinct unconfirmed current edits, not queued operations. Record `athena.drafts.unpersisted` with only count/timed_out and set the fixed AppContext warning. After Textual returns in `main`, print that warning to stderr once using existing terminal reporting patterns. Do not rely only on a toast that unmount removes.

```python
report = await ctx.athena_drafts_vm.shutdown()
if report.unpersisted:
    ctx.athena_drafts_shutdown_warning = (
        "Some Athena SQL edits were not confirmed saved before exit."
    )
    ctx.log_sink.warning(
        "athena.drafts.unpersisted",
        count=report.unpersisted,
        timed_out=report.timed_out,
    )
```

`LogSink.warning(event: str, **fields: object)` supports this block. Preserve the fields/event exactly. Test shared shutdown with at least two live sessions, then hosted query shutdown, and assert elapsed draft waiting remains under 2.5 seconds total. Use the Task 2 real stalled store and release it in `finally`; assert the warning is retained and late completion is observed with no late saved-state update.

- [ ] **4.7 Run green and lifecycle review.**

```bash
uv run pytest tests/unit/test_composition_athena_drafts.py tests/unit/services/athena/test_service.py tests/unit/vm/settings/test_settings_vm.py tests/unit/vm/athena/test_draft_recovery.py tests/unit/vm/athena/test_drafts_vm.py -q
bash scripts/check-layers.sh
```

Update all three `SimpleNamespace` contexts in `tests/unit/test_app_sanity.py` (near baseline lines 422, 509 and 584) with a mandatory fake draft owner and warning field, then run `uv run pytest tests/unit/test_app_sanity.py -q`. Strengthen exact cleanup order, failure continuation, cancellation and log-close-last assertions to include drafts shutdown/disposal. Do not add an optional production `getattr` fallback to accommodate incomplete test fakes. The implementer self-reviews normal/crash separation, demo no-I/O, the shared deadline, fixed warning and staged ownership, commits through normal hooks with `feat: wire Athena draft persistence across app lifetimes`, and reports the hash. Root then independently reviews the committed diff and integrates.

### Task 5: Deliver keyboard controls, truthful rendering and user documentation

**Files:**

- Create: `src/aws_tui/ui/widgets/athena/drafts_modal.py`
- Create: `src/aws_tui/ui/widgets/settings/athena_drafts_panel.py`
- Modify: `src/aws_tui/ui/widgets/athena/query_view.py`
- Modify: `src/aws_tui/ui/widgets/athena/page.py`
- Modify: `src/aws_tui/ui/widgets/settings_view.py`
- Modify: `src/aws_tui/vm/chrome/focus_coordinator_vm.py`
- Modify: `src/aws_tui/app.py` (only manager/confirmation presentation bridge if needed)
- Test: `tests/unit/ui/athena/test_page.py`
- Test: `tests/unit/ui/test_settings_view.py`
- Create: `tests/unit/ui/athena/test_drafts_modal.py`
- Create: `tests/integration/test_athena_drafts.py`
- Create: `tests/snapshot/test_athena_drafts.py`
- Create: `tests/snapshot/apps/athena_drafts.py`
- Modify: `tests/snapshot/apps/settings_view.py`, `tests/snapshot/test_settings_view.py` and affected Settings goldens after reviewing rendered differences
- Create: new enabled-drafts goldens beneath `tests/snapshot/__snapshots__/test_athena_drafts/`
- Modify: `docs/configuration.md`, `docs/cookbook.md`, `docs/services/athena.md`

**Interfaces:**

- Consumes PageVM `.drafts`, `.restore_draft`, `.keep_current_editor`, QueryVM draft properties, SettingsVM `.athena_drafts`, and existing ConfirmationVM/TextualDialogService.
- Produces `DraftModalResult = Literal["restored", "closed"]`, `AthenaDraftsModal(ModalScreen[DraftModalResult])` with constructor `__init__(self, page: AthenaPageVM, *, hub: MessageHub[Message], focus_coordinator: FocusCoordinatorVM | None = None) -> None`, and `AthenaDraftsPanel(vm: AthenaDraftsVM, *, hub: MessageHub[Message])`. The runtime exposes its injected `dispatcher: Dispatcher` read-only for the existing per-dialog ConfirmationVM pattern.
- Add `FocusSlot.ATHENA_DRAFTS = "athena.drafts"`; widget IDs are `athena-drafts`, `athena-drafts-list`, `athena-drafts-detail`, `athena-drafts-restore`, `athena-drafts-delete`, `athena-drafts-clear`, `athena-drafts-keep`, `athena-drafts-close`, `section-athena-drafts`, `athena-drafts-path`, `athena-drafts-retention`, `athena-drafts-toggle`, `athena-drafts-setting-status`.
- Modal state is presentation-only; recovery/unsaved decisions stay in the page/session VM. No UI code calls store.save directly.

- [ ] **5.1 Write UI red tests for off and enabled states.** Existing Athena fixture defaults remain off and unchanged. Assert off hides the button, adds no focus target and preserves old status text. Enabled mode contains Drafts in both manual query and native page focus rings; disabled controls drop out. Settings has an expanded Athena section with the actual path, plaintext retention explanation and a Button; test later section title focus too, since `_focus_controls` currently includes only the first Collapsible title. Ensure keyboard re-entry after collapsing/expanding still reaches the enable button. Modal selection alone never restores; Enter on Restore performs recovery; Ordinary Close/Escape returns `"closed"` and restores focus to the visible enabled Drafts button; successful recovery returns `"restored"` and focuses the editor. Assert the native focus slot as well as the focused widget. If the requested target is unavailable, use the existing page `focus_default()` route; if the page is detached or another modal owns the screen, do not focus it. Replace/delete/clear confirmations use keyboard and restore modal focus correctly.

- [ ] **5.2 Implement Settings with existing Button semantics.** Add this visible content only when SettingsVM has its shared runtime. Existing lightweight Settings harnesses without it retain their behavior.

```python
with Collapsible(title="Athena SQL drafts", collapsed=False, id="section-athena-drafts"):
    yield AthenaDraftsPanel(vm=self._vm.athena_drafts, hub=self._hub)
```

The panel composes plain `Static(..., markup=False)` path/retention/status and a Button. Path is `str(vm.directory)`; copy and labels match the design. Toggle launches an off-pump lifecycle worker. Disable asks an explicit danger confirmation and only then invokes `vm.set_enabled(False)`. Enable is the explicit local persistence action itself; do not add another permission menu. On failure retain the real effective preference and fixed error status. Disable in demo with `Unavailable in demo mode`.

Update Settings `_focus_controls` to gather each visible enabled Collapsible title plus visible enabled Buttons in DOM order. Existing connection controls/activation continue working. Do not introduce a Checkbox/Switch without adding it to app priority-key traversal; the selected design uses Button.

The settings panel and confirmation bridge are concrete. Define the shared `ask_draft_confirmation` in `ui/widgets/athena/drafts_modal.py`, import it into the settings panel, and use the same helper in the manager. This follows the existing S3ConnectionsPanel pattern; no AppContext access or composition import is needed.

```python
from aws_tui.ui.widgets.confirm_modal import TextualDialogService
from aws_tui.vm.chrome.confirm_vm import ConfirmationVM, ConfirmRequest


async def ask_draft_confirmation(host, *, drafts: AthenaDraftsVM, hub,
                                 request: ConfirmRequest) -> bool:
    confirmation = ConfirmationVM(hub=hub, dispatcher=drafts.dispatcher)
    confirmation.construct()
    try:
        return await confirmation.ask(
            request,
            dialog_service=TextualDialogService(host.app, confirmation, hub=hub),
        )
    finally:
        confirmation.dispose()
```

Use `DOMNode` for `host`, `MessageHub[Message]` for `hub` when typing this helper; both imports already follow existing widget conventions. The complete panel behavior is:

```python
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Button, Static
from aws_tui.ui.widgets._worker import DeferredWorkerMixin


class AthenaDraftsPanel(DeferredWorkerMixin, Widget):
    DEFAULT_CSS = "AthenaDraftsPanel { height: auto; } AthenaDraftsPanel Static { height: auto; }"

    def __init__(self, vm: AthenaDraftsVM, *, hub) -> None:
        super().__init__()
        self._vm = vm
        self._hub = hub
        self._subscription = None
        self._pending = False

    def compose(self) -> ComposeResult:
        yield Static(str(self._vm.directory), id="athena-drafts-path", markup=False)
        yield Static(
            "Keep the latest SQL for each query context on this device. "
            "Up to 50 drafts and 8 MiB total. SQL is stored as plaintext; "
            "results and credentials are not retained.",
            id="athena-drafts-retention", markup=False,
        )
        yield Button("Enable local SQL drafts", id="athena-drafts-toggle")
        yield Button("Retry draft cleanup", id="athena-drafts-cleanup")
        yield Static("", id="athena-drafts-setting-status", markup=False)

    def on_mount(self) -> None:
        self._subscription = self._vm.on_property_changed.subscribe(
            lambda _name: self.call_after_refresh(self._refresh)
        )
        self._refresh()

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        button = self.query_one("#athena-drafts-toggle", Button)
        button.label = "Disable and delete drafts" if self._vm.enabled else "Enable local SQL drafts"
        button.disabled = self._vm.read_only or self._vm.busy or self._pending
        cleanup = self.query_one("#athena-drafts-cleanup", Button)
        cleanup.display = self._vm.cleanup_required
        cleanup.disabled = self._vm.busy or self._pending or self._vm.read_only
        status = "Unavailable in demo mode" if self._vm.read_only else self._vm.error_text
        self.query_one("#athena-drafts-setting-status", Static).update(status or "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in {"athena-drafts-toggle", "athena-drafts-cleanup"} or self._pending or self._vm.busy:
            return
        event.stop()
        self._pending = True
        self._refresh()
        cleanup_only = event.button.id == "athena-drafts-cleanup"
        self._run_lifecycle_worker(lambda: self._toggle(cleanup_only), group="athena-drafts-setting")

    async def _toggle(self, cleanup_only: bool) -> None:
        try:
            if cleanup_only:
                await self._vm.set_enabled(False)
                return
            enabled = self._vm.enabled
            if enabled and not await ask_draft_confirmation(
                self, drafts=self._vm, hub=self._hub,
                request=ConfirmRequest(
                    title="Disable and delete local drafts?",
                    body_lines=("All local Athena SQL draft records will be deleted.",),
                    confirm_label="Disable and delete", danger=True,
                ),
            ):
                return
            await self._vm.set_enabled(not enabled)
        finally:
            self._pending = False
            if self.is_mounted:
                self._refresh()
```

Expose an explicit retry-cleanup button when the runtime reports disabled-with-cleanup-failure: add `cleanup_required: bool` to the runtime's read-only UI state and `#athena-drafts-cleanup` to the panel; its worker calls `await vm.set_enabled(False)` even when enabled is already false. Do not route that state to Enable by mistake. The focus traversal change is complete:

```python
def _focus_controls(self) -> tuple[Widget, ...]:
    controls: list[Widget] = []
    for widget in self.walk_children(Widget):
        is_title = callable(getattr(widget, "action_toggle_collapsible", None))
        if not (is_title or isinstance(widget, Button)):
            continue
        if not widget.can_focus or widget.disabled:
            continue
        if all(node.display for node in widget.ancestors_with_self):
            controls.append(widget)
    return tuple(controls)
```

- [ ] **5.3 Implement Athena control and status.** Add a compact `Drafts` Button after Cancel, set `display` from runtime.enabled, and subscribe to runtime/session properties without leaking subscriptions on unmount. Add the new focus slot to `_active_surface_focus_targets`, focus projection/activation mapping and QueryView's `_move_focus` list. `_is_focus_target` already filters display/disabled; verify the new branch uses it.

The enabled editor-title indicator is derived from session state, never timer age:

```python
DRAFT_LABELS = {
    "pending": "Draft pending",
    "saved": "Draft saved",
    "error": "Draft not saved",
    "context_required": "Draft pending",
    "empty": "No saved draft",
}
```

Off contributes no suffix or extra row. Keep execution status intact. At 80x24 preserve the current compact editor height and show the short saved/pending label in the editor border title; the full warning belongs in detail, not a clipped title or a tooltip. Put full fixed draft error/context reason in the existing detail area. Do not allow raw SQL into that status or diagnostics.

Use the editor border title for the enabled save indicator: it needs no extra compact-height row and remains visibly adjacent to SQL. Leave existing execution status text intact. In `_refresh`, after updating the existing status/detail, insert:

```python
drafts = self._page_vm.drafts
button = self.query_one("#athena-drafts", Button)
button.display = drafts is not None and drafts.enabled
if button.display:
    editor.border_title = "query editor · " + DRAFT_LABELS.get(self._vm.draft_state, "No saved draft")
else:
    editor.border_title = "query editor"
```

For `context_required`, use the short title `Draft pending` and put the full fixed explanation in detail so long copy does not clip the title. Compose the button immediately after Cancel:

```python
button = Button("Drafts", id="athena-drafts", compact=True, flat=True)
button.display = self._page_vm.drafts is not None and self._page_vm.drafts.enabled
yield button
```

Set its CSS `width: 8; min-width: 8; height: 3; margin: 0 1 0 0;`. Add `query_one("#athena-drafts", Button)` after Cancel in QueryView's manual focus list and `(FocusSlot.ATHENA_DRAFTS, self.query_one("#athena-drafts", Button))` after ATHENA_CANCEL in Page's query-surface target tuple. The existing visible/enabled filtering applies. Add the ID-to-slot projection entry wherever Page's existing Cancel entry is mapped. The opening bridge uses a Textual message rather than reaching into app internals:

```python
from textual.message import Message

# Nested in AthenaQueryView:
class OpenDrafts(Message):
    pass

# Add to on_button_pressed's existing elif chain:
elif event.button.id == "athena-drafts":
    self.post_message(self.OpenDrafts())

# AthenaPage handler:
def on_athena_query_view_open_drafts(self, event: AthenaQueryView.OpenDrafts) -> None:
    event.stop()
    if self._vm.drafts is None or not self._vm.drafts.enabled:
        return
    modal = AthenaDraftsModal(
        self._vm, hub=self._hub, focus_coordinator=self._focus_coordinator,
    )
    def restore_focus(result: DraftModalResult | None) -> None:
        def apply_focus() -> None:
            if not self.is_mounted or self.screen is not self.app.screen:
                return
            selector = "#athena-editor" if result == "restored" else "#athena-drafts"
            try:
                target = self.query_one(selector, Widget)
            except NoMatches:
                target = None
            if target is not None and self._is_focus_target(target):
                target.focus()
            else:
                self.focus_default()
        if self.is_mounted:
            self.call_after_refresh(apply_focus)
    self.app.push_screen(modal, restore_focus)
```

Import `AthenaDraftsModal` and `DraftModalResult` from `ui.widgets.athena.drafts_modal`; Page already uses `Widget` and `NoMatches`. The result callback has exact type `Callable[[DraftModalResult | None], None]`: `None` from an external/default dismissal is treated as ordinary close. It defers until refresh, then rechecks page attachment and current screen before choosing a visible enabled target with the existing `_is_focus_target` predicate. The fallback is the existing `focus_default()` method, preserving native focus-slot routing; it never focuses a removed page or the page behind another modal. Add subscriptions to `drafts.on_property_changed` and the query's value-free draft properties; dispose them on unmount.

- [ ] **5.4 Implement the metadata-only manager and confirmation bridge.** Use a ModalScreen with scrollable list/detail and buttons. Render all metadata with markup disabled and no SQL preview. Refresh asynchronously when opened; disable mutation/recovery buttons while busy. Buttons call VM methods via lifecycle workers. Restore passes a callback that presents `Replace unsaved SQL?`; decision to invoke it belongs to PageVM. Keep-current clears only failed recovery. Delete/Clear all show danger confirmation and await VM success before updating list. Nested confirmation and Escape use existing modal/focus coordinator conventions; closing a modal cancels presentation subscriptions, not owned persistence work.

The manager's full behavior mechanism follows. Use the existing theme's modal frame/footer classes and capture its render before accepting CSS; all dynamic text is plain `Text` or `markup=False`. The `focus_coordinator` argument is retained for the app's bridge, but do not call `modal_open` a second time: `AwsTuiApp` already synchronizes screen-stack transitions.

```python
from typing import Literal
from rich.text import Text
from vmx import Message, MessageHub
from aws_tui.vm.chrome.focus_coordinator_vm import FocusCoordinatorVM
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option
from aws_tui.ui.widgets._worker import DeferredWorkerMixin


DraftModalResult = Literal["restored", "closed"]


class AthenaDraftsModal(DeferredWorkerMixin, ModalScreen[DraftModalResult]):
    BINDINGS = [
        Binding("tab", "focus_next", show=False, priority=True),
        Binding("shift+tab", "focus_previous", show=False, priority=True),
        Binding("up", "move_up", show=False, priority=True),
        Binding("down", "move_down", show=False, priority=True),
        Binding("enter", "commit_focused", show=False, priority=True),
        Binding("escape", "close", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    AthenaDraftsModal { align: center middle; }
    AthenaDraftsModal > Vertical { width: 76; max-width: 96%; height: 90%; }
    #athena-drafts-list { height: 1fr; min-height: 3; }
    #athena-drafts-detail { height: 7; }
    #athena-drafts-actions { grid-size: 3 2; height: 6; }
    #athena-drafts-actions Button { width: 1fr; min-width: 8; }
    """

    def __init__(
        self, page: AthenaPageVM, *, hub: MessageHub[Message],
        focus_coordinator: FocusCoordinatorVM | None = None,
    ) -> None:
        super().__init__()
        if page.drafts is None:
            raise ValueError("Local drafts are unavailable")
        self._page = page
        self._drafts = page.drafts
        self._hub = hub
        self._focus_coordinator = focus_coordinator
        self._ids: list[str] = []
        self._subscription = None
        self._pending = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-frame"):
            yield Static("Local Athena SQL drafts", classes="modal-title", markup=False)
            yield OptionList(id="athena-drafts-list")
            yield Static("", id="athena-drafts-detail", markup=False)
            with Grid(id="athena-drafts-actions", classes="modal-footer"):
                yield Button("Restore", id="athena-drafts-restore")
                yield Button("Delete", id="athena-drafts-delete")
                yield Button("Clear all", id="athena-drafts-clear")
                yield Button("Keep current editor", id="athena-drafts-keep")
                yield Button("Close", id="athena-drafts-close")

    def on_mount(self) -> None:
        self._subscription = self._drafts.on_property_changed.subscribe(
            lambda _name: self.call_after_refresh(self._render)
        )
        self._run_lifecycle_worker(self._load, group="athena-drafts-list")
        self.query_one("#athena-drafts-list", OptionList).focus()

    def on_unmount(self) -> None:
        if self._subscription is not None:
            self._subscription.dispose()

    async def _load(self) -> None:
        await self._drafts.refresh()
        if self.is_mounted:
            self._render()

    def _selected_id(self) -> str | None:
        index = self.query_one("#athena-drafts-list", OptionList).highlighted
        return self._ids[index] if index is not None and 0 <= index < len(self._ids) else None

    def _render(self) -> None:
        if not self.is_mounted:
            return
        selected = self._selected_id()
        listing = self.query_one("#athena-drafts-list", OptionList)
        listing.clear_options()
        self._ids = [row.id for row in self._drafts.items]
        for row in self._drafts.items:
            listing.add_option(Option(Text(" · ".join(row.context)), id=row.id))
        if self._ids:
            listing.highlighted = self._ids.index(selected) if selected in self._ids else 0
        row = next((row for row in self._drafts.items if row.id == self._selected_id()), None)
        details = "No local drafts"
        if row is not None:
            labels = ("Connection", "Region", "Workgroup", "Catalog", "Database")
            details = "\n".join(f"{label}: {value}" for label, value in zip(labels, row.context, strict=True))
            details += "\nSaved: " + row.updated_at.isoformat()
        error = self._page.draft_recovery_error or self._drafts.error_text
        if error:
            details += "\n" + error
        self.query_one("#athena-drafts-detail", Static).update(details)
        busy = self._pending or self._drafts.busy or not self._drafts.enabled
        for identity in ("restore", "delete"):
            self.query_one(f"#athena-drafts-{identity}", Button).disabled = busy or row is None
        self.query_one("#athena-drafts-clear", Button).disabled = busy or not self._ids
        self.query_one("#athena-drafts-keep", Button).disabled = self._pending

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "athena-drafts-list":
            return
        # Update detail directly; do not clear/rebuild the list in response to its own highlight.
        row = next((row for row in self._drafts.items if row.id == event.option.id), None)
        if row is not None:
            labels = ("Connection", "Region", "Workgroup", "Catalog", "Database")
            text = "\n".join(f"{label}: {value}" for label, value in zip(labels, row.context, strict=True))
            text += "\nSaved: " + row.updated_at.isoformat()
            error = self._page.draft_recovery_error or self._drafts.error_text
            if error:
                text += "\n" + error
            self.query_one("#athena-drafts-detail", Static).update(text)

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_previous(self) -> None:
        self.focus_previous()

    def _move(self, delta: int) -> None:
        listing = self.query_one("#athena-drafts-list", OptionList)
        if self._ids:
            listing.highlighted = min(len(self._ids) - 1, max(0, (listing.highlighted or 0) + delta))

    def action_move_up(self) -> None:
        self._move(-1)

    def action_move_down(self) -> None:
        self._move(1)

    def action_commit_focused(self) -> None:
        if isinstance(self.focused, Button) and not self.focused.disabled:
            self.focused.press()
        # Enter in the list only selects/highlights; it never restores SQL.

    def action_close(self) -> None:
        self.dismiss("closed")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        identity = event.button.id
        if identity == "athena-drafts-close":
            self.action_close()
            return
        if self._pending or identity is None:
            return
        self._pending = True
        selected = self._selected_id()
        self._render()
        self._run_lifecycle_worker(
            lambda: self._perform(identity, selected), group="athena-drafts-action",
        )

    async def _confirm(self, title: str, *, danger: bool = False) -> bool:
        return await ask_draft_confirmation(
            self, drafts=self._drafts, hub=self._hub,
            request=ConfirmRequest(title=title, danger=danger,
                                   confirm_label="Confirm" if danger else "Replace"),
        )

    async def _perform(self, identity: str, selected: str | None) -> None:
        try:
            if identity == "athena-drafts-restore" and selected is not None:
                restored = await self._page.restore_draft(
                    selected, lambda: self._confirm("Replace unsaved SQL?"),
                )
                if restored and self.is_mounted:
                    self.dismiss("restored")
            elif identity == "athena-drafts-delete" and selected is not None:
                if await self._confirm("Delete this local draft?", danger=True):
                    await self._drafts.delete(selected)
            elif identity == "athena-drafts-clear":
                if await self._confirm("Delete all local drafts?", danger=True):
                    await self._drafts.clear()
            elif identity == "athena-drafts-keep":
                self._page.keep_current_editor()
        finally:
            self._pending = False
            if self.is_mounted:
                self._render()
```

The highlighted-detail handler preserves the fixed page/runtime error. Import `AthenaPageVM`, `AthenaDraftsVM`, `ConfirmRequest`, `MessageHub`, `Message` and proper callback/subscription types explicitly; annotate `_subscription` as `DisposableBase | None`, `hub` as `MessageHub[Message]`, and class CSS/bindings as `ClassVar` under the repository's strict lint/type rules. The API names and entire action logic are defined above; do not replace any body with a placeholder. The actual-app priority-key bridge already forwards `action_focus_next`, `action_focus_previous`, `action_move_up`, `action_move_down` and `action_commit_focused`; these method names are deliberate.

- [ ] **5.5 Build a writable actual-app keyboard fixture.** Add an isolated `tests/integration/test_athena_drafts.py` context manager patterned on `_mounted_athena_app`, but use a writable temporary ConfigStore and injected fake Athena service/runtime. It may reuse demo's in-memory services for unrelated AWS avoidance, but production `demo=True` runtime stays disabled; explicitly replace the draft runtime and Settings/service injection within the test and label this test-only setup. A cleaner alternative is the existing non-demo composition test fixture with all external providers fake. Assert no real AWS client factory is invoked. Do not claim the production demo toggle proved opt-in.

Test sequence at both 80x24 and 120x40:

1. Open Settings using the actual app action and Tab to `athena-drafts-toggle`; Enter enables.
2. Assert actual temporary path and retention copy are visible in screenshot and preference reload is true.
3. Navigate to Athena, seed SQL through VM/TextArea helper, hold worker commit with an event, and assert rendered pending while keyboard remains responsive.
4. Release, wait for saved, Tab to Drafts and Enter; arrow-select a record without restoring.
5. Inject a failed save for the new editor text (fixed `io` result, preserving the previous good record), seed unsaved SQL through VM, wait for Draft not saved, invoke Restore with keyboard, decline; assert text and start count unchanged.
6. Invoke Restore again and accept; assert recovered SQL, no new execution, modal result `"restored"`, and editor/native-primary focus after dismissal. Reopen the manager and Close/Escape without recovery; assert result `"closed"` and Drafts/native-drafts focus. Cover unavailable-target fallback and page teardown before the deferred callback.
7. Delete/clear using keyboard with confirmation; assert listing/disk absence. Disable in Settings; assert off setting and absent owned records.

Use `focus_and_settle`, `seed_athena_sql`, `wait_until`, and `drain_workers` from `tests/helpers.py` for event settlement. Avoid directly invoking button handlers as the only keyboard evidence.

- [ ] **5.6 Write pending/saved/manager/stale-context snapshot tests with content guards.** New snapshot app uses real enabled runtime, temporary paths and fake PageClient. A controllable worker gate yields deterministic pending; saved is captured only after acknowledged completion. For stable rendered path text, inject a deterministic fake display path while the fixture store writes to tmp_path, and include a separate full-app test asserting the real path is displayed. Fixture SQL uses a harmless marker `SELECT 42 AS draft_render_marker`. Test carbon and github-light at 80x24 and 120x40; follow existing all-theme coverage conventions for modal fixtures where applicable.

```python
from html import unescape


def assert_draft_svg(svg: str, *, state: str) -> None:
    rendered = unescape(svg).replace("\xa0", " ")
    assert "query editor" in rendered
    assert "SELECT" in rendered
    assert "draft_render_marker" in rendered
    assert state in rendered
    assert "Drafts" in rendered
```

Run the content guard against the checked-in expected SVG, not just VM/widget text or `snap_compare` truthiness. Preserve existing Athena snapshot content guards. Review new and changed Settings SVGs visually; capture artifacts under owned `/private/tmp` paths when tests fail. A required new visible Settings control justifies reviewed golden changes, not removal of assertions. Do not update existing Athena off-state goldens merely to suppress a regression.

- [ ] **5.7 Update documentation as part of the delivered feature.** Follow the repository documentation workflow for these three existing pages. Add the concrete text below and adapt headings/link placement to each file's current structure:

`docs/configuration.md`: file table row `<config-dir>/athena-drafts/<id>.json`; setting `[athena] sql_drafts = true`, default false, demo disabled, all exact schema/count/byte limits, permissions and plaintext statement. State that missing setting creates no draft directory. Explain clearing/deleting/disable and no untouched-text recreation.

`docs/services/athena.md`: enable via Settings; status Pending/Saved/Not saved; Drafts manager; exact source and workgroup/catalog/database selection before restore; fresh validation; unsaved replacement confirmation; no auto execution/results. State same-name AWS credential-account changes across restarts cannot be attested by the five persisted fields.

`docs/cookbook.md`: a reproducible keyboard recipe for enable → edit → wait Saved → quit/relaunch → select exact source/context → Drafts → Restore; separate abrupt-exit recovery after completed autosave. Include the precise shutdown caveat: `aws-tui waits up to two seconds for draft flushing and reports edits not confirmed saved. An already-running filesystem operation may finish later. This limit does not bound AWS cancellation or total process shutdown.` Document stalled/error feedback without claiming guaranteed recovery of pending text.

Do not add SQL to doctor/support reporting; mention draft contents are not collected by diagnostics. Do not publish the site/wiki during an implementation task; root handles authorized delivery.

- [ ] **5.8 Run focused UI/integration/render checks.**

```bash
uv run pytest tests/unit/ui/athena/test_drafts_modal.py tests/unit/ui/athena/test_page.py tests/unit/ui/test_settings_view.py -q
uv run pytest -o addopts='' tests/integration/test_athena_drafts.py -q
uv run pytest tests/snapshot/test_athena_drafts.py -q
uv run pytest tests/snapshot/test_athena.py -q
uv run python -m scripts.docs.check_docs
```

Run `uv run pytest tests/snapshot/test_settings_view.py -q` after updating its fixture to supply the real shared draft runtime; preserve every existing title/connection/form content guard and add the new section/control guards. New goldens may be generated using the repo's established snapshot-update flag after inspecting the first missing-golden output; rerun without update afterward. Existing snapshot failures outside changed feature state must be diagnosed, not silently overwritten; the already-approved historical #250 Nord uncertainty remains separately recorded.

The implementer self-reviews captured UI output, real keyboard evidence, unchanged off-state layout and documentation limits, commits through normal hooks with `feat: expose keyboard-accessible Athena draft recovery`, and reports the hash. Root then independently reviews the committed diff and integrates.

## Privacy evidence belongs to the store/recovery tasks

This is not an extra final testing task. Include these tests in Task 1's store deliverable and Task 3's recovery deliverable before accepting them.

- [ ] Add `test_draft_diagnostics_never_include_payload` parameterized over valid SQL sentinel, malformed JSON containing sentinel, invalid UTF-8 bytes, escaped surrogate SQL, unsupported version, hostile context field, over-limit write, and an injected `OSError("DRAFT_SQL_SENTINEL")`.
- [ ] Test `str`/`repr` for records/results/session private payload holders; a valid editor still contains the SQL intentionally, but no snapshot representation does.
- [ ] Capture LogSink output, fresh error strings, traceback, cause/context/notes and CrashDump bytes through the same pattern as existing `test_page_vm.py::_snapshot_failure_artifacts`. For a result-only store, synthesize a fresh constant-message `RuntimeError` outside the exception-catching helper solely to exercise the diagnostic sink; do not inject the sentinel directly into the sink and expect generic redaction to solve it.
- [ ] Assert no raw exception object crosses a future boundary and no UI worker rethrows decoder/Unicode/OSError data. Never attach raw payload to `add_note`.
- [ ] Verify current doctor remains read-only and never opens a draft path if composition code is touched; do not reintroduce the outdated issue assumption that no doctor exists.

Concrete sentinel helper for diagnostics assertions:

```python
import traceback


def assert_private_error(exc, *, sentinel, crash, log_text):
    texts = [str(exc), repr(exc), "".join(traceback.format_exception(exc)), crash, log_text]
    cursor = exc
    seen = set()
    while cursor is not None and id(cursor) not in seen:
        seen.add(id(cursor))
        texts.extend((str(cursor), repr(cursor), repr(getattr(cursor, "__notes__", ()))))
        cursor = cursor.__cause__ or cursor.__context__
    assert all(sentinel not in text for text in texts)
```

Use fresh exception creation outside any original except block. Check both cause and context individually if both exist; the helper above is the minimum chain check, not permission to leave an unexamined branch.

## Root's final acceptance checklist

This is a checklist over the five delivered tasks, not a new standalone code/test task. Do not rerun unchanged suites without a concrete gap. Collect the recorded task results, then run lint/type/format and applicable cross-feature checks required by the repository once:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
bash scripts/check-layers.sh
uv run pytest tests/unit/vm/athena/test_page_vm.py::test_page_snapshot_round_trip_restores_query_results_and_selections_without_execution -q
```

If a task changed files after its successful targeted run, rerun its relevant test module. Expand to the repository's normal local suite when required by the active root workflow, reporting environmental failures separately; do not waive checks or claim unavailable platforms passed. Maintain the following evidence ledger in root's execution report:

| AC | Deliverable and exact evidence |
|---|---|
| 1 | Task 1 no-path default store test plus Task 4 real app/query shutdown off path |
| 2 | Task 5 writable actual-app Settings keyboard test, path/retention screenshot |
| 3 | Task 5 pending/saved snapshots and checked-in SVG content guards |
| 4 | Task 4 two distinct fresh-page tests: normal exit and abrupt-exit file |
| 5 | Tasks 3/4/5 PageClient start-call invariants |
| 6 | Task 3 VM-seeded unsaved text and post-confirmation race tests |
| 7 | Task 1 five-field exact round trip |
| 8 | Task 3 cached-equal stale provider/config tests and no-fallback/direct-execute guards |
| 9 | Task 1 unsupported/corrupt/over-limit preservation |
| 10 | Tasks 1/2/5 disk/listing removal and fenced queued/in-flight saves |
| 11 | Tasks 1/3 sentinel matrix, log/crash/exception chains, unchanged snapshot repr test |
| 12 | Task 1 POSIX 0700/0600 test |
| 13 | Task 1 synchronized concurrent saves through separate store instances |
| 14 | Tasks 2/4 truly stalled synchronous store, retained ownership, one total deadline/report |
| 15 | Task 5 actual-app keyboard enable/recover/confirmation/focus tests |
| 16 | Exact snapshot round-trip command above |

Root should reject a task if any AC is represented only by a helper test, mock setter, state assertion without required rendered content, cancellation-friendly substitute for a stalled worker, or a scope change hidden in documentation. The plan author has not run tests or made implementation changes.
