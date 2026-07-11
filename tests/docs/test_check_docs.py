import textwrap
from pathlib import Path

from scripts.docs.check_docs import (
    INTERNAL_DOCS,
    check_completeness,
    check_numbering,
    check_placeholders,
    check_self_containment,
)
from scripts.docs.manifest import parse_manifest

MANIFEST = parse_manifest(
    textwrap.dedent(
        """
        surfaces: [repo, site, wiki]
        numbering: per-doc
        sections:
          - { id: overview, title: Overview, source: docs/index.md }
          - { id: architecture, title: Architecture, source: docs/architecture.md }
        diagrams: []
        """
    )
)


def _write_docs(root: Path):
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# 1. aws-tui\n\n## 1.1. Intro\n")
    (root / "docs" / "architecture.md").write_text("# 1. Architecture\n\n## 1.1. Layers\n")


def test_self_containment_flags_forbidden_link_in_generated_site(tmp_path):
    gen = tmp_path / "generated"
    (gen / "site").mkdir(parents=True)
    (gen / "wiki").mkdir(parents=True)
    (gen / "site" / "a.md").write_text("[x](https://github.com/thekaveh/aws-tui/wiki/Home)\n")
    (tmp_path / "README.md").write_text("clean\n")
    findings = check_self_containment(gen, tmp_path)
    assert any("a.md" in f.message for f in findings)


def test_self_containment_flags_forbidden_link_in_readme(tmp_path):
    gen = tmp_path / "generated"
    (gen / "site").mkdir(parents=True)
    (gen / "wiki").mkdir(parents=True)
    (tmp_path / "README.md").write_text(
        "See the [wiki](https://github.com/thekaveh/aws-tui/wiki/Home).\n"
    )
    findings = check_self_containment(gen, tmp_path)
    assert any("README" in f.message for f in findings)


def test_completeness_flags_unreferenced_published_doc(tmp_path):
    _write_docs(tmp_path)
    (tmp_path / "docs" / "orphan.md").write_text("# 1. Orphan\n")
    findings = check_completeness(MANIFEST, tmp_path)
    assert any("orphan.md" in f.message for f in findings)


def test_completeness_ignores_internal_docs(tmp_path):
    _write_docs(tmp_path)
    (tmp_path / "docs" / "recording-todo.md").write_text("# 1. Recording TODO\n")
    findings = check_completeness(MANIFEST, tmp_path)
    assert not any("recording-todo.md" in f.message for f in findings)
    assert "docs/recording-todo.md" in INTERNAL_DOCS


def test_completeness_clean_when_all_referenced(tmp_path):
    _write_docs(tmp_path)
    assert check_completeness(MANIFEST, tmp_path) == []


def test_placeholders_flags_todo_in_generated(tmp_path):
    gen = tmp_path / "generated"
    (gen / "site").mkdir(parents=True)
    (gen / "wiki").mkdir(parents=True)
    (gen / "site" / "a.md").write_text("Body\n\nTODO: finish this.\n")
    findings = check_placeholders(gen)
    assert any("TODO" in f.message for f in findings)


def test_numbering_flags_wrong_h1(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# 2. Wrong\n")
    (tmp_path / "docs" / "architecture.md").write_text("# 1. Architecture\n## 1.1. Layers\n")
    findings = check_numbering(MANIFEST, tmp_path)
    assert any("index.md" in f.message for f in findings)


def test_numbering_flags_wrong_h2(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# 1. aws-tui\n## 2.1. Bad\n")
    (tmp_path / "docs" / "architecture.md").write_text("# 1. Architecture\n## 1.1. Layers\n")
    findings = check_numbering(MANIFEST, tmp_path)
    assert any("index.md" in f.message for f in findings)


def test_numbering_clean(tmp_path):
    _write_docs(tmp_path)
    assert check_numbering(MANIFEST, tmp_path) == []
