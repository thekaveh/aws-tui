"""Render the generated site + wiki surfaces and the root mkdocs.yml."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from scripts.docs.manifest import Manifest, Section, load_manifest
from scripts.docs.render_diagrams import copy_assets, render_svg, write_svg
from scripts.docs.transforms import (
    build_source_map,
    output_name,
    rewrite_for_surface,
    wiki_slug,
)

_IMG_RE = re.compile(r"(!\[[^\]]*\]\()\s*((?:\.\./)*)diagrams/img/([\w-]+)\.png(\))")
_HERO_RE = re.compile(
    r"(<img\s+[^>]*src=[\"'])\.\./assets/screenshots/aws-tui-running\.png([\"'][^>]*>)"
)


def _rewrite_images(md: str, surface: str) -> str:
    def repl(m: re.Match[str]) -> str:
        head, prefix, name, tail = m.groups()
        if surface == "site":
            return f"{head}{prefix}assets/img/{name}.svg{tail}"
        return f"{head}{prefix}img/{name}.png{tail}"  # wiki

    rewritten = _IMG_RE.sub(repl, md)
    hero_target = (
        "assets/img/aws-tui-running.png" if surface == "site" else "img/aws-tui-running.png"
    )
    return _HERO_RE.sub(rf"\1{hero_target}\2", rewritten)


def _copy_hero(repo_root: Path, target: Path) -> None:
    source = repo_root / "assets" / "screenshots" / "aws-tui-running.png"
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def render_site(manifest: Manifest, repo_root: str | Path, out_dir: str | Path) -> None:
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_map = build_source_map(manifest, "site")
    for leaf in manifest.leaves():
        assert leaf.source is not None
        md = (repo_root / leaf.source).read_text(encoding="utf-8")
        md = rewrite_for_surface(md, "site", source_map)
        md = _rewrite_images(md, "site")
        (out_dir / output_name(leaf, "site")).write_text(md, encoding="utf-8")
    # theme assets
    (out_dir / "stylesheets").mkdir(exist_ok=True)
    shutil.copy2(
        repo_root / "docs" / "stylesheets" / "extra.css", out_dir / "stylesheets" / "extra.css"
    )
    # diagram SVGs
    img_dir = out_dir / "assets" / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    for d in manifest.diagrams:
        svg = render_svg(
            repo_root / d.master,
            font_path=repo_root / "assets" / "fonts" / "fira-code" / "FiraCode-Regular.ttf",
        )
        write_svg(img_dir / f"{d.id}.svg", svg)
    _copy_hero(repo_root, img_dir / "aws-tui-running.png")


def render_wiki(manifest: Manifest, repo_root: str | Path, out_dir: str | Path) -> None:
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_map = build_source_map(manifest, "wiki")
    for leaf in manifest.leaves():
        assert leaf.source is not None
        md = (repo_root / leaf.source).read_text(encoding="utf-8")
        md = rewrite_for_surface(md, "wiki", source_map)
        md = _rewrite_images(md, "wiki")
        (out_dir / output_name(leaf, "wiki")).write_text(md, encoding="utf-8")
    (out_dir / "_Sidebar.md").write_text(_wiki_sidebar(manifest), encoding="utf-8")
    (out_dir / "_Footer.md").write_text(
        "aws-tui documentation | Apache-2.0\n",
        encoding="utf-8",
    )
    copy_assets(repo_root, out_dir / "img")
    _copy_hero(repo_root, out_dir / "img" / "aws-tui-running.png")


def _wiki_link_name(section: Section) -> str:
    return "Home" if section.id == "overview" else wiki_slug(section.title)


def _wiki_sidebar(manifest: Manifest) -> str:
    lines: list[str] = []
    for section_index, section in enumerate(manifest.sections, start=1):
        section_title = f"{section_index}. {section.title}"
        if section.is_group:
            lines.append(f"**{section_title}**")
            lines.extend(
                f"  - [{section_index}.{child_index}. {child.title}]({_wiki_link_name(child)})"
                for child_index, child in enumerate(section.children, start=1)
            )
        else:
            lines.append(f"- [{section_title}]({_wiki_link_name(section)})")
    return "\n".join(lines) + "\n"


_MKDOCS_TEMPLATE = """\
site_name: aws-tui
site_url: https://thekaveh.github.io/aws-tui/
docs_dir: generated/site
site_dir: site
use_directory_urls: true
theme:
  name: material
  palette:
    - scheme: slate
      primary: cyan
      accent: cyan
      toggle:
        icon: material/weather-sunny
        name: Switch to light
    - scheme: default
      primary: cyan
      accent: cyan
      toggle:
        icon: material/weather-night
        name: Switch to dark
  font:
    text: Inter
    code: JetBrains Mono
  features:
    - navigation.sections
    - navigation.indexes
    - navigation.top
    - toc.follow
    - content.code.copy
    - content.code.annotate
    - header.autohide
extra_css:
  - stylesheets/extra.css
markdown_extensions:
  - admonition
  - attr_list
  - md_in_html
  - footnotes
  - def_list
  - pymdownx.superfences
  - pymdownx.highlight
  - pymdownx.inlinehilite
  - pymdownx.details
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.keys
  - toc:
      permalink: true
nav:
{nav}"""


def _mkdocs_nav(manifest: Manifest) -> str:
    lines: list[str] = []
    for section_index, section in enumerate(manifest.sections, start=1):
        section_title = f"{section_index}. {section.title}"
        if section.is_group:
            lines.append(f"  - {section_title}:")
            for child_index, child in enumerate(section.children, start=1):
                lines.append(
                    f"      - {section_index}.{child_index}. {child.title}: "
                    f"{output_name(child, 'site')}"
                )
        else:
            lines.append(f"  - {section_title}: {output_name(section, 'site')}")
    return "\n".join(lines) + "\n"


def render_mkdocs_yml(manifest: Manifest) -> str:
    return _MKDOCS_TEMPLATE.format(nav=_mkdocs_nav(manifest))


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _assert_dirs_equal(a: str | Path, b: str | Path) -> None:
    ha, hb = _hash_tree(Path(a)), _hash_tree(Path(b))
    assert ha == hb, f"regeneration not deterministic:\n  {a}: {sorted(ha)}\n  {b}: {sorted(hb)}"


def build(
    path: str | Path,
    repo_root: str | Path,
    *,
    site: bool = False,
    wiki: bool = False,
    check: bool = False,
) -> None:
    repo_root = Path(repo_root)
    manifest = load_manifest(path, repo_root)
    generated = repo_root / "generated"
    if site or check:
        render_site(manifest, repo_root, generated / "site")
        (repo_root / "mkdocs.yml").write_text(render_mkdocs_yml(manifest), encoding="utf-8")
    if wiki or check:
        render_wiki(manifest, repo_root, generated / "wiki")
    if check:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            render_site(manifest, repo_root, tmp_path / "site")
            render_wiki(manifest, repo_root, tmp_path / "wiki")
            _assert_dirs_equal(tmp_path / "site", generated / "site")
            _assert_dirs_equal(tmp_path / "wiki", generated / "wiki")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_docs")
    parser.add_argument("--site", action="store_true")
    parser.add_argument("--wiki", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    build(
        repo_root / "docs" / "manifest.yaml",
        repo_root,
        site=args.site,
        wiki=args.wiki,
        check=args.check,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
