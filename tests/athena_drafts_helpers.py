"""Pure, real-store fixtures shared by Athena draft tests."""

from datetime import UTC, datetime
from pathlib import Path

from aws_tui.infra.athena_draft_store import AthenaDraftStore, DraftContext, SqlDraft, draft_id
from aws_tui.infra.config_store import ConfigStore

CTX: DraftContext = ("analytics", "us-west-2", "primary", "AwsDataCatalog", "default")
STAMP = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)


def record(context: DraftContext = CTX, sql: str = "SELECT 1") -> SqlDraft:
    return SqlDraft(draft_id(context), context, sql, STAMP, STAMP)


def store_at(tmp_path: Path) -> AthenaDraftStore:
    config = ConfigStore(path=tmp_path / "config.toml")
    return AthenaDraftStore(config=config, directory=tmp_path / "athena-drafts")
