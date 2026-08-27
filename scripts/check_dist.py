"""Reject unsafe or repository-only members in built Python artifacts."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

_DENIED_COMPONENTS = frozenset(
    {
        ".claude",
        ".git",
        ".github",
        ".mypy_cache",
        ".overnight-maint",
        ".pytest_cache",
        ".ruff_cache",
        ".superpowers",
        ".uv-cache",
        ".venv",
        "__pycache__",
        "tests",
    }
)
_DENIED_SEQUENCES = (
    ("docs", "superpowers"),
    ("scripts", "test-services"),
)
_ARTIFACT_SUFFIXES = (".whl", ".tar.gz")
_SOURCE_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "aws_tui"
_PACKAGE_FILE_SUFFIXES = frozenset({".py", ".tcss"})


class ArtifactContentsError(ValueError):
    """A distribution contains an unsafe or repository-only member."""


def _member_names(path: Path) -> tuple[str, ...]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            return tuple(member.name for member in archive.getmembers())
    raise ArtifactContentsError(f"unsupported distribution artifact: {path}")


def _denied_reason(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return "unsafe path"
    parts = tuple(part for part in path.parts if part not in {"", "."})
    denied = next((part for part in parts if part in _DENIED_COMPONENTS), None)
    if denied is not None:
        return denied
    for sequence in _DENIED_SEQUENCES:
        width = len(sequence)
        if any(parts[index : index + width] == sequence for index in range(len(parts) - width + 1)):
            return "/".join(sequence)
    return None


def _required_wheel_members() -> frozenset[str]:
    return frozenset(
        f"aws_tui/{source.relative_to(_SOURCE_PACKAGE).as_posix()}"
        for source in _SOURCE_PACKAGE.rglob("*")
        if source.is_file()
        and "__pycache__" not in source.parts
        and (source.suffix in _PACKAGE_FILE_SUFFIXES or source.name == "py.typed")
    )


def _required_sdist_members(root: str) -> frozenset[str]:
    package_members = {f"{root}/src/{member}" for member in _required_wheel_members()}
    return frozenset({*package_members, f"{root}/PYPI.md", f"{root}/pyproject.toml"})


def validate_artifact(path: str | Path) -> None:
    artifact = Path(path)
    members = _member_names(artifact)
    violations = [
        f"{name} ({reason})" for name in members if (reason := _denied_reason(name)) is not None
    ]
    if violations:
        preview = ", ".join(violations[:8])
        suffix = "" if len(violations) <= 8 else f", and {len(violations) - 8} more"
        raise ArtifactContentsError(f"{artifact} contains denied members: {preview}{suffix}")
    if artifact.name.endswith(".whl"):
        missing = sorted(_required_wheel_members().difference(members))
    elif artifact.name.endswith(".tar.gz"):
        root = artifact.name.removesuffix(".tar.gz")
        missing = sorted(_required_sdist_members(root).difference(members))
    else:
        missing = []
    if missing:
        preview = ", ".join(missing[:8])
        suffix = "" if len(missing) <= 8 else f", and {len(missing) - 8} more"
        raise ArtifactContentsError(
            f"{artifact} is missing required package members: {preview}{suffix}"
        )


def _artifact_paths(arguments: Iterable[str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for argument in arguments:
        candidate = Path(argument)
        if candidate.is_dir():
            paths.extend(
                sorted(
                    path
                    for path in candidate.iterdir()
                    if path.is_file() and path.name.endswith(_ARTIFACT_SUFFIXES)
                )
            )
        else:
            paths.append(candidate)
    return tuple(paths)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_dist")
    parser.add_argument("artifacts", nargs="+", help="wheel, sdist, or artifact directory")
    args = parser.parse_args(argv)
    artifacts = _artifact_paths(args.artifacts)
    if not artifacts:
        parser.error("no distribution artifacts found")
    for artifact in artifacts:
        validate_artifact(artifact)
        print(f"artifact contents clean: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
