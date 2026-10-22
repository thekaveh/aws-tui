"""Navigation export/install is an allowlist, never a selection-store dump."""

import pytest

from aws_tui.domain.navigation_workspace import NavigationTarget, WorkspaceError
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore


def test_exports_known_scopes_and_maps_only_navigation_selectors():
    store = ServiceSelectionStore()
    scopes = [
        SelectionScope("athena", "analytics", "us-east-1"),
        SelectionScope("glue", "analytics", "us-east-1"),
        SelectionScope("emr-serverless", "compute", "eu-west-1"),
    ]
    for scope, values in zip(
        scopes,
        (
            {
                "workgroup": "reports",
                "catalog": "AwsDataCatalog",
                "database": "lake",
                "active_view": "results",
                "history_execution_id": "query-secret",
                "sql": "SELECT forbidden",
            },
            {
                "database_name": "lake",
                "table_name": "events",
                "active_view": "jobs",
                "job_name": "out-of-scope",
                "crawler_state": "RUNNING",
            },
            {"application_id": "app-123", "job_run_id": "not-navigation"},
        ),
        strict=True,
    ):
        for key, value in values.items():
            store.set(scope, key, value)
    targets = tuple(
        target
        for service in ("athena", "glue", "emr-serverless")
        for target in store.navigation_targets(service)
    )
    assert [dict(target.selectors) for target in targets] == [
        {"workgroup": "reports", "catalog": "AwsDataCatalog", "database": "lake"},
        {"database": "lake", "table": "events"},
        {"application": "app-123"},
    ]
    assert not store.navigation_targets("s3")
    assert not store.navigation_targets("settings")
    assert store.get(scopes[0], "sql") == "SELECT forbidden"


def test_install_replaces_only_requested_navigation_scope_preserving_unrelated_values():
    store = ServiceSelectionStore()
    chosen = SelectionScope("athena", "analytics", "us-east-1")
    other = SelectionScope("athena", "other", "eu-west-1")
    for key, value in {
        "workgroup": "old",
        "catalog": "old-catalog",
        "database": "old-db",
        "history_execution_id": "keep-current-history",
        "active_view": "history",
    }.items():
        store.set(chosen, key, value)
    store.set(other, "workgroup", "keep-other")
    target = NavigationTarget("athena", "analytics", "us-east-1", (("workgroup", "new"),))
    store.install_navigation_targets((target,))
    assert store.get(chosen, "workgroup") == "new"
    assert store.get(chosen, "catalog") is None
    assert store.get(chosen, "database") is None
    assert store.get(chosen, "history_execution_id") == "keep-current-history"
    assert store.get(chosen, "active_view") == "history"
    assert store.get(other, "workgroup") == "keep-other"


def test_invalid_or_duplicate_target_batch_does_not_partially_install():
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "analytics", "us-east-1")
    store.set(scope, "database_name", "keep")
    target = NavigationTarget("glue", "analytics", "us-east-1", (("database", "replacement"),))
    for targets in ((target, target), (target, object())):
        with pytest.raises(WorkspaceError):
            store.install_navigation_targets(targets)
        assert store.get(scope, "database_name") == "keep"


def test_known_scope_without_selected_resource_is_not_filled_with_invented_defaults():
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "analytics", "us-east-1")
    store.set(scope, "active_view", "jobs")
    targets = store.navigation_targets("glue")
    assert targets == (NavigationTarget("glue", "analytics", "us-east-1"),)
    assert not store.navigation_targets("athena")


def test_explicit_empty_scope_survives_clone_commit_and_cleared_selections():
    store = ServiceSelectionStore()
    scope = SelectionScope("glue", "analytics", "us-east-1")
    target = NavigationTarget("glue", "analytics", "us-east-1")
    store.install_navigation_targets((target,))
    assert store.navigation_targets("glue") == (target,)
    candidate = store.clone()
    store.replace_scope_from(candidate, scope)
    assert store.navigation_targets("glue") == (target,)
    store.set(scope, "database_name", "lake")
    store.discard(scope, "database_name")
    assert store.navigation_targets("glue") == (target,)


def test_adoption_is_one_way_and_cannot_retarget_or_form_cycle():
    live = ServiceSelectionStore()
    seed = ServiceSelectionStore()
    other = ServiceSelectionStore()
    target = NavigationTarget("glue", "analytics", "us-east-1", (("database", "lake"),))
    seed.install_navigation_targets((target,))
    with pytest.raises(WorkspaceError):
        seed.adopt_navigation_into(seed, target)
    assert live.navigation_targets("glue") == ()
    seed.adopt_navigation_into(live, target)
    for operation in (
        lambda: seed.adopt_navigation_into(other, target),
        lambda: live.adopt_navigation_into(seed, target),
        lambda: seed.adopt_navigation_into(live, NavigationTarget("glue", "other", "us-east-1")),
    ):
        with pytest.raises(WorkspaceError):
            operation()
    assert live.navigation_targets("glue") == (target,)
    assert other.navigation_targets("glue") == ()


def test_recovery_adoption_preserves_full_scope_and_binds_future_values_once():
    live = ServiceSelectionStore()
    scope = SelectionScope("glue", "dev", "us-east-1")
    other = SelectionScope("athena", "dev", "us-east-1")
    live.set(scope, "active_view", "jobs")
    live.set(scope, "database_name", "old")
    candidate = live.clone()
    candidate.set(scope, "database_name", "recovered")
    live.set(other, "workgroup", "newer-unrelated")
    candidate.adopt_recovery_scope_into(live, scope)
    assert live.get(scope, "active_view") == "jobs"
    assert candidate.get(other, "workgroup") == "newer-unrelated"
    candidate.set(scope, "database_name", "chosen-after-recovery")
    candidate.adopt_recovery_scope_into(live, scope)
    assert live.get(scope, "database_name") == "chosen-after-recovery"
    for destination, attempted_scope in (
        (ServiceSelectionStore(), scope),
        (live, other),
        (candidate, scope),
    ):
        with pytest.raises(WorkspaceError):
            candidate.adopt_recovery_scope_into(destination, attempted_scope)
    with pytest.raises(WorkspaceError):
        live.adopt_recovery_scope_into(candidate, scope)


def test_retained_seed_references_read_live_store_only_after_adoption():
    live, seed = ServiceSelectionStore(), ServiceSelectionStore()
    athena = SelectionScope("athena", "analytics", "us-east-1")
    glue = SelectionScope("glue", "analytics", "us-east-1")
    live.set(athena, "workgroup", "remembered")
    live.set(glue, "active_view", "jobs")
    target = NavigationTarget("glue", "analytics", "us-east-1", (("database", "lake"),))
    seed.install_navigation_targets((target,))
    assert seed.get(athena, "workgroup") is None
    assert seed.get(glue, "active_view") is None
    seed.adopt_navigation_into(live, target)
    assert seed.get(athena, "workgroup") == "remembered"
    assert seed.get(glue, "active_view") == "jobs"
    seed.set(glue, "database_name", "new")
    assert live.get(glue, "database_name") == "new"
    cloned = seed.clone()
    cloned.set(glue, "database_name", "cloned")
    assert live.get(glue, "database_name") == "new"
    copied = ServiceSelectionStore()
    copied.replace_scope_from(seed, glue)
    assert copied.get(glue, "database_name") == "new"
    seed.replace_scope_from(cloned, glue)
    assert live.get(glue, "database_name") == "cloned"
    seed.replace_scope_from(live, glue)
    assert live.get(glue, "database_name") == "cloned"


def test_unresolved_seed_cannot_publish_a_different_default():
    live, seed = ServiceSelectionStore(), ServiceSelectionStore()
    target = NavigationTarget("glue", "analytics", "us-east-1", (("database", "removed"),))
    seed.install_navigation_targets(
        (NavigationTarget("glue", "analytics", "us-east-1", (("database", "first"),)),)
    )
    with pytest.raises(WorkspaceError):
        seed.adopt_navigation_into(live, target)
    assert live.navigation_targets("glue") == ()
