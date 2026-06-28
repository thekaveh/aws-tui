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

    The public surface is ``list()`` + ``default()`` only — those
    are the two methods the composition root + boot chain call.
    Other ``ConnectionResolver`` methods (e.g. add_connection,
    remove_connection used by the Settings panel) are not required
    for demo mode because the demo connections are immutable.
    """

    def list(self) -> tuple[Connection, ...]:
        return demo_connections()

    def default(self) -> Connection | None:
        return demo_connections()[0]


__all__ = ["DemoConnectionResolver", "demo_connections"]
