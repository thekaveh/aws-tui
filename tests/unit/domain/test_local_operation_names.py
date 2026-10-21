"""Real POSIX filename boundaries exercise transaction names and cleanup."""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path

import pytest

from aws_tui.domain.cross_fs import ConflictResolution, CrossFsCopy, CrossFsMove
from aws_tui.domain.filesystem import PathRef, ProviderError
from aws_tui.domain.local_fs import LocalFS

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX component limits")


def _native_maximum(root: Path, token: str) -> int:
    """Probe actual creation, rather than treating NAME_MAX as Unicode bytes."""

    def accepts(count):
        path = root / (token * count)
        try:
            path.write_bytes(b"name-limit probe")
        except OSError as error:
            if error.errno == errno.ENAMETOOLONG:
                return False
            raise
        path.unlink()
        return True

    low, high = 1, 1024
    assert accepts(low)
    assert not accepts(high)
    while low + 1 < high:
        middle = (low + high) // 2
        if accepts(middle):
            low = middle
        else:
            high = middle
    return low


@pytest.fixture(scope="module")
def native_names(tmp_path_factory):
    root = tmp_path_factory.mktemp("name-probe")
    return {token: token * _native_maximum(root, token) for token in ["é", "😀"]}


def _leaf(case, native_names):
    return "n" * case if isinstance(case, int) else native_names[case]


def _provider(root: Path, rooted: bool):
    root.mkdir()
    return LocalFS(root=root) if rooted else LocalFS()


def _ref(root: Path, name: str, rooted: bool):
    return PathRef((name,)) if rooted else PathRef.from_posix(str(root / name))


def _seed(root: Path, leaf: str, directory: bool, data: bytes):
    path = root / leaf
    if directory:
        path.mkdir()
        (path / "value.bin").write_bytes(data)
    else:
        path.write_bytes(data)


def _bytes(root: Path, leaf: str, directory: bool):
    path = root / leaf
    return (path / "value.bin" if directory else path).read_bytes()


@pytest.mark.parametrize("case", [159, 160, 207, 208, 255, "é", "😀"])
@pytest.mark.parametrize("rooted", [False, True])
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("overwrite", [False, True])
async def test_valid_native_leaves_copy_and_overwrite_without_owned_residue(
    tmp_path, native_names, case, rooted, directory, overwrite
):
    leaf = _leaf(case, native_names)
    source_root, destination_root = tmp_path / "source", tmp_path / "destination"
    source = _provider(source_root, rooted)
    destination = _provider(destination_root, rooted)
    _seed(source_root, leaf, directory, b"exact copied bytes\x00\xff")
    if overwrite:
        _seed(destination_root, leaf, directory, b"old destination")
        if directory:
            (destination_root / leaf / "old.bin").write_bytes(b"obsolete")
    result = await CrossFsCopy(source=source, destination=destination).copy(
        _ref(source_root, leaf, rooted),
        _ref(destination_root, leaf, rooted),
        on_conflict=ConflictResolution.OVERWRITE if overwrite else ConflictResolution.ERROR,
    )
    assert result is True
    assert _bytes(destination_root, leaf, directory) == b"exact copied bytes\x00\xff"
    assert _bytes(source_root, leaf, directory) == b"exact copied bytes\x00\xff"
    assert {path.name for path in destination_root.iterdir()} == {leaf}
    if directory:
        assert {path.name for path in (destination_root / leaf).iterdir()} == {"value.bin"}


@pytest.mark.parametrize("case", [159, 160, 207, 208, 255, "é", "😀"])
@pytest.mark.parametrize("rooted", [False, True])
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_partial_stage_failure_or_cancel_cleans_valid_native_leaves(
    tmp_path, native_names, monkeypatch, case, rooted, directory, cancel
):
    leaf = _leaf(case, native_names)
    source_root, destination_root = tmp_path / "source", tmp_path / "destination"
    source = _provider(source_root, rooted)
    destination = _provider(destination_root, rooted)
    _seed(source_root, leaf, directory, b"partial staged bytes")
    read = source.read_stream
    entered = []

    async def interrupted(path):
        stream = await read(path)

        async def chunks():
            try:
                async for chunk in stream:
                    entered.append(True)
                    yield chunk
                    if cancel:
                        raise asyncio.CancelledError
                    raise ProviderError("controlled source stream failure")
            finally:
                await stream.aclose()

        return chunks()

    monkeypatch.setattr(source, "read_stream", interrupted)
    with pytest.raises(
        asyncio.CancelledError if cancel else ProviderError,
        match=None if cancel else "controlled source stream failure",
    ):
        await CrossFsCopy(source=source, destination=destination).copy(
            _ref(source_root, leaf, rooted), _ref(destination_root, leaf, rooted)
        )
    assert entered
    assert list(destination_root.iterdir()) == []
    assert _bytes(source_root, leaf, directory) == b"partial staged bytes"


@pytest.mark.parametrize("case", [159, 160, 207, 208, 255, "é", "😀"])
@pytest.mark.parametrize("rooted", [False, True])
@pytest.mark.parametrize("directory", [False, True])
async def test_move_removes_long_source_with_revision_guards_and_empty_dir_cleanup(
    tmp_path, native_names, case, rooted, directory
):
    leaf = _leaf(case, native_names)
    source_root, destination_root = tmp_path / "source", tmp_path / "destination"
    source = _provider(source_root, rooted)
    destination = _provider(destination_root, rooted)
    _seed(source_root, leaf, directory, b"moved bytes")
    result = await CrossFsMove(source=source, destination=destination).move(
        _ref(source_root, leaf, rooted), _ref(destination_root, leaf, rooted)
    )
    assert result is True
    assert list(source_root.iterdir()) == []
    assert _bytes(destination_root, leaf, directory) == b"moved bytes"
    assert {path.name for path in destination_root.iterdir()} == {leaf}
