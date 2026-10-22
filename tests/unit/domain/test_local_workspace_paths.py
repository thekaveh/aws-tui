"""Native locations map back through the configured LocalFS boundary."""

import os
from pathlib import Path

import pytest

from aws_tui.domain.filesystem import PathRef, ProviderError
from aws_tui.domain.local_fs import LocalFS


@pytest.mark.skipif(os.name == "nt", reason="POSIX literal backslash filename")
def test_rooted_navigation_capture_is_pure_and_retains_literal_native_names(tmp_path, monkeypatch):
    provider = LocalFS(root=tmp_path)
    location = tmp_path / "daily space" / "literal\\name"

    def no_filesystem_resolution(*_args, **_kwargs):
        pytest.fail("navigation capture must not perform filesystem I/O")

    monkeypatch.setattr(Path, "resolve", no_filesystem_resolution)
    assert provider.navigation_path(PathRef(("daily space", "literal\\name"))) == str(location)
    assert provider.path_from_navigation(str(location)) == PathRef(("daily space", "literal\\name"))
    assert provider.path_from_navigation(str(tmp_path)) == PathRef()


@pytest.mark.parametrize("outside", ["outside", "dotdot", "relative"])
def test_saved_path_cannot_change_or_escape_current_local_root(tmp_path, outside):
    root = tmp_path / "allowed"
    root.mkdir()
    provider = LocalFS(root=root)
    value = {
        "outside": str(tmp_path / "other"),
        "dotdot": str(root / ".." / "other"),
        "relative": "relative/path",
    }[outside]
    with pytest.raises(ProviderError):
        provider.path_from_navigation(value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX native location")
def test_unrooted_posix_native_locations_roundtrip_without_normalizing_backslashes():
    provider = LocalFS()
    target = PathRef(("tmp", "literal\\segment", "space and Unicode λ"))
    assert provider.path_from_navigation(provider.navigation_path(target)) == target
    assert provider.path_from_navigation("/") == PathRef()


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("/", PathRef()),
        ("C:\\data\\Unicode λ", PathRef(("C:", "data", "Unicode λ"))),
        ("D:\\", PathRef(("D:",))),
    ],
)
def test_windows_location_mapping_is_pure_not_native_filesystem_coverage(
    monkeypatch, native, expected
):
    import aws_tui.domain.local_fs as module

    monkeypatch.setattr(module, "_WINDOWS", True)
    provider = LocalFS()
    assert provider.path_from_navigation(native) == expected
    assert provider.navigation_path(expected) == native


def test_windows_unrooted_mapping_does_not_invent_share_anchor(monkeypatch):
    import aws_tui.domain.local_fs as module

    monkeypatch.setattr(module, "_WINDOWS", True)
    with pytest.raises(ProviderError):
        LocalFS().path_from_navigation("\\\\server\\share\\data")
