from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.service_source_vm import (
    SelectionScope,
    ServiceSelectionStore,
    ServiceSourceContext,
)


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
    assert source.label == "analytics-prod \u00b7 prod-sso \u00b7 us-west-2"


def test_source_context_does_not_repeat_matching_profile_name() -> None:
    connection = Connection(
        name="dev",
        kind="aws",
        profile="dev",
        region="us-east-1",
        source="auto-aws-profile",
    )
    assert ServiceSourceContext.from_connection(connection).label == "dev \u00b7 us-east-1"


def test_selection_store_is_scoped_by_service_connection_and_region() -> None:
    store = ServiceSelectionStore()
    dev = SelectionScope("emr-serverless", "dev", "us-east-1")
    prod = SelectionScope("emr-serverless", "prod", "us-east-1")
    store.set(dev, "application_id", "dev-app")
    store.set(prod, "application_id", "prod-app")
    assert store.get(dev, "application_id") == "dev-app"
    assert store.get(prod, "application_id") == "prod-app"


def test_selection_store_clone_commits_only_requested_scope() -> None:
    scope = SelectionScope("glue", "dev", "us-east-1")
    unrelated = SelectionScope("athena", "dev", "us-east-1")
    store = ServiceSelectionStore()
    store.set(scope, "active_view", "catalog")

    candidate = store.clone()
    candidate.set(scope, "active_view", "jobs")
    candidate.set(scope, "job_name", "nightly")
    store.set(unrelated, "workgroup", "updated-after-clone")

    assert store.get(scope, "active_view") == "catalog"
    assert store.get(scope, "job_name") is None

    store.replace_scope_from(candidate, scope)

    assert store.get(scope, "active_view") == "jobs"
    assert store.get(scope, "job_name") == "nightly"
    assert store.get(unrelated, "workgroup") == "updated-after-clone"
