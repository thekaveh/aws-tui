"""Release detached query results retained by pinned Textual 8.2.8 ancestors.

DOMNode.query_one keys its LRU by mount generation. Old keys cannot hit after
replacement but retain removed widgets and their VM trees until eviction. Keep
this private compatibility point isolated; do not change cache capacities or
clear entries that still refer to attached nodes.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual.dom import DOMNode


def release_detached_query_results(owners: Iterable[DOMNode]) -> None:
    for owner in owners:
        cache = owner._query_one_cache
        for key in tuple(cache.keys()):
            node = cache.get(key)
            if node is not None and not node.is_attached:
                cache.discard(key)
