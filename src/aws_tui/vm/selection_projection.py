"""Publication provenance for programmatic service-menu projections.

The hub may queue a publication and deliver it in a different asyncio task.
Capture the projection at emission rather than inferring it during delivery.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from vmx import PropertyChangedMessage


@dataclass(frozen=True)
class SelectionProjection:
    owner: object = field(repr=False)
    selected: str | None = field(repr=False)


_projection: ContextVar[SelectionProjection | None] = ContextVar(
    "selection_projection", default=None
)


@dataclass(frozen=True, slots=True)
class SelectionChangedMessage(PropertyChangedMessage[object]):
    projection: SelectionProjection | None = field(default=None, repr=False, compare=False)


@contextmanager
def project_selection(owner: object, selected: str | None) -> Iterator[None]:
    token = _projection.set(SelectionProjection(owner, selected))
    try:
        yield
    finally:
        _projection.reset(token)


def selection_changed(sender: object, name: str, selected: str | None) -> SelectionChangedMessage:
    projection = _projection.get()
    if projection is not None and projection.selected != selected:
        projection = None
    return SelectionChangedMessage(sender, name, "selected_id", projection)
