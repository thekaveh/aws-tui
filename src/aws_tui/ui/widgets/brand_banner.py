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

import colorsys

from reactivex.abc import DisposableBase
from rich.text import Text
from textual.widget import Widget
from vmx import Message, MessageHub

from aws_tui.version import __version__
from aws_tui.vm.messages import ThemeChangedMessage

# Project tagline (top-left of the banner's border) and pedigree
# (bottom-right). Match the visual treatment of the genai-vanilla
# reference: short positioning line up top, attribution / licence /
# version / repo line down bottom.
_TAGLINE = "A cross-platform TUI for select AWS and S3-compatible services."
_PEDIGREE = (
    "by Kaveh Razavi <kaveh.razavi@gmail.com>  ·  Apache License 2.0  ·  "
    f"v{__version__}  ·  https://github.com/thekaveh/aws-tui"
)

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

# ── Carbon-derived gradient transform ───────────────────────────────────────
#
# Carbon's user-specified six-stop palette is the *reference shape* the other
# themes follow: a smooth light→dark vertical sweep, holding one hue family,
# with the saturation peaking in the middle and the lightness walking from
# ~71% at the top down to ~19% at the bottom.
#
# Rather than hand-author equivalent palettes per theme (which drifts the
# moment carbon's stops change), we extract carbon's (L, S) progression once
# and re-apply it at each theme's *accent hue*. The output is six fresh hex
# stops that read as the same gradient shape rendered in the theme's
# signature colour family. Background, "passive" (low-emphasis chrome), and
# "active" (accent) inputs all live in the per-theme .tcss palette — the
# transform's job is to map carbon's lightness curve onto the active token's
# hue so the banner reads as part of the theme's identity.
#
# amber and lattice are explicitly excluded from the transform: the user
# signed off on those two earlier and asked they stay untouched.

# Reference palette — user-specified verbatim. Source of the (L, S) curve.
_CARBON_REFERENCE: tuple[str, ...] = (
    "#74A6F4",  # row 1 (top, brightest)
    "#4F8AED",  # row 2
    "#316DDF",  # row 3
    "#1F4FBE",  # row 4
    "#14338B",  # row 5
    "#0A1A55",  # row 6 (bottom, darkest)
)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex_to_hls(hex_str: str) -> tuple[float, float, float]:
    r, g, b = _hex_to_rgb(hex_str)
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def _hls_to_hex(h: float, lightness: float, saturation: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h, lightness, saturation)
    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


# Per-row (lightness, saturation) extracted from the carbon reference. These
# are the only knobs the transform reads from carbon — the hue is replaced
# per theme so the gradient SHAPE is preserved while the COLOUR shifts.
_CARBON_LS_CURVE: tuple[tuple[float, float], ...] = tuple(
    (_hex_to_hls(stop)[1], _hex_to_hls(stop)[2]) for stop in _CARBON_REFERENCE
)


def _carbon_like_palette(hue_degrees: float) -> tuple[str, ...]:
    """Return a 6-stop palette at ``hue_degrees`` using carbon's lightness +
    saturation progression byte-for-byte. The returned palette walks light →
    dark top-to-bottom just like carbon."""
    hue = (hue_degrees % 360.0) / 360.0
    return tuple(
        _hls_to_hex(hue, lightness, saturation) for lightness, saturation in _CARBON_LS_CURVE
    )


# Each non-reference theme picks a representative hue from its $accent (the
# "active" colour). voidline is the exception: its brand identity leans on
# $accent-hot (electric magenta) more than the $accent cyan, so we transform
# from the hot variant.
_THEME_HUES_DEGREES: dict[str, float] = {
    "voidline": 302.0,  # $accent-hot magenta carries the brand
    "solarized-light": 205.0,  # $accent #268bd2
    "github-light": 212.0,  # $accent #0969da
    "one-light": 221.0,  # $accent #4078f2
    "nord": 193.0,  # $accent #88c0d0 (Frost cyan)
    "dracula": 265.0,  # $accent #bd93f9 (Dracula purple)
    "gruvbox-dark": 42.0,  # $accent #fabd2f (yellow-gold)
}


_THEME_PALETTES: dict[str, tuple[str, ...]] = {
    # Carbon stays at the user's verbatim hex stops — not derived, so a
    # future tweak to the transform never silently drifts the reference.
    "carbon": _CARBON_REFERENCE,
    # amber + lattice are explicitly excluded from the transform.
    "amber": (
        "color(94)",  # #875f00  dark amber
        "color(130)",  # #af5f00  burnt orange
        "color(166)",  # #d75f00  orange
        "color(208)",  # #ff8700
        "color(214)",  # #ffaf00
        "color(220)",  # #ffd700  gold
    ),
    "lattice": (
        "color(23)",  # #005f5f  dark teal
        "color(30)",  # #008787  teal-cyan
        "color(37)",  # #00afaf  cyan
        "color(44)",  # #00d7d7  bright cyan
        "color(50)",  # #00ffd7  cyan-mint
        "color(122)",  # #87ffd7  pale mint
    ),
    # Every other theme is derived from carbon's (L, S) curve at the
    # theme's $accent hue (or $accent-hot for voidline).
    **{name: _carbon_like_palette(hue) for name, hue in _THEME_HUES_DEGREES.items()},
}


def _build_rows(word: str = _WORD, gap: str = "") -> tuple[str, ...]:
    """Concatenate per-letter rows into the 6 banner rows for ``word``.

    Default ``gap=""`` reproduces ANSI-Shadow's natural tight kerning:
    each glyph already includes the column of whitespace it needs to
    sit beside its neighbour, so inserting an additional space would
    add a stray column at every letter boundary (~2 cells too wide
    per letter, e.g. AWS-TUI: 59 cols with a single space vs 53 cols
    natural — the same density difference visible between the
    spread-out previous title and the genai-vanilla reference).
    """
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
        border-title-align: left;
        border-subtitle-align: right;
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
        # Tagline (border title) + heritage / pedigree (border subtitle)
        # — same visual treatment as the genai-vanilla reference. Set on
        # the widget so they survive theme swaps; the border itself
        # comes from the theme tcss.
        self.border_title = _TAGLINE
        self.border_subtitle = _PEDIGREE

    @property
    def palette(self) -> tuple[str, ...]:
        """Current 6-stop banner gradient (read-only).

        Exposed so callers (notably tests) can verify a theme swap took
        effect without reaching into the private ``_palette`` attribute.
        """
        return self._palette

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
