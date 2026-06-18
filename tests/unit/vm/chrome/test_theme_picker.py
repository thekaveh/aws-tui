"""Tests for the ThemePickerVM, the VMx-backed model that owns the
theme list and the currently-active theme."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def _build(active: str = "carbon") -> ThemePickerVM:
    picks: list[str] = []
    vm = ThemePickerVM(
        themes=("carbon", "voidline", "lattice", "amber"),
        active_theme=active,
        on_pick=lambda n: picks.append(n),
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    vm.construct()
    # Stash the recorder so tests can read it.
    vm._picks = picks  # type: ignore[attr-defined]
    return vm


def test_active_theme_returns_constructor_value() -> None:
    vm = _build(active="voidline")
    try:
        assert vm.active_theme == "voidline"
    finally:
        vm.dispose()


def test_next_theme_cycles_forward() -> None:
    vm = _build(active="carbon")
    try:
        assert vm.next_theme() == "voidline"
    finally:
        vm.dispose()


def test_next_theme_wraps_at_end() -> None:
    vm = _build(active="amber")  # last in the tuple
    try:
        assert vm.next_theme() == "carbon", "next after the last must wrap to the first"
    finally:
        vm.dispose()


def test_next_theme_handles_unregistered_active() -> None:
    """If active_theme isn't in the registered list (e.g. user theme),
    next_theme returns the first registered theme."""
    vm = _build(active="phantom")
    try:
        assert vm.next_theme() == "carbon"
    finally:
        vm.dispose()


def test_set_active_updates_options_and_emits_property_changed() -> None:
    vm = _build(active="carbon")
    try:
        seen: list[str] = []

        def _on_message(msg: object) -> None:
            name = getattr(msg, "property_name", None)
            if name == "active_theme":
                seen.append(str(getattr(msg, "sender_name", "")))

        sub = vm._hub.messages.subscribe(on_next=_on_message)  # type: ignore[attr-defined]
        try:
            vm.set_active("voidline")
            assert vm.active_theme == "voidline"
            assert seen, "set_active must broadcast active_theme PropertyChangedMessage"
            # Option state flipped — only one is active.
            actives = [opt for opt in vm.options if opt.is_active]
            assert len(actives) == 1
            assert actives[0].name == "voidline"
        finally:
            sub.dispose()
    finally:
        vm.dispose()


def test_set_active_is_idempotent() -> None:
    vm = _build(active="carbon")
    try:
        emitted: list[object] = []
        sub = vm._hub.messages.subscribe(  # type: ignore[attr-defined]
            on_next=lambda m: (
                emitted.append(m) if getattr(m, "property_name", None) == "active_theme" else None
            )
        )
        try:
            vm.set_active("carbon")  # same as current
            assert emitted == [], "set_active to the current theme must be a no-op"
        finally:
            sub.dispose()
    finally:
        vm.dispose()


def test_pick_theme_command_invokes_on_pick_and_sets_active() -> None:
    vm = _build(active="carbon")
    try:
        vm.pick_theme_command.execute("amber")
        assert vm._picks == ["amber"]  # type: ignore[attr-defined]
        assert vm.active_theme == "amber"
    finally:
        vm.dispose()


def test_pick_theme_command_ignores_empty_string_and_none() -> None:
    vm = _build(active="carbon")
    try:
        vm.pick_theme_command.execute(None)
        vm.pick_theme_command.execute("")
        assert vm._picks == []  # type: ignore[attr-defined]
        assert vm.active_theme == "carbon"
    finally:
        vm.dispose()
