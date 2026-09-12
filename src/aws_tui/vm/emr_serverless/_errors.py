"""EMR re-export of the shared VM error mapping.

Kept as a module so the EMR VMs' import sites stay stable; the behaviour lives
in :mod:`aws_tui.vm._errors`.
"""

from __future__ import annotations

from aws_tui.vm._errors import map_provider_error

__all__ = ["map_provider_error"]
