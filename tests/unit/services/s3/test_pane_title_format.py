"""``_format_pane_title`` per connection kind.

Locks in the user-visible format:

- ``aws``           → ``aws s3 · {profile} · {region}``
- ``s3-compatible`` → ``s3-compatible · {name} · {endpoint}``
  (region intentionally omitted — MinIO/R2/etc. don't have a meaningful
  region, and the internal SigV4 default ``us-east-1`` would be
  misleading to surface in the UI)

The endpoint loses its ``http(s)://`` scheme for compactness.
"""

from __future__ import annotations

from aws_tui.infra.connection_resolver import Connection
from aws_tui.services.s3.service import _format_pane_title, _strip_scheme


def _aws(
    name: str = "default", profile: str | None = "default", region: str | None = "us-east-1"
) -> Connection:
    return Connection(
        name=name,
        kind="aws",
        region=region,
        source="auto-aws-profile",
        profile=profile,
    )


def _s3c(
    name: str = "minio-local",
    endpoint: str | None = "http://localhost:64093",
    region: str | None = "us-east-1",
) -> Connection:
    return Connection(
        name=name,
        kind="s3-compatible",
        region=region,
        source="config",
        endpoint_url=endpoint,
        access_key_id="minioadmin",
        secret_access_key="not-a-real-secret",
        force_path_style=True,
        verify_tls=False,
    )


def test_aws_format_uses_aws_s3_prefix_with_profile_and_region() -> None:
    assert _format_pane_title(_aws()) == "aws s3 · default · us-east-1"


def test_aws_format_drops_missing_profile_and_region() -> None:
    bare = _aws(name="bare", profile=None, region=None)
    assert _format_pane_title(bare) == "aws s3"


def test_s3_compatible_format_uses_name_and_endpoint() -> None:
    assert _format_pane_title(_s3c()) == "s3-compatible · minio-local · localhost:64093"


def test_s3_compatible_format_omits_region_even_when_present() -> None:
    """The whole point of the user's request — never surface
    ``us-east-1`` for a MinIO-style connection."""
    title = _format_pane_title(_s3c(region="eu-west-3"))
    assert "eu-west-3" not in title
    assert "us-east-1" not in title


def test_s3_compatible_format_strips_https_scheme_too() -> None:
    title = _format_pane_title(
        _s3c(name="r2-prod", endpoint="https://abc.r2.cloudflarestorage.com")
    )
    assert title == "s3-compatible · r2-prod · abc.r2.cloudflarestorage.com"


def test_strip_scheme_handles_edge_cases() -> None:
    assert _strip_scheme(None) is None
    assert _strip_scheme("") == ""
    assert _strip_scheme("localhost:64093") == "localhost:64093"  # no scheme — unchanged
