"""Bounded ASCII leaves for operation-owned sibling entries."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4


def operation_name(purpose: Literal["stage", "backup", "delete", "rmdir"]) -> str:
    """Keep every leaf at most 48 ASCII units, independently of user names."""
    return f".aws-tui-{purpose}-{uuid4().hex}"
