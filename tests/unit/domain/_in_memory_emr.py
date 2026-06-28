"""Backward-compat re-export shim.

The fake was promoted to :mod:`aws_tui.demo.in_memory_emr` so demo
mode and tests share one validated implementation. This shim
preserves the historical ``_InMemoryEmr`` alias so existing test
files (32 importers) keep working without edits.
"""

from aws_tui.demo.in_memory_emr import InMemoryEmr as _InMemoryEmr

__all__ = ["_InMemoryEmr"]
