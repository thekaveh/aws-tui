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

# Per-theme 6-stop vertical sweep for the banner. Two layout conventions
# co-exist by deliberate choice:
#   * amber and lattice walk DARK → LIGHT top-to-bottom (their look the
#     user already approved — don't touch).
#   * carbon (the genai-vanilla reference) plus every other theme walk
#     LIGHT → DARK top-to-bottom, matching the upstream reference image
#     where the pale tint sits at the top of the block art and the
#     saturated dark sits at the bottom.
# Palette stops are picked from the 256-color cube so neighbours are
# perceptually adjacent within each hue family.
_THEME_PALETTES: dict[str, tuple[str, ...]] = {
    # Design language:
    #
    #   * SIX stops, one per banner row.
    #   * Walk through ONE hue family — no mixing greyscale with blues
    #     or jumping from purple to pink mid-gradient.
    #   * Direction: carbon + all non-amber/lattice themes go LIGHT →
    #     DARK top-to-bottom (matches the genai-vanilla reference
    #     image). amber and lattice keep their existing DARK → LIGHT
    #     orientation — the user signed off on those two specifically.
    #   * The darkest, most saturated stop sits at the *bottom* of the
    #     block art and lands close to the theme's ``$accent``
    #     (or ``$accent-hot`` for themes whose identity leans on the
    #     hot variant). The lightest pale tint sits at the top.
    #   * Source stops from the 256-color cube so neighbours are
    #     guaranteed perceptually adjacent.
    # carbon — REFERENCE. Pure-blue 6-stop walk that matches the
    # genai-vanilla upstream image: no cyan, no greenish tints. Every
    # stop has G≤175 / R≤95 (with the brightest stop slightly above
    # to reach a sky-blue tint without crossing into cyan). The
    # earlier carbon palette mixed in cyan stops color(123) / color(45)
    # — those have a strong green channel and read as turquoise, which
    # the reference image does not have.
    "carbon": (
        "color(75)",  # #5fafff  bright sky-blue
        "color(33)",  # #0087ff  azure
        "color(27)",  # #005fff  bright pure blue
        "color(21)",  # #0000ff  pure blue (saturated)
        "color(19)",  # #0000af  dark blue
        "color(17)",  # #00005f  deep navy
    ),
    # amber — REFERENCE (unchanged). Mahogany → gold, dark → light.
    "amber": (
        "color(94)",  # #875f00  dark amber
        "color(130)",  # #af5f00  burnt orange
        "color(166)",  # #d75f00  orange
        "color(208)",  # #ff8700
        "color(214)",  # #ffaf00
        "color(220)",  # #ffd700  gold
    ),
    # lattice — REFERENCE (unchanged). Dark teal → pale mint.
    "lattice": (
        "color(23)",  # #005f5f  dark teal
        "color(30)",  # #008787  teal-cyan
        "color(37)",  # #00afaf  cyan
        "color(44)",  # #00d7d7  bright cyan
        "color(50)",  # #00ffd7  cyan-mint
        "color(122)",  # #87ffd7  pale mint
    ),
    # voidline — pale pink → deep magenta, electric-CRT identity. Ends
    # near $accent-hot #ff3df8 family at the bottom.
    "voidline": (
        "color(219)",  # #ffafff  pale pink
        "color(206)",  # #ff5fd7  hot pink (kin to $accent-hot #ff3df8)
        "color(164)",  # #d700d7  pink-magenta
        "color(127)",  # #af00af  bright magenta
        "color(90)",  # #870087
        "color(53)",  # #5f005f  deep magenta
    ),
    # solarized-light — Solarized blue family. LIGHT theme: even the
    # "light" end is still a saturated blue so it reads on cream bg.
    "solarized-light": (
        "color(32)",  # #0087d7  (kin to $accent #268bd2)
        "color(26)",  # #005fd7
        "color(20)",  # #0000d7
        "color(19)",  # #0000af
        "color(18)",  # #000087
        "color(17)",  # #00005f  navy
    ),
    # github-light — Primer link-blue family, LIGHT theme.
    "github-light": (
        "color(33)",  # #0087ff  GitHub link blue (kin to $accent #0969da)
        "color(26)",  # #005fd7
        "color(20)",  # #0000d7
        "color(19)",  # #0000af
        "color(18)",  # #000087
        "color(17)",  # #00005f  navy
    ),
    # one-light — Atom One Light's deep-blue family. LIGHT theme.
    "one-light": (
        "color(33)",  # #0087ff  (kin to $accent #4078f2)
        "color(27)",  # #005fff
        "color(20)",  # #0000d7
        "color(19)",  # #0000af
        "color(18)",  # #000087
        "color(17)",  # #00005f
    ),
    # nord — Frost cyan family, pale → dark.
    "nord": (
        "color(110)",  # #87afd7  pale Frost (kin to $accent #88c0d0)
        "color(74)",  # #5fafd7  Frost-blue
        "color(45)",  # #00d7ff  cyan
        "color(38)",  # #00b7af  teal-cyan
        "color(31)",  # #0087af
        "color(24)",  # #005f87  dark Frost
    ),
    # dracula — pure purple walk, pale → deep. Lands near $accent
    # #bd93f9 at the saturated bottom rows.
    "dracula": (
        "color(183)",  # #d7afff  pale purple
        "color(141)",  # #af87ff  light purple (kin to $accent #bd93f9)
        "color(99)",  # #875fff  purple
        "color(63)",  # #5f5fff  purple-blue
        "color(56)",  # #5f00d7  bright purple
        "color(54)",  # #5f0087  deep purple
    ),
    # gruvbox-dark — forest-to-olive green walk, pale → dark.
    "gruvbox-dark": (
        "color(142)",  # #afaf00  gold-olive (kin to $accent #fabd2f)
        "color(106)",  # #87af00  olive
        "color(70)",  # #5faf00  yellow-green
        "color(34)",  # #00af00  bright green
        "color(28)",  # #008700
        "color(22)",  # #005f00  dark forest
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
