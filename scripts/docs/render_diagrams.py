"""Render diagram HTML masters to SVG (site) and PNG (committed + wiki)."""

from __future__ import annotations

import html as _html
import re
import shutil
import sys
from pathlib import Path

from scripts.docs.manifest import Manifest, load_manifest

_SVG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)
# Named entities that are NOT valid in standalone XML (exclude the 5 XML
# built-ins and numeric entities).
_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)[a-zA-Z][a-zA-Z0-9]*;")


def extract_svg(html_text: str) -> str:
    m = _SVG_RE.search(html_text)
    if not m:
        raise ValueError("no <svg> found in diagram master")
    svg = m.group(0)
    return _ENTITY_RE.sub(lambda mo: _html.unescape(mo.group(0)), svg)


def svg_to_png(svg: str, out_path: str | Path, *, width: int = 1600) -> None:
    import cairosvg  # lazy — only needed when rasterizing

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        write_to=str(out_path),
        output_width=width,
    )


def render_all(
    manifest: Manifest,
    repo_root: str | Path,
    site_img_dir: str | Path,
    png_dir: str | Path,
) -> None:
    repo_root = Path(repo_root)
    site_img_dir = Path(site_img_dir)
    png_dir = Path(png_dir)
    site_img_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)
    for d in manifest.diagrams:
        svg = extract_svg((repo_root / d.master).read_text(encoding="utf-8"))
        (site_img_dir / f"{d.id}.svg").write_text(svg, encoding="utf-8")
        svg_to_png(svg, png_dir / f"{d.id}.png")


def copy_assets(repo_root: str | Path, wiki_img_dir: str | Path) -> None:
    src = Path(repo_root) / "docs" / "diagrams" / "img"
    dst = Path(wiki_img_dir)
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for png in src.glob("*.png"):
        shutil.copy2(png, dst / png.name)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path.cwd()
    manifest = load_manifest(repo_root / "docs" / "manifest.yaml", repo_root)
    render_all(
        manifest,
        repo_root,
        repo_root / "generated" / "site" / "assets" / "img",
        repo_root / "docs" / "diagrams" / "img",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
