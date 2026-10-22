"""Reader-task order, consequential limits and compatibility links for service guides."""

from pathlib import Path

import markdown
import pytest

ROOT = Path(__file__).resolve().parents[2]


def headings(document):
    return [
        line.removeprefix("## ").split(". ", 1)[-1]
        for line in document.splitlines()
        if line.startswith("## ")
    ]


@pytest.mark.parametrize(
    ("service", "tasks"),
    [
        (
            "s3",
            [
                "Sources and navigation",
                "Loaded listing controls",
                "Current-object properties",
                "Historical object versions",
                "File operations",
                "Transfers and recovery",
                "Safety and limits",
            ],
        ),
        (
            "athena",
            [
                "Query context",
                "Author SQL and use local files",
                "Read-only execution policy",
                "Query lifecycle and results",
                "Inspect and control loaded results",
                "Local SQL draft recovery",
                "Cross-service handoffs",
            ],
        ),
    ],
)
def test_related_user_tasks_precede_implementation_references(service, tasks):
    guide = (ROOT / "docs" / "services" / f"{service}.md").read_text()
    actual = headings(guide)
    assert actual[: len(tasks)] == tasks
    assert actual[-1] == "Further reading and demo"
    assert not any(
        "Architecture" in heading or "Verification" in heading for heading in actual[:-1]
    )


@pytest.mark.parametrize(
    ("service", "fragments"),
    [
        (
            "s3",
            [
                "2-file-operations",
                "21-reviewed-recursive-folder-upload",
                "22-reviewed-one-way-sync",
                "3-architecture",
                "4-safety-and-limits",
                "5-verification-and-demo",
                "6-loaded-listing-controls",
                "7-current-object-properties",
                "8-historical-object-versions",
            ],
        ),
        (
            "athena",
            [
                "2-read-only-execution-policy",
                "3-query-lifecycle-and-results",
                "4-cross-service-handoffs",
                "5-architecture-and-verification",
                "6-local-sql-draft-recovery",
                "7-inspect-and-control-loaded-results",
                "8-author-sql-and-use-local-files",
                "81-complete-and-format-sql",
                "82-open-and-save-sql-files",
                "83-keep-local-snippets",
            ],
        ),
    ],
)
def test_published_service_fragments_keep_resolving_after_reordering(service, fragments):
    guide = (ROOT / "docs" / "services" / f"{service}.md").read_text()
    html = markdown.markdown(guide, extensions=["toc", "tables"])
    for fragment in fragments:
        assert f'id="{fragment}"' in html
