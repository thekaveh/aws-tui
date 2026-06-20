"""Smoke test for ServicesMenuFooter."""

from __future__ import annotations

from aws_tui.ui.widgets.services_menu_footer import ServicesMenuFooter


def test_services_menu_footer_construction() -> None:
    footer = ServicesMenuFooter()
    assert footer is not None
