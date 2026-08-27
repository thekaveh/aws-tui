"""The docs CI gate: self-containment, local anchors, completeness, placeholders,
numbering, and regeneration determinism (via ``build --check``)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown import Markdown
from mkdocs.config import load_config
from scripts.docs.build_docs import build
from scripts.docs.links import find_links, is_forbidden
from scripts.docs.manifest import Manifest, load_manifest

# Docs deliberately kept in-repo only (never published/flagged).
INTERNAL_DOCS: frozenset[str] = frozenset({"docs/recording-todo.md"})
INTERNAL_DOC_PREFIXES: tuple[str, ...] = ("docs/superpowers/",)

_PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(\d+(?:\.\d+)*)\.\s+\S")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str


def _surface_of(md_path: Path, generated_root: Path) -> str:
    rel = md_path.relative_to(generated_root)
    return rel.parts[0]  # "site" or "wiki"


def check_self_containment(generated_root: str | Path, repo_root: str | Path) -> list[Finding]:
    generated_root = Path(generated_root)
    repo_root = Path(repo_root)
    findings: list[Finding] = []
    for md_path in sorted(generated_root.rglob("*.md")):
        surface = _surface_of(md_path, generated_root)
        if surface not in ("site", "wiki"):
            continue
        for link in find_links(md_path.read_text(encoding="utf-8")):
            if is_forbidden(link.target, surface):
                rel = md_path.relative_to(generated_root)
                findings.append(Finding("error", f"{rel}: forbidden link {link.target}"))
    repository_docs = [repo_root / "README.md"]
    manifest_path = repo_root / "docs" / "manifest.yaml"
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path, repo_root)
        repository_docs.extend(
            repo_root / leaf.source for leaf in manifest.leaves() if leaf.source is not None
        )
    for repository_doc in repository_docs:
        if not repository_doc.is_file():
            continue
        for link in find_links(repository_doc.read_text(encoding="utf-8")):
            if is_forbidden(link.target, "repo"):
                rel = repository_doc.relative_to(repo_root)
                findings.append(Finding("error", f"{rel}: forbidden link {link.target}"))
    return findings


def check_completeness(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    repo_root = Path(repo_root)
    referenced = {leaf.source for leaf in manifest.leaves()}
    findings: list[Finding] = []
    for md in sorted((repo_root / "docs").rglob("*.md")):
        rel = md.relative_to(repo_root).as_posix()
        if rel in INTERNAL_DOCS or rel.startswith(INTERNAL_DOC_PREFIXES) or rel in referenced:
            continue
        findings.append(Finding("error", f"{rel}: published doc not referenced by manifest"))
    return findings


def check_placeholders(
    generated_root: str | Path, repo_root: str | Path | None = None
) -> list[Finding]:
    generated_root = Path(generated_root)
    findings: list[Finding] = []
    for md_path in sorted(generated_root.rglob("*.md")):
        if md_path.relative_to(generated_root).parts[0] not in ("site", "wiki"):
            continue
        for m in _PLACEHOLDER_RE.finditer(md_path.read_text(encoding="utf-8")):
            rel = md_path.relative_to(generated_root)
            findings.append(Finding("error", f"{rel}: placeholder {m.group(1)}"))
    if repo_root is not None:
        readme = Path(repo_root) / "README.md"
        if readme.is_file():
            for match in _PLACEHOLDER_RE.finditer(readme.read_text(encoding="utf-8")):
                findings.append(Finding("error", f"README.md: placeholder {match.group(1)}"))
    return findings


def check_numbering(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    del manifest
    repo_root = Path(repo_root)
    findings: list[Finding] = []
    markdown = sorted(repo_root.glob("*.md")) + sorted((repo_root / "docs").rglob("*.md"))
    for path in markdown:
        rel = path.relative_to(repo_root)
        seen: set[tuple[int, ...]] = set()
        fence_length: int | None = None
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.lstrip()
            fence = re.match(r"^(`{3,})([^`]*)$", stripped)
            if fence is not None:
                ticks = len(fence.group(1))
                if fence_length is None:
                    fence_length = ticks
                elif ticks >= fence_length and not fence.group(2).strip():
                    fence_length = None
                continue
            if fence_length is not None or not re.match(r"^#{1,6}\s+", line):
                continue
            match = _HEADING_RE.match(line)
            if match is None:
                findings.append(
                    Finding(
                        "error", f"{rel}:{line_number}: heading must be hierarchically numbered"
                    )
                )
                continue
            level = len(match.group(1))
            number = tuple(int(part) for part in match.group(2).split("."))
            if len(number) != level or number[0] != 1:
                findings.append(
                    Finding(
                        "error",
                        f"{rel}:{line_number}: heading number {match.group(2)} does not match H{level}",
                    )
                )
                continue
            if level > 1 and number[:-1] not in seen:
                findings.append(
                    Finding(
                        "error",
                        f"{rel}:{line_number}: missing parent heading for {match.group(2)}",
                    )
                )
            if number in seen:
                findings.append(
                    Finding(
                        "error", f"{rel}:{line_number}: duplicate heading number {match.group(2)}"
                    )
                )
            seen.add(number)
    return findings


def _unfenced_lines(markdown: str) -> list[tuple[int, str]]:
    """Return Markdown lines outside fenced code blocks."""
    lines: list[tuple[int, str]] = []
    fence_length: int | None = None
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        fence = re.match(r"^\s*(`{3,})([^`]*)$", line)
        if fence is not None:
            ticks = len(fence.group(1))
            if fence_length is None:
                fence_length = ticks
            elif ticks >= fence_length and not fence.group(2).strip():
                fence_length = None
            continue
        if fence_length is None:
            lines.append((line_number, line))
    return lines


def _github_anchor(heading: str) -> str:
    """Match GitHub's simple heading fragment normalization for local docs."""
    without_links = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", heading)
    without_markup = re.sub(r"<[^>]+>|[`*_~]", "", without_links)
    without_punctuation = re.sub(r"[^\w\s-]", "", without_markup.casefold())
    return re.sub(r"\s", "-", without_punctuation)


def _github_anchors(markdown: str) -> set[str]:
    anchors: set[str] = set()
    duplicate_counts: dict[str, int] = {}
    for _, line in _unfenced_lines(markdown):
        match = _MARKDOWN_HEADING_RE.match(line)
        if match is None:
            continue
        base = _github_anchor(match.group(1))
        duplicate = duplicate_counts.get(base, 0)
        anchor = base if duplicate == 0 else f"{base}-{duplicate}"
        duplicate_counts[base] = duplicate + 1
        anchors.add(anchor)
    return anchors


class _HeadingIdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not re.fullmatch(r"h[1-6]", tag):
            return
        anchor = dict(attrs).get("id")
        if anchor is not None:
            self.anchors.add(anchor)


def _mkdocs_anchors(
    markdown: str,
    extensions: list[str],
    extension_configs: dict[str, dict[str, object]],
) -> set[str]:
    rendered = Markdown(
        extensions=extensions,
        extension_configs=extension_configs,
    ).convert(markdown)
    parser = _HeadingIdParser()
    parser.feed(rendered)
    return parser.anchors


def _local_markdown_paths(repo_root: Path) -> list[Path]:
    return sorted(repo_root.glob("*.md")) + sorted((repo_root / "docs").rglob("*.md"))


def check_local_anchors(repo_root: str | Path) -> list[Finding]:
    """Reject local Markdown fragments that GitHub or configured MkDocs cannot resolve."""
    repo_root = Path(repo_root).resolve()
    mkdocs_config = load_config(config_file=str(repo_root / "mkdocs.yml"))
    extensions = list(mkdocs_config["markdown_extensions"])
    extension_configs = dict(mkdocs_config["mdx_configs"])
    anchors_by_path: dict[Path, tuple[set[str], set[str]]] = {}
    findings: list[Finding] = []

    for source_path in _local_markdown_paths(repo_root):
        source_path = source_path.resolve()
        source_rel = source_path.relative_to(repo_root)
        markdown = source_path.read_text(encoding="utf-8")
        for link in find_links(markdown):
            target = urlsplit(link.target)
            if target.scheme or target.netloc:
                continue
            target_path = source_path
            if target.path:
                target_path = (source_path.parent / unquote(target.path)).resolve()
            try:
                target_path.relative_to(repo_root)
            except ValueError:
                continue
            if target.path and not target_path.exists():
                findings.append(
                    Finding(
                        "error",
                        f"{source_rel}: local link target {target.path} does not exist",
                    )
                )
                continue
            if not target.fragment:
                continue
            if not target_path.is_file():
                findings.append(
                    Finding(
                        "error",
                        f"{source_rel}: local anchor target {target.path} is not a file",
                    )
                )
                continue
            if target_path not in anchors_by_path:
                target_markdown = target_path.read_text(encoding="utf-8")
                anchors_by_path[target_path] = (
                    _github_anchors(target_markdown),
                    _mkdocs_anchors(target_markdown, extensions, extension_configs),
                )
            github_anchors, mkdocs_anchors = anchors_by_path[target_path]
            anchor = unquote(target.fragment)
            if anchor not in github_anchors:
                findings.append(
                    Finding(
                        "error",
                        f"{source_rel}: unknown GitHub local anchor #{anchor} in "
                        f"{target_path.relative_to(repo_root)}",
                    )
                )
            if anchor not in mkdocs_anchors:
                findings.append(
                    Finding(
                        "error",
                        f"{source_rel}: unknown MkDocs local anchor #{anchor} in "
                        f"{target_path.relative_to(repo_root)}",
                    )
                )
    return findings


def check(repo_root: str | Path, generated_root: str | Path) -> int:
    repo_root = Path(repo_root)
    generated_root = Path(generated_root)
    manifest = load_manifest(repo_root / "docs" / "manifest.yaml", repo_root)
    build(repo_root / "docs" / "manifest.yaml", repo_root, site=True, wiki=True, check=True)
    findings: list[Finding] = []
    findings += check_self_containment(generated_root, repo_root)
    findings += check_completeness(manifest, repo_root)
    findings += check_placeholders(generated_root, repo_root)
    findings += check_numbering(manifest, repo_root)
    findings += check_local_anchors(repo_root)
    for f in findings:
        print(f"[{f.severity}] {f.message}", file=sys.stderr)
    if findings:
        print(f"check_docs: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("check_docs: clean")
    return 0


def main(argv: list[str] | None = None) -> int:
    repo_root = Path.cwd()
    return check(repo_root, repo_root / "generated")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
