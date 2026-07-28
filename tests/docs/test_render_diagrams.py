import os
import stat
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

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


def test_extract_svg_escapes_xml_significant_html_aliases():
    html = (
        "<svg><text data-amp='&AMP;' data-lt='&LT;' data-quote='&QUOT;'>"
        "&AMP;&LT;&QUOT;</text></svg>"
    )

    out = extract_svg(html)
    root = ET.fromstring(out)
    text = root.find("text")

    assert "&amp;" in out
    assert "&lt;" in out
    assert "&quot;" in out
    assert text is not None
    assert text.attrib == {"data-amp": "&", "data-lt": "<", "data-quote": '"'}
    assert text.text == '&<"'


def test_extract_svg_rejects_unknown_named_entities():
    with pytest.raises(ValueError, match=r"unknown named HTML entity.*&notARealEntity;"):
        extract_svg("<svg><text>&notARealEntity;</text></svg>")


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
    assert (png_dir / "system.svg").is_file()
    assert (png_dir / "system.svg").read_text(encoding="utf-8").startswith("<svg")
    assert (png_dir / "system.svg").read_text(encoding="utf-8").endswith("\n")
    assert (site_img / "system.svg").read_text(encoding="utf-8").endswith("\n")
    assert (png_dir / "system.png").read_bytes()[:4] == b"\x89PNG"
    for asset in (png_dir / "system.svg", png_dir / "system.png"):
        if os.name == "nt":
            assert not asset.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
            assert os.access(asset, os.W_OK)
        else:
            assert stat.S_IMODE(asset.stat().st_mode) == 0o644


def test_copy_assets_copies_pngs(tmp_path):
    src = tmp_path / "docs" / "diagrams" / "img"
    src.mkdir(parents=True)
    (src / "system.png").write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    (src / "system.svg").write_text("<svg/>", encoding="utf-8")
    wiki_img = tmp_path / "generated" / "wiki" / "img"
    copy_assets(tmp_path, wiki_img)
    assert (wiki_img / "system.png").read_bytes().startswith(b"\x89PNG")
    assert (wiki_img / "system.svg").read_text(encoding="utf-8") == "<svg/>"


def test_architecture_diagram_is_landscape_and_current():
    repo_root = Path(__file__).parents[2]
    master = (repo_root / "docs/diagrams/architecture.html").read_text(encoding="utf-8")
    svg = extract_svg(master)
    root = ET.fromstring(svg)
    view_box = tuple(float(value) for value in root.attrib["viewBox"].split())

    assert view_box[2] > view_box[3]
    for label in (
        "TEXTUAL VIEW",
        "VIEWMODEL / VMX",
        "SERVICE",
        "DOMAIN",
        "INFRA",
        "S3",
        "EMR Serverless",
        "AWS Glue",
        "Amazon Athena",
        "runtime AWS + filesystem I/O",
        "sessions + SDK client construction",
        "credentials, config, OS-backed stores",
        "await Athena shutdown",
        "then dispose outgoing VM",
        "exact connection + region",
        "GluePage",
        "AthenaPage",
        "GluePageVM",
        "AthenaPageVM",
        "ServiceSelectionStore",
        "ConnectionResolver",
        "QueryContext",
        "TableRef",
        "IcebergInspector",
        "OpenAthenaTableRequest",
        "OpenGlueTableRequest",
        "OpenS3LocationRequest",
        "Lake Formation",
    ):
        assert label in svg
    for inaccurate_claim in (
        "only layer touching external systems",
        "Infrastructure owns external I/O",
    ):
        assert inaccurate_claim not in master
    assert "Glue-to-Athena" in svg
    assert "Snapshot time travel" in svg
    assert 'data-route="orthogonal"' in svg

    groups = {
        group.attrib["id"]: " ".join(group.itertext())
        for group in root.iter()
        if group.tag.endswith("g") and "id" in group.attrib
    }
    assert "DualPane" in groups["textual-views"]
    assert "service_view_factory.py" in groups["textual-views"]
    assert "S3Page" not in groups["textual-views"]
    assert "ServiceSelectionStore" in groups["viewmodels"]
    assert "ServiceSelectionStore" not in groups["infrastructure"]
