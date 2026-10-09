from __future__ import annotations

import re
import stat
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from scripts.check_dist import ArtifactContentsError, main, validate_artifact

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_complete_wheel(path: Path, *, omit: str | None = None) -> None:
    package_root = REPO_ROOT / "src" / "aws_tui"
    with zipfile.ZipFile(path, "w") as archive:
        for source in sorted(package_root.rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(package_root).as_posix()
            member = f"aws_tui/{relative}"
            if member != omit:
                archive.writestr(member, source.read_bytes())
        archive.writestr("aws_tui-0.8.0.dist-info/METADATA", "")


def _write_complete_sdist(path: Path, *, omit: str | None = None) -> None:
    root = "aws_tui-0.8.0"
    package_root = REPO_ROOT / "src" / "aws_tui"
    members = [
        (f"{root}/src/aws_tui/{source.relative_to(package_root).as_posix()}", source.read_bytes())
        for source in sorted(package_root.rglob("*"))
        if source.is_file() and "__pycache__" not in source.parts
    ]
    members.extend(
        (f"{root}/{name}", (REPO_ROOT / name).read_bytes())
        for name in ("PYPI.md", "pyproject.toml")
    )
    with tarfile.open(path, "w:gz") as archive:
        for member, payload in members:
            if member == omit:
                continue
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            archive.addfile(info, BytesIO(payload))


def test_validate_sdist_rejects_transient_cache(tmp_path: Path) -> None:
    source = tmp_path / "payload"
    denied = source / "aws_tui-0.8.0" / ".uv-cache" / "sdists-v9"
    denied.mkdir(parents=True)
    (denied / "entry").write_text("cached", encoding="utf-8")
    artifact = tmp_path / "aws_tui-0.8.0.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(source / "aws_tui-0.8.0", arcname="aws_tui-0.8.0")

    with pytest.raises(ArtifactContentsError, match=r"\.uv-cache"):
        validate_artifact(artifact)


def test_validate_wheel_rejects_repository_metadata(tmp_path: Path) -> None:
    artifact = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("aws_tui/__init__.py", "")
        archive.writestr(".github/workflows/release.yml", "")

    with pytest.raises(ArtifactContentsError, match=r"\.github"):
        validate_artifact(artifact)


def test_validate_clean_artifacts(tmp_path: Path) -> None:
    wheel = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
    sdist = tmp_path / "aws_tui-0.8.0.tar.gz"
    _write_complete_wheel(wheel)
    _write_complete_sdist(sdist)

    validate_artifact(wheel)
    validate_artifact(sdist)


@pytest.mark.parametrize(
    "required_member",
    [
        "aws_tui/py.typed",
        "aws_tui/ui/themes/operational-panes.tcss",
    ],
)
def test_validate_wheel_rejects_missing_package_payload(
    tmp_path: Path,
    required_member: str,
) -> None:
    wheel = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
    _write_complete_wheel(wheel, omit=required_member)

    with pytest.raises(ArtifactContentsError, match=re.escape(required_member)):
        validate_artifact(wheel)


@pytest.mark.parametrize(
    "required_member",
    [
        "aws_tui-0.8.0/src/aws_tui/py.typed",
        "aws_tui-0.8.0/src/aws_tui/ui/themes/operational-panes.tcss",
        "aws_tui-0.8.0/pyproject.toml",
    ],
)
def test_validate_sdist_rejects_missing_package_payload(
    tmp_path: Path,
    required_member: str,
) -> None:
    sdist = tmp_path / "aws_tui-0.8.0.tar.gz"
    _write_complete_sdist(sdist, omit=required_member)

    with pytest.raises(ArtifactContentsError, match=re.escape(required_member)):
        validate_artifact(sdist)


def test_directory_mode_ignores_non_artifact_housekeeping_files(tmp_path: Path) -> None:
    wheel = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
    _write_complete_wheel(wheel)
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")

    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize("member", ["C:/temp/payload.py", "C:\\temp\\payload.py", "C:payload.py"])
@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_validate_artifact_rejects_windows_drive_paths(
    tmp_path: Path, member: str, kind: str
) -> None:
    if kind == "wheel":
        artifact = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
        _write_complete_wheel(artifact)
        with zipfile.ZipFile(artifact, "a") as archive:
            archive.writestr(member, "fixture")
    else:
        artifact = tmp_path / "aws_tui-0.8.0.tar.gz"
        _write_complete_sdist(artifact)
        with tarfile.open(artifact, "r:gz") as archive:
            members = [(info.name, archive.extractfile(info).read()) for info in archive]
        with tarfile.open(artifact, "w:gz") as archive:
            for name, payload in [*members, (member, b"fixture")]:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, BytesIO(payload))
    with pytest.raises(ArtifactContentsError, match="unsafe path"):
        validate_artifact(artifact)


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_validate_sdist_rejects_non_file_members(tmp_path: Path, member_type: bytes) -> None:
    artifact = tmp_path / "aws_tui-0.8.0.tar.gz"
    _write_complete_sdist(artifact)
    with tarfile.open(artifact, "r:gz") as archive:
        members = [(info, archive.extractfile(info).read()) for info in archive]
    with tarfile.open(artifact, "w:gz") as archive:
        for info, payload in members:
            archive.addfile(info, BytesIO(payload))
        unsafe = tarfile.TarInfo("aws_tui-0.8.0/payload")
        unsafe.type = member_type
        unsafe.linkname = "../../outside"
        archive.addfile(unsafe)
    with pytest.raises(ArtifactContentsError, match="unsupported member type"):
        validate_artifact(artifact)


def test_validate_wheel_rejects_symlink(tmp_path: Path) -> None:
    artifact = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
    _write_complete_wheel(artifact)
    with zipfile.ZipFile(artifact, "a") as archive:
        link = zipfile.ZipInfo("aws_tui/payload")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
    with pytest.raises(ArtifactContentsError, match="unsupported member type"):
        validate_artifact(artifact)


@pytest.mark.parametrize("kind", ["wheel", "wheel-dos-directory", "sdist"])
def test_required_package_member_must_be_a_file(tmp_path: Path, kind: str) -> None:
    if kind.startswith("wheel"):
        artifact = tmp_path / "aws_tui-0.8.0-py3-none-any.whl"
        _write_complete_wheel(artifact, omit="aws_tui/py.typed")
        with zipfile.ZipFile(artifact, "a") as archive:
            directory = zipfile.ZipInfo("aws_tui/py.typed")
            directory.create_system = 3
            directory.external_attr = (stat.S_IFDIR | 0o755) << 16
            if kind == "wheel-dos-directory":
                directory.create_system = 0
                directory.external_attr = 0x10
            archive.writestr(directory, "")
    else:
        artifact = tmp_path / "aws_tui-0.8.0.tar.gz"
        _write_complete_sdist(artifact, omit="aws_tui-0.8.0/src/aws_tui/py.typed")
        with tarfile.open(artifact, "r:gz") as archive:
            members = [(info, archive.extractfile(info).read()) for info in archive]
        with tarfile.open(artifact, "w:gz") as archive:
            for info, payload in members:
                archive.addfile(info, BytesIO(payload))
            directory = tarfile.TarInfo("aws_tui-0.8.0/src/aws_tui/py.typed")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
    with pytest.raises(ArtifactContentsError, match="missing required package members"):
        validate_artifact(artifact)
