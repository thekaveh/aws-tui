"""Per-surface link rewriting.

``rewrite_for_surface`` walks every ``[text](target)`` link and:
  * strips forbidden (cross-surface / GitHub-source) links to bare text,
  * strips ``.ipynb`` links to bare text,
  * rewrites known published ``.md`` links to the surface's output filename,
  * strips other relative ``.md`` links (non-manifest docs) to bare text,
  * leaves everything else (external URLs, ``#anchors``, images, ``../``) as-is.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.docs.links import is_forbidden
from scripts.docs.manifest import Manifest, Section

# [text](target) but NOT ![alt](target) images: negative lookbehind on '!'.
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*([^)\s]+)\s*\)")


def wiki_slug(title: str) -> str:
    return title.replace(" ", "-")


def output_name(section: Section, surface: str) -> str:
    if section.id == "overview":
        return "index.md" if surface == "site" else "Home.md"
    if surface == "site":
        return f"{section.id}.md"
    return f"{wiki_slug(section.title)}.md"


def build_source_map(manifest: Manifest, surface: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for leaf in manifest.leaves():
        assert leaf.source is not None
        out[leaf.source] = output_name(leaf, surface)
    return out


def _split_anchor(target: str) -> tuple[str, str]:
    if "#" in target:
        path, _, anchor = target.partition("#")
        return path, f"#{anchor}"
    return target, ""


def rewrite_for_surface(md: str, surface: str, source_map: dict[str, str]) -> str:
    by_basename = {Path(canon).name: out for canon, out in source_map.items()}

    def repl(m: re.Match[str]) -> str:
        text, target = m.group(1), m.group(2)
        if is_forbidden(target, surface):
            return text
        path, anchor = _split_anchor(target)
        if path.endswith(".ipynb"):
            return text
        if path.endswith(".md") and not path.startswith(("/", "http")):
            mapped = by_basename.get(Path(path).name)
            if mapped is not None:
                return f"[{text}]({mapped}{anchor})"
            return text  # relative .md to a non-published/internal doc
        return m.group(0)  # external, anchor-only, or otherwise untouched

    return _LINK_RE.sub(repl, md)
