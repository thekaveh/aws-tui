from pathlib import Path

CONNECTIONS_DOC = Path(__file__).parents[2] / "docs" / "connections.md"


def test_connections_docs_scope_reachability_to_s3_panes() -> None:
    text = " ".join(CONNECTIONS_DOC.read_text(encoding="utf-8").split())

    assert (
        "`Shift+S` filters out connections that have been observed unreachable"
        " during the session in S3 panes"
    ) in text
    assert (
        "Single-context AWS services, including EMR Serverless and Glue,"
        " intentionally do not consult or mutate the S3"
        " pane reachability set"
    ) in text
    assert "Authentication and service API failures remain visible in the mounted service" in text
