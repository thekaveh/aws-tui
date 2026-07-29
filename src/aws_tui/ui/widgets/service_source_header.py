"""Compact identity header for single-context AWS service pages."""

from __future__ import annotations

from textual.widgets import Static

from aws_tui.vm.service_source_vm import ServiceSourceContext


class ServiceSourceHeader(Static):
    """Render the active connection identity in one stable row."""

    DEFAULT_CSS = """
    ServiceSourceHeader {
        width: 1fr;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    """

    def __init__(self, source: ServiceSourceContext, *, id: str | None = None) -> None:
        super().__init__(source.label, id=id, markup=False)


__all__ = ["ServiceSourceHeader"]
