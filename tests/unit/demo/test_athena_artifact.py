from dataclasses import replace

import pytest

from aws_tui.demo.in_memory_athena import serialize_result_pages_csv
from aws_tui.demo.seeds import seeded_demo_athena, seeded_demo_fs
from aws_tui.domain.filesystem import ValidationError


async def test_demo_original_artifact_reads_seeded_bytes_without_starting_sql():
    fs = seeded_demo_fs("demo-dev")
    client = seeded_demo_athena("demo-dev", result_store=fs)
    detail = await client.get_query_execution("q-dev-succeeded")
    expected = serialize_result_pages_csv(client._result_page_order["q-dev-succeeded"])
    chunks = [
        part async for part in client.iter_result_artifact(detail, max_bytes=10000, chunk_bytes=3)
    ]
    assert b"".join(chunks) == expected
    assert all(0 < len(chunk) <= 3 for chunk in chunks)
    assert not any(call.method == "start_query" for call in client.calls)


@pytest.mark.parametrize("reason", ["managed", "stale", "limit"])
async def test_demo_artifact_refuses_unsupported_changed_or_oversized_export(reason):
    client = seeded_demo_athena("demo-dev", result_store=seeded_demo_fs("demo-dev"))
    detail = await client.get_query_execution("q-dev-succeeded")
    if reason == "managed":
        detail = replace(detail, managed_query_results_enabled=True)
        client.query_executions["q-dev-succeeded"] = detail
    elif reason == "stale":
        detail = replace(detail, context=replace(detail.context, database="other"))
    with pytest.raises(ValidationError):
        _ = [
            part
            async for part in client.iter_result_artifact(
                detail, max_bytes=0 if reason == "limit" else 10000
            )
        ]
