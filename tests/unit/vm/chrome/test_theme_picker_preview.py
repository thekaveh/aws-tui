"""Tests for ThemePickerVM.preview_command and Esc-rollback semantics."""

from __future__ import annotations

from typing import cast

from vmx import NULL_DISPATCHER, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm.chrome.theme_picker_vm import ThemePickerVM


def _hub() -> MessageHub[Message]:
    return cast("MessageHub[Message]", MessageHub())


def test_preview_command_calls_on_preview_without_committing_pick() -> None:
    previewed: list[str] = []
    picked: list[str] = []

    def _record_pick(name: str) -> bool:
        picked.append(name)
        return True

    def _record_preview(name: str) -> bool:
        previewed.append(name)
        return True

    picker = ThemePickerVM(
        themes=("carbon", "amber", "voidline"),
        active_theme="carbon",
        on_pick=_record_pick,
        on_preview=_record_preview,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")
        assert previewed == ["amber"]
        assert picked == []  # preview did NOT call on_pick
        # The active-theme bookkeeping IS updated by preview so the
        # marker glyph in the modal follows the cursor.
        assert picker.active_theme == "amber"
    finally:
        picker.dispose()


def test_pick_command_still_calls_on_pick_after_preview() -> None:
    previewed: list[str] = []
    picked: list[str] = []

    def _record_pick(name: str) -> bool:
        picked.append(name)
        return True

    def _record_preview(name: str) -> bool:
        previewed.append(name)
        return True

    picker = ThemePickerVM(
        themes=("carbon", "amber", "voidline"),
        active_theme="carbon",
        on_pick=_record_pick,
        on_preview=_record_preview,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")
        picker.pick_theme_command.execute("amber")
        assert previewed == ["amber"]
        assert picked == ["amber"]
    finally:
        picker.dispose()


def test_on_preview_defaults_to_noop_when_omitted() -> None:
    """Backward-compat: existing callers that don't pass on_preview
    must still construct cleanly."""
    picker = ThemePickerVM(
        themes=("carbon", "amber"),
        active_theme="carbon",
        on_pick=lambda _n: True,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")  # must not raise
        assert picker.active_theme == "amber"
    finally:
        picker.dispose()


def test_preview_command_keeps_active_theme_when_callback_rejects() -> None:
    picker = ThemePickerVM(
        themes=("carbon", "broken"),
        active_theme="carbon",
        on_pick=lambda _name: True,
        on_preview=lambda _name: False,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("broken")
        assert picker.active_theme == "carbon"
        assert [option.name for option in picker.options if option.is_active] == ["carbon"]
    finally:
        picker.dispose()
