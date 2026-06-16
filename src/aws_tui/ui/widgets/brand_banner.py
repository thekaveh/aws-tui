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
# This is the *carbon* (default) palette — the one bouncing the user
# off the original genai-vanilla source code. The other themes use
# similar 6-stop sweeps in their own accent color family, picked from
# the 256-color palette and tuned to roughly track each theme's accent.
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

# Per-theme 6-stop vertical sweep for the banner: dark at the top → light
# at the bottom, in each theme's accent color family. New themes can
# extend this dict; the fallback is the carbon (blue) palette.
_THEME_PALETTES: dict[str, tuple[str, ...]] = {
    # carbon — blue, taken from the genai-vanilla stops at
    # indices (0, 3, 6, 8, 11, 14) of the 15-stop palette.
    "carbon": (
        "color(17)",
        "color(20)",
        "color(27)",
        "color(39)",
        "color(51)",
        "color(195)",
    ),
    # amber — gold / warm yellow → cream. Tracks the amber CRT theme.
    "amber": (
        "color(52)",
        "color(94)",
        "color(130)",
        "color(166)",
        "color(208)",
        "color(222)",
    ),
    # voidline — deep magenta → soft pink, mirrors the voidline accent.
    "voidline": (
        "color(53)",
        "color(89)",
        "color(125)",
        "color(161)",
        "color(197)",
        "color(219)",
    ),
    # lattice — teal / mint, mirrors the lattice accent.
    "lattice": (
        "color(23)",
        "color(30)",
        "color(37)",
        "color(44)",
        "color(50)",
        "color(122)",
    ),
}


def _build_rows(word: str = _WORD, gap: str = " ") -> tuple[str, ...]:
    """Concatenate per-letter rows into the 6 banner rows for ``word``."""
    return tuple(gap.join(_LETTERS[c][row] for c in word) for row in range(6))


def _palette_for(theme_name: str) -> tuple[str, ...]:
    """Return the 6-stop vertical gradient for ``theme_name``. Falls
    back to the carbon (blue) palette if the theme isn't registered."""
    return _THEME_PALETTES.get(theme_name, _THEME_PALETTES["carbon"])


class BrandBanner(Widget):
    """Top-of-screen block-art "aws-tui" banner inside its own subtle border.

    Palette is byte-for-byte the same 15-stop blue→cyan sweep from
    genai-vanilla's ``bootstrapper/utils/banner.py``
    (``color(17)`` Dark Navy → ``color(195)`` Pale Blue). The gradient
    flows vertically: each row of the 6-row block gets one solid color
    from the palette, top dark to bottom light.
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

    def __init__(
        self,
        *,
        theme_name: str = "carbon",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._rows: tuple[str, ...] = _build_rows()
        self._palette: tuple[str, ...] = _palette_for(theme_name)

    def set_theme(self, theme_name: str) -> None:
        """Swap to the theme's color family. The app calls this from
        :meth:`AwsTuiApp.switch_theme` so the banner repaints alongside
        the rest of the chrome instead of staying frozen on the
        previous theme's palette."""
        new_palette = _palette_for(theme_name)
        if new_palette == self._palette:
            return
        self._palette = new_palette
        self.refresh()

    def render(self) -> Text:
        out = Text(justify="center")
        for i, row in enumerate(self._rows):
            if i > 0:
                out.append("\n")
            color = self._palette[min(i, len(self._palette) - 1)]
            out.append(row, style=f"bold {color}")
        return out


__all__ = ["BrandBanner"]
