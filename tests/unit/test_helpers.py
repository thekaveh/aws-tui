"""Coverage for the shared test helpers themselves."""

from __future__ import annotations

import pytest

from tests.helpers import WAIT_UNTIL_TIMEOUT_SECONDS, wait_until


@pytest.mark.asyncio
async def test_wait_until_names_the_condition_that_never_settled() -> None:
    """The diagnostic is the reason this helper exists rather than a sleep.

    Mutation testing covered the waiting but not the bound, so the failure
    path shipped untested.
    """
    with pytest.raises(AssertionError) as excinfo:
        await wait_until(lambda: False, what="the thing never happened", timeout=0.05)

    assert "the thing never happened" in str(excinfo.value)
    assert "0.05" in str(excinfo.value)


@pytest.mark.asyncio
async def test_wait_until_returns_immediately_when_already_satisfied() -> None:
    await wait_until(lambda: True, what="already true", timeout=0.05)


def test_wait_until_default_leaves_room_for_two_waits_under_pytests_kill() -> None:
    """Two sequential waits must not outlast pytest's own per-test timeout.

    `pyproject.toml` sets `timeout = 60`. A 30s default meant a test that waits
    twice lost the race to pytest's kill, and the caller got a bare
    `Failed: Timeout` instead of the named condition above.
    """
    assert 2 * WAIT_UNTIL_TIMEOUT_SECONDS < 60
