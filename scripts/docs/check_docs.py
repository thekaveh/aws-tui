"""The docs CI gate: self-containment, local anchors, completeness, placeholders,
numbering, and regeneration determinism (via ``build --check``)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

from markdown import Markdown
from mkdocs.config import load_config
from scripts.docs.build_docs import build
from scripts.docs.links import find_links, is_forbidden
from scripts.docs.manifest import Manifest, load_manifest

# Docs deliberately kept in-repo only (never published/flagged).
INTERNAL_DOCS: frozenset[str] = frozenset({"docs/recording-todo.md"})
DESIGN_SPEC: str = "docs/superpowers/specs/2026-06-13-aws-tui-design.md"
INTERNAL_DOC_PREFIXES: tuple[str, ...] = ("docs/superpowers/",)

# Release history and the vendored Code of Conduct are never section-numbered:
# changelog headings are version names that would renumber on every release, and
# the Contributor Covenant is reproduced verbatim.
UNNUMBERED_DOCS: frozenset[str] = frozenset({"CHANGELOG.md", "CODE_OF_CONDUCT.md"})

# Root documents that deliberately stay repo-only.
#
# ``README.md`` is the repository entry point; ``docs/index.md`` is the
# published landing page, and publishing both would be two sources for one
# page. ``PYPI.md`` is a generated output, declared as the manifest's
# package surface. ``CHANGELOG.md`` cannot be published without breaking
# self-containment: its version headings resolve through nine
# ``/compare/`` links into the repository, and those are the whole point of
# a Keep a Changelog reference block -- stripping them would leave
# unresolved bracket text on the page.
UNPUBLISHED_ROOT_DOCS: frozenset[str] = frozenset({"README.md", "PYPI.md", "CHANGELOG.md"})

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
    """Flag a canonical document that no surface publishes.

    Root-level documents are in scope, not just ``docs/``. Scanning only
    ``docs/`` meant a root document could never be reported: ``CONTRIBUTING.md``,
    ``SECURITY.md`` and the code of conduct were numbering-checked as canonical
    while reaching no published surface at all, and nothing could say so.
    """
    repo_root = Path(repo_root)
    referenced = {leaf.source for leaf in manifest.leaves()}
    if manifest.package is not None:
        referenced.add(manifest.package.source)
    findings: list[Finding] = []
    candidates = sorted((repo_root / "docs").rglob("*.md")) + sorted(repo_root.glob("*.md"))
    for md in candidates:
        rel = md.relative_to(repo_root).as_posix()
        if (
            rel in INTERNAL_DOCS
            or rel in UNPUBLISHED_ROOT_DOCS
            or rel.startswith(INTERNAL_DOC_PREFIXES)
            or rel in referenced
        ):
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


_EMBED_RE = re.compile(r"<img\s+[^>]*\bsrc=[\"']([^\"']+)[\"']|!\[[^\]]*\]\(\s*([^)\s]+)\s*\)")


def check_assets(generated_root: str | Path) -> list[Finding]:
    """Reject generated pages that embed an image the surface does not carry.

    ``mkdocs build --strict`` validates Markdown links but not the ``src`` of a
    raw ``<img>`` tag, so a poster or screenshot can go missing from a published
    surface while every other gate stays green.
    """
    generated_root = Path(generated_root)
    findings: list[Finding] = []
    for md_path in sorted(generated_root.rglob("*.md")):
        rel = md_path.relative_to(generated_root)
        for line_number, line in enumerate(
            md_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in _EMBED_RE.finditer(line):
                target = match.group(1) or match.group(2)
                if urlsplit(target).scheme or target.startswith("//"):
                    continue
                resolved = (md_path.parent / unquote(urlsplit(target).path)).resolve()
                if not resolved.is_file():
                    findings.append(
                        Finding("error", f"{rel}:{line_number}: embedded image {target} is missing")
                    )
    return findings


# The landing page's H1 is the product name while its nav entry reads "Overview".
# Every other page carries one title across the nav, the wiki sidebar, the wiki
# filename, and its own H1.
TITLE_EXEMPT_IDS: frozenset[str] = frozenset({"overview"})


def check_titles(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    """Reject a page whose H1 disagrees with the title the manifest publishes.

    The manifest title names the page in the site nav, the wiki sidebar, and the
    wiki filename; the H1 names it on the page itself. Two sources drift
    silently — a wiki page called ``Platforms`` opened with ``# Supported
    platforms`` for as long as nothing compared them.
    """
    repo_root = Path(repo_root)
    findings: list[Finding] = []
    for leaf in manifest.leaves():
        if leaf.source is None or leaf.id in TITLE_EXEMPT_IDS:
            continue
        path = repo_root / leaf.source
        if not path.is_file():
            continue
        heading = next(
            (
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("# ")
            ),
            None,
        )
        if heading is None:
            findings.append(Finding("error", f"{leaf.source}: no H1 to compare with the manifest"))
            continue
        title = heading[2:].strip()
        if title != leaf.title:
            findings.append(
                Finding(
                    "error",
                    f"{leaf.source}: H1 {title!r} does not match manifest title {leaf.title!r}",
                )
            )
    return findings


_SECTION_REF_RE = re.compile(r"§ ?(\d+(?:\.\d+)+)")
# A citation of the design spec, not merely a line containing the letters
# "spec". Routing on the bare substring diverted any line with "respect",
# "specific" or "inspect" to the spec's numbering, and conversely let a
# genuine same-document reference pass whenever the spec happened to have a
# heading with the same number.
_SPEC_CITATION_RE = re.compile(r"\bspec(?:ification)?\b[^.\n]{0,40}?§", re.IGNORECASE)


def _design_spec_sections(repo_root: Path) -> set[str]:
    """Heading numbers declared by the canonical design spec, if it is present."""
    spec = repo_root / DESIGN_SPEC
    if not spec.is_file():
        return set()
    return {
        match.group(2)
        for match in (
            _HEADING_RE.match(line) for line in spec.read_text(encoding="utf-8").splitlines()
        )
        if match
    }


def check_section_references(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    """Reject a ``§N.M`` cross-reference that resolves to no such section.

    These are plain prose, so nothing rewrites them when sections are renumbered
    and no link checker sees them.

    A line mentioning "spec" used to be skipped outright, on the theory that such
    references were external and unverifiable. That exemption covered exactly the
    ``spec §N.M`` citations -- and every one of them had since rotted, each off by
    one top-level section, because a section was inserted after those pages were
    written. The design spec lives in this repository, so its headings are
    checkable: resolve against it instead of skipping. A spec-citing line is only
    exempt when the spec itself is absent (it is excluded from the sdist).
    """
    repo_root = Path(repo_root)
    spec_numbers = _design_spec_sections(repo_root)
    findings: list[Finding] = []
    for leaf in manifest.leaves():
        if leaf.source is None:
            continue
        path = repo_root / leaf.source
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        numbers = {match.group(2) for match in (_HEADING_RE.match(line) for line in lines) if match}
        for line_number, line in enumerate(lines, start=1):
            cites_spec = _SPEC_CITATION_RE.search(line) is not None
            if cites_spec and not spec_numbers:
                continue
            expected = spec_numbers if cites_spec else numbers
            where = "the design spec" if cites_spec else "this document"
            for match in _SECTION_REF_RE.finditer(line):
                if match.group(1) not in expected:
                    findings.append(
                        Finding(
                            "error",
                            f"{leaf.source}:{line_number}: section reference "
                            f"§{match.group(1)} has no such section in {where}",
                        )
                    )
    return findings


def check_numbering(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    """Enforce unnumbered page titles with hierarchically numbered sections.

    The page's position in the published hierarchy is owned by the manifest and
    rendered into the site nav and the wiki sidebar; baking it into the H1 too
    would be a second source that drifts silently. Section numbers therefore
    restart per document and sit one level shallower than their heading: ``##``
    carries ``N.``, ``###`` carries ``N.M.``. Release history and the vendored
    Code of Conduct carry no numbering at all.
    """
    if manifest.numbering != "per-doc":
        return [Finding("error", f"unsupported numbering mode: {manifest.numbering}")]
    repo_root = Path(repo_root)
    findings: list[Finding] = []
    markdown = sorted(repo_root.glob("*.md")) + sorted((repo_root / "docs").rglob("*.md"))
    for path in markdown:
        rel = path.relative_to(repo_root)
        unnumbered = rel.as_posix() in UNNUMBERED_DOCS
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
            level_match = re.match(r"^(#{1,6})\s+", line)
            if fence_length is not None or level_match is None:
                continue
            level = len(level_match.group(1))
            match = _HEADING_RE.match(line)
            if unnumbered:
                if match is not None:
                    findings.append(
                        Finding("error", f"{rel}:{line_number}: heading must not be numbered")
                    )
                continue
            if level == 1:
                if match is not None:
                    findings.append(
                        Finding("error", f"{rel}:{line_number}: page title must not be numbered")
                    )
                continue
            if match is None:
                findings.append(
                    Finding(
                        "error", f"{rel}:{line_number}: heading must be hierarchically numbered"
                    )
                )
                continue
            number = tuple(int(part) for part in match.group(2).split("."))
            if len(number) != level - 1:
                findings.append(
                    Finding(
                        "error",
                        f"{rel}:{line_number}: heading number {match.group(2)} does not match H{level}",
                    )
                )
                continue
            if level > 2 and number[:-1] not in seen:
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


def _repo_relative_github_target(target: SplitResult, repo_root: Path) -> tuple[Path, str] | None:
    """Resolve an absolute GitHub URL that points back into this repository.

    Returns ``(path, fragment)`` for a link that names a file in this repo with
    a fragment, else ``None``. Only the repository's own ``blob``/``tree`` URLs
    and its root README are recognised; anything else is genuinely external.
    """
    if target.scheme not in {"http", "https"} or target.netloc != "github.com":
        return None
    if not target.fragment:
        return None
    parts = [part for part in unquote(target.path).split("/") if part]
    if [part.casefold() for part in parts[:2]] != ["thekaveh", "aws-tui"]:
        return None
    rest = parts[2:]
    if not rest:
        candidate = repo_root / "README.md"
    elif rest[0] in {"blob", "tree"} and len(rest) > 2:
        candidate = repo_root / Path(*rest[2:])
    else:
        return None
    return candidate, unquote(target.fragment)


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
                # An absolute URL back into this same repository still points at
                # a heading this checker can resolve. Skipping every scheme'd
                # link left the PyPI blurb's "Installation and quickstart"
                # anchor pointing at a heading number that had since changed --
                # on the page published to PyPI, where it is the primary
                # navigation affordance.
                resolved = _repo_relative_github_target(target, repo_root)
                if resolved is not None:
                    target_path, fragment = resolved
                    if not target_path.is_file():
                        # The path resolves into this repository but nothing is
                        # there. Skipping it threw away a finding the function
                        # had already done the work to produce.
                        findings.append(
                            Finding(
                                "error",
                                f"{source_rel}: absolute repository link {link.target} "
                                f"points at a missing file",
                            )
                        )
                        continue
                    if target_path not in anchors_by_path:
                        target_markdown = target_path.read_text(encoding="utf-8")
                        anchors_by_path[target_path] = (
                            _github_anchors(target_markdown),
                            _mkdocs_anchors(target_markdown, extensions, extension_configs),
                        )
                    if fragment not in anchors_by_path[target_path][0]:
                        findings.append(
                            Finding(
                                "error",
                                f"{source_rel}: absolute repository link {link.target} "
                                f"has no matching heading in "
                                f"{target_path.relative_to(repo_root)}",
                            )
                        )
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
    findings += check_assets(generated_root)
    findings += check_numbering(manifest, repo_root)
    findings += check_titles(manifest, repo_root)
    findings += check_section_references(manifest, repo_root)
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
