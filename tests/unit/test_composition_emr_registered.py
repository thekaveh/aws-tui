"""Pin that EmrServerlessService is registered post-PR-A.

The nav rail is order-sensitive — services appear in registration
order, so ⚡ EMR must sit AFTER 🪣 S3 in the registry."""

from __future__ import annotations

from pathlib import Path

from aws_tui.composition import build_app_context


def test_emr_serverless_service_registered_after_s3(tmp_path: Path) -> None:
    ctx = build_app_context(config_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    try:
        ids = [s.descriptor.id for s in ctx.root_vm._registry.all()]  # type: ignore[attr-defined]
        assert "s3" in ids
        assert "emr-serverless" in ids
        assert ids.index("s3") < ids.index("emr-serverless")
    finally:
        ctx.root_vm.dispose()
