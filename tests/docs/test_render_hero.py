import shutil
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from scripts.docs.render_hero import (
    FONT_DIR,
    SOURCE,
    TARGET,
    _outline_terminal_text,
    _same_png_pixels,
    main,
    render,
)


def _render_or_skip(root: Path) -> bytes:
    try:
        return render(root)
    except OSError as exc:
        pytest.skip(f"cairosvg/libcairo unavailable: {exc}")


def test_committed_hero_matches_canonical_demo_snapshot() -> None:
    root = Path.cwd()
    assert (root / SOURCE).is_file()
    assert _same_png_pixels((root / TARGET).read_bytes(), _render_or_skip(root))


def test_hero_renderer_vendors_both_fira_code_weights() -> None:
    root = Path.cwd()
    assert (root / FONT_DIR / "FiraCode-Regular.ttf").stat().st_size > 200_000
    assert (root / FONT_DIR / "FiraCode-Bold.ttf").stat().st_size > 200_000
    assert (root / FONT_DIR / "LICENSE.txt").is_file()


def test_hero_comparison_ignores_png_compression() -> None:
    image = Image.new("RGB", (2, 2), "#123456")
    compact = BytesIO()
    verbose = BytesIO()
    image.save(compact, format="PNG", compress_level=9)
    image.save(verbose, format="PNG", compress_level=0)

    assert compact.getvalue() != verbose.getvalue()
    assert _same_png_pixels(compact.getvalue(), verbose.getvalue())


def test_hero_comparison_rejects_visible_changes() -> None:
    original = BytesIO()
    changed = BytesIO()
    Image.new("RGB", (20, 20), "#123456").save(original, format="PNG")
    Image.new("RGB", (20, 20), "#654321").save(changed, format="PNG")

    assert not _same_png_pixels(original.getvalue(), changed.getvalue())


def test_terminal_unicode_is_converted_to_font_outlines() -> None:
    root = Path.cwd()
    outlined = _outline_terminal_text(
        (root / SOURCE).read_bytes(),
        regular=root / FONT_DIR / "FiraCode-Regular.ttf",
        bold=root / FONT_DIR / "FiraCode-Bold.ttf",
    ).decode("utf-8")

    assert "█████" not in outlined
    assert "Commands" not in outlined
    assert outlined.count("<path") > 1_000


def test_render_hero_check_reports_stale_target(tmp_path: Path, monkeypatch) -> None:
    root = Path.cwd()
    source = tmp_path / SOURCE
    source.parent.mkdir(parents=True)
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes(b"stale")
    shutil.copytree(root / FONT_DIR, tmp_path / FONT_DIR)
    monkeypatch.chdir(tmp_path)

    _render_or_skip(tmp_path)

    assert main(["--check"]) == 1
