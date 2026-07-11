import textwrap

import pytest
from scripts.docs.manifest import (
    ManifestError,
    load_manifest,
    parse_manifest,
)

MINIMAL = textwrap.dedent(
    """
    surfaces: [repo, site, wiki]
    numbering: per-doc
    sections:
      - { id: overview, title: Overview, source: docs/index.md }
      - id: dev
        title: Development
        children:
          - { id: arch, title: Architecture, source: docs/architecture.md, diagrams: [architecture] }
    diagrams:
      - { id: architecture, master: docs/diagrams/architecture.html }
    """
)


def test_parse_builds_sections_and_diagrams():
    m = parse_manifest(MINIMAL)
    assert m.surfaces == ("repo", "site", "wiki")
    assert m.numbering == "per-doc"
    assert m.sections[0].id == "overview"
    assert m.sections[0].source == "docs/index.md"
    assert m.sections[0].is_group is False
    assert m.sections[1].is_group is True
    assert m.sections[1].children[0].diagrams == ("architecture",)
    assert m.diagrams[0].master == "docs/diagrams/architecture.html"


def test_leaves_returns_all_leaf_sources_in_order():
    m = parse_manifest(MINIMAL)
    assert [s.source for s in m.leaves()] == [
        "docs/index.md",
        "docs/architecture.md",
    ]


def test_section_with_both_source_and_children_is_error():
    bad = textwrap.dedent(
        """
        surfaces: [repo]
        numbering: per-doc
        sections:
          - id: x
            title: X
            source: docs/x.md
            children:
              - { id: y, title: Y, source: docs/y.md }
        diagrams: []
        """
    )
    with pytest.raises(ManifestError, match=r"source.*children|children.*source"):
        parse_manifest(bad)


def test_section_with_neither_source_nor_children_is_error():
    bad = "surfaces: [repo]\nnumbering: per-doc\nsections:\n  - {id: x, title: X}\ndiagrams: []\n"
    with pytest.raises(ManifestError):
        parse_manifest(bad)


def test_malformed_yaml_wrapped_as_manifest_error():
    with pytest.raises(ManifestError):
        parse_manifest("surfaces: [repo\n  bad: : :")


def test_missing_required_key_wrapped_as_manifest_error():
    with pytest.raises(ManifestError):
        parse_manifest("numbering: per-doc\nsections: []\ndiagrams: []\n")  # no surfaces


def test_load_manifest_validates_files_exist(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "manifest.yaml").write_text(
        "surfaces: [repo]\nnumbering: per-doc\n"
        "sections:\n  - {id: o, title: O, source: docs/missing.md}\ndiagrams: []\n"
    )
    with pytest.raises(ManifestError, match=r"missing.md"):
        load_manifest(tmp_path / "docs" / "manifest.yaml", tmp_path)


def test_load_manifest_ok_when_files_exist(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# 1. X\n")
    (tmp_path / "docs" / "manifest.yaml").write_text(
        "surfaces: [repo]\nnumbering: per-doc\n"
        "sections:\n  - {id: o, title: O, source: docs/index.md}\ndiagrams: []\n"
    )
    m = load_manifest(tmp_path / "docs" / "manifest.yaml", tmp_path)
    assert m.leaves()[0].source == "docs/index.md"
