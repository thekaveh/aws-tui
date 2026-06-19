"""Package-level sanity smoke tests — the package imports, version is
set, and the app class is exposed. The bare-minimum CI must keep
green; per-layer behavioral coverage lives in the tier suites under
unit/, integration/, snapshot/, and e2e/.
"""

from __future__ import annotations

import re


def test_package_imports() -> None:
    """Importing the top-level package shouldn't raise."""
    import aws_tui  # noqa: F401


def test_version_is_set() -> None:
    """``__version__`` is exposed at the package root and follows semver.

    The literal value is *not* pinned here so a version bump doesn't
    cascade into a test failure — release plumbing is the right place
    for that gate.
    """
    from aws_tui import __version__

    assert isinstance(__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+([.-].+)?$", __version__), __version__


def test_app_class_is_exposed() -> None:
    """``AwsTuiApp`` is importable from the top-level package."""
    from aws_tui import AwsTuiApp, __version__

    assert AwsTuiApp.__name__ == "AwsTuiApp"
    assert AwsTuiApp.TITLE == "aws-tui"
    assert f"v{__version__}" == AwsTuiApp.SUB_TITLE
