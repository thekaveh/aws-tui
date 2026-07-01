"""Snapshot host for demo mode — boots the app with demo=True under
one theme so the snapshot tier captures the rail + S3 pane + banner
subtitle chip."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context

# Matches a single SVG <rect> element used for background fills in the
# Rich terminal SVG output.
_BG_RECT_RE = re.compile(
    r'<rect fill="([^"]+)" x="([0-9.]+)" y="([0-9.]+)" '
    r'width="([0-9.]+)" height="([0-9.]+)" shape-rendering="crispEdges"/>'
)


def _normalise_svg(svg: str) -> str:
    """Merge adjacent same-colour background rects on the same SVG line.

    The Rich SVG exporter can split what is visually a single contiguous
    block of background colour into two or more consecutive <rect> elements
    when adjacent terminal cells have the *same* background colour but
    *different* foreground/style attributes.  The exact split point depends
    on the order in which Textual's Style equality is evaluated, which can
    vary when multiple App instances run sequentially in the same process.
    The visual output is byte-for-byte identical, but the SVG text differs,
    causing pytest-textual-snapshot's raw-string comparison to fail.

    This normaliser collapses adjacent same-fill rects that share both the
    same y-coordinate and height into a single wider rect, making the SVG
    text deterministic regardless of how Rich chose to segment the row.
    """

    def _merge_rects(block: str) -> str:
        rects: list[tuple[str, float, float, float, float]] = []
        for m in _BG_RECT_RE.finditer(block):
            fill, x, y, w, h = m.group(1, 2, 3, 4, 5)
            rects.append((fill, float(x), float(y), float(w), float(h)))

        if not rects:
            return block

        # Merge consecutive rects that share fill AND y AND height.
        merged: list[tuple[str, float, float, float, float]] = [rects[0]]
        for fill, x, y, w, h in rects[1:]:
            pf, px, py, pw, ph = merged[-1]
            # Adjacent means the current rect starts exactly where the
            # previous one ends (within floating-point rounding).
            if fill == pf and y == py and h == ph and abs((px + pw) - x) < 0.01:
                merged[-1] = (pf, px, py, pw + w, ph)
            else:
                merged.append((fill, x, y, w, h))

        out_parts: list[str] = []
        for fill, x, y, w, h in merged:
            out_parts.append(
                f'<rect fill="{fill}" x="{x}" y="{y}" '
                f'width="{w}" height="{h}" shape-rendering="crispEdges"/>'
            )
        return "".join(out_parts)

    # The Rich SVG has one long line per terminal row; apply rect merging
    # line by line so we never cross row boundaries.
    return "\n".join(_merge_rects(line) for line in svg.splitlines())


class DemoModeApp(AwsTuiApp):
    def __init__(self, *, theme: str) -> None:
        # snapshot tier reuses the per-theme theme-store: passing
        # the theme via env preserves the existing fixture style.
        tmpdir = Path(tempfile.mkdtemp(prefix="demo-snapshot-"))
        local_root = tmpdir / "local"
        local_root.mkdir()
        for name, modified in (
            (".a", datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)),
            (".b", datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)),
            (".vol", datetime(2025, 10, 29, 12, 0, 0, tzinfo=UTC)),
            ("Applications", datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)),
            ("Library", datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)),
            ("System", datetime(2025, 10, 29, 12, 0, 0, tzinfo=UTC)),
            ("Users", datetime(2025, 11, 17, 12, 0, 0, tzinfo=UTC)),
        ):
            child = local_root / name
            child.mkdir()
            fixed_mtime = modified.timestamp()
            os.utime(child, (fixed_mtime, fixed_mtime))

        ctx = build_app_context(config_dir=tmpdir, cache_dir=tmpdir, demo=True)
        # Keep production demo mode honest ("local pane is real") while making
        # the snapshot harness deterministic across host machines and dates.
        ctx.registry.get("s3")._local_root = local_root  # type: ignore[attr-defined]
        ctx.initial_theme = theme
        super().__init__(context=ctx)

    def export_screenshot(  # type: ignore[override]
        self,
        *,
        title: str | None = None,
        simplify: bool = False,
    ) -> str:
        """Export screenshot with SVG background-rect normalisation.

        Merges adjacent same-colour background rectangles so the snapshot
        text is stable even when sequential App instances produce subtly
        different style-segment boundaries for visually identical output.
        """
        raw = super().export_screenshot(title=title, simplify=simplify)
        return _normalise_svg(raw)


__all__ = ["DemoModeApp"]
