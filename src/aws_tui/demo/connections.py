"""Synthetic connections injected in demo mode.

Replaces ``aws_tui.infra.connection_resolver.ConnectionResolver`` at
the composition-root level when demo mode is active. The Shift+S
cycle, the Settings panel, and the boot chain all see these as if
they were real entries.

Four connections (spec § "Demo connection resolver"):

- ``demo-dev``    (aws,    us-east-1)
- ``demo-prod``   (aws,    us-east-1)
- ``demo-shared`` (aws,    us-west-2)
- ``demo-minio``  (s3-compatible, us-east-1)
"""

from __future__ import annotations

from aws_tui.infra.connection_resolver import Connection


def demo_connections() -> tuple[Connection, ...]:
    """Return the canonical demo connection tuple."""
    return (
        Connection(
            name="demo-dev",
            kind="aws",
            region="us-east-1",
            source="demo",
            profile="demo-dev",
        ),
        Connection(
            name="demo-prod",
            kind="aws",
            region="us-east-1",
            source="demo",
            profile="demo-prod",
        ),
        Connection(
            name="demo-shared",
            kind="aws",
            region="us-west-2",
            source="demo",
            profile="demo-shared",
        ),
        Connection(
            name="demo-minio",
            kind="s3-compatible",
            region="us-east-1",
            source="demo",
            profile=None,
        ),
    )


class DemoConnectionResolver:
    """Drop-in for ``ConnectionResolver`` in demo mode.

    The public surface mirrors the methods the composition root and boot
    chain call on ``ConnectionResolver``.  Demo connections are immutable
    so add/remove/update are not implemented.
    """

    def list(self) -> tuple[Connection, ...]:
        return demo_connections()

    def default(self) -> Connection | None:
        return demo_connections()[0]

    def resolve(self, name: str) -> Connection:
        """Return the demo connection matching ``name``; raises if not found.

        Mirrors :meth:`~aws_tui.infra.connection_resolver.ConnectionResolver.resolve`
        so the composition root can substitute a ``DemoConnectionResolver``
        for a ``ConnectionResolver`` without divergent error semantics.
        """
        match = next((c for c in demo_connections() if c.name == name), None)
        if match is None:
            from aws_tui.infra.connection_resolver import ConnectionNotFound

            raise ConnectionNotFound(name)
        return match


__all__ = ["DemoConnectionResolver", "demo_connections"]
