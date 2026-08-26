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
_CODE_FENCE_RE = re.compile(r"^\s*(`{3,})([^`]*)$")
_INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")

_FORBIDDEN = {
    "site": {"repo", "wiki"},
    "wiki": {"repo", "site"},
    "repo": {"site", "wiki"},
}


@dataclass(frozen=True)
class Link:
    target: str


def find_links(md: str) -> list[Link]:
    rendered_lines: list[str] = []
    fence_length: int | None = None
    for line in md.splitlines():
        fence = _CODE_FENCE_RE.match(line)
        if fence is not None:
            ticks = len(fence.group(1))
            if fence_length is None:
                fence_length = ticks
            elif ticks >= fence_length and not fence.group(2).strip():
                fence_length = None
            continue
        if fence_length is None:
            rendered_lines.append(_INLINE_CODE_RE.sub("", line))
    rendered = "\n".join(rendered_lines)
    return [Link(match.group(1)) for match in _LINK_RE.finditer(rendered)]


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
