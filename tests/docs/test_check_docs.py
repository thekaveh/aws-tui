import textwrap
from pathlib import Path

from scripts.docs.check_docs import (
    INTERNAL_DOCS,
    check_completeness,
    check_local_anchors,
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


def _write_docs(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# 1. aws-tui\n\n## 1.1. Intro\n")
    (root / "docs" / "architecture.md").write_text("# 1. Architecture\n\n## 1.1. Layers\n")


def _write_mkdocs_config(root: Path) -> None:
    (root / "mkdocs.yml").write_text(
        "site_name: test\ndocs_dir: docs\nmarkdown_extensions:\n  - toc:\n      permalink: true\n"
    )


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


def test_self_containment_scans_every_canonical_repository_doc(tmp_path):
    gen = tmp_path / "generated"
    (gen / "site").mkdir(parents=True)
    (gen / "wiki").mkdir(parents=True)
    _write_docs(tmp_path)
    (tmp_path / "README.md").write_text("clean\n")
    (tmp_path / "docs" / "manifest.yaml").write_text(
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
    (tmp_path / "docs" / "architecture.md").write_text(
        "# 1. Architecture\n\nSee https://thekaveh.github.io/aws-tui/architecture/.\n"
    )

    findings = check_self_containment(gen, tmp_path)

    assert any("docs/architecture.md" in finding.message for finding in findings)


def test_completeness_flags_unreferenced_published_doc(tmp_path):
    _write_docs(tmp_path)
    (tmp_path / "docs" / "orphan.md").write_text("# 1. Orphan\n")
    findings = check_completeness(MANIFEST, tmp_path)
    assert any("orphan.md" in f.message for f in findings)


def test_completeness_flags_unreferenced_nested_service_doc(tmp_path):
    _write_docs(tmp_path)
    services = tmp_path / "docs" / "services"
    services.mkdir()
    (services / "orphan.md").write_text("# 1. Orphan service\n")

    findings = check_completeness(MANIFEST, tmp_path)

    assert any("docs/services/orphan.md" in finding.message for finding in findings)


def test_completeness_ignores_historical_superpowers_docs(tmp_path):
    _write_docs(tmp_path)
    specs = tmp_path / "docs" / "superpowers" / "specs"
    specs.mkdir(parents=True)
    (specs / "historical.md").write_text("# 1. Historical spec\n")

    assert check_completeness(MANIFEST, tmp_path) == []


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


def test_placeholders_flags_todo_in_readme(tmp_path):
    gen = tmp_path / "generated"
    (gen / "site").mkdir(parents=True)
    (gen / "wiki").mkdir(parents=True)
    (tmp_path / "README.md").write_text("TODO: remove placeholder\n")

    findings = check_placeholders(gen, tmp_path)

    assert any("README.md" in finding.message for finding in findings)


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


def test_numbering_checks_nested_historical_docs(tmp_path):
    _write_docs(tmp_path)
    specs = tmp_path / "docs" / "superpowers" / "specs"
    specs.mkdir(parents=True)
    (specs / "history.md").write_text("# 1. History\n\n### 1.2.1. Missing parent\n")

    findings = check_numbering(MANIFEST, tmp_path)

    assert any("missing parent" in finding.message for finding in findings)


def test_numbering_rejects_duplicate_section_numbers(tmp_path):
    _write_docs(tmp_path)
    (tmp_path / "docs" / "index.md").write_text(
        "# 1. aws-tui\n\n## 1.1. Intro\n\n## 1.1. Duplicate\n"
    )

    findings = check_numbering(MANIFEST, tmp_path)

    assert any("duplicate heading number 1.1" in finding.message for finding in findings)


def test_numbering_ignores_nested_fences_in_four_tick_markdown_block(tmp_path):
    _write_docs(tmp_path)
    (tmp_path / "docs" / "index.md").write_text(
        "# 1. aws-tui\n\n````markdown\n# Example\n\n```sh\n# shell comment\n```\n````\n"
    )

    assert check_numbering(MANIFEST, tmp_path) == []


def test_local_anchors_accept_punctuation_when_github_and_mkdocs_agree(tmp_path):
    (tmp_path / "docs").mkdir()
    _write_mkdocs_config(tmp_path)
    (tmp_path / "docs" / "guide.md").write_text("# S3: Operations\n")
    (tmp_path / "README.md").write_text("[operations](docs/guide.md#s3-operations)\n")

    assert check_local_anchors(tmp_path) == []


def test_local_links_flag_missing_path_without_fragment(tmp_path):
    (tmp_path / "docs").mkdir()
    _write_mkdocs_config(tmp_path)
    (tmp_path / "README.md").write_text("[missing](docs/does-not-exist.md)\n")

    findings = check_local_anchors(tmp_path)

    assert any(
        "local link target docs/does-not-exist.md does not exist" in finding.message
        for finding in findings
    )


def test_local_anchors_require_both_github_and_mkdocs_duplicate_suffixes(tmp_path):
    (tmp_path / "docs").mkdir()
    _write_mkdocs_config(tmp_path)
    (tmp_path / "docs" / "guide.md").write_text("# Copy Object\n\n# Copy Object\n")
    (tmp_path / "README.md").write_text(
        "[GitHub duplicate](docs/guide.md#copy-object-1)\n"
        "[MkDocs duplicate](docs/guide.md#copy-object_1)\n"
    )

    findings = check_local_anchors(tmp_path)

    assert any(
        "unknown MkDocs local anchor #copy-object-1" in finding.message for finding in findings
    )
    assert any(
        "unknown GitHub local anchor #copy-object_1" in finding.message for finding in findings
    )


def test_local_anchors_flag_missing_same_document_fragment(tmp_path):
    (tmp_path / "docs").mkdir()
    _write_mkdocs_config(tmp_path)
    (tmp_path / "docs" / "guide.md").write_text("# 1. Guide\n\n[missing](#11-missing)\n")

    findings = check_local_anchors(tmp_path)

    assert any("unknown GitHub local anchor #11-missing" in finding.message for finding in findings)
    assert any("unknown MkDocs local anchor #11-missing" in finding.message for finding in findings)


def test_local_anchors_validate_reference_style_links(tmp_path):
    _write_docs(tmp_path)
    _write_mkdocs_config(tmp_path)
    (tmp_path / "README.md").write_text(
        "# 1. README\n\nSee [missing][architecture].\n\n"
        "[architecture]: docs/architecture.md#missing\n"
    )

    findings = check_local_anchors(tmp_path)

    assert any("unknown GitHub local anchor #missing" in finding.message for finding in findings)
    assert any("unknown MkDocs local anchor #missing" in finding.message for finding in findings)


def test_local_anchors_ignore_links_inside_fenced_code_blocks(tmp_path):
    (tmp_path / "docs").mkdir()
    _write_mkdocs_config(tmp_path)
    (tmp_path / "docs" / "guide.md").write_text(
        "# 1. Guide\n\n```markdown\n[example](#11-missing)\n```\n"
    )

    assert check_local_anchors(tmp_path) == []
