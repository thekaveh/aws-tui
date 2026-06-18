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

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.vm.messages import ThemeChangedMessage

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
# at the bottom, in each theme's accent color family. Palette ranges are
# picked from the 256-color cube so neighbors are perceptually close —
# the lattice (teal) palette is the reference the others now match.
_THEME_PALETTES: dict[str, tuple[str, ...]] = {
    # carbon — slate gray → ice-blue. The carbon theme is "near-
    # monochrome with one accent" (per spec §4.5); the previous
    # navy→azure gradient was too saturated for that aesthetic. This
    # palette walks from charcoal up to the carbon accent token's
    # ice-blue family so the banner reads as part of the theme.
    "carbon": (
        "color(236)",  # #303030  charcoal
        "color(238)",  # #444444
        "color(60)",  # #5f5f87  slate
        "color(67)",  # #5f87af  slate-blue
        "color(74)",  # #5fafd7  cool azure
        "color(117)",  # #87d7ff  pale ice-blue (kin to $accent #6fb8ff)
    ),
    # amber — dark mahogany → gold, the smooth orange band of the
    # cube. Tracks the amber-CRT theme's accent.
    "amber": (
        "color(94)",  # #875f00  dark amber
        "color(130)",  # #af5f00  burnt orange
        "color(166)",  # #d75f00  orange
        "color(208)",  # #ff8700
        "color(214)",  # #ffaf00
        "color(220)",  # #ffd700  gold
    ),
    # voidline — deep purple → soft pink. Smooth march through the
    # magenta band; the bottom stop is light enough to read on the
    # dark background.
    "voidline": (
        "color(54)",  # #5f0087  deep purple
        "color(91)",  # #8700af
        "color(128)",  # #af00d7
        "color(165)",  # #d700ff  magenta
        "color(207)",  # #ff5fff
        "color(219)",  # #ffafff  light pink
    ),
    # lattice — teal / mint, the user-approved reference palette.
    "lattice": (
        "color(23)",  # #005f5f  dark teal
        "color(30)",
        "color(37)",
        "color(44)",
        "color(50)",
        "color(122)",  # #87ffd7  pale mint
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
        hub: MessageHub[Message] | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._rows: tuple[str, ...] = _build_rows()
        self._palette: tuple[str, ...] = _palette_for(theme_name)
        self._hub: MessageHub[Message] | None = hub
        self._sub: DisposableBase | None = None

    def on_mount(self) -> None:
        if self._hub is not None:
            self._sub = self._hub.messages.subscribe(on_next=self._on_hub_message)

    def on_unmount(self) -> None:
        if self._sub is not None:
            self._sub.dispose()
            self._sub = None

    def _on_hub_message(self, msg: object) -> None:
        """React to a hub-broadcast theme change so the banner stays in
        sync with the rest of the chrome without the app reaching in
        per widget type."""
        if isinstance(msg, ThemeChangedMessage):
            self.set_theme(msg.name)

    def set_theme(self, theme_name: str) -> None:
        """Swap to the theme's color family. Idempotent; called from
        :meth:`_on_hub_message` (preferred) or directly during initial
        composition."""
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
