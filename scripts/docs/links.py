"""Link discovery and the 3x3 self-containment matrix.

Each surface must not link to the OTHER two surfaces (or to GitHub source
views of the repo). ``WIKI_URL`` contains ``REPO_URL`` as a prefix, so wiki
links are classified before repo links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from markdown import Markdown

REPO_URL = "https://github.com/thekaveh/aws-tui"
WIKI_URL = "https://github.com/thekaveh/aws-tui/wiki"
SITE_URL = "https://thekaveh.github.io/aws-tui/"

_RAW_URL_RE = re.compile(r"https?://[^\s<>\"']+")

_FORBIDDEN = {
    "site": {"repo", "wiki"},
    "wiki": {"repo", "site"},
    "repo": {"site", "wiki"},
}


@dataclass(frozen=True)
class Link:
    target: str


class _RenderedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[Link] = []
        self._code_depth = 0
        self._link_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self._code_depth == 0:
            target = attributes.get("href") if tag == "a" else attributes.get("src")
            if target is not None and tag in {"a", "img"}:
                self.links.append(Link(target))
        if tag in {"code", "pre"}:
            self._code_depth += 1
        if tag == "a":
            self._link_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"code", "pre"} and self._code_depth:
            self._code_depth -= 1
        if tag == "a" and self._link_depth:
            self._link_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._code_depth or self._link_depth:
            return
        self.links.extend(Link(match.group(0)) for match in _RAW_URL_RE.finditer(data))


def find_links(md: str) -> list[Link]:
    """Return rendered links, images, autolinks, and visible bare URLs.

    Rendering through Python-Markdown resolves reference-style links and
    distinguishes code from visible content. Parsing the resulting HTML also
    covers raw HTML anchors without relying on Markdown-shaped regular
    expressions.
    """
    rendered = Markdown(extensions=["fenced_code"]).convert(md)
    parser = _RenderedLinkParser()
    parser.feed(rendered)
    parser.close()
    return parser.links


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
