"""Reconcile composite rows by model key while preserving surviving children."""

from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

from vmx import ComponentVMOf, CompositeVM

M = TypeVar("M")


def reconcile_rows(
    rows: CompositeVM[ComponentVMOf[M]],
    models: Sequence[M],
    *,
    key: Callable[[M], Hashable],
    create: Callable[[M], ComponentVMOf[M]],
) -> None:
    """Update rows in model order, keeping the first occurrence of each key."""
    wanted: dict[Hashable, M] = {}
    for model in models:
        wanted.setdefault(key(model), model)

    with rows.batch_update():
        removed = [row for row in rows if key(row.model) not in wanted]
        for row in removed:
            rows.remove(row)
            row.dispose()

        by_key = {key(row.model): row for row in rows}
        for index, (row_key, model) in enumerate(wanted.items()):
            existing = by_key.get(row_key)
            if existing is None:
                rows.insert(index, create(model))
                continue
            existing.model = model
            if rows[index] is not existing:
                rows.move(rows.index(existing, index), index)
