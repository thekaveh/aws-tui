import textwrap
from pathlib import Path

import pytest
import scripts.docs.render_diagrams as render_diagrams
from scripts.docs.build_docs import (
    _assert_dirs_equal,
    build,
    render_mkdocs_yml,
    render_site,
    render_wiki,
)
from scripts.docs.manifest import parse_manifest
from scripts.docs.render_diagrams import render_all


def _fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "stylesheets").mkdir(parents=True)
    (docs / "javascripts").mkdir(parents=True)
    (docs / "index.md").write_text("# 1. aws-tui\n\nWelcome.\n")
    (docs / "architecture.md").write_text(
        "# 1. Architecture\n\n![arch](diagrams/img/architecture.png)\n\n"
        "See [keys](keybindings.md) and [repo](https://github.com/thekaveh/aws-tui/blob/main/x).\n"
    )
    (docs / "keybindings.md").write_text("# 1. Keybindings\n\nKeys.\n")
    (docs / "stylesheets" / "extra.css").write_text("/* theme */\n")
    (docs / "javascripts" / "mathjax.js").write_text("window.MathJax = {};\n")
    (docs / "diagrams" / "img").mkdir(parents=True)
    (docs / "diagrams" / "img" / "architecture.png").write_bytes(b"\x89PNG\r\n\x1a\nX")
    (docs / "diagrams" / "architecture.html").write_text(
        "<html><svg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 16 9'><text>Architecture &middot; layers</text></svg></html>"
    )
    m = parse_manifest(
        textwrap.dedent(
            """
            surfaces: [repo, site, wiki]
            numbering: per-doc
            sections:
              - { id: overview, title: Overview, source: docs/index.md }
              - id: dev
                title: Development
                children:
                  - { id: architecture, title: Architecture, source: docs/architecture.md }
                  - { id: keybindings, title: Keybindings, source: docs/keybindings.md }
            diagrams:
              - { id: architecture, master: docs/diagrams/architecture.html }
            """
        )
    )
    return m, tmp_path


def test_render_site_emits_pages_assets_and_rewrites(tmp_path):
    m, root = _fixture(tmp_path)
    out = root / "generated" / "site"
    render_site(m, root, out)
    assert (out / "index.md").is_file()
    assert (out / "architecture.md").is_file()
    assert (out / "stylesheets" / "extra.css").is_file()
    assert (out / "javascripts" / "mathjax.js").is_file()
    body = (out / "architecture.md").read_text()
    assert "assets/img/architecture.svg" in body  # image rewritten to SVG
    assert "[keys](keybindings.md)" in body  # internal .md kept
    assert "https://github.com/thekaveh/aws-tui/blob" not in body  # forbidden stripped


def test_render_wiki_emits_special_pages_and_images(tmp_path):
    m, root = _fixture(tmp_path)
    out = root / "generated" / "wiki"
    render_wiki(m, root, out)
    assert (out / "Home.md").is_file()
    assert (out / "Architecture.md").is_file()
    assert (out / "Keybindings.md").is_file()
    assert (out / "_Sidebar.md").is_file()
    assert (out / "_Footer.md").is_file()
    assert (out / "img" / "architecture.png").is_file()
    body = (out / "Architecture.md").read_text()
    assert "img/architecture.png" in body
    sidebar = (out / "_Sidebar.md").read_text()
    assert "[Overview](Home)" in sidebar
    assert "Development" in sidebar
    assert "[Architecture](Architecture)" in sidebar


def test_three_surface_build_keeps_svg_assets_byte_identical(tmp_path, monkeypatch):
    m, root = _fixture(tmp_path)
    canonical_dir = root / "docs" / "diagrams" / "img"
    site_dir = root / "generated" / "site"
    wiki_dir = root / "generated" / "wiki"

    def write_test_png(_svg, out_path, *, width=1600):
        del width
        Path(out_path).write_bytes(b"\x89PNG\r\n\x1a\nTEST")

    monkeypatch.setattr(render_diagrams, "svg_to_png", write_test_png)
    render_all(m, root, site_dir / "assets" / "img", canonical_dir)
    render_site(m, root, site_dir)
    render_wiki(m, root, wiki_dir)

    canonical = (canonical_dir / "architecture.svg").read_bytes()
    site = (site_dir / "assets" / "img" / "architecture.svg").read_bytes()
    wiki = (wiki_dir / "img" / "architecture.svg").read_bytes()
    assert canonical == site == wiki


def test_render_mkdocs_yml_has_nav_and_no_repo_url(tmp_path):
    m, _ = _fixture(tmp_path)
    text = render_mkdocs_yml(m)
    assert "repo_url" not in text
    assert "edit_uri" not in text
    assert "docs_dir: generated/site" in text
    assert "Overview: index.md" in text
    assert "Development:" in text
    assert "Architecture: architecture.md" in text


def test_build_check_is_deterministic(tmp_path):
    _m, root = _fixture(tmp_path)
    (root / "docs" / "manifest.yaml").write_text(_manifest_yaml())
    # build --check must not raise (idempotent regeneration).
    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=True)


def _manifest_yaml() -> str:
    return textwrap.dedent(
        """
        surfaces: [repo, site, wiki]
        numbering: per-doc
        sections:
          - { id: overview, title: Overview, source: docs/index.md }
          - id: dev
            title: Development
            children:
              - { id: architecture, title: Architecture, source: docs/architecture.md }
              - { id: keybindings, title: Keybindings, source: docs/keybindings.md }
        diagrams:
          - { id: architecture, master: docs/diagrams/architecture.html }
        """
    )


def test_assert_dirs_equal_detects_difference(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("one")
    (b / "f.txt").write_text("two")
    with pytest.raises(AssertionError):
        _assert_dirs_equal(a, b)
