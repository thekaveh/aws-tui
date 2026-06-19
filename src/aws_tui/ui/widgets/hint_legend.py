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
from aws_tui.vm.chrome.hint_legend_vm import HintLegendVM


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
        align: center middle;
        border-title-align: left;
    }
    HintLegend > #hint-strip {
        height: 1;
        width: auto;
        align-horizontal: center;
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
        # Border title at top-left — matches the genai-vanilla reference
        # where the keymap footer is framed by a labelled rule.
        self.border_title = "Shortcuts"

    @property
    def vm(self) -> HintLegendVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Horizontal(id="hint-strip"):
            yield from self._build_chips()

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
        try:
            strip = self.query_one("#hint-strip", Horizontal)
        except Exception:
            return
        for child in list(strip.children):
            child.remove()
        for chip in self._build_chips():
            strip.mount(chip)

    def _build_chips(self) -> list[Widget]:
        widgets: list[Widget] = []
        for i, chip in enumerate(self._vm.actions):
            if i > 0:
                widgets.append(Static("·", classes="hint-sep"))
            # Wrap the key in ``[...]`` brackets — same visual treatment
            # as the genai-vanilla reference (``[a] all  ·  [e] errors  ·  …``)
            # so the bound key is unambiguous even when an action label
            # itself looks key-like.
            widgets.append(Static(f"[{chip.key_label}]", classes="hint-key"))
            widgets.append(Static(chip.action_label, classes="hint-label"))
        return widgets


__all__ = ["HintLegend"]
