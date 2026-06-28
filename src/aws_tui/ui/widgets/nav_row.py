"""NavRow — one row in the NavMenu rail.

Each NavRow shows ONE service (or the Settings peer) using the
SAME CSS class — ``entry-row`` — that the S3 file-pane rows use.
User feedback (post-PR-#94): "I expect to see exactly the same
style of text, highlights, selected items, current theme, thin
vertical ribbon etc that is currently applied to the selected
and non-selected items inside the left or right s3 panes, to
also be applied to the items inside the menu".

The CSS contract is shared across both surfaces:

- ``entry-row``                — base row chrome (height 1, padding).
- ``entry-row.-selected``      — cursor-row treatment.

Reusing the class names means a theme tweak in the file pane
flows automatically through the nav rail with zero per-theme
duplication, and the visual styles match by construction. The
ribbon glyph (``▌``) is rendered as the first cell of the row's
text — same column position the file pane uses for its cursor
glyph.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.widget import Widget

# Single source of truth for the ribbon glyph + spacer. The
# file pane uses the same glyph for its cursor row; centralising
# here keeps the two surfaces in step if the look ever changes.
_RIBBON_GLYPH: str = "▌"
_RIBBON_SPACER: str = " "


class NavRow(Widget):
    """One nav-rail row backed by a descriptor.

    Mirrors :class:`EntryRow` shape: a custom ``render()`` returns
    a Rich :class:`Text` with the ribbon glyph in column 0 and
    the descriptor label in the rest of the row.
    """

    DEFAULT_CSS: ClassVar[str] = """
    NavRow {
        height: 1;
        /* No horizontal padding — match EntryRow exactly so the
           ribbon glyph (column 0) sits flush against the pane's
           inner left edge, same as the S3 file pane. User feedback
           (post-PR-#101): "The thin vertical bar selected item
           indicator is rendered much closer to the left edge for
           the file pane (correct and expected behavior) which is
           not the case for the menu item". EntryRow's CSS
           (ui/widgets/pane.py) is just ``height: 1`` — mirror that
           here. */
    }
    """

    def __init__(
        self,
        *,
        descriptor_id: str,
        label: str,
        is_selected: bool,
        is_settings: bool = False,
        classes: str | None = None,
    ) -> None:
        # Merge the file-pane row class so per-theme tcss for
        # ``Pane .entry-row`` / ``.-selected`` styles us too.
        # ``-settings`` lets the per-theme CSS apply a divider on
        # the Settings row if it wants to.
        base_classes = ["entry-row"]
        if is_selected:
            base_classes.append("-selected")
        if is_settings:
            base_classes.append("-settings")
        if classes:
            base_classes.append(classes)
        super().__init__(classes=" ".join(base_classes))
        self._descriptor_id: str = descriptor_id
        self._label: str = label
        self._is_selected: bool = is_selected

    @property
    def descriptor_id(self) -> str:
        return self._descriptor_id

    def render(self) -> Text:
        # ``Text.append`` adds literal text — NOT parsed as Rich
        # markup — so descriptor labels containing characters like
        # ``[`` or ``]`` can never crash the renderer.
        text = Text()
        text.append(_RIBBON_GLYPH if self._is_selected else _RIBBON_SPACER)
        text.append(" ")
        text.append(self._label)
        return text


__all__ = ["NavRow"]
