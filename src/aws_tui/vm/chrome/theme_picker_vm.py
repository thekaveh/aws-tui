"""ThemePickerVM — VMx-backed model for the help-modal theme switcher.

Each available theme is exposed as a child :class:`ThemeOptionVM`. The active
theme is the composite's ``current`` child, so each row's ``is_current`` marks
it (mirrors the file-manager panes' ``EntryVM.is_current``). The picker
owns a :class:`RelayCommandOf[str]` ``pick_theme_command`` that delegates
to a caller-injected callback — the View never touches the App service
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from vmx import (
    ComponentVMOf,
    CompositeVM,
    ConstructionStatus,
    Dispatcher,
    Message,
    MessageHub,
    RelayCommandOf,
)

from aws_tui.vm._component import IsolatedComponentVM


@dataclass(frozen=True, slots=True)
class ThemeOptionState:
    """Per-row model: the theme name."""

    name: str


class ThemeOptionVM:
    """One row in the theme picker. ``is_current`` (the picker's active
    theme) drives the glyph swap (●/○) in the view."""

    def __init__(
        self,
        *,
        name: str,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "theme_option",
    ) -> None:
        self._inner: ComponentVMOf[ThemeOptionState] = (
            ComponentVMOf[ThemeOptionState]
            .builder()
            .name(f"{id_prefix}.{name}")
            .model(ThemeOptionState(name=name))
            .services(hub, dispatcher)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._inner.model.name

    @property
    def is_current(self) -> bool:
        return self._inner.is_current

    @property
    def marker_glyph(self) -> str:
        """``●`` for the active theme, ``○`` otherwise."""
        return "●" if self._inner.is_current else "○"

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


def next_theme_name(names: Sequence[str], active: str) -> str:
    """Return the theme after ``active``, wrapping at the end.

    Return the first name when ``active`` is not listed. Return ``active``
    when ``names`` is empty.
    """
    if not names:
        return active
    index = names.index(active) if active in names else -1
    return names[(index + 1) % len(names)]


class ThemePickerVM(IsolatedComponentVM):
    """Composite VM for the help-modal theme switcher."""

    def __init__(
        self,
        *,
        themes: tuple[str, ...],
        active_theme: str,
        on_pick: Callable[[str], bool],
        on_preview: Callable[[str], bool] | None = None,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        id_prefix: str = "theme_picker",
    ) -> None:
        super().__init__(name=id_prefix, hub=hub, dispatcher=dispatcher)
        self._on_pick: Callable[[str], bool] = on_pick
        self._on_preview: Callable[[str], bool] = (
            on_preview if on_preview is not None else (lambda _name: True)
        )
        self._active_theme: str = active_theme

        self._options: list[ThemeOptionVM] = [
            ThemeOptionVM(
                name=name,
                hub=hub,
                dispatcher=dispatcher,
                id_prefix=f"{id_prefix}.option",
            )
            for name in themes
        ]

        self._inner: CompositeVM[ComponentVMOf[ThemeOptionState]] = (
            CompositeVM[ComponentVMOf[ThemeOptionState]]
            .builder()
            .name(f"{id_prefix}.options")
            .services(hub, dispatcher)
            .children(self._initial_children)
            .current(self._initial_current)
            .auto_construct_on_add(True)
            .build()
        )
        self._own(self._inner)

        self._pick_theme_command: RelayCommandOf[str] = (
            RelayCommandOf[str].builder().task(self._pick).build()
        )
        self._own(self._pick_theme_command)

        self._preview_theme_command: RelayCommandOf[str] = (
            RelayCommandOf[str].builder().task(self._preview).build()
        )
        self._own(self._preview_theme_command)

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
    def preview_command(self) -> RelayCommandOf[str]:
        return self._preview_theme_command

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def _on_construct(self) -> None:
        super()._on_construct()
        self._complete_lifecycle_hook_after(
            self._transition_children((self._inner,), construct=True)
        )

    def _on_destruct(self) -> None:
        super()._on_destruct()
        self._complete_lifecycle_hook_after(
            self._transition_children((self._inner,), construct=False)
        )

    def _on_dispose(self) -> None:
        for opt in self._options:
            opt.dispose()

    # ── Mutators ───────────────────────────────────────────────────────────

    def set_active(self, name: str) -> None:
        """Mark ``name`` as the active theme and clear the rest. Idempotent."""
        if self._active_theme == name:
            return
        self._active_theme = name
        if self._inner.is_constructed:
            option = next((o for o in self._options if o.name == name), None)
            # VMx publishes is_current for the old and the new row; None clears every row.
            self._inner.current = option.inner if option is not None else None
        self._publish("active_theme")

    def next_theme(self) -> str:
        """Return the next theme in the cycle order. Wraps at the end."""
        return next_theme_name([opt.name for opt in self._options], self._active_theme)

    # ── Internal ────────────────────────────────────────────────────────────

    def _pick(self, name: str | None) -> None:
        """Delegate to the injected callback then update the active row."""
        if not name:
            return
        if self._on_pick(name):
            self.set_active(name)

    def _preview(self, name: str | None) -> None:
        """Live-preview ``name`` without committing the pick.

        Calls the injected ``on_preview`` (which the modal wires to
        ``AwsTuiApp.switch_theme`` so the live repaint cascade fires)
        then updates ``active_theme`` so the row marker follows the
        cursor.
        """
        if not name:
            return
        if self._on_preview(name):
            self.set_active(name)

    def _initial_children(self) -> Iterable[ComponentVMOf[ThemeOptionState]]:
        return tuple(opt.inner for opt in self._options)

    def _initial_current(
        self, children: Iterable[ComponentVMOf[ThemeOptionState]]
    ) -> ComponentVMOf[ThemeOptionState] | None:
        return next((c for c in children if c.model.name == self._active_theme), None)


__all__ = ["ThemeOptionState", "ThemeOptionVM", "ThemePickerVM", "next_theme_name"]
