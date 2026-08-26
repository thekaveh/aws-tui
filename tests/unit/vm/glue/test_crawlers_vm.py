from __future__ import annotations

import asyncio

import pytest
from vmx import NULL_DISPATCHER, MessageHub, TokenPagedComposition
from vmx.messages.protocols import Message

from aws_tui.domain.filesystem import PermissionDeniedError, ProviderError
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.glue.crawlers_vm import GlueCrawlersVM
from tests.unit.vm.glue._fake_glue import InMemoryGlue, seeded_glue


def make_crawlers_vm(fake: InMemoryGlue) -> GlueCrawlersVM:
    hub: MessageHub[Message] = MessageHub()
    vm = GlueCrawlersVM(client=fake, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    return vm


@pytest.mark.asyncio
async def test_crawlers_page_and_load_detail() -> None:
    fake = seeded_glue()
    fake.crawler_page_size = 1
    vm = make_crawlers_vm(fake)

    assert isinstance(vm._crawler_pager, TokenPagedComposition)  # type: ignore[attr-defined]
    await vm.setup()
    assert len(vm.crawlers) == 1
    assert vm.has_more_crawlers
    await vm.load_more_crawlers()
    assert fake.crawler_requests == [(None, None), ("1", None)]

    await vm.select_crawler("ready-crawler")
    assert vm.crawler_detail is not None
    assert vm.crawler_detail.summary.name == "ready-crawler"


@pytest.mark.asyncio
async def test_crawler_pager_rejects_multi_token_cycle_before_mutation() -> None:
    fake = seeded_glue()
    lineage = {None: "A", "A": "B", "B": "A"}
    original = fake.list_crawlers_page

    async def crawlers(
        *,
        start_token: str | None = None,
        state: str | None = None,
    ) -> tuple[list, str | None]:
        rows, _ = await original(start_token=None, state=state)
        return rows, lineage[start_token]

    fake.list_crawlers_page = crawlers  # type: ignore[method-assign]
    vm = make_crawlers_vm(fake)
    pager = vm._make_crawler_pager()
    try:
        await pager.refresh_command.execute_async()
        await pager.load_more_command.execute_async()
        before = pager.items
        with pytest.raises(ProviderError, match="crawler continuation"):
            await pager.load_more_command.execute_async()
        assert pager.items == before
    finally:
        pager.dispose()
        vm.dispose()


@pytest.mark.asyncio
async def test_crawler_filter_replaces_pager_and_drops_old_token() -> None:
    fake = seeded_glue()
    fake.crawler_page_size = 1
    vm = make_crawlers_vm(fake)
    await vm.setup()
    old_pager = vm._crawler_pager  # type: ignore[attr-defined]
    await vm.load_more_crawlers()

    await vm.set_state_filter("RUNNING")

    assert old_pager._disposed  # type: ignore[attr-defined]
    assert old_pager.refresh_command._disposed  # type: ignore[attr-defined]
    assert vm.state_filter == "RUNNING"
    assert [crawler.state for crawler in vm.crawlers] == ["RUNNING"]
    assert fake.crawler_requests[-1] == (None, "RUNNING")


@pytest.mark.asyncio
async def test_select_crawler_discards_stale_detail() -> None:
    fake = seeded_glue()
    detail_started = fake.block_crawler_detail("ready-crawler")
    vm = make_crawlers_vm(fake)
    await vm.setup()

    first = asyncio.create_task(vm.select_crawler("ready-crawler"))
    await detail_started.wait()
    await vm.select_crawler("running-crawler")
    fake.release_crawler_detail("ready-crawler")
    await first

    assert vm.selected_crawler_name == "running-crawler"
    assert vm.crawler_detail is not None
    assert vm.crawler_detail.summary.name == "running-crawler"


@pytest.mark.asyncio
async def test_lake_formation_denial_is_forbidden_and_redacted() -> None:
    from aws_tui.domain.glue import LakeFormationPermissionError

    class BrokenCrawlers(InMemoryGlue):
        async def list_crawlers_page(  # type: ignore[override]
            self,
            *,
            start_token: str | None = None,
            state: str | None = None,
        ) -> tuple[list, str | None]:
            raise LakeFormationPermissionError("Lake Formation denied Authorization: Bearer SECRET")

    vm = make_crawlers_vm(BrokenCrawlers())
    await vm.setup()

    assert vm.state is PaneState.FORBIDDEN
    assert vm.error_text is not None
    assert "SECRET" not in vm.error_text
    assert "[REDACTED]" in vm.error_text


@pytest.mark.asyncio
async def test_plain_crawler_access_denial_is_forbidden() -> None:
    fake = seeded_glue()
    fake.crawlers_error = PermissionDeniedError("glue:GetCrawlers denied")
    vm = make_crawlers_vm(fake)
    await vm.setup()
    assert vm.state is PaneState.FORBIDDEN
    assert vm.error_text == "glue:GetCrawlers denied"


@pytest.mark.asyncio
async def test_crawler_load_more_unexpected_error_is_scoped_and_redacted() -> None:
    class BrokenNextPage(InMemoryGlue):
        async def list_crawlers_page(
            self,
            *,
            start_token: str | None = None,
            state: str | None = None,
        ) -> tuple[list, str | None]:
            if start_token is not None:
                raise RuntimeError("Authorization: Bearer CRAWLER_SECRET")
            return await super().list_crawlers_page(start_token=start_token, state=state)

    fake = BrokenNextPage()
    fake.add_crawler("first")
    fake.add_crawler("second")
    fake.crawler_page_size = 1
    vm = make_crawlers_vm(fake)

    await vm.setup()
    await vm.load_more_crawlers()

    assert vm.state is PaneState.ERROR
    assert vm.error_text is not None
    assert "CRAWLER_SECRET" not in vm.error_text
    assert "[REDACTED]" in vm.error_text


@pytest.mark.asyncio
async def test_crawlers_dispose_invalidates_blocked_load_without_notifications() -> None:
    fake = seeded_glue()
    detail_started = fake.block_crawler_detail("ready-crawler")
    vm = make_crawlers_vm(fake)
    await vm.setup()
    notifications: list[str] = []
    subscription = vm.on_property_changed.subscribe(on_next=notifications.append)

    selection = asyncio.create_task(vm.select_crawler("ready-crawler"))
    await detail_started.wait()
    notifications.clear()
    generations = (
        vm._crawler_generation,  # type: ignore[attr-defined]
        vm._detail_generation,  # type: ignore[attr-defined]
    )

    vm.dispose()
    fake.release_crawler_detail("ready-crawler")
    await selection

    assert notifications == []
    assert (
        vm._crawler_generation,  # type: ignore[attr-defined]
        vm._detail_generation,  # type: ignore[attr-defined]
    ) == tuple(generation + 1 for generation in generations)
    subscription.dispose()


def test_crawlers_dispose_reaches_pager_commands_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vm = make_crawlers_vm(seeded_glue())
    pager = vm._crawler_pager  # type: ignore[attr-defined]
    calls = 0
    original = pager.dispose

    def counted_dispose() -> None:
        nonlocal calls
        calls += 1
        original()

    monkeypatch.setattr(pager, "dispose", counted_dispose)
    vm.dispose()
    vm.dispose()

    assert calls == 1
    assert pager._disposed  # type: ignore[attr-defined]
    assert pager.load_more_command._disposed  # type: ignore[attr-defined]
    assert pager.refresh_command._disposed  # type: ignore[attr-defined]
