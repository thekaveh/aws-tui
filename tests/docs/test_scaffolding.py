"""Smoke tests and public-documentation contract checks."""

from pathlib import Path


def test_scripts_docs_package_imports():
    import scripts.docs  # noqa: F401


def test_pyyaml_available():
    import yaml

    assert yaml.safe_load("a: 1") == {"a": 1}


def test_public_docs_cover_athena_read_only_contract() -> None:
    """Keep standalone Athena behavior discoverable on every public surface."""
    repo_root = Path(__file__).parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    architecture = (repo_root / "docs/architecture.md").read_text(encoding="utf-8")
    services = (repo_root / "docs/adding-a-service.md").read_text(encoding="utf-8")
    connections = (repo_root / "docs/connections.md").read_text(encoding="utf-8")
    cookbook = (repo_root / "docs/cookbook.md").read_text(encoding="utf-8")
    ledger = (repo_root / "docs/contract-ledger.md").read_text(encoding="utf-8")
    keybindings = (repo_root / "docs/keybindings.md").read_text(encoding="utf-8")
    releasing = (repo_root / "docs/RELEASING.md").read_text(encoding="utf-8")
    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "Athena" in readme
    assert "standalone" in readme
    assert "AthenaPageVM" in architecture
    assert "AthenaService" in services
    assert "Athena is AWS-only" in connections
    assert "SELECT, SHOW, DESCRIBE, and EXPLAIN" in cookbook
    assert "result artifacts" in cookbook
    assert "bytes scanned" in cookbook
    assert "Lake Formation" in cookbook
    assert "s3:GetObject" in cookbook
    assert "EnforceWorkGroupConfiguration" in cookbook
    assert "list_work_groups" in ledger
    assert "start_query_execution" in ledger
    assert "get_query_results" in ledger
    assert "athena.execute" in keybindings
    assert "athena.open_result_location" in keybindings
    assert "Athena" in releasing
    assert "Athena" in changelog
