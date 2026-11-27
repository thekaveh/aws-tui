"""Option ids that no AWS resource can carry."""

from __future__ import annotations

# AWS names never contain control characters, so a NUL-bearing id cannot match a Glue job,
# database, table, crawler or saved query that happens to carry a placeholder-like name.
PLACEHOLDER_OPTION_ID = "\x00placeholder"
