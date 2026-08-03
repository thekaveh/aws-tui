from __future__ import annotations

import json
import re
from pathlib import Path

from aws_tui.domain.transfer_journal import TransferJournal


def test_published_transfer_journal_records_replay_with_the_real_parser(tmp_path: Path) -> None:
    repo_root = Path(__file__).parents[2]
    examples: list[tuple[Path, str]] = []
    for relative in ("docs/cookbook.md", "docs/recording-todo.md"):
        path = repo_root / relative
        records = re.findall(
            r'^\{"kind":"begin","transfer_id":.*\}$',
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        assert records, f"no transfer journal example found in {relative}"
        examples.extend((path, record) for record in records)

    for index, (source, raw) in enumerate(examples):
        record = json.loads(raw)
        transfer_id = record["transfer_id"]
        example_dir = tmp_path / str(index)
        example_dir.mkdir()
        (example_dir / f"{transfer_id}.jsonl").write_text(raw + "\n", encoding="utf-8")

        replayed = TransferJournal(base_dir=example_dir).find_unfinished()

        assert [entry.transfer_id for entry in replayed] == [transfer_id], source
