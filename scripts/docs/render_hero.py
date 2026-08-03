"""Render the documentation hero from the canonical Carbon demo snapshot."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from scripts.docs.render_diagrams import _atomic_write_bytes, _same_png_pixels

SOURCE = Path("tests/snapshot/__snapshots__/test_demo_mode/test_demo_iceberg_snapshot[carbon].raw")
TARGET = Path("assets/screenshots/aws-tui-running.png")
FONT_DIR = Path("assets/fonts/fira-code")

_SVG_NS = "http://www.w3.org/2000/svg"
_FONT_SIZE = 20.0
_GLYPH_FALLBACKS = {"⚙": "◆", "⚠": "▲", "️": ""}


def render(repo_root: Path) -> bytes:
    import cairosvg

    font_dir = (repo_root / FONT_DIR).resolve()
    required = (font_dir / "FiraCode-Regular.ttf", font_dir / "FiraCode-Bold.ttf")
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing hero font assets: {', '.join(map(str, missing))}")

    source = (repo_root / SOURCE).read_bytes()
    outlined = _outline_terminal_text(source, regular=required[0], bold=required[1])
    png = cairosvg.svg2png(bytestring=outlined, output_width=2000)
    if png is None:
        raise RuntimeError("CairoSVG did not return hero PNG bytes")
    return png


def _outline_terminal_text(source: bytes, *, regular: Path, bold: Path) -> bytes:
    """Replace terminal text with Fira Code paths before rasterization."""
    ET.register_namespace("", _SVG_NS)
    root = ET.fromstring(source)
    style = "".join(element.text or "" for element in root.findall(f".//{{{_SVG_NS}}}style"))
    fonts = {False: TTFont(regular), True: TTFont(bold)}
    parents = {child: parent for parent in root.iter() for child in parent}

    for text in tuple(root.iter(f"{{{_SVG_NS}}}text")):
        class_name = text.attrib.get("class", "")
        if class_name.endswith("-title"):
            continue
        content = "".join(text.itertext())
        for original, replacement in _GLYPH_FALLBACKS.items():
            content = content.replace(original, replacement)
        if not content:
            parents[text].remove(text)
            continue
        is_bold = bool(
            re.search(
                rf"\.{re.escape(class_name)}\s*\{{[^}}]*font-weight:\s*bold",
                style,
            )
        )
        replacements = _glyph_paths(text, content=content, font=fonts[is_bold])
        parent = parents[text]
        index = list(parent).index(text)
        parent.remove(text)
        for offset, replacement in enumerate(replacements):
            parent.insert(index + offset, replacement)

    return ET.tostring(root, encoding="utf-8")


def _glyph_paths(text: ET.Element, *, content: str, font: TTFont) -> list[ET.Element]:
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"].metrics
    glyph_names = [cmap.get(ord(character), ".notdef") for character in content]
    advances = [hmtx[name][0] for name in glyph_names]
    total_advance = sum(advances)
    target_width = float(text.attrib.get("textLength", total_advance))
    x_scale = target_width / total_advance if total_advance else 0.0
    y_scale = _FONT_SIZE / font["head"].unitsPerEm
    cursor = float(text.attrib.get("x", "0"))
    baseline = float(text.attrib.get("y", "0"))
    inherited = {
        key: value for key, value in text.attrib.items() if key not in {"x", "y", "textLength"}
    }
    paths: list[ET.Element] = []
    for name, advance in zip(glyph_names, advances, strict=True):
        pen = SVGPathPen(glyph_set)
        transformed = TransformPen(
            pen,
            (x_scale, 0.0, 0.0, -y_scale, cursor, baseline),
        )
        glyph_set[name].draw(transformed)
        command = pen.getCommands()
        if command:
            path = ET.Element(
                f"{{{_SVG_NS}}}path",
                {
                    **inherited,
                    "d": command,
                },
            )
            paths.append(path)
        cursor += advance * x_scale
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    expected = render(repo_root)
    target = repo_root / TARGET
    if args.check:
        if not target.is_file() or not _same_png_pixels(target.read_bytes(), expected):
            print(f"stale generated hero: {TARGET}", file=sys.stderr)
            return 1
        return 0
    _atomic_write_bytes(target, expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
