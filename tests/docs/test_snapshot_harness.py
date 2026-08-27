from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


def test_demo_snapshot_harness_does_not_force_private_iceberg_refresh() -> None:
    source = (REPO_ROOT / "tests/snapshot/test_demo_mode.py").read_text()
    assert "iceberg_view._refresh()" not in source


def test_demo_snapshot_harness_does_not_force_private_pane_refresh() -> None:
    source = (REPO_ROOT / "tests/snapshot/test_demo_mode.py").read_text()
    assert "pane._refresh_chrome()" not in source
