from __future__ import annotations

import asyncio
from importlib.metadata import version

import pytest

from aws_tui.domain.filesystem import ProviderError
from aws_tui.vm.athena._pager_compat import SnapshotTokenPager, seed_token_pager


@pytest.mark.asyncio
async def test_seed_token_pager_contract_matches_vmx_3_23() -> None:
    assert version("vmx").partition(".")[0] == "3"
    calls: list[str | None] = []

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        return ["fetched"], None

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    source = ["first"]

    seed_token_pager(pager, source, "next")
    source.append("mutated")

    assert pager.items == ["first"]
    assert pager.current_token == "next"
    assert pager.has_more
    assert calls == []

    await pager.load_more_command.execute_async()

    assert calls == ["next"]
    assert pager.items == ["first", "fetched"]
    assert pager.current_token is None
    assert not pager.has_more


@pytest.mark.asyncio
async def test_snapshot_token_pager_refresh_replaces_restored_items() -> None:
    calls: list[str | None] = []

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        return ["fresh"], "fresh-next"

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    seed_token_pager(pager, ["restored"], "stale-next")

    await pager.refresh_command.execute_async()

    assert calls == [None]
    assert pager.items == ["fresh"]
    assert pager.current_token == "fresh-next"


@pytest.mark.asyncio
async def test_snapshot_token_pager_refresh_preserves_loaded_later_pages_for_unchanged_prefix() -> (
    None
):
    calls: list[str | None] = []

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        if token is None:
            return ["first", "second"], "page-2"
        if token == "page-2":
            return ["third"], "page-3"
        if token == "page-3":
            return ["fourth"], None
        raise AssertionError(f"unexpected token: {token}")

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    await pager.load_more_command.execute_async()
    await pager.load_more_command.execute_async()
    resets: list[object] = []
    properties: list[str] = []
    pager.on_collection_changed.subscribe(resets.append)
    pager.on_property_changed.subscribe(properties.append)

    await pager.refresh_command.execute_async()

    assert calls == [None, "page-2", None]
    assert pager.items == ["first", "second", "third"]
    assert pager.current_token == "page-3"
    assert resets == []
    assert properties == ["items", "current_token", "has_more"]

    await pager.load_more_command.execute_async()

    assert calls == [None, "page-2", None, "page-3"]
    assert pager.items == ["first", "second", "third", "fourth"]
    assert pager.current_token is None
    assert len(resets) == 1
    assert properties == [
        "items",
        "current_token",
        "has_more",
        "items",
        "current_token",
        "has_more",
    ]


@pytest.mark.asyncio
async def test_snapshot_token_pager_refresh_replaces_items_when_first_page_boundary_changes() -> (
    None
):
    calls: list[str | None] = []
    first_page_calls = 0

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        nonlocal first_page_calls
        calls.append(token)
        if token is None:
            first_page_calls += 1
            if first_page_calls == 1:
                return ["a", "b"], "page-2"
            return ["a", "b", "c"], "refreshed-next"
        if token == "page-2":
            return ["c", "d"], "page-3"
        if token == "refreshed-next":
            return ["e"], None
        raise AssertionError(f"unexpected token: {token}")

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    await pager.load_more_command.execute_async()
    await pager.load_more_command.execute_async()
    resets: list[object] = []
    properties: list[str] = []
    pager.on_collection_changed.subscribe(resets.append)
    pager.on_property_changed.subscribe(properties.append)

    await pager.refresh_command.execute_async()

    assert pager.items == ["a", "b", "c"]
    assert pager.current_token == "refreshed-next"
    assert len(resets) == 1
    assert properties == ["items", "current_token", "has_more"]

    await pager.load_more_command.execute_async()

    assert calls == [None, "page-2", None, "refreshed-next"]
    assert pager.items == ["a", "b", "c", "e"]


@pytest.mark.asyncio
async def test_snapshot_token_pager_refresh_replaces_suffix_when_first_page_token_changes() -> None:
    calls: list[str | None] = []
    first_page_calls = 0

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        nonlocal first_page_calls
        calls.append(token)
        if token is None:
            first_page_calls += 1
            next_token = "old-page-2" if first_page_calls == 1 else "new-page-2"
            return ["a", "b"], next_token
        if token == "old-page-2":
            return ["c", "d"], "old-terminal"
        if token == "new-page-2":
            return ["c", "d"], "new-terminal"
        if token == "new-terminal":
            return ["e"], None
        if token == "old-terminal":
            return ["stale"], None
        raise AssertionError(f"unexpected token: {token}")

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    await pager.load_more_command.execute_async()
    await pager.load_more_command.execute_async()
    resets: list[object] = []
    properties: list[str] = []
    pager.on_collection_changed.subscribe(resets.append)
    pager.on_property_changed.subscribe(properties.append)

    await pager.refresh_command.execute_async()

    assert pager.items == ["a", "b"]
    assert pager.current_token == "new-page-2"
    assert len(resets) == 1
    assert properties == ["items", "current_token", "has_more"]

    await pager.load_more_command.execute_async()
    await pager.load_more_command.execute_async()

    assert calls == [None, "old-page-2", None, "new-page-2", "new-terminal"]
    assert pager.items == ["a", "b", "c", "d", "e"]
    assert pager.current_token is None
    assert len(resets) == 3


@pytest.mark.asyncio
async def test_snapshot_token_pager_preserves_terminal_token_after_empty_later_page() -> None:
    calls: list[str | None] = []

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        if token is None:
            return ["first"], "page-2"
        if token == "page-2":
            return [], "page-3"
        if token == "page-3":
            return ["last"], None
        raise AssertionError(f"unexpected token: {token}")

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    await pager.load_more_command.execute_async()
    await pager.load_more_command.execute_async()
    resets: list[object] = []
    pager.on_collection_changed.subscribe(resets.append)

    await pager.refresh_command.execute_async()

    assert pager.items == ["first"]
    assert pager.current_token == "page-3"
    assert resets == []

    await pager.load_more_command.execute_async()

    assert calls == [None, "page-2", None, "page-3"]
    assert pager.items == ["first", "last"]
    assert pager.current_token is None


@pytest.mark.asyncio
async def test_snapshot_token_pager_restore_treats_snapshot_as_one_conservative_page() -> None:
    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        assert token is None
        return ["a", "b"], "page-2"

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    seed_token_pager(pager, ["a", "b", "c", "d"], "page-3")
    resets: list[object] = []
    pager.on_collection_changed.subscribe(resets.append)

    await pager.refresh_command.execute_async()

    assert pager.items == ["a", "b"]
    assert pager.current_token == "page-2"
    assert len(resets) == 1


@pytest.mark.asyncio
async def test_snapshot_token_pager_rejects_immediate_token_repeat_without_appending() -> None:
    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        if token is None:
            return ["first"], "repeat"
        return ["duplicate"], "repeat"

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    await pager.load_more_command.execute_async()

    with pytest.raises(ProviderError, match=r"repeated.*continuation token"):
        await pager.load_more_command.execute_async()

    assert pager.items == ["first"]
    assert pager.current_token is None
    assert not pager.has_more


@pytest.mark.asyncio
async def test_snapshot_token_pager_rejects_multi_page_token_cycle_without_appending() -> None:
    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        if token is None:
            return ["first"], "page-a"
        if token == "page-a":
            return ["second"], "page-b"
        if token == "page-b":
            return ["duplicate"], "page-a"
        raise AssertionError(f"unexpected token: {token}")

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    await pager.load_more_command.execute_async()
    await pager.load_more_command.execute_async()

    with pytest.raises(ProviderError, match=r"repeated.*continuation token"):
        await pager.load_more_command.execute_async()

    assert pager.items == ["first", "second"]
    assert pager.current_token is None
    assert not pager.has_more


@pytest.mark.asyncio
async def test_snapshot_token_pager_rejects_cumulative_items_beyond_limit() -> None:
    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        if token is None:
            return ["first"], "next"
        return ["second", "third"], "more"

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch, max_items=2)
    await pager.load_more_command.execute_async()

    with pytest.raises(ProviderError, match="collection safety limit"):
        await pager.load_more_command.execute_async()

    assert pager.items == ["first"]
    assert pager.current_token is None
    assert not pager.has_more


def test_seed_token_pager_uses_aws_tui_owned_public_restore_boundary() -> None:
    class IncompatiblePager:
        def restore(self, items: object, next_token: object) -> None:
            del items, next_token

    with pytest.raises(TypeError, match="SnapshotTokenPager"):
        seed_token_pager(IncompatiblePager(), ["value"], None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_refresh_retires_an_in_flight_continuation_page() -> None:
    continuation_started = asyncio.Event()
    release_continuation = asyncio.Event()

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        if token == "next":
            continuation_started.set()
            await release_continuation.wait()
            return ["stale-page"], None
        return ["fresh-page"], "fresh-next"

    pager: SnapshotTokenPager[str, str] = SnapshotTokenPager(fetch)
    seed_token_pager(pager, ["seed"], "next")
    continuation = asyncio.create_task(pager.load_more_command.execute_async())
    await continuation_started.wait()

    await pager.refresh_command.execute_async()
    release_continuation.set()
    await continuation

    assert pager.items == ["fresh-page"]
    assert pager.current_token == "fresh-next"
