"""Package-level sanity smoke tests — the package imports, version is
set, and the app class is exposed. The bare-minimum CI must keep
green; per-layer behavioral coverage lives in the tier suites under
unit/, integration/, snapshot/, and e2e/.
"""

from __future__ import annotations


def test_package_imports() -> None:
    """Importing the top-level package shouldn't raise."""
    import aws_tui  # noqa: F401


def test_version_is_set() -> None:
    """``__version__`` is exposed at the package root and matches v0.7.0."""
    from aws_tui import __version__

    assert __version__ == "0.7.0"


def test_app_class_is_exposed() -> None:
    """``AwsTuiApp`` is importable from the top-level package."""
    from aws_tui import AwsTuiApp

    assert AwsTuiApp.__name__ == "AwsTuiApp"
    assert AwsTuiApp.TITLE == "aws-tui"
    assert AwsTuiApp.SUB_TITLE == "v0.7.0"
