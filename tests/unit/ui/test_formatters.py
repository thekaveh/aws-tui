"""Byte labels roll over to the next unit when the displayed value reaches 1024."""

from __future__ import annotations

import pytest

from aws_tui.ui.formatters import humanize_bytes


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 kB"),
        (1048525, "1.0 MB"),
        (1048575, "1.0 MB"),
        (1048576, "1.0 MB"),
        (None, "?"),
    ],
)
def test_humanize_bytes_rolls_over_at_the_displayed_boundary(value: int | None, label: str) -> None:
    assert humanize_bytes(value) == label
