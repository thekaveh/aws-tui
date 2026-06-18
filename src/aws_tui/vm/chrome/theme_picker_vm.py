"""ThemePickerVM — VMx-backed model for the help-modal theme switcher.

Each built-in theme is exposed as a child :class:`ThemeOptionVM` with an
observable ``is_active`` flag (mirrors the row-level reactivity the
file-manager panes use for :class:`EntryVM.is_selected`). The picker
owns a :class:`RelayCommandOf[str]` ``pick_theme_command`` that delegates
to a caller-injected callback — the View never touches the App service
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from vmx import (
    ComponentVMOf,
    CompositeVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommandOf,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


@dataclass(frozen=True, slots=True)
class ThemeOptionState:
    """Per-row model: theme name + whether it's the active stylesheet."""

    name: str
    is_active: bool = False


class ThemeOptionVM:
    """One row in the theme-picker. Observable ``is_active`` drives the
    glyph swap (●/○) in the view."""

    def __init__(
        self,
        *,
        name: str,
        is_active: bool,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "theme_option",
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._inner: ComponentVMOf[ThemeOptionState] = (
            ComponentVMOf[ThemeOptionState]
            .builder()
            .name(f"{id_prefix}.{name}")
            .model(ThemeOptionState(name=name, is_active=is_active))
            .services(hub, dispatcher)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._inner.model.name

    @property
    def is_active(self) -> bool:
        return self._inner.model.is_active

    @property
    def marker_glyph(self) -> str:
        """``●`` for the active theme, ``○`` otherwise."""
        return "●" if self._inner.model.is_active else "○"

    @property
    def inner(self) -> ComponentVMOf[ThemeOptionState]:
        return self._inner

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    # ── State mutators ─────────────────────────────────────────────────────

    def set_active(self, value: bool) -> None:
        if self._inner.model.is_active == value:
            return
        self._inner.model = replace(self._inner.model, is_active=value)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "is_active"))


class ThemePickerVM:
    """Composite VM for the help-modal theme switcher."""

    def __init__(
        self,
        *,
        themes: tuple[str, ...],
        active_theme: str,
        on_pick: Callable[[str], None],
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "theme_picker",
    ) -> None:
        self._hub: MessageHub[Message] = hub
        self._on_pick: Callable[[str], None] = on_pick
        self._active_theme: str = active_theme

        self._options: list[ThemeOptionVM] = [
            ThemeOptionVM(
                name=name,
                is_active=(name == active_theme),
                hub=hub,
                dispatcher=dispatcher,
                id_prefix=f"{id_prefix}.option",
            )
            for name in themes
        ]

        self._inner: CompositeVM[ComponentVMOf[ThemeOptionState]] = (
            CompositeVM[ComponentVMOf[ThemeOptionState]]
            .builder()
            .name(id_prefix)
            .services(hub, dispatcher)
            .children(self._initial_children)
            .auto_construct_on_add(True)
            .build()
        )

        self._pick_theme_command: RelayCommandOf[str] = (
            RelayCommandOf[str].builder().task(self._pick).build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def options(self) -> tuple[ThemeOptionVM, ...]:
        return tuple(self._options)

    @property
    def active_theme(self) -> str:
        return self._active_theme

    @property
    def pick_theme_command(self) -> RelayCommandOf[str]:
        return self._pick_theme_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._pick_theme_command.dispose()
        for opt in self._options:
            opt.dispose()
        self._inner.dispose()

    # ── Mutators ───────────────────────────────────────────────────────────

    def set_active(self, name: str) -> None:
        """Mark ``name`` as the active theme and clear the rest. Idempotent."""
        if self._active_theme == name:
            return
        self._active_theme = name
        for opt in self._options:
            opt.set_active(opt.name == name)
        self._hub.send(PropertyChangedMessage.create(self, self._inner.name, "active_theme"))

    def next_theme(self) -> str:
        """Return the next theme in the cycle order. Wraps at the end.

        Used by the cycle action (Shift+T) so both the cycle and modal
        paths share one canonical source of truth (the registered theme
        tuple this VM owns) instead of computing the order in the View
        layer.
        """
        names = [opt.name for opt in self._options]
        if not names:
            return self._active_theme
        try:
            idx = names.index(self._active_theme)
        except ValueError:
            idx = -1
        return names[(idx + 1) % len(names)]

    # ── Internal ────────────────────────────────────────────────────────────

    def _pick(self, name: str | None) -> None:
        """Delegate to the injected callback then update the active row."""
        if not name:
            return
        self._on_pick(name)
        self.set_active(name)

    def _initial_children(self) -> Iterable[ComponentVMOf[ThemeOptionState]]:
        return tuple(opt.inner for opt in self._options)


__all__ = ["ThemeOptionState", "ThemeOptionVM", "ThemePickerVM"]
