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
    # Design language for every gradient (carbon + amber + lattice are
    # the three reference palettes; all others follow the same rule):
    #
    #   * SIX stops, one per banner row.
    #   * Walk through ONE hue family — no mixing greyscale with blues
    #     or jumping from purple to pink mid-gradient.
    #   * Start DARK (top of the block art) → end LIGHT (bottom).
    #   * The early/middle stops sit in the saturated dark band; the
    #     final stop "pops" notably lighter (large cube-position jump)
    #     for a clear dark-to-bright sweep — same shape as the
    #     genai-vanilla 15-stop blue→cyan reference the carbon palette
    #     subsamples directly.
    #   * The brightest stop lands close to the theme's ``$accent``
    #     (or ``$accent-hot`` for themes whose identity leans on the
    #     hot variant) so the banner reads as part of the brand.
    #   * Source stops from the 256-color cube so neighbours are
    #     guaranteed perceptually adjacent.
    # carbon — REFERENCE. Direct 6-stop subsample of the genai-vanilla
    # 15-stop blue→cyan gradient (color(17) deep navy → color(195)
    # pale blue), matching the upstream banner the carbon theme was
    # spec'd against.
    "carbon": (
        "color(17)",  # #00005f  deep navy
        "color(21)",  # #0000ff  pure blue (genai-vanilla mid)
        "color(33)",  # #0087ff  azure
        "color(45)",  # #00d7ff  bright cyan
        "color(123)",  # #87ffff  pale cyan
        "color(195)",  # #d7d7ff  very pale blue
    ),
    # amber — REFERENCE. Mahogany → gold through the orange band.
    "amber": (
        "color(94)",  # #875f00  dark amber
        "color(130)",  # #af5f00  burnt orange
        "color(166)",  # #d75f00  orange
        "color(208)",  # #ff8700
        "color(214)",  # #ffaf00
        "color(220)",  # #ffd700  gold
    ),
    # lattice — REFERENCE. Dark teal → pale mint through the cyan band.
    "lattice": (
        "color(23)",  # #005f5f  dark teal
        "color(30)",  # #008787  teal-cyan
        "color(37)",  # #00afaf  cyan
        "color(44)",  # #00d7d7  bright cyan
        "color(50)",  # #00ffd7  cyan-mint
        "color(122)",  # #87ffd7  pale mint
    ),
    # voidline — deep magenta → pale pink, electric-CRT identity. Lands
    # close to $accent-hot #ff3df8 (the louder of voidline's twin
    # accents — magenta carries the brand more than the cyan does).
    "voidline": (
        "color(53)",  # #5f005f  deep magenta
        "color(90)",  # #870087
        "color(127)",  # #af00af  bright magenta
        "color(164)",  # #d700d7  pink-magenta
        "color(206)",  # #ff5fd7  hot pink (kin to $accent-hot #ff3df8)
        "color(219)",  # #ffafff  pale pink
    ),
    # solarized-light — Solarized blue family. LIGHT theme: stops stay
    # in the saturated dark band so the gradient pops on the cream bg.
    "solarized-light": (
        "color(17)",  # #00005f  navy
        "color(18)",  # #000087
        "color(19)",  # #0000af
        "color(20)",  # #0000d7
        "color(26)",  # #005fd7  Solarized blue family
        "color(32)",  # #0087d7  (kin to $accent #268bd2)
    ),
    # github-light — Primer link-blue family. LIGHT theme; dark stops
    # for contrast against the white bg.
    "github-light": (
        "color(17)",  # #00005f  navy
        "color(18)",  # #000087
        "color(19)",  # #0000af
        "color(20)",  # #0000d7
        "color(26)",  # #005fd7
        "color(33)",  # #0087ff  GitHub link blue (kin to $accent #0969da)
    ),
    # one-light — Atom One Light's deep-blue family. LIGHT theme.
    "one-light": (
        "color(17)",  # #00005f
        "color(18)",  # #000087
        "color(19)",  # #0000af
        "color(20)",  # #0000d7
        "color(27)",  # #005fff
        "color(33)",  # #0087ff  (kin to $accent #4078f2)
    ),
    # nord — Frost cyan family. Walks the Frost band end-to-end.
    "nord": (
        "color(24)",  # #005f87  dark Frost
        "color(31)",  # #0087af
        "color(38)",  # #00b7af  teal-cyan
        "color(45)",  # #00d7ff  cyan
        "color(74)",  # #5fafd7  Frost-blue
        "color(110)",  # #87afd7  pale Frost (kin to $accent #88c0d0)
    ),
    # dracula — pure purple walk, Dracula's signature palette. Ends
    # near $accent #bd93f9 instead of drifting into pink — leaves
    # $accent-hot's pink as a footer-only accent rather than competing
    # with the banner.
    "dracula": (
        "color(54)",  # #5f0087  deep purple
        "color(56)",  # #5f00d7  bright purple
        "color(63)",  # #5f5fff  purple-blue
        "color(99)",  # #875fff  purple
        "color(141)",  # #af87ff  light purple (kin to $accent #bd93f9)
        "color(183)",  # #d7afff  pale purple
    ),
    # gruvbox-dark — Gruvbox's forest-to-olive green walk. Distinct
    # hue from amber's orange-gold (amber's identity owns the warm
    # yellow band); green is Gruvbox's other signature accent and
    # lands at olive-gold near $accent #fabd2f.
    "gruvbox-dark": (
        "color(22)",  # #005f00  dark forest
        "color(28)",  # #008700
        "color(34)",  # #00af00  bright green
        "color(70)",  # #5faf00  yellow-green
        "color(106)",  # #87af00  olive
        "color(142)",  # #afaf00  gold-olive (kin to $accent #fabd2f)
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
