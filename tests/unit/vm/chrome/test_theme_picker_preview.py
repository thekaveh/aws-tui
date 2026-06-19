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
    picker = ThemePickerVM(
        themes=("carbon", "amber", "voidline"),
        active_theme="carbon",
        on_pick=picked.append,
        on_preview=previewed.append,
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
    picker = ThemePickerVM(
        themes=("carbon", "amber", "voidline"),
        active_theme="carbon",
        on_pick=picked.append,
        on_preview=previewed.append,
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
        on_pick=lambda _n: None,
        hub=_hub(),
        dispatcher=NULL_DISPATCHER,
    )
    picker.construct()
    try:
        picker.preview_command.execute("amber")  # must not raise
        assert picker.active_theme == "amber"
    finally:
        picker.dispose()
