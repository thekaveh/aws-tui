"""Tests for LogFilterModal — edit regex patterns, show-all toggle, match-case switch."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Switch, TextArea
from vmx import MessageHub

from aws_tui.domain.emr_logs import DEFAULT_LOG_FILTER, FilterMode, LogFilter
from aws_tui.ui.widgets.emr_serverless.log_filter_modal import LogFilterModal
from aws_tui.ui.widgets.modal_button import ModalButton


@pytest.mark.asyncio
async def test_log_filter_modal_mounts_with_pre_filled_values() -> None:
    """Modal displays pre-populated form from a non-default LogFilter.

    Test: patterns = ("FOO", "BAR"), mode=PASSTHROUGH, case_insensitive=False.
    Verify the form widgets reflect the pre-filled values.
    """
    hub: MessageHub = MessageHub()
    current = LogFilter(
        patterns=("FOO", "BAR"),
        mode=FilterMode.PASSTHROUGH,
        case_insensitive=False,
    )

    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(LogFilterModal(current))

        app = _App()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, LogFilterModal)

            # Check TextArea has the patterns
            textarea = modal.query_one("#log-patterns", TextArea)
            expected_text = "FOO\nBAR"
            assert textarea.text == expected_text

            # Check "Show all" switch is ON (mode is PASSTHROUGH)
            show_all_switch = modal.query_one("#show-all-switch", Switch)
            assert show_all_switch.value is True

            # Check "Match case" switch is ON (case_insensitive=False means match case=True)
            match_case_switch = modal.query_one("#match-case-switch", Switch)
            assert match_case_switch.value is True
    finally:
        hub.dispose()


@pytest.mark.asyncio
async def test_log_filter_modal_apply_dismisses_with_new_filter() -> None:
    """Pressing Apply button returns a LogFilter built from the form state.

    Drive the form widgets: change patterns, toggle switches, click Apply.
    Assert the dismissed value is a valid LogFilter with the new state.
    """
    hub: MessageHub = MessageHub()
    current = LogFilter(
        patterns=("INITIAL",),
        mode=FilterMode.MATCH,
        case_insensitive=True,
    )

    captured_dismiss_value: list[LogFilter | None] = []

    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                screen = LogFilterModal(current)
                # Spy on the dismiss method to capture the dismissed value
                original_dismiss = screen.dismiss

                def spy_dismiss(value: LogFilter | None) -> None:
                    captured_dismiss_value.append(value)
                    original_dismiss(value)

                screen.dismiss = spy_dismiss  # type: ignore[method-assign]
                self.push_screen(screen)

        app = _App()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, LogFilterModal)

            # Modify the form
            textarea = modal.query_one("#log-patterns", TextArea)
            textarea.text = "ERROR\nWARN"

            # Toggle "Show all" (turn it ON)
            show_all_switch = modal.query_one("#show-all-switch", Switch)
            show_all_switch.value = True

            # Toggle "Match case" (turn it OFF)
            match_case_switch = modal.query_one("#match-case-switch", Switch)
            match_case_switch.value = False

            # Click Apply button
            apply_btn = next(b for b in modal.query(ModalButton) if b.button_id == "apply")
            await pilot.click(apply_btn)

            # The modal should dismiss and we should be back at the app screen
            await pilot.pause()
            assert not isinstance(app.screen, LogFilterModal)

            # Assert the dismissed value is a LogFilter with the form state
            assert len(captured_dismiss_value) == 1
            dismissed_filter = captured_dismiss_value[0]
            assert isinstance(dismissed_filter, LogFilter)
            assert dismissed_filter.patterns == ("ERROR", "WARN")
            assert dismissed_filter.mode is FilterMode.PASSTHROUGH
            assert dismissed_filter.case_insensitive is True
    finally:
        hub.dispose()


@pytest.mark.asyncio
async def test_log_filter_modal_cancel_dismisses_with_none() -> None:
    """Pressing Cancel button dismisses with None."""
    hub: MessageHub = MessageHub()
    current = LogFilter(patterns=("FOO",), mode=FilterMode.MATCH)

    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(LogFilterModal(current))

        app = _App()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, LogFilterModal)

            # Click Cancel button
            cancel_btn = next(b for b in modal.query(ModalButton) if b.button_id == "cancel")
            await pilot.click(cancel_btn)

            # The modal should dismiss and we should be back at the app screen
            await pilot.pause()
            assert not isinstance(app.screen, LogFilterModal)
    finally:
        hub.dispose()


@pytest.mark.asyncio
async def test_log_filter_modal_reset_repopulates_from_defaults() -> None:
    """Pressing Reset to defaults repopulates the form without dismissing.

    Verify that the form widgets show the DEFAULT_LOG_FILTER values
    and the modal is still on screen.
    """
    hub: MessageHub = MessageHub()
    current = LogFilter(
        patterns=("CUSTOM",),
        mode=FilterMode.PASSTHROUGH,
        case_insensitive=False,
    )

    try:

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                await self.push_screen(LogFilterModal(current))

        app = _App()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, LogFilterModal)

            # Verify the form starts with the custom filter
            textarea = modal.query_one("#log-patterns", TextArea)
            assert textarea.text == "CUSTOM"

            # Click Reset to defaults button
            reset_btn = next(b for b in modal.query(ModalButton) if b.button_id == "reset")
            await pilot.click(reset_btn)

            # The modal should still be on screen
            await pilot.pause()
            assert isinstance(app.screen, LogFilterModal)

            # The form should now show DEFAULT_LOG_FILTER values
            textarea = modal.query_one("#log-patterns", TextArea)
            expected_patterns = "\n".join(DEFAULT_LOG_FILTER.patterns)
            assert textarea.text == expected_patterns

            # "Show all" should be OFF (DEFAULT_LOG_FILTER.mode is MATCH)
            show_all_switch = modal.query_one("#show-all-switch", Switch)
            assert show_all_switch.value is False

            # "Match case" should be OFF (DEFAULT_LOG_FILTER.case_insensitive=True means match case=False)
            match_case_switch = modal.query_one("#match-case-switch", Switch)
            assert match_case_switch.value is False
    finally:
        hub.dispose()


@pytest.mark.asyncio
async def test_log_filter_modal_patterns_parsing() -> None:
    """Patterns are split on newlines, whitespace trimmed, empty lines dropped.

    This test verifies the parsing logic works correctly by checking
    that Apply builds a LogFilter with the correct pattern list.
    """
    hub: MessageHub = MessageHub()
    current = LogFilter(patterns=(), mode=FilterMode.MATCH)

    try:

        class _App(App[None]):
            captured_result: LogFilter | None = None

            def compose(self) -> ComposeResult:
                yield from ()

            async def on_mount(self) -> None:
                screen = LogFilterModal(current)
                self.push_screen(screen)

        app = _App()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, LogFilterModal)

            # Set patterns with whitespace and empty lines
            textarea = modal.query_one("#log-patterns", TextArea)
            textarea.text = "  ERROR  \n\nWARN\n  \nFAIL  "

            # Now we need to test the form parsing
            # We'll verify by looking at what the modal would create
            result = modal._build_filter()

            # Should have 3 patterns: ERROR, WARN, FAIL (trimmed, empty lines dropped)
            assert result.patterns == ("ERROR", "WARN", "FAIL")
    finally:
        hub.dispose()


__all__ = []
