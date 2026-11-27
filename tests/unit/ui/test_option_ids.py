from __future__ import annotations

from aws_tui.ui.widgets.option_ids import PLACEHOLDER_OPTION_ID


def test_placeholder_id_carries_a_control_character_no_aws_name_can_hold() -> None:
    """A Glue job or database named like the old literal selected as a placeholder row."""
    assert "\x00" in PLACEHOLDER_OPTION_ID
    assert PLACEHOLDER_OPTION_ID != "__placeholder__"
