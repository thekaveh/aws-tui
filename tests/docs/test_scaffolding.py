"""Smoke tests and public-documentation contract checks."""

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy

REPO_ROOT = Path(__file__).parents[2]

ATHENA_BOTO_OPERATIONS = (
    "list_work_groups",
    "get_work_group",
    "list_data_catalogs",
    "list_databases",
    "list_table_metadata",
    "list_query_executions",
    "get_query_execution",
    "get_query_runtime_statistics",
    "start_query_execution",
    "stop_query_execution",
    "get_query_results",
    "list_named_queries",
    "batch_get_named_query",
    "list_prepared_statements",
    "get_prepared_statement",
)

ATHENA_IAM_ACTIONS = {
    "".join(part.title() for part in operation.split("_")) for operation in ATHENA_BOTO_OPERATIONS
}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _squash(text: str) -> str:
    return " ".join(line.removeprefix("> ").strip() for line in text.splitlines() if line.strip())


def _fenced_block_after(text: str, marker: str, language: str) -> str:
    marker_pattern = r"\s+".join(re.escape(part) for part in marker.split())
    pattern = rf"{marker_pattern}.*?```{language}\n(.*?)\n```"
    match = re.search(pattern, text, flags=re.DOTALL)
    assert match is not None, f"missing {language} block after {marker!r}"
    return match.group(1)


def _sql_examples_after(text: str, marker: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in _fenced_block_after(text, marker, "sql").splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )


def test_scripts_docs_package_imports():
    import scripts.docs  # noqa: F401


def test_pyyaml_available():
    import yaml

    assert yaml.safe_load("a: 1") == {"a": 1}


def test_public_docs_cover_athena_read_only_contract() -> None:
    """Keep standalone Athena behavior discoverable on every public surface."""
    readme = _read("README.md")
    index = _read("docs/index.md")
    architecture = _read("docs/architecture.md")
    services = _read("docs/adding-a-service.md")
    connections = _read("docs/connections.md")
    cookbook = _read("docs/cookbook.md")
    ledger = _read("docs/contract-ledger.md")
    keybindings = _read("docs/keybindings.md")
    releasing = _read("docs/RELEASING.md")
    changelog = _read("CHANGELOG.md")

    assert "Athena" in readme
    assert "standalone" in readme
    assert "Amazon Athena read-only query console" in index
    assert "AthenaPageVM" in architecture
    assert "AthenaService" in services
    assert "Athena is AWS-only" in connections
    assert "A root set operation may use `SELECT` or `VALUES` operands" in _squash(cookbook)
    assert "SHOW CREATE TABLE" in cookbook
    assert "EXPLAIN ANALYZE" in cookbook
    assert "result artifacts" in cookbook
    assert "bytes scanned" in cookbook
    assert "lakeformation:GetDataAccess" in cookbook
    assert "DATA_LOCATION_ACCESS" in cookbook
    assert "EnforceWorkGroupConfiguration" in cookbook
    assert "get_prepared_statement" in ledger
    assert "athena.execute" in keybindings
    assert "athena.open_result_location" in keybindings
    assert "Athena" in releasing
    assert "Athena" in changelog
    assert (
        "read read"
        not in _squash(
            "\n".join(
                (
                    readme,
                    index,
                    architecture,
                    services,
                    connections,
                    cookbook,
                    ledger,
                    keybindings,
                    releasing,
                    changelog,
                )
            )
        ).lower()
    )


def test_athena_operation_ledger_and_minimum_iam_are_exact() -> None:
    ledger = _read("docs/contract-ledger.md")
    cookbook = _read("docs/cookbook.md")
    client_source = _read("src/aws_tui/domain/athena.py")

    operation_block = _fenced_block_after(
        ledger,
        "Exact boto operation ledger (15)",
        "text",
    )
    assert tuple(operation_block.splitlines()) == ATHENA_BOTO_OPERATIONS
    assert set(re.findall(r"await client\.([a-z_]+)", client_source)) == set(ATHENA_BOTO_OPERATIONS)

    policy = json.loads(
        _fenced_block_after(
            cookbook,
            "minimum Athena API policy used by aws-tui",
            "json",
        )
    )
    actions = {
        action.removeprefix("athena:")
        for statement in policy["Statement"]
        for action in statement["Action"]
    }
    assert actions == ATHENA_IAM_ACTIONS


def test_athena_permissions_and_output_modes_are_pinned_to_aws_contracts() -> None:
    cookbook = _read("docs/cookbook.md")
    ledger = _read("docs/contract-ledger.md")
    client_source = _read("src/aws_tui/domain/athena.py")
    query_vm_source = _read("src/aws_tui/vm/athena/query_vm.py")
    normalized = _squash(cookbook)

    required_facts = (
        "`lakeformation:GetDataAccess`",
        "`DATA_LOCATION_ACCESS` permits creating or altering Data Catalog resources",
        "`s3:GetObject` on every underlying source-data object",
        "`s3:ListBucketMultipartUploads`",
        "`s3:AbortMultipartUpload`",
        "`s3:ListMultipartUploadParts`",
        "Source data encrypted with a customer managed KMS key requires `kms:Decrypt`",
        "customer-managed S3 results, require `kms:GenerateDataKey` and `kms:Decrypt`",
        "Managed results do not create a customer S3 result artifact",
        "available through Athena for 24 hours",
        "managed results do not support result reuse",
        "aws-tui continues to page rows with `GetQueryResults`",
        "**Open Athena result in S3** remains on Athena",
        "workgroup-enforced customer S3 output",
    )
    for fact in required_facts:
        assert fact in normalized

    for fact in (
        "`AthenaClient.start_query(...)` accepts an optional `output_location`",
        "adds `ResultConfiguration.OutputLocation` only when its caller supplies that value",
        "The shipped `AthenaQueryVM` query runner does not supply `output_location`",
        "relies on the selected workgroup's enforced customer S3 or Athena managed-results configuration",
    ):
        assert fact in _squash(ledger)

    assert "if output_location is not None:" in client_source
    assert 'kwargs["ResultConfiguration"]' in client_source
    query_vm_tree = ast.parse(query_vm_source)
    start_calls = [
        node
        for node in ast.walk(query_vm_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "start_query"
    ]
    assert len(start_calls) == 1
    assert {keyword.arg for keyword in start_calls[0].keywords} == {"request_token"}


def test_athena_sql_grammar_matches_policy_tests() -> None:
    cookbook = _read("docs/cookbook.md")
    normalized = _squash(cookbook)
    policy = ReadOnlySqlPolicy()

    required_contracts = (
        "`SHOW DATABASES|SCHEMAS [IN catalog] [LIKE 'pattern']`",
        "`SHOW TABLES [IN [catalog.]database] ['pattern']`",
        "`SHOW COLUMNS FROM|IN table`",
        "`SHOW PARTITIONS table`",
        "`SHOW TBLPROPERTIES table [('property')]`",
        "`SHOW VIEWS [IN [catalog.]database] [LIKE 'pattern']`",
        "one- or two-part table name",
        "literal string or number equality",
        "`'$elem$'`, `'$key$'`, or `'$value$'`",
        "`FORMAT GRAPHVIZ|JSON|TEXT`",
        "`TYPE DISTRIBUTED|IO|LOGICAL|VALIDATE`",
        "at most once each",
        "A root `SELECT` may contain a `VALUES` relation",
        "A root set operation may use `SELECT` or `VALUES` operands",
        "A standalone `VALUES` root is rejected",
    )
    for contract in required_contracts:
        assert contract in normalized

    expected_accepted = (
        "SELECT orderkey FROM analytics.orders LIMIT 10",
        "SELECT * FROM (VALUES (1), (2)) AS samples(value)",
        "SELECT 1 UNION ALL VALUES (2)",
        "VALUES (1) INTERSECT SELECT 1",
        "VALUES (1) EXCEPT VALUES (2)",
        "SHOW DATABASES IN AwsDataCatalog LIKE 'analytics*'",
        "SHOW SCHEMAS",
        "SHOW TABLES IN AwsDataCatalog.analytics '*logs'",
        "SHOW COLUMNS FROM AwsDataCatalog.analytics.orders",
        "SHOW COLUMNS FROM orders FROM AwsDataCatalog.analytics",
        "SHOW PARTITIONS AwsDataCatalog.analytics.orders",
        "SHOW TBLPROPERTIES analytics.orders('comment')",
        "SHOW VIEWS IN AwsDataCatalog.analytics LIKE 'orders*'",
        "DESCRIBE FORMATTED analytics.orders",
        "DESCRIBE analytics.orders PARTITION (`event date` = '2026-07-25', shard = 7) payload.'$elem$'.field",
        "EXPLAIN (TYPE VALIDATE, FORMAT GRAPHVIZ) SELECT 1",
        "EXPLAIN (TYPE IO, FORMAT TEXT) DESCRIBE analytics.orders payload.'$key$'.'$value$'",
    )
    expected_rejected = (
        "VALUES (1), (2)",
        "SELECT 1; SELECT 2",
        "SHOW CREATE TABLE analytics.orders",
        "SHOW COLUMNS",
        "SHOW TABLES IN analytics LIKE 'orders*'",
        "DESCRIBE AwsDataCatalog.analytics.orders",
        "DESCRIBE orders PARTITION (shard > 1)",
        "DESCRIBE orders payload.'$unknown$'",
        "EXPLAIN ANALYZE SELECT 1",
        "EXPLAIN (FORMAT YAML) SELECT 1",
        "EXPLAIN (TYPE IO, TYPE LOGICAL) SELECT 1",
        "CREATE TABLE copy AS SELECT * FROM analytics.orders",
        "INSERT INTO archive SELECT * FROM analytics.orders",
        "UNLOAD (SELECT 1) TO 's3://example/results/'",
        "CALL system.runtime.kill_query()",
    )
    accepted = _sql_examples_after(cookbook, "Accepted examples (one statement per line)")
    rejected = _sql_examples_after(cookbook, "Rejected examples (one statement per line)")
    assert accepted == expected_accepted
    assert rejected == expected_rejected

    for sql in accepted:
        assert policy.validate(sql) == sql
    for sql in rejected:
        with pytest.raises(QueryRejectedError):
            policy.validate(sql)


def test_athena_release_framing_and_smoke_are_minor_unreleased_work() -> None:
    readme = _read("README.md")
    changelog = _read("CHANGELOG.md")
    releasing = _read("docs/RELEASING.md")
    normalized_releasing = _squash(releasing)
    version = _read("src/aws_tui/version.py")

    assert "Athena is Unreleased feature work for the next v0.9.0 minor release" in _squash(readme)
    assert "Athena targets v0.9.0" in _squash(changelog)
    assert "not a v0.8.0 headline or a v0.8.1 patch candidate" in _squash(changelog)
    assert "will either ship as v0.8.1" not in changelog
    assert "Athena is minor-version feature work" in normalized_releasing
    for step in (
        "execute a valid bounded query",
        "reject an unsafe statement before dispatch",
        "cancel only the active query started by this page",
        "page results when a continuation is available",
        "Query and History surfaces",
        "exact S3 artifact",
    ):
        assert step in normalized_releasing
    assert '__version__ = "0.8.0"' in version


def test_athena_canonical_surfaces_and_diagram_match_current_tree() -> None:
    manifest = yaml.safe_load(_read("docs/manifest.yaml"))
    index = _read("docs/index.md")
    architecture = _read("docs/architecture.md")
    diagram = _read("docs/diagrams/architecture.html")
    public_docs = "\n".join(
        _read(path)
        for path in (
            "README.md",
            "docs/index.md",
            "docs/architecture.md",
            "docs/adding-a-service.md",
            "docs/cookbook.md",
        )
    )

    assert manifest["sections"][0]["source"] == "docs/index.md"
    assert manifest["diagrams"] == [
        {"id": "architecture", "master": "docs/diagrams/architecture.html"}
    ]
    for service in ("S3", "EMR Serverless", "AWS Glue", "Amazon Athena"):
        assert service in index
        assert service in diagram
    for layer in ("TEXTUAL VIEW", "VIEWMODEL / VMX", "SERVICE", "DOMAIN", "INFRA"):
        assert layer in diagram
    assert "await Athena shutdown" in diagram
    assert "then dispose outgoing VM" in diagram
    assert "exact connection + region" in diagram
    alt_match = re.search(r"!\[([^\]]+)\]\(diagrams/img/architecture\.png\)", architecture)
    assert alt_match is not None
    alt_text = alt_match.group(1)
    for phrase in (
        "five-layer architecture",
        "S3, EMR Serverless, AWS Glue, and Amazon Athena",
        "awaited Athena shutdown before disposal",
        "exact-profile S3 handoff",
    ):
        assert phrase in alt_text
    assert "Athena shutdown is awaited before disposal" in _squash(architecture)
    assert "Domain adapters perform the runtime AWS and filesystem I/O" in _squash(architecture)
    assert (
        "Infrastructure owns sessions, credentials, configuration, SDK client construction, and OS-backed stores"
        in _squash(architecture)
    )
    for inaccurate_claim in (
        "The only layer that touches the OS, AWS APIs, the file system, or the macOS keychain",
        "only layer touching external systems",
        "Infrastructure owns external I/O",
    ):
        assert inaccurate_claim not in f"{architecture}\n{diagram}"
    assert "Iceberg" not in diagram
    assert "Glue-to-Athena" not in diagram
    for premature_claim in (
        "Glue-to-Athena navigation inserts",
        "Iceberg metadata views show",
        "Query in Athena command opens",
    ):
        assert premature_claim not in public_docs


def test_glue_and_athena_palette_only_actions_are_not_default_bindings() -> None:
    keybindings = _read("docs/keybindings.md")
    keymap = _read("src/aws_tui/infra/keymap_store.py")

    for action in ("glue.open_s3_location", "athena.open_result_location"):
        assert f"`{action}` is palette-only" in keybindings
        assert f'"{action}"' not in keymap.partition("DEFAULT_BINDINGS")[2].partition("}")[0]
