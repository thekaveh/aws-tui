import textwrap

from scripts.docs.manifest import parse_manifest
from scripts.docs.transforms import (
    build_source_map,
    output_name,
    rewrite_for_surface,
    wiki_slug,
)

MANIFEST = parse_manifest(
    textwrap.dedent(
        """
        surfaces: [repo, site, wiki]
        numbering: per-doc
        sections:
          - { id: overview, title: Overview, source: docs/index.md }
          - id: dev
            title: Development
            children:
              - { id: architecture, title: Architecture, source: docs/architecture.md }
              - { id: adding-a-service, title: Adding a Service, source: docs/adding-a-service.md }
        diagrams: []
        """
    )
)


def test_wiki_slug_dashes_spaces():
    assert wiki_slug("Adding a Service") == "Adding-a-Service"


def test_output_name_site_and_wiki_overview():
    overview = MANIFEST.leaves()[0]
    assert output_name(overview, "site") == "index.md"
    assert output_name(overview, "wiki") == "Home.md"


def test_output_name_regular_page():
    arch = MANIFEST.leaves()[1]
    assert output_name(arch, "site") == "architecture.md"
    assert output_name(arch, "wiki") == "Architecture.md"


def test_build_source_map_site():
    sm = build_source_map(MANIFEST, "site")
    assert sm["docs/index.md"] == "index.md"
    assert sm["docs/architecture.md"] == "architecture.md"
    assert sm["docs/adding-a-service.md"] == "adding-a-service.md"


def test_build_source_map_wiki():
    sm = build_source_map(MANIFEST, "wiki")
    assert sm["docs/index.md"] == "Home.md"
    assert sm["docs/adding-a-service.md"] == "Adding-a-Service.md"


def test_rewrite_strips_forbidden_link_to_bare_text():
    md = "See [the repo](https://github.com/thekaveh/aws-tui/blob/main/src/x.py) now."
    out = rewrite_for_surface(md, "site", build_source_map(MANIFEST, "site"))
    assert out == "See the repo now."


def test_rewrite_maps_known_md_link_wiki():
    md = "Read [arch](architecture.md#12-composition-root)."
    out = rewrite_for_surface(md, "wiki", build_source_map(MANIFEST, "wiki"))
    assert out == "Read [arch](Architecture.md#12-composition-root)."


def test_rewrite_maps_known_md_link_site_keeps_name():
    md = "Read [arch](architecture.md)."
    out = rewrite_for_surface(md, "site", build_source_map(MANIFEST, "site"))
    assert out == "Read [arch](architecture.md)."


def test_rewrite_non_manifest_relative_md_to_bare_text():
    md = "See [todo](recording-todo.md) (internal)."
    out = rewrite_for_surface(md, "site", build_source_map(MANIFEST, "site"))
    assert out == "See todo (internal)."


def test_rewrite_ipynb_link_to_bare_text():
    md = "Open [nb](analysis.ipynb)."
    out = rewrite_for_surface(md, "site", build_source_map(MANIFEST, "site"))
    assert out == "Open nb."


def test_rewrite_leaves_external_and_anchor_links():
    md = "See [textual](https://textual.textualize.io/) and [top](#12-layers)."
    out = rewrite_for_surface(md, "site", build_source_map(MANIFEST, "site"))
    assert out == md
