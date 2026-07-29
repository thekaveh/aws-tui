from __future__ import annotations

from importlib.metadata import version

import pytest
from vmx.collections.token_paged_composition import TokenPagedComposition

from aws_tui.vm.athena._pager_compat import seed_token_pager


@pytest.mark.asyncio
async def test_seed_token_pager_contract_matches_vmx_3_1_0() -> None:
    assert version("vmx").partition(".")[0] == "3"
    calls: list[str | None] = []

    async def fetch(token: str | None) -> tuple[list[str], str | None]:
        calls.append(token)
        return ["fetched"], None

    pager: TokenPagedComposition[str, str] = TokenPagedComposition(fetch)
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


def test_seed_token_pager_fails_clearly_when_vmx_contract_changes() -> None:
    class IncompatiblePager:
        pass

    with pytest.raises(RuntimeError, match="VMx TokenPagedComposition internals changed"):
        seed_token_pager(IncompatiblePager(), ["value"], None)  # type: ignore[arg-type]
