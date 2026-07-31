from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _module(path: str) -> ast.Module:
    return ast.parse(_text(path), filename=path)


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
    ledger = _text("docs/contract-ledger.md")

    for name in ("textual", "vmx", "hatchling"):
        assert f"`{name}=={versions[name]}`" in ledger

    build_requirement = project["build-system"]["requires"][0]
    assert f"`build-system.requires` constrained to `{build_requirement}`" in ledger


def test_workflow_metadata_avoids_stale_e2e_counts() -> None:
    workflow = _text(".github/workflows/ci.yml")
    assert re.search(r"name: e2e \(user journeys\)", workflow)
    assert not re.search(r"name: e2e \(\d+ user journeys\)", workflow)
