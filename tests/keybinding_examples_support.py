"""Read executable migration examples without duplicating their chosen keys."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


def documented_migration_key(action: str) -> str:
    guide = (Path(__file__).parents[1] / "docs/keybindings.md").read_text()
    assignment = re.search(rf'"{re.escape(action)}"\s*=\s*("[^"]+"|\[[^\]\n]*\])', guide)
    assert assignment is not None, f"no executable migration example for {action}"
    value = tomllib.loads(assignment.group(0))[action]
    keys = [value] if isinstance(value, str) else value
    assert len(keys) == 1, f"migration example for {action} must identify its tested key"
    return keys[0]


def traditional_ctrl_byte(key: str) -> str:
    assert re.fullmatch(r"ctrl\+[a-z]", key), f"expected a standalone Ctrl-letter example: {key}"
    return chr(ord(key[-1]) - ord("a") + 1)
