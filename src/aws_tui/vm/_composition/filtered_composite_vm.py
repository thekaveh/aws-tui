"""FilteredCompositeVM — aws-tui-side mini-primitive.

Composes a VMx :class:`CompositeVM` internally + adds:

- a mutable filter predicate
- a derived ``visible`` projection (subset of source children where
  the predicate returns True)
- a visible cursor (``current``) that snaps to the first visible
  entry when the predicate changes
- ``on_changed`` observable that fires when either the visible
  projection or the cursor moves

The source ``CompositeVM`` is held internally and NOT exposed in the
public surface (round-3 directive §9.bis.11). Consumers bind to the
``visible``, ``current``, ``set_predicate``, and ``on_changed``
surface.

Upstream candidate: VMx vNext could ship this natively (see
``docs/superpowers/specs/2026-06-28-vmx-upstream-vnext-asks.md``
Item 3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

import reactivex as rx
from reactivex.subject import Subject
from vmx import CompositeVM
from vmx.components.base import _ComponentVMBase

VM = TypeVar("VM", bound=_ComponentVMBase)


class FilteredCompositeVM(Generic[VM]):
    """Filter projection over a source ``CompositeVM[VM]``.

    Parameters
    ----------
    source:
        The source ``CompositeVM[VM]`` to project. NOT mutated by
        this class — it's strictly read-only over the source.
    predicate:
        Initial predicate ``(VM) -> bool``. Items where this returns
        ``True`` are visible. Default: ``lambda _: True`` (all
        visible).
    cursor_policy:
        Behaviour when the current cursor item is filtered out:

        - ``"snap_to_first"``: cursor snaps to the first visible
          item (or ``None`` if no visible items). DEFAULT — matches
          both PaneVM and CommandPaletteVM's today behaviour.
        - ``"clear"``: cursor goes to ``None`` when filtered out.

    Notes
    -----
    The projection is recomputed lazily on access. The visible cursor
    is recomputed eagerly when the predicate changes or the source
    mutates.
    """

    _VALID_POLICIES: frozenset[str] = frozenset({"snap_to_first", "clear"})

    def __init__(
        self,
        source: CompositeVM[VM],
        *,
        predicate: Callable[[VM], bool] | None = None,
        cursor_policy: str = "snap_to_first",
    ) -> None:
        if cursor_policy not in self._VALID_POLICIES:
            raise ValueError(
                f"cursor_policy must be one of {sorted(self._VALID_POLICIES)}, "
                f"got {cursor_policy!r}"
            )
        self._source: CompositeVM[VM] = source
        self._predicate: Callable[[VM], bool] = predicate if predicate is not None else _accept_all
        self._cursor_policy: str = cursor_policy
        self._current: VM | None = None
        self._on_changed: Subject[None] = Subject()
        self._disposed = False
        # Subscribe to the source's collection-changed Observable so we
        # can re-evaluate cursor placement when items appear / vanish.
        # The visible projection itself is computed lazily on access.
        self._subscription = source.on_collection_changed.subscribe(
            on_next=lambda _evt: self._on_source_changed()
        )

    # ── Public surface ──────────────────────────────────────────────────────

    @property
    def visible(self) -> tuple[VM, ...]:
        """Items in the source that satisfy the current predicate, in
        source order."""
        return tuple(item for item in self._source if self._predicate(item))

    @property
    def visible_count(self) -> int:
        return sum(1 for item in self._source if self._predicate(item))

    @property
    def current(self) -> VM | None:
        """The cursor, always either ``None`` or a member of
        :attr:`visible`."""
        return self._current

    @property
    def predicate(self) -> Callable[[VM], bool]:
        return self._predicate

    @property
    def cursor_policy(self) -> str:
        return self._cursor_policy

    @property
    def on_changed(self) -> rx.Observable[None]:
        """Fires when the visible projection OR the cursor changes.

        The payload is always ``None``; subscribers re-read
        :attr:`visible` / :attr:`current`. Hot observable —
        subscribers receive only events that arrive after they
        subscribe.
        """
        return self._on_changed

    # ── Mutation ────────────────────────────────────────────────────────────

    def set_predicate(self, predicate: Callable[[VM], bool]) -> None:
        """Replace the filter predicate. Re-evaluates the cursor per
        the configured ``cursor_policy``.

        **Identity-equality gotcha:** the check below is
        ``is`` (object identity), NOT value equality. Passing the
        SAME predicate object (e.g. a bound method whose state
        you mutated externally) is treated as a no-op even if its
        captured state changed. Consumers that drive a stateful
        predicate (PaneVM threads ``filter_text`` through one)
        should wrap the call in a fresh closure each invocation:

        .. code-block:: python

           # Closure captures the LIVE filter_text at call time.
           def _live(item: VM) -> bool:
               return matches(item, self.filter_text)
           filtered.set_predicate(_live)
        """
        if predicate is self._predicate:
            return
        self._predicate = predicate
        self._reconcile_cursor()
        self._on_changed.on_next(None)

    def set_current(self, item: VM | None) -> None:
        """Set the cursor explicitly.

        Raises ``ValueError`` when ``item`` is non-None and either not
        in the source or not currently visible.
        """
        if item is None:
            if self._current is None:
                return
            self._current = None
            self._on_changed.on_next(None)
            return
        if item not in self._source:
            raise ValueError(
                f"Cannot set current to {item!r}: not a member of the source composite."
            )
        if not self._predicate(item):
            raise ValueError(
                f"Cannot set current to {item!r}: not visible under the current predicate."
            )
        if self._current is item:
            return
        self._current = item
        self._on_changed.on_next(None)

    def move_to_next_visible(self) -> None:
        """Advance the cursor to the next visible item, wrapping at
        the end. No-op when there are no visible items."""
        visible = self.visible
        if not visible:
            return
        if self._current is None:
            self._current = visible[0]
            self._on_changed.on_next(None)
            return
        try:
            idx = visible.index(self._current)
        except ValueError:
            # Cursor item is no longer visible (defensive — shouldn't
            # happen because _reconcile_cursor runs on every mutation).
            self._current = visible[0]
            self._on_changed.on_next(None)
            return
        next_idx = (idx + 1) % len(visible)
        if visible[next_idx] is self._current:
            return  # Single-item visible list — wrap is identity.
        self._current = visible[next_idx]
        self._on_changed.on_next(None)

    def move_to_previous_visible(self) -> None:
        """Retreat the cursor to the previous visible item, wrapping
        at the start. No-op when there are no visible items."""
        visible = self.visible
        if not visible:
            return
        if self._current is None:
            self._current = visible[-1]
            self._on_changed.on_next(None)
            return
        try:
            idx = visible.index(self._current)
        except ValueError:
            self._current = visible[-1]
            self._on_changed.on_next(None)
            return
        prev_idx = (idx - 1) % len(visible)
        if visible[prev_idx] is self._current:
            return
        self._current = visible[prev_idx]
        self._on_changed.on_next(None)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._subscription.dispose()
        self._on_changed.on_completed()
        self._on_changed.dispose()

    # ── Internal ────────────────────────────────────────────────────────────

    def _on_source_changed(self) -> None:
        if self._disposed:
            return
        self._reconcile_cursor()
        self._on_changed.on_next(None)

    def _reconcile_cursor(self) -> None:
        """Ensure the cursor remains valid given the current predicate
        and source. Promotes to first-visible / clears per the
        configured ``cursor_policy``."""
        # Cursor is still valid: in source AND visible.
        if (
            self._current is not None
            and self._current in self._source
            and self._predicate(self._current)
        ):
            return
        # Cursor is stale — apply policy.
        if self._cursor_policy == "snap_to_first":
            for item in self._source:
                if self._predicate(item):
                    self._current = item
                    return
            self._current = None
        else:  # "clear"
            self._current = None


def _accept_all(_item: object) -> bool:
    return True


__all__ = ["FilteredCompositeVM"]
