"""The docs CI gate: self-containment, completeness, placeholders, numbering,
and regeneration determinism (via ``build --check``)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.docs.build_docs import build
from scripts.docs.links import find_links, is_forbidden
from scripts.docs.manifest import Manifest, load_manifest

# Top-level docs deliberately kept in-repo only (never published/flagged).
INTERNAL_DOCS: frozenset[str] = frozenset({"docs/recording-todo.md"})

_PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b")
_H1_RE = re.compile(r"^# (\d+)\. ", re.MULTILINE)
_H2_RE = re.compile(r"^## (\d+)\.\d+\. ", re.MULTILINE)


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
    readme = repo_root / "README.md"
    if readme.is_file():
        for link in find_links(readme.read_text(encoding="utf-8")):
            if is_forbidden(link.target, "repo"):
                findings.append(Finding("error", f"README.md: forbidden link {link.target}"))
    return findings


def check_completeness(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    repo_root = Path(repo_root)
    referenced = {leaf.source for leaf in manifest.leaves()}
    findings: list[Finding] = []
    for md in sorted((repo_root / "docs").glob("*.md")):
        rel = f"docs/{md.name}"
        if rel in INTERNAL_DOCS or rel in referenced:
            continue
        findings.append(Finding("error", f"{rel}: published doc not referenced by manifest"))
    return findings


def check_placeholders(generated_root: str | Path) -> list[Finding]:
    generated_root = Path(generated_root)
    findings: list[Finding] = []
    for md_path in sorted(generated_root.rglob("*.md")):
        if md_path.relative_to(generated_root).parts[0] not in ("site", "wiki"):
            continue
        for m in _PLACEHOLDER_RE.finditer(md_path.read_text(encoding="utf-8")):
            rel = md_path.relative_to(generated_root)
            findings.append(Finding("error", f"{rel}: placeholder {m.group(1)}"))
    return findings


def check_numbering(manifest: Manifest, repo_root: str | Path) -> list[Finding]:
    repo_root = Path(repo_root)
    findings: list[Finding] = []
    for leaf in manifest.leaves():
        assert leaf.source is not None
        text = (repo_root / leaf.source).read_text(encoding="utf-8")
        h1 = _H1_RE.search(text)
        if not h1 or h1.group(1) != "1":
            findings.append(Finding("error", f"{leaf.source}: H1 must be '# 1. <title>'"))
        for m in _H2_RE.finditer(text):
            if m.group(1) != "1":
                findings.append(
                    Finding(
                        "error",
                        f"{leaf.source}: H2 must be '## 1.x. <title>' (got {m.group(0).strip()})",
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
    findings += check_placeholders(generated_root)
    findings += check_numbering(manifest, repo_root)
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
