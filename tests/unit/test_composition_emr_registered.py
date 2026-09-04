"""Pin the complete built-in service registry and navigation order."""

from __future__ import annotations

from pathlib import Path

from aws_tui.composition import build_app_context


def test_all_builtin_services_are_registered_in_navigation_order(tmp_path: Path) -> None:
    ctx = build_app_context(config_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    try:
        ids = [s.descriptor.id for s in ctx.root_vm._registry.all()]  # type: ignore[attr-defined]
        assert ids == ["s3", "emr-serverless", "glue", "athena"]
    finally:
        ctx.root_vm.dispose()
