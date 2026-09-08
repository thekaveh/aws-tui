"""Render the generated site + wiki surfaces and the root mkdocs.yml."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from scripts.docs.manifest import Manifest, ManifestError, Section, load_manifest
from scripts.docs.render_diagrams import copy_assets, render_svg, write_svg
from scripts.docs.transforms import (
    build_source_map,
    output_name,
    rewrite_for_surface,
    wiki_slug,
)

_IMG_RE = re.compile(r"(!\[[^\]]*\]\()\s*(?:\.\./)*diagrams/img/([\w-]+)\.png(\))")
# Raw <img> embeds pointing anywhere under the repo-root assets/ tree. Matching the
# whole tree rather than one hard-coded filename is deliberate: the previous
# single-file regex passed every other asset through unrewritten and uncopied, so
# the landing poster rendered as a broken image on both published surfaces.
_ASSET_RE = re.compile(
    r"(<img\s+[^>]*\bsrc=[\"'])(?:\.\./)*assets/"
    r"([\w./-]+\.(?:png|jpg|jpeg|gif|webp|svg))([\"'][^>]*>)"
)


def _asset_target(surface: str, name: str) -> str:
    """Both generated surfaces are flat, so surface-relative paths carry no prefix."""
    return f"assets/img/{name}" if surface == "site" else f"img/{name}"


def _referenced_assets(md: str) -> set[str]:
    """Return the ``assets/``-relative files a canonical page embeds via <img>."""
    return {match.group(2) for match in _ASSET_RE.finditer(md)}


def _rewrite_images(md: str, surface: str) -> str:
    def diagram(m: re.Match[str]) -> str:
        head, name, tail = m.groups()
        suffix = "svg" if surface == "site" else "png"
        return f"{head}{_asset_target(surface, f'{name}.{suffix}')}{tail}"

    def asset(m: re.Match[str]) -> str:
        head, name, tail = m.groups()
        return f"{head}{_asset_target(surface, Path(name).name)}{tail}"

    return _ASSET_RE.sub(asset, _IMG_RE.sub(diagram, md))


def _copy_referenced_assets(repo_root: Path, names: set[str], img_dir: Path) -> None:
    """Copy every embedded asset onto the surface, refusing silent omissions.

    A page that embeds an asset the surface does not carry renders as a broken
    image, and ``mkdocs build --strict`` cannot see it because the embed is raw
    HTML rather than Markdown. Failing the build is the only place to catch it.
    """
    img_dir.mkdir(parents=True, exist_ok=True)
    by_basename: dict[str, str] = {}
    for rel in sorted(names):
        basename = Path(rel).name
        collision = by_basename.get(basename)
        if collision is not None:
            raise ManifestError(
                f"assets/{rel} and assets/{collision} share the basename {basename}; "
                "generated surfaces are flat and cannot carry both"
            )
        by_basename[basename] = rel
        source = repo_root / "assets" / rel
        if not source.is_file():
            raise ManifestError(f"embedded asset assets/{rel} does not exist")
        shutil.copy2(source, img_dir / basename)


def render_site(manifest: Manifest, repo_root: str | Path, out_dir: str | Path) -> None:
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_map = build_source_map(manifest, "site")
    assets: set[str] = set()
    for leaf in manifest.leaves():
        assert leaf.source is not None
        md = (repo_root / leaf.source).read_text(encoding="utf-8")
        assets |= _referenced_assets(md)
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
    _copy_referenced_assets(repo_root, assets, img_dir)


def render_wiki(manifest: Manifest, repo_root: str | Path, out_dir: str | Path) -> None:
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_map = build_source_map(manifest, "wiki")
    assets: set[str] = set()
    for leaf in manifest.leaves():
        assert leaf.source is not None
        md = (repo_root / leaf.source).read_text(encoding="utf-8")
        assets |= _referenced_assets(md)
        md = rewrite_for_surface(md, "wiki", source_map)
        md = _rewrite_images(md, "wiki")
        (out_dir / output_name(leaf, "wiki")).write_text(md, encoding="utf-8")
    (out_dir / "_Sidebar.md").write_text(_wiki_sidebar(manifest), encoding="utf-8")
    (out_dir / "_Footer.md").write_text(
        "aws-tui documentation | Apache-2.0\n",
        encoding="utf-8",
    )
    copy_assets(repo_root, out_dir / "img")
    _copy_referenced_assets(repo_root, assets, out_dir / "img")


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
site_description: >-
  Cross-platform terminal UI for Amazon S3, EMR Serverless, AWS Glue, Apache
  Iceberg metadata, and Amazon Athena, built with Textual and the VMx MVVM
  framework.
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


def render_package_readme(manifest: Manifest, repo_root: str | Path) -> str:
    if manifest.package is None:
        raise ManifestError("package surface is not configured")
    return (Path(repo_root) / manifest.package.source).read_text(encoding="utf-8")


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
    package: bool = False,
    check: bool = False,
) -> None:
    repo_root = Path(repo_root)
    manifest = load_manifest(path, repo_root)
    requested = {
        name for name, enabled in (("site", site), ("wiki", wiki), ("package", package)) if enabled
    }
    unsupported = requested - set(manifest.surfaces)
    if unsupported:
        raise ManifestError(f"requested undeclared surfaces: {sorted(unsupported)}")
    generated = repo_root / "generated"
    build_site = site or (check and "site" in manifest.surfaces)
    build_wiki = wiki or (check and "wiki" in manifest.surfaces)
    build_package = package or (check and "package" in manifest.surfaces)
    if build_site:
        render_site(manifest, repo_root, generated / "site")
        (repo_root / "mkdocs.yml").write_text(render_mkdocs_yml(manifest), encoding="utf-8")
    if build_wiki:
        render_wiki(manifest, repo_root, generated / "wiki")
    if build_package:
        assert manifest.package is not None
        expected = render_package_readme(manifest, repo_root)
        output = repo_root / manifest.package.output
        if check:
            actual = output.read_text(encoding="utf-8") if output.is_file() else ""
            assert actual == expected, f"package README is stale: {manifest.package.output}"
        else:
            output.write_text(expected, encoding="utf-8")
    if check:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            if build_site:
                render_site(manifest, repo_root, tmp_path / "site")
                _assert_dirs_equal(tmp_path / "site", generated / "site")
            if build_wiki:
                render_wiki(manifest, repo_root, tmp_path / "wiki")
                _assert_dirs_equal(tmp_path / "wiki", generated / "wiki")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_docs")
    parser.add_argument("--site", action="store_true")
    parser.add_argument("--wiki", action="store_true")
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    build(
        repo_root / "docs" / "manifest.yaml",
        repo_root,
        site=args.site,
        wiki=args.wiki,
        package=args.package,
        check=args.check,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
