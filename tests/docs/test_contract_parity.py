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


def test_keybinding_action_table_covers_every_default_action() -> None:
    keybindings = _text("docs/keybindings.md")
    missing = [action for action in _default_binding_actions() if f"`{action}`" not in keybindings]
    assert missing == []


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
