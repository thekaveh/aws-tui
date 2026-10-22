"""A saved workspace survives an actual Python process boundary."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(("active", "size"), [("s3", (80, 24)), ("athena", (120, 40))])
def test_keyboard_save_then_list_and_open_in_distinct_processes(tmp_path, active, size):
    (tmp_path / "config").mkdir()
    (tmp_path / "local" / "chosen").mkdir(parents=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[connections.dev]\nkind="aws"\nregion="us-east-1"\n'
        'profile="PROFILE_MUST_NOT_BE_SERIALIZED_263"\n'
        '[connections.other]\nkind="aws"\nregion="us-west-2"\n'
        '[defaults]\nconnection="dev"\n'
    )
    for mode in ("save", "open"):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "tests.workspace_process_helper",
                str(tmp_path),
                mode,
                active,
                str(size[0]),
                str(size[1]),
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads((tmp_path / "save.json").read_text())
    opened = json.loads((tmp_path / "open.json").read_text())
    assert len({os.getpid(), saved["pid"], opened["pid"]}) == 3
    assert saved["workspace"] == opened["workspace"]
    raw = (tmp_path / "config" / "workspaces.json").read_text()
    assert "WORKSPACE_MUST_NOT_STORE_SQL_263" not in raw
    assert "PROFILE_MUST_NOT_BE_SERIALIZED_263" not in raw
    stored = json.loads(raw)
    assert set(stored) == {"schema_version", "workspaces"}
    assert len(stored["workspaces"]) == 1
    assert len(opened["workspace"]["scopes"]) == 6
    for clients in opened["calls"].values():
        for names in clients.values():
            assert all(name.startswith(("list", "get", "stat")) for name in names)
            assert "get_query_results" not in names
