from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from aws_tui.demo.in_memory_athena import InMemoryAthena
from aws_tui.demo.seeds import seeded_demo_athena
from aws_tui.domain.filesystem import ValidationError
from aws_tui.domain.query import QueryContext, QueryState
from aws_tui.domain.sql_policy import QueryRejectedError

DEV_CONTEXT = QueryContext("demo-dev", "us-east-1", "dev-analytics", "DevDataCatalog", "dev_events")
REQUEST_TOKEN = "reuse-token".ljust(32, "-")


def _execution_state(fake: InMemoryAthena) -> tuple[object, ...]:
    return (
        tuple(fake.query_executions),
        tuple(fake.result_pages),
        tuple((key, tuple(value)) for key, value in fake.history.items()),
        tuple(fake._request_tokens.items()),
        fake._next_execution_number,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [0, 60, 10_080])
async def test_demo_accepts_reuse_age_and_reports_ordinary_execution(age: int) -> None:
    fake = seeded_demo_athena("demo-dev")

    ref = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token=REQUEST_TOKEN,
        result_reuse_max_age_minutes=age,
    )
    states = [(await fake.get_query_execution(ref.execution_id)).summary.state for _ in range(3)]

    assert states == [QueryState.QUEUED, QueryState.RUNNING, QueryState.SUCCEEDED]
    assert fake.query_executions[ref.execution_id].statistics.reused_previous_result is False
    start = next(call for call in fake.calls if call.method == "start_query")
    assert start.arguments == ("SELECT 1", DEV_CONTEXT, REQUEST_TOKEN, None, age)


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [-1, 10_081, True, False, "60", 1.0, object()])
async def test_demo_rejects_invalid_reuse_age_without_execution(age: object) -> None:
    fake = seeded_demo_athena("demo-dev")
    before = _execution_state(fake)

    with pytest.raises(ValidationError, match="reuse age"):
        await fake.start_query(
            "SELECT 1",
            DEV_CONTEXT,
            request_token=REQUEST_TOKEN,
            result_reuse_max_age_minutes=cast(int, age),
        )

    assert _execution_state(fake) == before


@pytest.mark.asyncio
async def test_demo_reuse_rejects_caller_output_even_when_workgroup_enforces_output() -> None:
    fake = seeded_demo_athena("demo-dev")
    before = _execution_state(fake)

    with pytest.raises(ValidationError, match="caller output"):
        await fake.start_query(
            "SELECT 1",
            DEV_CONTEXT,
            request_token=REQUEST_TOKEN,
            output_location="s3://caller-results/private/",
            result_reuse_max_age_minutes=60,
        )

    assert _execution_state(fake) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["missing", "disabled", "mismatched", "managed"])
async def test_demo_reuse_requires_current_eligible_workgroup(metadata: str) -> None:
    fake = seeded_demo_athena("demo-dev")
    detail = fake.workgroup_details[DEV_CONTEXT.workgroup]
    if metadata == "missing":
        del fake.workgroup_details[DEV_CONTEXT.workgroup]
    elif metadata == "disabled":
        fake.workgroup_details[DEV_CONTEXT.workgroup] = replace(
            detail, summary=replace(detail.summary, state="DISABLED")
        )
    elif metadata == "mismatched":
        fake.workgroup_details[DEV_CONTEXT.workgroup] = replace(
            detail, summary=replace(detail.summary, name="another-workgroup")
        )
    else:
        fake.workgroup_details[DEV_CONTEXT.workgroup] = replace(
            detail, managed_query_results_enabled=True
        )
    before = _execution_state(fake)

    with pytest.raises(ValidationError):
        await fake.start_query(
            "SELECT 1",
            DEV_CONTEXT,
            request_token=REQUEST_TOKEN,
            result_reuse_max_age_minutes=60,
        )

    assert _execution_state(fake) == before


@pytest.mark.asyncio
async def test_demo_replays_unchanged_enabled_request_once() -> None:
    fake = seeded_demo_athena("demo-dev")
    first = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token=REQUEST_TOKEN,
        result_reuse_max_age_minutes=60,
    )
    before = _execution_state(fake)

    replay = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token=REQUEST_TOKEN,
        result_reuse_max_age_minutes=60,
    )

    assert replay == first
    assert _execution_state(fake) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(("first_age", "next_age"), [(60, 61), (60, None), (None, 60)])
async def test_demo_rejects_changed_reuse_option_with_existing_token(
    first_age: int | None, next_age: int | None
) -> None:
    fake = seeded_demo_athena("demo-dev")
    await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token=REQUEST_TOKEN,
        result_reuse_max_age_minutes=first_age,
    )
    before = _execution_state(fake)

    with pytest.raises(ValidationError, match="different query parameters"):
        await fake.start_query(
            "SELECT 1",
            DEV_CONTEXT,
            request_token=REQUEST_TOKEN,
            result_reuse_max_age_minutes=next_age,
        )

    assert _execution_state(fake) == before


@pytest.mark.asyncio
async def test_demo_explicitly_disabled_reuse_preserves_default_call_and_replay() -> None:
    fake = seeded_demo_athena("demo-dev")
    first = await fake.start_query("SELECT 1", DEV_CONTEXT, request_token=REQUEST_TOKEN)

    replay = await fake.start_query(
        "SELECT 1",
        DEV_CONTEXT,
        request_token=REQUEST_TOKEN,
        result_reuse_max_age_minutes=None,
    )

    assert replay == first
    assert all(
        call.arguments == ("SELECT 1", DEV_CONTEXT, REQUEST_TOKEN, None) for call in fake.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "error"), [("DROP TABLE events", QueryRejectedError), ("SELECT 404", ValidationError)]
)
async def test_demo_reuse_preserves_read_only_and_seeded_sql_policy(
    sql: str, error: type[Exception]
) -> None:
    fake = seeded_demo_athena("demo-dev")
    before = _execution_state(fake)

    with pytest.raises(error):
        await fake.start_query(
            sql,
            DEV_CONTEXT,
            request_token=REQUEST_TOKEN,
            result_reuse_max_age_minutes=60,
        )

    assert _execution_state(fake) == before
