# 1. VMx FormVM S3 Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace aws-tui's local `ValidatingFormVM` settings-form mini-primitive with VMx 3.1.0 `FormVM` validators while preserving the public `S3ConnectionFormVM` facade.

**Architecture:** Keep `S3ConnectionFormVM` as the app-owned facade that views bind to. Internally, hold a VMx `FormVM[S3CompatForm]` configured with closure-backed field/model validators so extra validators can still be registered after construction. Delete the aws-tui-only `ValidatingFormVM` primitive once the facade owns the small aggregation layer.

**Tech Stack:** Python 3.11+, VMx 3.1.0 `FormVM`, reactivex observables, pytest, pytest-cov, ruff, mypy.

## 1.1. Global Constraints

- Runtime dependency remains `vmx>=3.1.0,<4.0.0`.
- Public view-facing surface remains `model`, `errors`, `can_submit`, `set_field`, `submit_command`, `revert_command`, and `on_errors_changed`.
- Do not expose raw VMx internals in public aws-tui VM APIs.
- Preserve field validator order: first non-`None` per-field error wins.
- Preserve model validator order: later model validators may overwrite earlier errors for the same field.
- Preserve strict submit gating: strict forms require `is_dirty and is_valid`.
- Record VM/view/test LOC deltas and coverage requirements in the VMx 3.1 adoption audit.

---

## 1.2. File Structure

- Modify `src/aws_tui/vm/settings/s3_connection_form_vm.py`: replace `ValidatingFormVM` composition with `vmx.FormVM` plus local validator aggregation.
- Modify `src/aws_tui/vm/_composition/__init__.py`: stop exporting the deleted validating-form primitive.
- Delete `src/aws_tui/vm/_composition/validating_form_vm.py`: obsolete mini-primitive now covered upstream.
- Modify `tests/unit/vm/settings/test_s3_connection_form_vm.py`: keep behavior coverage and add post-construction validator/event coverage.
- Modify `tests/unit/vm/test_round3_compliance.py`: assert the facade composes VMx `FormVM` directly.
- Delete `tests/unit/vm/_composition/test_validating_form_vm.py`: removes tests for deleted aws-tui primitive.
- Modify `src/aws_tui/ui/widgets/settings/connection_form.py`: update comments/docstrings that mention `ValidatingFormVM`.
- Modify `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`: mark the `FormVM` replacement as implemented and add the replacement savings ledger.

---

### 1.3.1. Task 1: Pin Facade Behavior And Architecture

**Files:**
- Modify: `tests/unit/vm/settings/test_s3_connection_form_vm.py`
- Modify: `tests/unit/vm/test_round3_compliance.py`

**Interfaces:**
- Consumes: `S3ConnectionFormVM(initial: S3CompatForm, *, persister: S3FormPersister, strict: bool = True)`
- Produces: tests proving the facade composes `vmx.FormVM` and preserves dynamic validator/event behavior.

- [ ] **Step 1: Write failing architecture test**

Update `tests/unit/vm/test_round3_compliance.py`:

```python
def test_s3_connection_form_vm_composes_vmx_form_vm_internally() -> None:
    from vmx import FormVM

    from aws_tui.vm.chrome.first_run_vm import S3CompatForm
    from aws_tui.vm.settings.s3_connection_form_vm import S3ConnectionFormVM

    async def _persist(_m: S3CompatForm) -> None:
        pass

    blank = S3CompatForm(
        name="",
        endpoint_url="",
        region="",
        access_key_id="",
        secret_access_key="",
    )
    vm = S3ConnectionFormVM(initial=blank, persister=_persist)
    assert hasattr(vm, "_inner")
    assert isinstance(vm._inner, FormVM)
    assert not any("inner" in name for name in dir(vm) if not name.startswith("_"))
    vm.dispose()
```

- [ ] **Step 2: Write dynamic validator behavior test**

Add to `tests/unit/vm/settings/test_s3_connection_form_vm.py`:

```python
def test_extra_field_validator_added_after_construction_revalidates_and_emits() -> None:
    f = S3ConnectionFormVM(initial=_valid(), persister=_ok_persister, strict=False)
    payloads: list[dict[str, str]] = []
    sub = f.on_errors_changed.subscribe(on_next=payloads.append)
    try:
        f.add_field_validator(
            "name",
            lambda form: "name must start with team-" if not form.name.startswith("team-") else None,
        )
        assert f.errors == {"name": "name must start with team-"}
        assert payloads == [{"name": "name must start with team-"}]
        assert f.can_submit is False

        f.set_field("name", "team-minio")

        assert f.errors == {}
        assert payloads == [{"name": "name must start with team-"}, {}]
        assert f.can_submit is True
    finally:
        sub.dispose()
        f.dispose()
```

- [ ] **Step 3: Verify red**

Run:

```bash
uv run pytest tests/unit/vm/test_round3_compliance.py::test_s3_connection_form_vm_composes_vmx_form_vm_internally tests/unit/vm/settings/test_s3_connection_form_vm.py::test_extra_field_validator_added_after_construction_revalidates_and_emits -q
```

Expected: the architecture test fails because `_inner` is still `ValidatingFormVM`. The dynamic validator test may already pass; it exists to preserve behavior during the replacement.

---

### 1.3.2. Task 2: Replace The Internal Primitive

**Files:**
- Modify: `src/aws_tui/vm/settings/s3_connection_form_vm.py`

**Interfaces:**
- Consumes: VMx `FormVM[S3CompatForm]`
- Produces: unchanged public `S3ConnectionFormVM` facade.

- [ ] **Step 1: Replace imports and local validator storage**

Use:

```python
from vmx import FormVM, RelayCommand
```

Add instance fields:

```python
self._field_validators: dict[str, list[Callable[[S3CompatForm], str | None]]] = {}
self._model_validators: list[Callable[[S3CompatForm], dict[str, str]]] = []
self._inner: FormVM[S3CompatForm] = FormVM(
    initial=initial,
    persister=persister,
    strict=strict,
    validators={field: self._validate_field(field) for field in self._REQUIRED_FIELDS},
    model_validator=self._validate_model,
)
```

- [ ] **Step 2: Add aggregation helpers**

Add:

```python
_REQUIRED_FIELDS = (
    "name",
    "endpoint_url",
    "region",
    "access_key_id",
    "secret_access_key",
)

def _validate_field(self, field: str) -> Callable[[S3CompatForm], str | None]:
    def _validator(form: S3CompatForm) -> str | None:
        for validator in self._field_validators.get(field, ()):
            message = validator(form)
            if message is not None:
                return message
        return None

    return _validator

def _validate_model(self, form: S3CompatForm) -> dict[str, str]:
    errors: dict[str, str] = {}
    for validator in self._model_validators:
        errors.update(validator(form))
    return errors

def _revalidate(self) -> None:
    self._inner.set_model(self._inner.model)
```

- [ ] **Step 3: Keep registration API**

Update `add_field_validator()` and `add_model_validator()` so they append to the facade-owned lists and call `_revalidate()`.

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/unit/vm/settings/test_s3_connection_form_vm.py tests/unit/vm/test_round3_compliance.py -q
```

Expected: all selected tests pass.

---

### 1.3.3. Task 3: Remove Obsolete Mini-Primitive

**Files:**
- Delete: `src/aws_tui/vm/_composition/validating_form_vm.py`
- Delete: `tests/unit/vm/_composition/test_validating_form_vm.py`
- Modify: `src/aws_tui/vm/_composition/__init__.py`
- Modify: `src/aws_tui/ui/widgets/settings/connection_form.py`

**Interfaces:**
- Consumes: `S3ConnectionFormVM` now owns validation aggregation.
- Produces: no remaining imports of `ValidatingFormVM`.

- [ ] **Step 1: Remove exports**

`src/aws_tui/vm/_composition/__init__.py` should only export the remaining local composition primitives:

```python
from aws_tui.vm._composition.filtered_composite_vm import FilteredCompositeVM

__all__ = ["FilteredCompositeVM"]
```

- [ ] **Step 2: Delete obsolete files**

Remove:

```bash
git rm src/aws_tui/vm/_composition/validating_form_vm.py tests/unit/vm/_composition/test_validating_form_vm.py
```

- [ ] **Step 3: Update comments**

Replace comments/docstrings in `src/aws_tui/ui/widgets/settings/connection_form.py` that mention `ValidatingFormVM` with `S3ConnectionFormVM` or VMx `FormVM` language.

- [ ] **Step 4: Verify no stale imports**

Run:

```bash
rg -n "ValidatingFormVM|FieldValidator|ModelValidator" src tests
```

Expected: no matches.

---

### 1.3.4. Task 4: Record Metrics And Verify

**Files:**
- Modify: `docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md`

**Interfaces:**
- Consumes: final git diff for the replacement.
- Produces: filled replacement ledger and LOC/test coverage accounting for `FormVM` validators.

- [ ] **Step 1: Capture LOC delta**

Run:

```bash
git diff --numstat HEAD -- src/aws_tui/vm src/aws_tui/ui tests
```

Use the output to record VM LOC saved, view LOC saved, test LOC delta, and implementation LOC saved.

- [ ] **Step 2: Update the audit report**

Add a ledger entry for `vmx31-formvm-s3-settings`, mark `FormVM` validators implemented in the aggregate table, and note the exact verification commands.

- [ ] **Step 3: Run focused verification**

Run:

```bash
uv run pytest tests/unit/vm/settings/test_s3_connection_form_vm.py tests/unit/vm/test_round3_compliance.py tests/unit/ui/test_connection_form_inline.py -q
uv run pytest tests/unit/vm tests/unit/ui -q
uv run ruff check
uv run mypy
```

- [ ] **Step 4: Commit**

```bash
git add src/aws_tui/vm/settings/s3_connection_form_vm.py src/aws_tui/vm/_composition/__init__.py src/aws_tui/ui/widgets/settings/connection_form.py tests/unit/vm/settings/test_s3_connection_form_vm.py tests/unit/vm/test_round3_compliance.py docs/superpowers/specs/2026-07-02-vmx-3-1-adoption-audit.md docs/superpowers/plans/2026-07-02-vmx-formvm-s3-settings.md
git add -u src/aws_tui/vm/_composition/validating_form_vm.py tests/unit/vm/_composition/test_validating_form_vm.py
git commit -m "refactor: adopt VMx FormVM for S3 settings"
```
