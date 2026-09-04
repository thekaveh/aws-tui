"""Render diagram HTML masters to SVG (site) and PNG (committed + wiki)."""

from __future__ import annotations

import argparse
import base64
import re
import sys
import tempfile
from html.entities import html5
from io import BytesIO
from pathlib import Path

from scripts.docs.manifest import Manifest, load_manifest

_SVG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)
# Named entities that are NOT valid in standalone XML (exclude the 5 XML
# built-ins and numeric entities).
_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)[a-zA-Z][a-zA-Z0-9]*;")
_XML_ENTITY_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
}
_FONT_FAMILY = "Fira Code Embedded"


def _same_png_pixels(
    left: bytes,
    right: bytes,
    *,
    max_changed_ratio: float = 0.001,
    max_rms: float = 3.0,
) -> bool:
    """Compare rendered output while allowing tiny Cairo antialiasing drift."""
    from PIL import Image, ImageChops, ImageStat

    try:
        with Image.open(BytesIO(left)) as left_image, Image.open(BytesIO(right)) as right_image:
            if left_image.mode != right_image.mode or left_image.size != right_image.size:
                return False
            difference = ImageChops.difference(
                left_image.convert("RGBA"),
                right_image.convert("RGBA"),
            )
            channels = difference.split()
            maximum = channels[0]
            for channel in channels[1:]:
                maximum = ImageChops.lighter(maximum, channel)
            changed_pixels = difference.width * difference.height - maximum.histogram()[0]
            changed_ratio = changed_pixels / (difference.width * difference.height)
            maximum_rms = max(ImageStat.Stat(difference).rms)
            return changed_ratio <= max_changed_ratio and maximum_rms <= max_rms
    except (OSError, ValueError):
        return False


def _same_committed_png(left: bytes, right: bytes) -> bool:
    """Allow bounded raster-backend drift without accepting visual changes."""
    return _same_png_pixels(
        left,
        right,
        max_changed_ratio=0.05,
        max_rms=21.0,
    )


def _replace_named_entity(match: re.Match[str]) -> str:
    entity = match.group(0)
    entity_name = entity[1:]
    if entity_name not in html5:
        raise ValueError(f"unknown named HTML entity in SVG: {entity}")
    return "".join(_XML_ENTITY_ESCAPES.get(char, char) for char in html5[entity_name])


def extract_svg(html_text: str) -> str:
    m = _SVG_RE.search(html_text)
    if not m:
        raise ValueError("no <svg> found in diagram master")
    svg = m.group(0)
    return _ENTITY_RE.sub(_replace_named_entity, svg)


def render_svg(master_path: str | Path, *, font_path: str | Path) -> str:
    """Render one HTML master to the canonical standalone SVG serialization."""
    svg = extract_svg(Path(master_path).read_text(encoding="utf-8"))
    regular_font_path = Path(font_path)
    bold_font_path = regular_font_path.with_name("FiraCode-Bold.ttf")
    encoded_regular_font = base64.b64encode(regular_font_path.read_bytes()).decode("ascii")
    encoded_bold_font = base64.b64encode(bold_font_path.read_bytes()).decode("ascii")
    font_style = (
        '<style type="text/css">'
        f'@font-face {{ font-family: "{_FONT_FAMILY}"; '
        f'src: url("data:font/ttf;base64,{encoded_regular_font}") format("truetype"); '
        "font-style: normal; font-weight: 400; }"
        f'@font-face {{ font-family: "{_FONT_FAMILY}"; '
        f'src: url("data:font/ttf;base64,{encoded_bold_font}") format("truetype"); '
        "font-style: normal; font-weight: 600 700; }"
        f'svg {{ font-family: "{_FONT_FAMILY}", monospace; }}'
        "</style>"
    )
    svg = svg.replace(">", f">{font_style}", 1)
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
    font_path = repo_root / "assets" / "fonts" / "fira-code" / "FiraCode-Regular.ttf"
    for d in manifest.diagrams:
        svg = render_svg(repo_root / d.master, font_path=font_path)
        write_svg(site_img_dir / f"{d.id}.svg", svg)
        write_svg(png_dir / f"{d.id}.svg", svg)
        svg_to_png(svg, png_dir / f"{d.id}.png")


def check_committed_assets(manifest: Manifest, repo_root: str | Path) -> list[str]:
    """Return committed diagram assets that differ from a fresh render."""
    repo_root = Path(repo_root)
    committed_dir = repo_root / "docs" / "diagrams" / "img"
    expected_names = {
        f"{diagram.id}.{suffix}" for diagram in manifest.diagrams for suffix in ("svg", "png")
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        rendered_dir = temp_root / "committed"
        render_all(manifest, repo_root, temp_root / "site", rendered_dir)

        mismatches: list[str] = []
        for name in sorted(expected_names):
            committed = committed_dir / name
            rendered = rendered_dir / name
            if not committed.is_file():
                mismatches.append(name)
                continue
            committed_bytes = committed.read_bytes()
            rendered_bytes = rendered.read_bytes()
            if name.endswith(".png"):
                matches = _same_committed_png(committed_bytes, rendered_bytes)
            else:
                matches = committed_bytes == rendered_bytes
            if not matches:
                mismatches.append(name)

        for asset in committed_dir.iterdir() if committed_dir.is_dir() else ():
            if (
                asset.is_file()
                and asset.suffix in {".svg", ".png"}
                and asset.name not in expected_names
            ):
                mismatches.append(asset.name)
        return sorted(mismatches)


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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when committed diagram assets differ from a fresh render",
    )
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    manifest = load_manifest(repo_root / "docs" / "manifest.yaml", repo_root)
    if args.check:
        mismatches = check_committed_assets(manifest, repo_root)
        if mismatches:
            print(
                "stale or unexpected committed diagram assets: " + ", ".join(mismatches),
                file=sys.stderr,
            )
            print("run `make docs-diagrams` to regenerate them", file=sys.stderr)
            return 1
        return 0
    render_all(
        manifest,
        repo_root,
        repo_root / "generated" / "site" / "assets" / "img",
        repo_root / "docs" / "diagrams" / "img",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
