"""HintLegend widget — responsive command grid bound to :class:`HintLegendVM`.

Each chip is rendered as a pair of Static widgets (key + label) with
distinct CSS classes (``.hint-key`` / ``.hint-label``). Coloring now
comes from the theme tcss (``$accent`` for keys, ``$text-muted`` for
labels) instead of hard-coded Rich styles — that's what makes the bar
adopt the new accent the moment the user switches themes.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, ItemGrid
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.hint_legend_vm import HintAction, HintLegendVM


class HintLegend(HubSubscriberMixin, Widget):
    """Bottom hint legend that wraps commands to fit the viewport."""

    # Structural only — colors / border come from the theme stylesheet
    # so that a runtime theme swap repaints the footer immediately.
    # ``align: center middle`` on the host + ``align-horizontal: center``
    # on the chip strip centers the row even when chips don't fill it.
    DEFAULT_CSS = """
    HintLegend {
        height: auto;
        min-height: 3;
        margin: 0 1 1 1;
        border-title-align: left;
    }
    HintLegend > #hint-strip {
        height: auto;
        min-height: 1;
        width: 1fr;
        grid-gutter: 0 1;
    }
    HintLegend .hint-chip {
        width: 1fr;
        height: 1;
        align-horizontal: center;
    }
    HintLegend .hint-key {
        width: auto;
        height: 1;
        text-style: bold;
    }
    HintLegend .hint-key.-disabled {
        text-style: dim;
    }
    HintLegend .hint-label {
        width: auto;
        height: 1;
        padding-left: 1;
    }
    HintLegend .hint-label.-disabled {
        text-style: dim;
    }
    """

    def __init__(
        self,
        vm: HintLegendVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: HintLegendVM = vm
        self._hub: MessageHub[Message] = hub
        # Border title at top-left — renamed from "Shortcuts" to
        # "Commands" per user feedback ("I want the Bottom pane fr
        # Shortcuts to be renamed to 'Commands'"). The chips are
        # actionable commands, not keyboard shortcuts as such — the
        # name "Commands" reads honestly.
        self.border_title = "Commands"

    @property
    def vm(self) -> HintLegendVM:
        return self._vm

    def compose(self) -> ComposeResult:
        # One responsive grid — service-specific chips first
        # (S3 copy/delete etc., EMR switch-app etc.), then the
        # always-visible app-chrome globals (themes / help / quit).
        # The split into left/right docks was reverted at user's
        # explicit ask ("I want their concatenation displayed at the
        # bottom"). ItemGrid preserves that order while wrapping the
        # full command list instead of clipping later actions.
        with ItemGrid(
            id="hint-strip",
            min_column_width=22,
            stretch_height=False,
            regular=True,
        ):
            yield from self._build_chips(self._all_chips())

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=("actions",),
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "actions":
            self.call_after_refresh(self._rebuild_chips)

    def _all_chips(self) -> tuple[HintAction, ...]:
        """Service-specific chips followed by the app-chrome globals
        in a single ordered tuple."""
        return tuple(self._vm.actions) + tuple(self._vm.global_actions)

    def _rebuild_chips(self) -> None:
        try:
            strip = self.query_one("#hint-strip", ItemGrid)
        except Exception:
            return
        for child in list(strip.children):
            child.remove()
        for chip in self._build_chips(self._all_chips()):
            strip.mount(chip)

    def _build_chips(self, chips: tuple[HintAction, ...]) -> list[Widget]:
        widgets: list[Widget] = []
        for chip in chips:
            # Wrap the key in ``[...]`` brackets — same visual treatment
            # as the genai-vanilla reference (``[a] all  ·  [e] errors  ·  …``)
            # so the bound key is unambiguous even when an action label
            # itself looks key-like.
            #
            # ``markup=False`` is CRITICAL: Static parses its content as
            # Rich markup by default, and ``[tab]`` / ``[c]`` / ``[d]``
            # etc. would get parsed as (unknown) style tags and silently
            # stripped — so only the chips whose key isn't a valid Rich
            # tag name (``:``, ``?``, …) would render correctly. With
            # markup disabled, every chip prints its bracketed key as
            # plain text.
            disabled_suffix = " -disabled" if not chip.enabled else ""
            key = Static(
                f"[{chip.key_label}]",
                classes=f"hint-key{disabled_suffix}",
                markup=False,
            )
            label = Static(chip.action_label, classes=f"hint-label{disabled_suffix}")
            widgets.append(Horizontal(key, label, classes="hint-chip"))
        return widgets


__all__ = ["HintLegend"]
