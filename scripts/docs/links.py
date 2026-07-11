"""Link discovery and the 3x3 self-containment matrix.

Each surface must not link to the OTHER two surfaces (or to GitHub source
views of the repo). ``WIKI_URL`` contains ``REPO_URL`` as a prefix, so wiki
links are classified before repo links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REPO_URL = "https://github.com/thekaveh/aws-tui"
WIKI_URL = "https://github.com/thekaveh/aws-tui/wiki"
SITE_URL = "https://thekaveh.github.io/aws-tui/"

# Matches both [text](target) links and ![alt](target) images.
_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)")

_FORBIDDEN = {
    "site": {"repo", "wiki"},
    "wiki": {"repo", "site"},
    "repo": {"site", "wiki"},
}


@dataclass(frozen=True)
class Link:
    target: str


def find_links(md: str) -> list[Link]:
    return [Link(m.group(1)) for m in _LINK_RE.finditer(md)]


def _classify(target: str) -> str | None:
    t = target.strip()
    if t.startswith(SITE_URL):
        return "site"
    if t.startswith(WIKI_URL):  # MUST precede REPO_URL (prefix overlap)
        return "wiki"
    if t.startswith(REPO_URL):
        return "repo"
    return None


def is_forbidden(target: str, surface: str) -> bool:
    kind = _classify(target)
    return kind is not None and kind in _FORBIDDEN[surface]
