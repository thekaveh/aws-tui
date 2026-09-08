from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import botocore.session
from botocore import xform_name

ROOT = Path(__file__).parents[2]

_MODELED_OPERATIONS = {
    "athena": {
        "ListWorkGroups",
        "GetWorkGroup",
        "ListDataCatalogs",
        "ListDatabases",
        "ListTableMetadata",
        "ListQueryExecutions",
        "GetQueryExecution",
        "BatchGetQueryExecution",
        "GetQueryRuntimeStatistics",
        "StartQueryExecution",
        "StopQueryExecution",
        "GetQueryResults",
        "ListNamedQueries",
        "BatchGetNamedQuery",
        "ListPreparedStatements",
        "GetPreparedStatement",
    },
    "emr-serverless": {"ListApplications", "ListJobRuns", "GetJobRun", "StartJobRun"},
    "glue": {
        "GetDatabases",
        "GetTables",
        "GetTable",
        "GetPartitions",
        "GetColumnStatisticsForTable",
        "GetJobs",
        "GetJobRuns",
        "GetCrawlers",
        "GetCrawler",
        "GetCrawlerMetrics",
        "GetTags",
    },
    "s3": {
        "CreateBucket",
        "HeadBucket",
        "ListBuckets",
        "ListObjectsV2",
        "HeadObject",
        "GetObject",
        "GetObjectTagging",
        "PutObject",
        "CopyObject",
        "DeleteObject",
        "DeleteObjects",
        "CreateMultipartUpload",
        "UploadPart",
        "UploadPartCopy",
        "CompleteMultipartUpload",
        "AbortMultipartUpload",
    },
    "sts": {"GetCallerIdentity"},
}

_OPERATION_SOURCES = {
    "athena": ("src/aws_tui/domain/athena.py", "src/aws_tui/domain/athena_runner.py"),
    "emr-serverless": ("src/aws_tui/domain/emr_serverless.py",),
    "glue": ("src/aws_tui/domain/glue.py",),
    "s3": ("src/aws_tui/domain/s3_fs.py", "scripts/test-services/s3/seed.py"),
    "sts": ("src/aws_tui/infra/aws_session.py", "src/aws_tui/domain/glue.py"),
}

_CONSUMED_INPUT_MEMBERS = {
    ("athena", "StartQueryExecution"): {
        "QueryString",
        "ClientRequestToken",
        "QueryExecutionContext",
        "WorkGroup",
        "ResultConfiguration",
    },
    ("emr-serverless", "ListApplications"): {"maxResults", "nextToken"},
    ("emr-serverless", "ListJobRuns"): {
        "applicationId",
        "maxResults",
        "nextToken",
        "states",
    },
    ("emr-serverless", "GetJobRun"): {"applicationId", "jobRunId"},
    ("glue", "GetTables"): {"CatalogId", "DatabaseName", "NextToken"},
    ("glue", "GetCrawlerMetrics"): {"CrawlerNameList"},
    ("s3", "ListObjectsV2"): {"Bucket", "Prefix", "Delimiter", "ContinuationToken"},
    ("s3", "DeleteObjects"): {"Bucket", "Delete"},
    ("s3", "GetObjectTagging"): {"Bucket", "Key"},
    ("s3", "UploadPartCopy"): {
        "Bucket",
        "Key",
        "UploadId",
        "PartNumber",
        "CopySource",
        "CopySourceRange",
        "CopySourceIfMatch",
    },
}


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _s3mock_image_from(text: str) -> str:
    """Return the single ``adobe/s3mock`` tag+digest pin named in ``text``."""
    pins = set(re.findall(r"adobe/s3mock:[0-9][^\s\"'`]*@sha256:[0-9a-f]{64}", text))
    assert len(pins) == 1, f"expected exactly one S3Mock pin, found {sorted(pins)}"
    return pins.pop()


def _numbered_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    _, separator, remainder = text.partition(marker)
    assert separator, f"missing section: {heading}"
    return remainder.partition("\n## ")[0]


def _module(path: str) -> ast.Module:
    return ast.parse(_text(path), filename=path)


def _source_aws_operations(service_name: str) -> set[str]:
    model = botocore.session.get_session().get_service_model(service_name)
    by_method = {xform_name(operation): operation for operation in model.operation_names}
    called_methods: set[str] = set()
    for path in _OPERATION_SOURCES[service_name]:
        for node in ast.walk(_module(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called_methods.add(node.func.attr)
    return {by_method[method] for method in called_methods & set(by_method)}


def _default_binding_actions() -> tuple[str, ...]:
    for node in ast.walk(_module("src/aws_tui/infra/keymap_store.py")):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "DEFAULT_BINDINGS"
            and isinstance(node.value, ast.Dict)
        ):
            return tuple(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    raise AssertionError("KeymapStore.DEFAULT_BINDINGS not found")


def _registered_service_actions() -> tuple[str, ...]:
    actions: set[str] = set()
    for node in ast.walk(_module("src/aws_tui/app.py")):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith(("glue.", "athena."))
        ):
            actions.add(node.args[0].value)
    return tuple(sorted(actions))


def _public_requests() -> tuple[str, ...]:
    return tuple(
        node.name
        for node in _module("src/aws_tui/vm/messages.py").body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Request")
    )


def _text_ledger_block(text: str, heading: str) -> tuple[str, ...]:
    assert heading in text, f"missing ledger heading: {heading}"
    tail = text.split(heading, maxsplit=1)[1]
    assert "```text" in tail, f"missing text ledger after: {heading}"
    block = tail.split("```text", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return tuple(line.strip() for line in block.splitlines() if line.strip())


def _action_id_table_actions(text: str) -> tuple[str, ...]:
    """Extract action IDs from the first table under the Action IDs heading."""
    heading = "## 1.3. Action IDs"
    assert heading in text, f"missing Action IDs heading: {heading}"
    section = text.split(heading, maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    table_lines = [line for line in section.splitlines() if line.startswith("|")]
    assert len(table_lines) >= 3, "missing Action IDs table"

    actions: list[str] = []
    for row in table_lines[2:]:
        first_cell = row.split("|", maxsplit=2)[1]
        actions.extend(re.findall(r"`([^`]+)`", first_cell))
    return tuple(actions)


def test_keybinding_action_table_covers_every_default_action() -> None:
    documented_actions = _action_id_table_actions(_text("docs/keybindings.md"))
    duplicate_or_missing = {
        action: documented_actions.count(action)
        for action in _default_binding_actions()
        if documented_actions.count(action) != 1
    }
    assert duplicate_or_missing == {}


def test_theme_docs_explain_composed_full_custom_themes() -> None:
    for path in ("docs/cookbook.md", "docs/theming.md"):
        text = re.sub(r"\s+", " ", _text(path))
        assert "operational-panes.tcss" in text
        assert "full replacement bypasses the built-in composition" in text
        assert "raw built-in theme, then the shared operational layer" in text


def test_theme_docs_describe_registered_palette_commands() -> None:
    for path in ("docs/cookbook.md", "docs/theming.md"):
        text = _text(path)
        assert "Theme picker" in text
        assert "Cycle theme" in text
        assert "theme switch ▸ voidline" in text
        assert "deferred" in text
        assert "palette: `theme switch" not in text


def test_public_service_action_ledger_matches_registered_source() -> None:
    ledger = _text("docs/contract-ledger.md")
    assert (
        _text_ledger_block(
            ledger,
            "Public Glue and Athena action ledger",
        )
        == _registered_service_actions()
    )


def test_cross_service_message_ledger_matches_request_classes() -> None:
    ledger = _text("docs/contract-ledger.md")
    assert (
        _text_ledger_block(
            ledger,
            "Public cross-service message ledger",
        )
        == _public_requests()
    )


def test_dependency_ledger_matches_locked_runtime_and_build_versions() -> None:
    lock = tomllib.loads(_text("uv.lock"))
    versions = {
        package["name"]: package["version"] for package in lock["package"] if "version" in package
    }
    project = tomllib.loads(_text("pyproject.toml"))
    ledger = _numbered_section(
        _text("docs/contract-ledger.md"),
        "1.5. 2026-08-25 maintenance pass",
    )

    for name in (
        "aiobotocore",
        "aiofiles",
        "aioboto3",
        "anyio",
        "botocore",
        "hatchling",
        "keyring",
        "platformdirs",
        "reactivex",
        "rich",
        "sqlglot",
        "testcontainers",
        "textual",
        "textual-dev",
        "tomli-w",
        "vmx",
    ):
        assert f"`{name}=={versions[name]}`" in ledger

    build_requirement = project["build-system"]["requires"][0]
    assert f"`build-system.requires` constrained to `{build_requirement}`" in ledger
    # Derive the S3Mock pin from the harness rather than restating it, so the
    # literal cannot agree with itself while the compose file moves on. The
    # docker-compose ecosystem only rewrites the compose file, so these four
    # sites are exactly what a dependabot bump splits apart.
    harness_image = _s3mock_image_from(_text("tests/integration/conftest.py"))
    assert f"`{harness_image}`" in ledger
    assert _s3mock_image_from(_text("scripts/test-services/s3/docker-compose.yml")) == harness_image
    assert harness_image in _text("docs/cookbook.md")


def test_pre_commit_revisions_are_recorded_in_the_current_ledger_pass() -> None:
    """Every pinned hook rev must appear in the current ledger pass.

    A dependabot pre-commit bump touches only ``.pre-commit-config.yaml``. With
    nothing comparing that file to the ledger, the ruff-pre-commit ref silently
    drifted a whole release behind while every tier stayed green.
    """
    ledger = _numbered_section(
        _text("docs/contract-ledger.md"),
        "1.5. 2026-08-25 maintenance pass",
    )
    config = _text(".pre-commit-config.yaml")
    revisions = re.findall(r"^\s*rev:\s*([0-9a-f]{40})\b", config, flags=re.MULTILINE)

    assert revisions, "expected pinned 40-character hook revisions"
    missing = [rev for rev in revisions if rev not in ledger]
    assert not missing, f"pre-commit revs absent from the current ledger pass: {missing}"


def test_numbered_section_excludes_historical_dependency_decoys() -> None:
    ledger = "## 1.4. Old\n`package==1`\n\n## 1.5. Current\n`package==2`\n\n## 1.6. Next\n"

    current = _numbered_section(ledger, "1.5. Current")

    assert "`package==2`" in current
    assert "`package==1`" not in current


def test_s3_operation_ledger_matches_locked_model_contract() -> None:
    ledger = _text("docs/contract-ledger.md")
    assert set(_text_ledger_block(ledger, "Exact S3 boto operation ledger")) == set(
        _MODELED_OPERATIONS["s3"]
    )


def test_credential_docs_match_keychain_reference_storage() -> None:
    security = re.sub(r"\s+", " ", _text("SECURITY.md"))
    cookbook = re.sub(r"\s+", " ", _text("docs/cookbook.md"))
    connections = re.sub(r"\s+", " ", _text("docs/connections.md"))
    ledger = re.sub(r"\s+", " ", _text("docs/contract-ledger.md"))

    assert "Settings form stores secrets in the OS keychain" in security
    assert "persists only a `keychain:` reference" in security
    assert "stores the secret fields in the OS keychain" in cookbook
    assert "persists only a `keychain:` reference" in cookbook
    assert "The in-TUI Settings form writes credentials to the OS keychain" in connections
    assert "keychain:aws-tui:connections/<url-escaped-name>" in connections
    assert "keychain:aws-tui:connection-revisions/<url-escaped-name>/0" in connections
    assert "keychain:aws-tui:connection-revisions/<url-escaped-name>/1" in connections
    assert 'credentials = "keychain:aws-tui:connections/s3mock-local"' in cookbook
    assert "under the namespaced service `aws-tui:<connection>`" not in connections
    assert "Production Settings saves store only" in ledger
    assert "two bounded revision slots" in ledger

    retired_claims = (
        "default written by the in-TUI add form",
        "That writes a `static` entry to `config.toml`",
        "Settings and first-run forms write credentials",
        "Settings and first-run saves store only",
    )
    combined = " ".join((security, cookbook, connections, ledger))
    for claim in retired_claims:
        assert claim not in combined


def test_consumed_aws_operations_and_inputs_exist_in_locked_botocore_models() -> None:
    session = botocore.session.get_session()
    for service_name, operations in _MODELED_OPERATIONS.items():
        model = session.get_service_model(service_name)
        assert operations <= set(model.operation_names)
        assert operations == _source_aws_operations(service_name)

    for (service_name, operation_name), members in _CONSUMED_INPUT_MEMBERS.items():
        operation = session.get_service_model(service_name).operation_model(operation_name)
        assert operation.input_shape is not None
        assert members <= set(operation.input_shape.members)


def test_emr_job_run_state_filter_matches_locked_model_constraint() -> None:
    model = botocore.session.get_session().get_service_model("emr-serverless")
    operation = model.operation_model("ListJobRuns")
    assert operation.input_shape is not None
    states = operation.input_shape.members["states"]
    assert states.metadata["max"] == 8
    assert set(states.member.enum) == {
        "SUBMITTED",
        "PENDING",
        "SCHEDULED",
        "RUNNING",
        "SUCCESS",
        "FAILED",
        "CANCELLING",
        "CANCELLED",
        "QUEUED",
    }


def test_workflow_metadata_avoids_stale_e2e_counts() -> None:
    workflow = _text(".github/workflows/ci.yml")
    assert re.search(r"name: e2e \(user journeys\)", workflow)
    assert not re.search(r"name: e2e \(\d+ user journeys\)", workflow)
