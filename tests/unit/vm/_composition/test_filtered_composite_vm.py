"""Tests for the FilteredCompositeVM mini-primitive."""

from __future__ import annotations

import pytest
from vmx import NULL_DISPATCHER, ComponentVMOf, CompositeVM, MessageHub
from vmx.messages.protocols import Message

from aws_tui.vm._composition import FilteredCompositeVM


def _hub() -> MessageHub[Message]:
    return MessageHub()


def _make_source(values: list[str]) -> CompositeVM[ComponentVMOf[str]]:
    hub = _hub()
    composite: CompositeVM[ComponentVMOf[str]] = (
        CompositeVM[ComponentVMOf[str]]
        .builder()
        .name("source")
        .services(hub, NULL_DISPATCHER)
        .children(
            lambda: tuple(
                ComponentVMOf[str]
                .builder()
                .name(f"c.{v}")
                .model(v)
                .services(hub, NULL_DISPATCHER)
                .build()
                for v in values
            )
        )
        .auto_construct_on_add(True)
        .build()
    )
    composite.construct()
    return composite


# -------------------- visible / predicate --------------------


def test_visible_is_all_when_no_predicate() -> None:
    source = _make_source(["a", "b", "c"])
    f: FilteredCompositeVM[ComponentVMOf[str]] = FilteredCompositeVM(source)
    assert tuple(item.model for item in f.visible) == ("a", "b", "c")
    f.dispose()
    source.dispose()


def test_predicate_filters_source() -> None:
    source = _make_source(["apple", "banana", "apricot"])
    f = FilteredCompositeVM(source, predicate=lambda v: v.model.startswith("a"))
    assert tuple(item.model for item in f.visible) == ("apple", "apricot")
    assert f.visible_count == 2
    f.dispose()
    source.dispose()


def test_set_predicate_re_evaluates() -> None:
    source = _make_source(["x", "y", "z"])
    f = FilteredCompositeVM(source)
    assert f.visible_count == 3
    f.set_predicate(lambda v: v.model == "y")
    assert tuple(item.model for item in f.visible) == ("y",)
    f.dispose()
    source.dispose()


# -------------------- cursor policy --------------------


def test_default_cursor_is_none() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source)
    assert f.current is None
    f.dispose()
    source.dispose()


def test_snap_to_first_cursor_policy_promotes_on_predicate_change() -> None:
    source = _make_source(["a", "b", "c"])
    f = FilteredCompositeVM(source)
    # Initially no cursor. Setting one explicitly, then narrowing
    # filter to exclude it, should snap to the first surviving item.
    target = source[0]  # "a"
    f.set_current(target)
    assert f.current is target
    # Narrow to "c" only — cursor should snap to "c" (first visible).
    f.set_predicate(lambda v: v.model == "c")
    assert f.current is source[2]
    f.dispose()
    source.dispose()


def test_clear_cursor_policy_clears_on_predicate_change() -> None:
    source = _make_source(["a", "b"])
    f = FilteredCompositeVM(source, cursor_policy="clear")
    f.set_current(source[0])
    f.set_predicate(lambda v: v.model == "b")
    assert f.current is None
    f.dispose()
    source.dispose()


def test_invalid_cursor_policy_raises() -> None:
    source = _make_source(["a"])
    with pytest.raises(ValueError, match="cursor_policy"):
        FilteredCompositeVM(source, cursor_policy="bogus")
    source.dispose()


# -------------------- set_current validation --------------------


def test_set_current_to_non_source_member_raises() -> None:
    source = _make_source(["a"])
    other_source = _make_source(["q"])
    f = FilteredCompositeVM(source)
    with pytest.raises(ValueError, match="not a member"):
        f.set_current(other_source[0])
    f.dispose()
    source.dispose()
    other_source.dispose()


def test_set_current_to_invisible_item_raises() -> None:
    source = _make_source(["a", "b"])
    f = FilteredCompositeVM(source, predicate=lambda v: v.model == "a")
    with pytest.raises(ValueError, match="not visible"):
        f.set_current(source[1])  # "b" is filtered out
    f.dispose()
    source.dispose()


def test_set_current_none_clears_cursor() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source)
    f.set_current(source[0])
    assert f.current is source[0]
    f.set_current(None)
    assert f.current is None
    f.dispose()
    source.dispose()


# -------------------- navigation --------------------


def test_move_to_next_visible_wraps() -> None:
    source = _make_source(["a", "b", "c"])
    f = FilteredCompositeVM(source)
    f.set_current(source[0])
    f.move_to_next_visible()
    assert f.current is source[1]
    f.move_to_next_visible()
    assert f.current is source[2]
    f.move_to_next_visible()
    assert f.current is source[0]  # wrap
    f.dispose()
    source.dispose()


def test_move_to_next_visible_with_no_cursor_lands_on_first() -> None:
    source = _make_source(["a", "b"])
    f = FilteredCompositeVM(source)
    assert f.current is None
    f.move_to_next_visible()
    assert f.current is source[0]
    f.dispose()
    source.dispose()


def test_move_to_previous_visible_wraps() -> None:
    source = _make_source(["a", "b", "c"])
    f = FilteredCompositeVM(source)
    f.set_current(source[0])
    f.move_to_previous_visible()
    assert f.current is source[2]  # wrap
    f.move_to_previous_visible()
    assert f.current is source[1]
    f.dispose()
    source.dispose()


def test_move_to_previous_visible_with_no_cursor_lands_on_last() -> None:
    source = _make_source(["a", "b"])
    f = FilteredCompositeVM(source)
    f.move_to_previous_visible()
    assert f.current is source[1]
    f.dispose()
    source.dispose()


def test_move_navigation_skips_filtered_items() -> None:
    source = _make_source(["keep1", "skip", "keep2"])
    f = FilteredCompositeVM(source, predicate=lambda v: "keep" in v.model)
    f.set_current(source[0])
    f.move_to_next_visible()
    # "skip" is not visible — must land on "keep2".
    assert f.current is source[2]
    f.dispose()
    source.dispose()


def test_navigation_no_op_when_no_visible_items() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source, predicate=lambda _: False)
    assert f.visible_count == 0
    f.move_to_next_visible()
    assert f.current is None
    f.move_to_previous_visible()
    assert f.current is None
    f.dispose()
    source.dispose()


# -------------------- on_changed events --------------------


def test_on_changed_fires_on_predicate_change() -> None:
    source = _make_source(["a", "b"])
    f = FilteredCompositeVM(source)
    events: list[None] = []
    sub = f.on_changed.subscribe(on_next=events.append)
    try:
        f.set_predicate(lambda v: v.model == "a")
        assert len(events) == 1
    finally:
        sub.dispose()
        f.dispose()
        source.dispose()


def test_on_changed_fires_on_source_mutation() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source)
    events: list[None] = []
    sub = f.on_changed.subscribe(on_next=events.append)
    try:
        hub = _hub()
        new_child = (
            ComponentVMOf[str]
            .builder()
            .name("c.b")
            .model("b")
            .services(hub, NULL_DISPATCHER)
            .build()
        )
        source.append(new_child)
        assert len(events) == 1
    finally:
        sub.dispose()
        f.dispose()
        source.dispose()


def test_on_changed_does_not_fire_on_set_predicate_to_same() -> None:
    source = _make_source(["a"])
    p = lambda v: True  # noqa: E731 — terse predicate for test
    f = FilteredCompositeVM(source, predicate=p)
    events: list[None] = []
    sub = f.on_changed.subscribe(on_next=events.append)
    try:
        f.set_predicate(p)  # same identity
        assert events == []
    finally:
        sub.dispose()
        f.dispose()
        source.dispose()


# -------------------- cursor reconciliation on source mutation --------------------


def test_cursor_clears_when_current_removed_from_source() -> None:
    source = _make_source(["a", "b"])
    f = FilteredCompositeVM(source)
    target = source[0]
    f.set_current(target)
    source.remove(target)
    # Per snap_to_first policy: cursor should advance to first
    # remaining visible item.
    assert f.current is source[0]  # the surviving "b"
    f.dispose()
    source.dispose()


def test_cursor_survives_source_append_when_still_valid() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source)
    target = source[0]
    f.set_current(target)
    hub = _hub()
    new_child = (
        ComponentVMOf[str].builder().name("c.b").model("b").services(hub, NULL_DISPATCHER).build()
    )
    source.append(new_child)
    assert f.current is target  # unchanged
    f.dispose()
    source.dispose()


# -------------------- dispose --------------------


def test_dispose_is_idempotent() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source)
    f.dispose()
    f.dispose()  # second dispose is silent
    source.dispose()


def test_dispose_unsubscribes_from_source() -> None:
    source = _make_source(["a"])
    f = FilteredCompositeVM(source)
    events: list[None] = []
    f.on_changed.subscribe(on_next=events.append)
    f.dispose()
    # After dispose, mutating the source must NOT re-enter
    # FilteredCompositeVM and must NOT fire ``on_changed`` again.
    # Without this assertion the regression "we forgot to dispose
    # the source subscription" passes silently — the
    # ``if self._disposed: return`` shortcut would still swallow
    # the event without anyone noticing the leaked subscription.
    hub = _hub()
    new_child = (
        ComponentVMOf[str].builder().name("c.b").model("b").services(hub, NULL_DISPATCHER).build()
    )
    source.append(new_child)
    assert events == [], "FilteredCompositeVM kept its source subscription alive after dispose"
    source.dispose()
