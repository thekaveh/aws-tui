"""HintLegend widget — bottom contextual hint row bound to :class:`HintLegendVM`.

Each chip is rendered as a pair of Static widgets (key + label) with
distinct CSS classes (``.hint-key`` / ``.hint-label``). Coloring now
comes from the theme tcss (``$accent`` for keys, ``$text-muted`` for
labels) instead of hard-coded Rich styles — that's what makes the bar
adopt the new accent the moment the user switches themes.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.chrome.hint_legend_vm import HintAction, HintLegendVM


class HintLegend(HubSubscriberMixin, Widget):
    """Bottom hint-legend row."""

    # Structural only — colors / border come from the theme stylesheet
    # so that a runtime theme swap repaints the footer immediately.
    # ``align: center middle`` on the host + ``align-horizontal: center``
    # on the chip strip centers the row even when chips don't fill it.
    DEFAULT_CSS = """
    HintLegend {
        height: 3;
        margin: 0 1 1 1;
        layout: horizontal;
        border-title-align: left;
    }
    HintLegend > #hint-strip-service {
        height: 1;
        width: 1fr;
        layout: horizontal;
    }
    HintLegend > #hint-strip-global {
        height: 1;
        width: auto;
        layout: horizontal;
        dock: right;
    }
    HintLegend .hint-key {
        width: auto;
        height: 1;
        text-style: bold;
    }
    HintLegend .hint-label {
        width: auto;
        height: 1;
        padding: 0 1 0 1;
    }
    HintLegend .hint-sep {
        width: auto;
        height: 1;
        padding: 0 1 0 1;
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
        # LEFT strip — service-specific chips (S3 copy/delete etc.,
        # EMR switch-app etc.). Rebuilt on every ``actions`` /
        # service-id change.
        with Horizontal(id="hint-strip-service"):
            yield from self._build_chips(self._vm.actions)
        # RIGHT strip — always-visible app-chrome globals (themes /
        # help / quit). Docked right via CSS so they sit flush
        # against the right edge of the Commands pane.
        with Horizontal(id="hint-strip-global"):
            yield from self._build_chips(self._vm.global_actions)

    def on_mount(self) -> None:
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name == "actions":
            self.call_after_refresh(self._rebuild_chips)

    def _rebuild_chips(self) -> None:
        for strip_id, chips in (
            ("#hint-strip-service", self._vm.actions),
            ("#hint-strip-global", self._vm.global_actions),
        ):
            try:
                strip = self.query_one(strip_id, Horizontal)
            except Exception:
                continue
            for child in list(strip.children):
                child.remove()
            for chip in self._build_chips(chips):
                strip.mount(chip)

    def _build_chips(self, chips: tuple[HintAction, ...]) -> list[Widget]:
        widgets: list[Widget] = []
        for i, chip in enumerate(chips):
            if i > 0:
                widgets.append(Static("·", classes="hint-sep"))
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
            widgets.append(Static(f"[{chip.key_label}]", classes="hint-key", markup=False))
            widgets.append(Static(chip.action_label, classes="hint-label"))
        return widgets


__all__ = ["HintLegend"]
