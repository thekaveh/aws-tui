"""Render diagram HTML masters to SVG (site) and PNG (committed + wiki)."""

from __future__ import annotations

import re
import sys
import tempfile
from html.entities import html5
from pathlib import Path

from scripts.docs.manifest import Manifest, load_manifest

_SVG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)
# Named entities that are NOT valid in standalone XML (exclude the 5 XML
# built-ins and numeric entities).
_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)[a-zA-Z][a-zA-Z0-9]*;")


def _replace_named_entity(match: re.Match[str]) -> str:
    entity = match.group(0)
    entity_name = entity[1:]
    if entity_name not in html5:
        raise ValueError(f"unknown named HTML entity in SVG: {entity}")
    return html5[entity_name]


def extract_svg(html_text: str) -> str:
    m = _SVG_RE.search(html_text)
    if not m:
        raise ValueError("no <svg> found in diagram master")
    svg = m.group(0)
    return _ENTITY_RE.sub(_replace_named_entity, svg)


def render_svg(master_path: str | Path) -> str:
    """Render one HTML master to the canonical standalone SVG serialization."""
    svg = extract_svg(Path(master_path).read_text(encoding="utf-8"))
    return f"{svg}\n"


def _atomic_write_bytes(out_path: str | Path, content: bytes) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=out_path.parent,
            prefix=f".{out_path.name}.",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        temp_path.chmod(0o644)
        temp_path.replace(out_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def write_svg(out_path: str | Path, svg: str) -> None:
    _atomic_write_bytes(out_path, svg.encode("utf-8"))


def svg_to_png(svg: str, out_path: str | Path, *, width: int = 1600) -> None:
    import cairosvg  # lazy — only needed when rasterizing

    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=width,
    )
    if png is None:
        raise RuntimeError("CairoSVG did not return PNG bytes")
    _atomic_write_bytes(out_path, png)


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
        svg = render_svg(repo_root / d.master)
        write_svg(site_img_dir / f"{d.id}.svg", svg)
        write_svg(png_dir / f"{d.id}.svg", svg)
        svg_to_png(svg, png_dir / f"{d.id}.png")


def copy_assets(repo_root: str | Path, wiki_img_dir: str | Path) -> None:
    src = Path(repo_root) / "docs" / "diagrams" / "img"
    dst = Path(wiki_img_dir)
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for pattern in ("*.png", "*.svg"):
        for asset in src.glob(pattern):
            _atomic_write_bytes(dst / asset.name, asset.read_bytes())


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
