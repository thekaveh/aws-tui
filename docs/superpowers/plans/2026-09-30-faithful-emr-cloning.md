# Faithful EMR Cloning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete every acceptance criterion of #238 without silently reducing a source job's settings.

**Architecture:** Extend the existing detail record, explicit request boundary, clone VM and demo client. A bounded edit/review modal displays the immutable source identity and exact request differences before submit. Unknown or unsupported settings are disclosed or rejected as specified in the design.

**Tech Stack:** Python 3.11–3.13, botocore/aioboto3, VMx, Textual, pytest and syrupy SVG snapshots.

## 1. Global Constraints

- Preserve absence separately from an explicit empty structure or zero.
- Only BATCH and STREAMING modes are accepted when present.
- No live AWS mutation, generic submit form, Hive authoring or cancellation API.
- Preserve source values exactly, including empty arguments and Spark whitespace.
- Notifications and errors must not expose request values, policies or credentials.
- Retain all eight acceptance criteria in the design's evidence matrix.
- Reuse the clean primary checkout on `codex/issue-238-faithful-emr-cloning`.
- Run tests with AWS config/credentials isolated, using stubs and demo clients.

## 2. Task 1: Preserve source details and explicit request settings

**Files:** `src/aws_tui/domain/emr_serverless.py`, new `src/aws_tui/domain/emr_job_request.py`, `tests/unit/domain/test_emr_serverless.py`.

**Interfaces:** Add detail fields `job_driver`, `configuration_overrides`, `execution_timeout_minutes`, `retry_policy`, `mode`, `execution_iam_policy`, `tags`, and `source_application_settings`. Extend the protocol/client `start_job_run` with optional `configuration_overrides`, `execution_timeout_minutes`, `retry_policy`, `mode`, `execution_iam_policy`, and `tags`. The request helper builds only StartJobRun keys and validates optional blocks against SDK shapes before any call.

- [x] Add a stubbed GetJobRun test containing every supported configuration block, both nested application and monitoring overrides, explicit timeout zero, mode, IAM policy and tags. Assert exact preservation, independent nested copies, and driver identity. Add missing/empty/mixed/Hive driver cases.
- [x] Run the new tests and record their expected failures before production changes.
- [x] Extend the record and extraction with deep copies. Keep `s3_monitoring_log_uri` as a projection, and suppress configuration/arguments from repr. Source application settings carry available release/network/image/worker fields for the review only.
- [x] Add exact request contract tests for BATCH and STREAMING, omitted versus zero/empty settings, source-only fields, unsupported mode/nested setting, and exact argument/parameter whitespace.
- [x] Observe request tests fail; implement the explicit helper and client forwarding. Optional fields are included on `is not None`, not truthiness. Do not forward read-only fields or strip nested keys. Parameter validation emits a field-level reason without raw values.
- [x] Run the entire domain module, lint and type checks. Commit the coherent domain/request change.

Exact contract core:

```python
assert kwargs == {
    "applicationId": "00abc",
    "executionRoleArn": "arn:aws:iam::123456789012:role/EmrJobRole",
    "jobDriver": {"sparkSubmit": {
        "entryPoint": "s3://b/job.py",
        "entryPointArguments": ["", "a\nb", "  padded  "],
        "sparkSubmitParameters": "  --conf k=v  ",
    }},
    "clientToken": "clone-intent",
    "mode": "STREAMING",
    "executionTimeoutMinutes": 0,
    "configurationOverrides": overrides,
    "retryPolicy": {"maxFailedAttemptsPerHour": 2},
    "executionIamPolicy": {"policyArns": [policy_arn]},
    "tags": {"team": "analytics"},
}
```

## 3. Task 2: VM fidelity, validation, review state and demo parity

**Files:** `src/aws_tui/vm/emr_serverless/clone_vm.py`, `src/aws_tui/demo/in_memory_emr.py`, `tests/unit/vm/emr_serverless/test_clone_vm.py`, `tests/unit/domain/test_emr_serverless.py`.

**Interfaces:** VM construction accepts source context, retains a private source snapshot, exposes copied advanced settings, and validates unsupported driver/settings in `is_valid` and `submit`. `apply_settings(settings: dict[str, Any])` atomically replaces the optional wire-named settings; `settings` returns a deep copy. Existing `apply_field` and token properties remain.

- [x] Extend the token regression tests to all six optional setting fields, nested edits, equal reapplication, omission and empty values. Assert two ambiguous attempts carry identical tokens and payloads.
- [x] Add failing tests for source immutability, settings aliasing, unsupported-driver direct submit, disposed/cancelled VM submit, and source/target comparison content.
- [x] Implement the VM changes. Intent equality includes all settings; copies prevent mutation without token rotation. Submit captures the intended payload/token once and checks validity before calling the client. Use value-free observable notifications.
- [x] Extend demo submission with the same optional settings and materialize them in JobRunDetail. Preserve token replay behavior and existing call tuple offsets while recording new fields after them. Derive S3 monitoring from carried overrides.
- [x] Verify success, ambiguous retry, edit rotation, failure/cancel/disposal and exact demo round-trip. Commit the VM/demo change after relevant tests and hooks pass.

Core token regression:

```python
before = vm.client_token
vm.apply_settings({"mode": "STREAMING", "executionTimeoutMinutes": 0})
assert vm.client_token != before
same_intent = vm.client_token
vm.apply_settings({"executionTimeoutMinutes": 0, "mode": "STREAMING"})
assert vm.client_token == same_intent
settings = vm.settings
settings["mode"] = "BATCH"
assert vm.settings["mode"] == "STREAMING"
```

## 4. Task 3: Bounded edit/review workflow and sensitive errors

**Files:** `src/aws_tui/ui/widgets/emr_serverless/clone_modal.py`, `src/aws_tui/ui/widgets/emr_serverless/page.py`, `tests/unit/ui/emr_serverless/test_clone_modal.py`, `tests/integration/test_emr_page.py`.

**Interfaces:** The page passes the exact `ServiceSourceContext` to the clone VM. Unsupported source drivers produce an advisory and no form. The modal uses edit/review stages, fixed reachable footer, scrollable content, JSON arguments and advanced settings, and a reviewed-intent guard.

- [x] Write pilot regressions showing Enter on edit opens review without calling StartJobRun, changed values/source identity appear in review, Back preserves values, subsequent edits require fresh review, and double submit makes one call.
- [x] Add empty/newline argument round-trip, invalid JSON, unsupported settings and compact 80x24 keyboard/scroll tests. Verify cancellation and stale source handling without a live client.
- [x] Implement edit/review composition using existing widgets, markup disabled for external values. Display source id/profile/region/application/role; show source and target values, unchanged/changed states, inherited application settings and unknown read-only outputs/defaults. Avoid claiming inherited equality.
- [x] On parse/validation/provider/unexpected errors, retain the form and show category-specific recovery guidance without echoed request values or unsafe exception chains. Preserve retry token when the request did not change.
- [x] Capture emitted logs and diagnostic formatting with distinctive values in arguments, Spark parameters, policies and credentials. Test success and each error path; inspect both messages and extras/traceback content.
- [x] Run affected UI/integration and privacy tests, then commit the workflow after review.

Pilot submission invariant:

```python
await pilot.press("enter")
assert not fake_start.called
assert modal.reviewing
assert "Source run" in review_text
assert "Inherited" in review_text
assert "unknown" in review_text.lower()
await modal.action_submit()
assert fake_start.await_count == 1
```

## 5. Task 4: Rendered evidence, docs, review and full delivery

**Files:** `tests/snapshot/apps/emr_clone_modal.py`, `tests/snapshot/test_emr_clone_modal.py`, affected EMR golden files, `docs/services/emr-serverless.md`, `docs/contract-ledger.md`.

- [x] Seed rich source configuration and identity in the snapshot harness. Keep all existing theme coverage; add changed review and inherited/unknown review states at 80x24 and 120x40 in Carbon/GitHub Light.
- [x] Replace disk-only content guards with live pilot screenshot guards. Assert rendered identity, changed old/new values, inheritance/unknown labels and reachable action footer. Scroll when inspecting the full review.
- [x] Generate only intended golden changes, inspect rendered artifacts visually, and verify unchanged snapshots stay unchanged.
- [x] Document exact preservation, JSON editing, two-stage review, unsupported driver/settings refusal, inherited/unknown application defaults, privacy and token retry rules. Update the consumed contract ledger to the installed SDK contract.
- [ ] Audit every AC against tests and actual behavior. Run lint/type/architecture/docs/build, relevant local suites and independent code review; resolve findings.
- [ ] Push, create/attach PR into develop using `Refs #238`, pass final required CI and merge normally. Create/attach checked develop-to-main promotion and any history back-merge. Verify final post-merge CI and source parity.
- [ ] Delete only the completed #238 local/remote feature branch, preserve unrelated work, publish evidence/limitations, close #238 and mark Done. Record promotion evidence and only then begin #244.

## 6. Verification commands

Use this prefix for test runs:

```sh
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true .venv/bin/python -m pytest
```

Target suites: `tests/unit/domain/test_emr_serverless.py`, `tests/unit/vm/emr_serverless/test_clone_vm.py`, `tests/unit/ui/emr_serverless/test_clone_modal.py`, `tests/integration/test_emr_page.py`, `tests/snapshot/test_emr_clone_modal.py`. Expected final result: all pass; failing red runs are retained as reproduction evidence, never relabeled successful. Run the repository's required hooks and CI unchanged, without reruns that conceal failures.

## 7. Task 4 delivery follow-up: retain focus selected after EMR mount

1. Capture the real mount callback in a deterministic full-app test, release it
   after explicit focus, and observe the existing picker/focus assertion fail.
2. Restrict `_maybe_focus_left` to an active screen with no focused widget,
   using `screen.focused` to preserve loading-widget focus.
3. Verify source/application triggers and overlays, details/logs/navigation,
   loading focus, and no-focus default behavior; run affected focus, compact,
   EMR integration and snapshots plus normal hooks.
4. Update PR312 and its evidence, then continue the existing protected delivery
   sequence. Do not waive CI failures or close #238 before promotion validation.

## 8. Task 4 delivery follow-up: reject detached pane refreshes

1. Hold a real mounted filter refresh and release it through the screen after
   removal and a later navigation; verify the original detached-render assertion
   fails while the ordinary-order control passes.
2. Clear `_body_refresh_pending` and guard `_refresh_all` with `is_attached`.
3. Run both regression cases and affected pane/VM/compact/snapshot suites,
   source and selected-test typing, normal hooks and strict documentation checks.
4. Update PR312 and continue the existing protected delivery gates on its new
   head. Preserve failed Windows evidence for #283; do not rerun the failed run.
