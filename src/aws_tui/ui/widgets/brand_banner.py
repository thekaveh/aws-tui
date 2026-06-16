"""BrandBanner — block-art aws-tui logo with a Rich gradient.

Stylistic twin of the genai-vanilla bootstrap-wizard banner:

- Same heavy-block / box-drawing letterforms (``██╗ ╔══╝ ╚═╝``).
- Same 15-stop blue→cyan 256-color gradient applied char-by-char per
  line (``color(17)`` dark navy → ``color(195)`` pale blue).

The widget is intentionally static — it owns no VM state and presents
identity, not behavior. Mounted above the StatusBar with its own rounded
border via the parent screen's layout.
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

# Block-art rows for "AWS-TUI" — built letter-by-letter and concatenated
# so the gradient flows horizontally across each row exactly like the
# genai-vanilla banner does.
_LETTERS: dict[str, tuple[str, str, str, str, str, str]] = {
    "A": (
        " █████╗ ",
        "██╔══██╗",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ),
    "W": (
        "██╗    ██╗",
        "██║    ██║",
        "██║ █╗ ██║",
        "██║███╗██║",
        "╚███╔███╔╝",
        " ╚══╝╚══╝ ",
    ),
    "S": (
        "███████╗",
        "██╔════╝",
        "███████╗",
        "╚════██║",
        "███████║",
        "╚══════╝",
    ),
    "-": (
        "      ",
        "      ",
        "█████╗",
        "╚════╝",
        "      ",
        "      ",
    ),
    "T": (
        "████████╗",
        "╚══██╔══╝",
        "   ██║   ",
        "   ██║   ",
        "   ██║   ",
        "   ╚═╝   ",
    ),
    "U": (
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ),
    "I": (
        "██╗",
        "██║",
        "██║",
        "██║",
        "██║",
        "╚═╝",
    ),
}

_WORD = "AWS-TUI"

# 15-stop blue→cyan gradient, identical to genai-vanilla's banner palette.
_GRADIENT: tuple[str, ...] = (
    "color(17)",
    "color(18)",
    "color(19)",
    "color(20)",
    "color(21)",
    "color(26)",
    "color(27)",
    "color(33)",
    "color(39)",
    "color(45)",
    "color(51)",
    "color(87)",
    "color(123)",
    "color(159)",
    "color(195)",
)


def _build_rows(word: str = _WORD, gap: str = " ") -> tuple[str, ...]:
    """Concatenate per-letter rows into the 6 banner rows for ``word``."""
    return tuple(gap.join(_LETTERS[c][row] for c in word) for row in range(6))


def _apply_genai_gradient(line: str) -> Text:
    """Apply the genai-vanilla apply_enhanced_gradient algorithm verbatim:
    map each character's position within the line to one of the 15
    palette stops via ``idx = i * len(palette) // len(line)``. Same
    formula their banner uses, so the colors land in the same places."""
    text = Text()
    n = len(line)
    if n == 0:
        return text
    stops = len(_GRADIENT)
    for i, ch in enumerate(line):
        idx = min((i * stops) // n, stops - 1)
        text.append(ch, style=f"bold {_GRADIENT[idx]}")
    return text


class BrandBanner(Widget):
    """Top-of-screen block-art "aws-tui" banner inside its own subtle border.

    Gradient algorithm + 256-color stops are byte-for-byte the same as the
    genai-vanilla bootstrap-wizard's full banner — each character within a
    row gets a stop from ``color(17)`` (dark navy) through ``color(195)``
    (pale blue), so the sweep flows left-to-right with smooth horizontal
    columns of consistent color across the 6-row block.
    """

    # Only structural CSS lives here — colors / borders are theme tokens
    # ($rule-dim, $bg-elev) defined in each theme's .tcss. Putting theme
    # vars in DEFAULT_CSS fails parsing because DEFAULT_CSS is processed
    # before the theme overlay is loaded.
    DEFAULT_CSS = """
    BrandBanner {
        height: auto;
        width: 100%;
        content-align: center middle;
    }
    """

    DEFAULT_CLASSES = "brand-banner"

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._rows: tuple[str, ...] = _build_rows()

    def render(self) -> Text:
        out = Text(justify="center")
        for i, row in enumerate(self._rows):
            if i > 0:
                out.append("\n")
            out.append(_apply_genai_gradient(row))
        return out


__all__ = ["BrandBanner"]
