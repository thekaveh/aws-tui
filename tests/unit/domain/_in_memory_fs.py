"""Backward-compat re-export shim.

The fake was promoted to :mod:`aws_tui.demo.in_memory_fs` so demo
mode and tests share one validated implementation. This shim keeps
the historical import path working for tests that haven't been
migrated to import from the new location directly.
"""

from aws_tui.demo.in_memory_fs import InMemoryFS

__all__ = ["InMemoryFS"]
