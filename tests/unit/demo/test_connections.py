"""DemoConnectionResolver tests — shape parity with ConnectionResolver."""

from __future__ import annotations

import pytest

from aws_tui.demo.connections import DemoConnectionResolver, demo_connections
from aws_tui.infra.connection_resolver import Connection


def test_demo_connections_returns_four_entries() -> None:
    conns = demo_connections()
    # Spec: 3 AWS + 1 s3-compatible = 4 entries.
    assert len(conns) == 4


def test_demo_connections_first_three_are_aws() -> None:
    conns = demo_connections()
    assert all(c.kind == "aws" for c in conns[:3])


def test_demo_connections_last_is_s3_compatible() -> None:
    conns = demo_connections()
    assert conns[3].kind == "s3-compatible"


def test_demo_connections_names_match_spec() -> None:
    names = {c.name for c in demo_connections()}
    assert {"demo-dev", "demo-prod", "demo-shared", "demo-minio"}.issubset(names)


def test_demo_connections_aws_have_profile_set() -> None:
    aws_conns = [c for c in demo_connections() if c.kind == "aws"]
    for c in aws_conns:
        assert c.profile is not None, f"AWS demo conn {c.name!r} must have a profile"


def test_demo_connection_resolver_list_matches_demo_connections() -> None:
    resolver = DemoConnectionResolver()
    assert resolver.list() == demo_connections()


def test_demo_connection_resolver_default_is_first_entry() -> None:
    resolver = DemoConnectionResolver()
    assert resolver.default() == demo_connections()[0]


def test_demo_connection_resolver_default_returns_connection_type() -> None:
    """The default() return type must match ``ConnectionResolver.default()``
    so the composition root can substitute one for the other."""
    resolver = DemoConnectionResolver()
    default = resolver.default()
    assert default is None or isinstance(default, Connection)


def test_demo_connection_resolver_resolves_known_name() -> None:
    resolver = DemoConnectionResolver()
    conn = resolver.resolve("demo-prod")
    assert conn.name == "demo-prod"


def test_demo_connection_resolver_resolves_all_demo_names() -> None:
    """resolve() must succeed for every name returned by list()."""
    resolver = DemoConnectionResolver()
    for conn in resolver.list():
        assert resolver.resolve(conn.name) == conn


def test_demo_connection_resolver_resolve_unknown_name_raises() -> None:
    from aws_tui.infra.connection_resolver import ConnectionNotFound

    resolver = DemoConnectionResolver()
    with pytest.raises(ConnectionNotFound):
        resolver.resolve("not-a-real-connection")
