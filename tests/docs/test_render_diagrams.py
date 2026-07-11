import textwrap

import pytest
from scripts.docs.manifest import parse_manifest
from scripts.docs.render_diagrams import copy_assets, extract_svg, render_all, svg_to_png


def _require_cairosvg():
    try:
        import cairosvg  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(f"cairosvg/libcairo unavailable: {exc}")


def test_extract_svg_pulls_inline_svg():
    html = "<html><body><svg width='10'><rect/></svg></body></html>"
    assert extract_svg(html) == "<svg width='10'><rect/></svg>"


def test_extract_svg_sanitizes_named_entities():
    html = "<svg><text>A &middot; B &Sigma; C &amp; D &#160; E</text></svg>"
    out = extract_svg(html)
    assert "&middot;" not in out
    assert "&Sigma;" not in out
    assert "·" in out
    assert "Σ" in out
    assert "&amp;" in out  # standard XML entity preserved
    assert "&#160;" in out  # numeric entity preserved


def test_extract_svg_raises_when_absent():
    with pytest.raises(ValueError, match="no <svg>"):
        extract_svg("<html>nope</html>")


def test_svg_to_png_writes_png_magic(tmp_path):
    _require_cairosvg()
    svg = "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'><rect width='4' height='4' fill='red'/></svg>"
    out = tmp_path / "x.png"
    svg_to_png(svg, out, width=4)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_all_writes_svg_and_png(tmp_path):
    _require_cairosvg()
    (tmp_path / "docs" / "diagrams").mkdir(parents=True)
    (tmp_path / "docs" / "diagrams" / "d.html").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'><rect width='4' height='4'/></svg>"
    )
    m = parse_manifest(
        textwrap.dedent(
            """
            surfaces: [site]
            numbering: per-doc
            sections: [{id: overview, title: O, source: docs/diagrams/d.html}]
            diagrams: [{id: system, master: docs/diagrams/d.html}]
            """
        )
    )
    site_img = tmp_path / "generated" / "site" / "assets" / "img"
    png_dir = tmp_path / "docs" / "diagrams" / "img"
    render_all(m, tmp_path, site_img, png_dir)
    assert (site_img / "system.svg").is_file()
    assert (png_dir / "system.png").read_bytes()[:4] == b"\x89PNG"


def test_copy_assets_copies_pngs(tmp_path):
    src = tmp_path / "docs" / "diagrams" / "img"
    src.mkdir(parents=True)
    (src / "system.png").write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    wiki_img = tmp_path / "generated" / "wiki" / "img"
    copy_assets(tmp_path, wiki_img)
    assert (wiki_img / "system.png").read_bytes().startswith(b"\x89PNG")
