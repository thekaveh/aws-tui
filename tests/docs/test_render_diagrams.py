import os
import stat
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image
from scripts.docs.manifest import parse_manifest
from scripts.docs.render_diagrams import (
    _same_committed_png,
    check_committed_assets,
    copy_assets,
    extract_svg,
    render_all,
    render_svg,
    svg_to_png,
)


def _png_bytes(image: Image.Image) -> bytes:
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_test_font(repo_root: Path) -> None:
    font_dir = repo_root / "assets" / "fonts" / "fira-code"
    font_dir.mkdir(parents=True)
    (font_dir / "FiraCode-Regular.ttf").write_bytes(b"test-regular-font")
    (font_dir / "FiraCode-Bold.ttf").write_bytes(b"test-bold-font")


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


def test_committed_png_comparison_allows_bounded_cross_platform_raster_drift():
    baseline = Image.new("RGBA", (100, 100), "black")
    platform_render = baseline.copy()
    for x in range(20):
        for y in range(20):
            platform_render.putpixel((x, y), (102, 102, 102, 255))

    assert _same_committed_png(_png_bytes(baseline), _png_bytes(platform_render))

    visually_stale = baseline.copy()
    for x in range(25):
        for y in range(25):
            visually_stale.putpixel((x, y), (102, 102, 102, 255))

    assert not _same_committed_png(_png_bytes(baseline), _png_bytes(visually_stale))


def test_render_all_writes_svg_and_png(tmp_path):
    _require_cairosvg()
    _write_test_font(tmp_path)
    (tmp_path / "docs" / "diagrams").mkdir(parents=True)
    (tmp_path / "docs" / "diagrams" / "d.html").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'><rect width='4' height='4'/></svg>"
    )
    m = parse_manifest(
        textwrap.dedent(
            """
            surfaces: [site]
            numbering: per-doc
            sections: [{id: overview, title: O, source: docs/diagrams/d.html, diagrams: [system]}]
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


def test_check_committed_assets_detects_stale_and_unexpected_assets(tmp_path):
    _require_cairosvg()
    _write_test_font(tmp_path)
    diagrams = tmp_path / "docs" / "diagrams"
    diagrams.mkdir(parents=True)
    (diagrams / "d.html").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'>"
        "<rect width='4' height='4'/></svg>"
    )
    manifest = parse_manifest(
        textwrap.dedent(
            """
            surfaces: [site]
            numbering: per-doc
            sections: [{id: overview, title: O, source: docs/diagrams/d.html, diagrams: [system]}]
            diagrams: [{id: system, master: docs/diagrams/d.html}]
            """
        )
    )
    committed = diagrams / "img"
    render_all(manifest, tmp_path, tmp_path / "generated", committed)

    assert check_committed_assets(manifest, tmp_path) == []

    (committed / "system.svg").write_text("<svg/>", encoding="utf-8")
    Image.new("RGBA", (4, 4), "red").save(committed / "system.png")
    (committed / "orphan.png").write_bytes(b"orphan")
    assert check_committed_assets(manifest, tmp_path) == [
        "orphan.png",
        "system.png",
        "system.svg",
    ]


def test_copy_assets_copies_pngs(tmp_path):
    src = tmp_path / "docs" / "diagrams" / "img"
    src.mkdir(parents=True)
    (src / "system.png").write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    (src / "system.svg").write_text("<svg/>", encoding="utf-8")
    wiki_img = tmp_path / "generated" / "wiki" / "img"
    copy_assets(tmp_path, wiki_img)
    assert (wiki_img / "system.png").read_bytes().startswith(b"\x89PNG")
    assert (wiki_img / "system.svg").read_text(encoding="utf-8") == "<svg/>"


def test_render_svg_embeds_regular_and_bold_fonts_without_external_dependency(tmp_path):
    _write_test_font(tmp_path)
    master = tmp_path / "docs" / "diagrams" / "d.html"
    master.parent.mkdir(parents=True)
    master.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Diagram</text></svg>',
        encoding="utf-8",
    )

    svg = render_svg(master, font_path=tmp_path / "assets/fonts/fira-code/FiraCode-Regular.ttf")

    assert svg.count('@font-face { font-family: "Fira Code Embedded";') == 2
    assert "data:font/ttf;base64,dGVzdC1yZWd1bGFyLWZvbnQ=" in svg
    assert "data:font/ttf;base64,dGVzdC1ib2xkLWZvbnQ=" in svg
    assert "font-style: normal; font-weight: 400;" in svg
    assert "font-style: normal; font-weight: 600 700;" in svg
    assert "fonts.googleapis.com" not in svg
    assert "fonts.gstatic.com" not in svg
    assert "@import" not in svg
    assert "url(http" not in svg


def test_diagram_masters_load_both_vendored_fonts_without_external_urls() -> None:
    repo_root = Path(__file__).parents[2]
    for master in (repo_root / "docs" / "diagrams").glob("*.html"):
        text = master.read_text(encoding="utf-8")
        assert text.count('@font-face { font-family: "Fira Code Embedded";') == 2, master
        assert 'url("../../assets/fonts/fira-code/FiraCode-Regular.ttf")' in text, master
        assert 'url("../../assets/fonts/fira-code/FiraCode-Bold.ttf")' in text, master
        assert "font-style: normal; font-weight: 400;" in text, master
        assert "font-style: normal; font-weight: 600 700;" in text, master
        assert "fonts.googleapis.com" not in text, master
        assert "fonts.gstatic.com" not in text, master
        assert "@import" not in text, master
        assert "url(http" not in text, master
        assert "JetBrains Mono" not in text, master


def test_architecture_diagram_is_landscape_and_current():
    repo_root = Path(__file__).parents[2]
    master = (repo_root / "docs/diagrams/architecture.html").read_text(encoding="utf-8")
    svg = extract_svg(master)
    root = ET.fromstring(svg)
    view_box = tuple(float(value) for value in root.attrib["viewBox"].split())
    title = root.find("{http://www.w3.org/2000/svg}title")
    description = root.find("{http://www.w3.org/2000/svg}desc")

    assert view_box[2] > view_box[3]
    assert title is not None
    assert "EMR Serverless" in (title.text or "")
    assert description is not None
    assert "EMR Serverless" in (description.text or "")
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
        "await hosted VM shutdown",
        "then dispose outgoing VM",
        "exact connection + region",
        "GluePage",
        "AthenaPage",
        "GluePageVM",
        "GlueCatalogVM",
        "GlueJobsVM",
        "GlueCrawlersVM",
        "AthenaPageVM",
        "AthenaQueryVM",
        "AthenaHistoryVM",
        "AthenaResultsVM",
        "AthenaSavedVM",
        "ServiceSelectionStore",
        "ConnectionResolver",
        "QueryContext",
        "TableRef",
        "IcebergInspector",
        "AthenaQueryRunner",
        "OpenAthenaTableRequest",
        "OpenGlueTableRequest",
        "OpenS3LocationRequest",
        "Lake Formation",
        "ContextPicker",
        "ServiceTabStrip",
        "TableClipboardVM",
        "CopyTableReferenceRequest",
        "copy quoted table ref",
        "same-source insert",
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
    assert "ContextPicker" in groups["textual-views"]
    assert "ServiceTabStrip" in groups["textual-views"]
    assert "ServiceSelectionStore" in groups["viewmodels"]
    assert "TableClipboardVM" in groups["viewmodels"]
    assert "ServiceSelectionStore" not in groups["infrastructure"]
    assert "CopyTableReferenceRequest" in groups["cross-service-handoffs"]
    assert "S3FS + LocalFS" in groups["domain-models"]
    assert "CrossFsCopy / Move" in groups["domain-models"]
    assert "TransferJournal" in groups["domain-models"]
    assert "EMR Serverless Client" in groups["domain-models"]
    assert "S3 log adapter" in groups["domain-models"]
    assert 'd="M455 610V680"' not in svg
    assert 'd="M715 610V680"' not in svg
    assert 'd="M455 610V635H330V672H455V680"' in svg
    assert 'd="M715 610V635H920V672H715V680"' in svg
    assert 'x="625" y="658"' in svg


def test_operations_flow_assigns_transfer_journal_to_dual_pane() -> None:
    master = Path("docs/diagrams/operations-flow.html").read_text(encoding="utf-8")

    assert 'data-owner="DualPaneVM" data-target="TransferJournal"' in master
    assert 'd="M180 215V270H1230V215"' in master
    assert 'd="M620 165H1080"' not in master
