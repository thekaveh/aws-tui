# Three-Surface Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project the canonical `docs/*.md` into three self-contained surfaces (in-repo, MkDocs `.io` site, GitHub wiki) that stay in sync by construction.

**Architecture:** A `uv`-native Python package `scripts/docs/` reads one `docs/manifest.yaml` and generates the gitignored `generated/site/` + `generated/wiki/` trees and the root `mkdocs.yml`. The in-repo surface is the canonical `docs/*.md` rendered by GitHub directly. A `check_docs` CI gate proves self-containment, completeness, no-placeholders, per-doc numbering, and regeneration determinism. External/publish steps (enable wiki/Pages, deploy key, first push) are a gated Phase 2 runbook, not part of the build.

**Tech Stack:** Python 3.11+, `uv`, PyYAML, MkDocs Material, cairosvg (+ system libcairo2), GitHub Actions.

**Source spec:** `docs/superpowers/specs/2026-07-10-three-surface-docs-design.md`

## Global Constraints

- Repo: `thekaveh/aws-tui` (public). `REPO_URL=https://github.com/thekaveh/aws-tui`, `WIKI_URL=https://github.com/thekaveh/aws-tui/wiki`, `SITE_URL=https://thekaveh.github.io/aws-tui/`.
- Wiki renders from branch **`master`**, not `main` (gotcha #1).
- Invoke scripts as `python -m scripts.docs.<x>` — never `python scripts/docs/<x>.py` (gotcha #4).
- Self-containment is absolute: no surface links to another or to GitHub source views; MkDocs gets **no** `repo_url`/`repo_name`/`edit_uri`; `README.md` is gated too (gotcha #7).
- Wiki special pages need the underscore prefix: `Home.md`, `_Sidebar.md`, `_Footer.md`.
- Image rewrites preserve any `../` prefix (gotcha #6).
- Generated trees + root `mkdocs.yml` + `site/` are gitignored; committed PNGs live at `docs/diagrams/img/`.
- Internal-only docs (never published, never flagged): `docs/recording-todo.md`, `docs/superpowers/**`.
- Per-doc numbering is preserved: every published doc keeps `# 1. Title` / `## 1.x`. Nav labels come from the manifest `title:` (no leading number).
- Dependency floors: `mkdocs-material>=9.6,<10.0`, `pyyaml>=6.0,<7.0`, `cairosvg>=2.7,<3.0`.
- CI triggers cover **both** `main` and `develop` (gotcha #22). Actions are pinned by SHA to match existing workflows.
- All Python invocations go through `uv run` (the repo is uv-managed). **macOS cairo exception:** cairosvg loads `libcairo` via dlopen at import; on macOS Homebrew's libcairo is off the default dyld path AND `uv run` drops `DYLD_*` (SIP), so any cairo-touching command (`render_diagrams`, `build_docs`, `check_docs`) must run as `DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix cairo)/lib" ./.venv/bin/python -m scripts.docs.<x>` (run `uv sync --group docs` first so `.venv` exists). On Linux/CI, apt's libcairo2 is on a standard path and plain `uv run python` works. `mkdocs`/`push_wiki` don't touch cairo. The Makefile (Task 10) encapsulates this via an OS-detected `DOCS_PY`. Also: `import cairosvg` raises `OSError` (not `ImportError`) when libcairo is unloadable, so raster tests guard on `(ImportError, OSError)`, not `importorskip` alone.
- Every commit message ends with the two trailers used in this repo:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DweQ5mC7bQWMWGFneMweJx
  ```
  (Omitted from the per-step `git commit` snippets below for brevity — append them.)

---

## File Structure

**New package `scripts/docs/`** — one responsibility per module:
- `__init__.py` — package marker.
- `manifest.py` — parse/validate `manifest.yaml` → dataclasses.
- `links.py` — link discovery + the 3×3 forbidden matrix.
- `transforms.py` — per-surface link rewriting + source map.
- `render_diagrams.py` — HTML master → SVG (site) + PNG (committed) + wiki copy.
- `build_docs.py` — render site/wiki/`mkdocs.yml` + determinism check.
- `check_docs.py` — the CI gate (probes + orchestration).
- `push_wiki.py` — sync `generated/wiki/` → `aws-tui.wiki.git`.

**New tests `tests/docs/`** — one file per module.

**New canonical content** — `docs/index.md`, `docs/manifest.yaml`, `docs/diagrams/architecture.html` (+ committed `docs/diagrams/img/architecture.png`), `docs/stylesheets/extra.css`, `docs/javascripts/mathjax.js`, one image embed added to `docs/architecture.md`.

**Modified config** — `.gitignore`, `pyproject.toml`, new `Makefile`, new `.github/workflows/docs.yml` + `.github/workflows/pages.yml`.

---

## Task 1: Scaffolding, gitignore, and uv docs dependency group

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml` (add `[dependency-groups] docs`; add `"."` to pytest `pythonpath`)
- Create: `scripts/docs/__init__.py`
- Create: `tests/docs/__init__.py`
- Test: `tests/docs/test_scaffolding.py`

**Interfaces:**
- Produces: an importable `scripts.docs` package; `uv sync --group docs` installs mkdocs-material/pyyaml/cairosvg; `pytest` collects `tests/docs/`.

- [ ] **Step 1: Append to `.gitignore`**

Add at the end of `.gitignore`:
```gitignore

# three-surface docs — generated surfaces + mkdocs output (regenerated, never committed)
/generated/
/mkdocs.yml
/site/
```

- [ ] **Step 2: Add the docs dependency group to `pyproject.toml`**

Add a new top-level table (place it near any existing `[dependency-groups]`; if none exists, add the whole block):
```toml
[dependency-groups]
docs = [
  "mkdocs-material>=9.6,<10.0",
  "pyyaml>=6.0,<7.0",
  "cairosvg>=2.7,<3.0",
]
```
If a `[dependency-groups]` table already exists, add only the `docs = [...]` key inside it.

- [ ] **Step 3: Add `"."` to the pytest pythonpath**

In `pyproject.toml`, under `[tool.pytest.ini_options]`, change:
```toml
pythonpath = ["src"]
```
to:
```toml
pythonpath = ["src", "."]
```

- [ ] **Step 4: Create the package markers**

Create `scripts/docs/__init__.py`:
```python
"""Three-surface documentation pipeline for aws-tui.

Generates the ``.io`` MkDocs site and the GitHub wiki from the canonical
``docs/*.md`` sources declared in ``docs/manifest.yaml``. Invoke modules as
``python -m scripts.docs.<module>`` (never as a bare file path).
"""
```

Create `tests/docs/__init__.py`:
```python
```
(empty file)

- [ ] **Step 5: Write the scaffolding test**

Create `tests/docs/test_scaffolding.py`:
```python
"""Smoke test: the docs package imports and pyyaml is available."""


def test_scripts_docs_package_imports():
    import scripts.docs  # noqa: F401


def test_pyyaml_available():
    import yaml

    assert yaml.safe_load("a: 1") == {"a": 1}
```

- [ ] **Step 6: Sync the docs group and run the test**

Run:
```bash
uv sync --group docs
uv run pytest tests/docs/test_scaffolding.py -v
```
Expected: both tests PASS.

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml uv.lock scripts/docs/__init__.py tests/docs/__init__.py tests/docs/test_scaffolding.py
git commit -m "chore(docs): scaffold three-surface docs package + uv docs group"
```

---

## Task 2: `manifest.py` — parse and validate `manifest.yaml`

**Files:**
- Create: `scripts/docs/manifest.py`
- Test: `tests/docs/test_manifest.py`

**Interfaces:**
- Produces:
  - `class ManifestError(Exception)`
  - `@dataclass(frozen=True) DiagramEntry(id: str, master: str)`
  - `@dataclass(frozen=True) Section(id: str, title: str, source: str | None = None, children: tuple[Section, ...] = (), diagrams: tuple[str, ...] = ())` with `@property is_group -> bool`
  - `@dataclass(frozen=True) Manifest(surfaces: tuple[str, ...], numbering: str, sections: tuple[Section, ...], diagrams: tuple[DiagramEntry, ...])` with `leaves() -> list[Section]` (all leaf sections, nav order)
  - `parse_manifest(text: str) -> Manifest`
  - `load_manifest(path: str | Path, repo_root: str | Path) -> Manifest`

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_manifest.py`:
```python
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
    with pytest.raises(ManifestError, match="source.*children|children.*source"):
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
    with pytest.raises(ManifestError, match="missing.md"):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_manifest.py -v`
Expected: FAIL (`ModuleNotFoundError: scripts.docs.manifest`).

- [ ] **Step 3: Write `scripts/docs/manifest.py`**

```python
"""Parse and validate ``docs/manifest.yaml`` into typed dataclasses.

A section is EITHER a source-leaf (has ``source``) OR a children-group (has
``children``) — never both, never neither (gotcha #14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ManifestError(Exception):
    """Raised when the manifest is malformed or references a missing file."""


@dataclass(frozen=True)
class DiagramEntry:
    id: str
    master: str


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    source: str | None = None
    children: tuple["Section", ...] = ()
    diagrams: tuple[str, ...] = ()

    @property
    def is_group(self) -> bool:
        return bool(self.children)


@dataclass(frozen=True)
class Manifest:
    surfaces: tuple[str, ...]
    numbering: str
    sections: tuple[Section, ...]
    diagrams: tuple[DiagramEntry, ...]

    def leaves(self) -> list[Section]:
        out: list[Section] = []
        _collect_leaves(self.sections, out)
        return out


def _collect_leaves(sections: tuple[Section, ...], out: list[Section]) -> None:
    for s in sections:
        if s.is_group:
            _collect_leaves(s.children, out)
        else:
            out.append(s)


def _build_section(raw: dict) -> Section:
    try:
        id_ = raw["id"]
        title = raw["title"]
    except (KeyError, TypeError) as exc:  # TypeError if raw is not a mapping
        raise ManifestError(f"section missing id/title: {raw!r}") from exc
    has_children = "children" in raw and raw["children"]
    has_source = "source" in raw and raw["source"]
    if has_children and has_source:
        raise ManifestError(f"section {id_!r} has both source and children")
    if not has_children and not has_source:
        raise ManifestError(f"section {id_!r} has neither source nor children")
    children = tuple(_build_section(c) for c in raw.get("children", []))
    diagrams = tuple(raw.get("diagrams", []) or ())
    return Section(
        id=id_,
        title=title,
        source=raw.get("source"),
        children=children,
        diagrams=diagrams,
    )


def parse_manifest(text: str) -> Manifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a mapping")
    try:
        surfaces = tuple(data["surfaces"])
        numbering = str(data["numbering"])
        sections = tuple(_build_section(s) for s in data["sections"])
        diagrams = tuple(
            DiagramEntry(id=d["id"], master=d["master"]) for d in data.get("diagrams", [])
        )
    except (KeyError, TypeError) as exc:
        raise ManifestError(f"missing/invalid manifest key: {exc}") from exc
    return Manifest(surfaces=surfaces, numbering=numbering, sections=sections, diagrams=diagrams)


def load_manifest(path: str | Path, repo_root: str | Path) -> Manifest:
    path = Path(path)
    repo_root = Path(repo_root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    manifest = parse_manifest(text)
    for leaf in manifest.leaves():
        assert leaf.source is not None
        if not (repo_root / leaf.source).is_file():
            raise ManifestError(f"section source not found: {leaf.source}")
    for d in manifest.diagrams:
        if not (repo_root / d.master).is_file():
            raise ManifestError(f"diagram master not found: {d.master}")
    return manifest
```

Note: `field` is imported for forward-compat but unused; if ruff flags F401, remove the `field` import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_manifest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/manifest.py tests/docs/test_manifest.py
git commit -m "feat(docs): manifest parser + validation"
```

---

## Task 3: `links.py` — link discovery + the 3×3 forbidden matrix

**Files:**
- Create: `scripts/docs/links.py`
- Test: `tests/docs/test_links.py`

**Interfaces:**
- Produces:
  - `REPO_URL, WIKI_URL, SITE_URL: str`
  - `@dataclass(frozen=True) Link(target: str)`
  - `find_links(md: str) -> list[Link]`
  - `is_forbidden(target: str, surface: str) -> bool` (surface ∈ {"repo","site","wiki"})

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_links.py`:
```python
from scripts.docs.links import (
    REPO_URL,
    SITE_URL,
    WIKI_URL,
    find_links,
    is_forbidden,
)


def test_find_links_extracts_targets():
    md = "See [a](one.md) and [b](https://x.test/y) and ![img](p.png)."
    assert [ln.target for ln in find_links(md)] == ["one.md", "https://x.test/y", "p.png"]


def test_site_forbids_repo_and_wiki_links():
    assert is_forbidden(f"{REPO_URL}/blob/main/src/x.py", "site") is True
    assert is_forbidden(f"{WIKI_URL}/Home", "site") is True
    assert is_forbidden(SITE_URL, "site") is False  # linking to self is fine
    assert is_forbidden("architecture.md", "site") is False  # internal


def test_wiki_forbids_repo_and_site_links():
    assert is_forbidden(f"{REPO_URL}/tree/main/docs", "wiki") is True
    assert is_forbidden(f"{SITE_URL}architecture/", "wiki") is True


def test_repo_forbids_site_and_wiki_but_allows_repo():
    assert is_forbidden(f"{SITE_URL}architecture/", "repo") is True
    assert is_forbidden(f"{WIKI_URL}/Home", "repo") is True
    assert is_forbidden(f"{REPO_URL}/blob/main/CHANGELOG.md", "repo") is False


def test_wiki_url_contains_repo_url_but_is_classified_as_wiki():
    # WIKI_URL startswith REPO_URL — must not be misclassified as a repo link.
    assert is_forbidden(f"{WIKI_URL}/Architecture", "site") is True
    assert is_forbidden(f"{WIKI_URL}/Architecture", "repo") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_links.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `scripts/docs/links.py`**

```python
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

_FORBIDDEN = {
    "site": {"repo", "wiki"},
    "wiki": {"repo", "site"},
    "repo": {"site", "wiki"},
}


@dataclass(frozen=True)
class Link:
    target: str


def find_links(md: str) -> list[Link]:
    return [Link(m.group(1)) for m in _LINK_RE.finditer(md)]


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_links.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/links.py tests/docs/test_links.py
git commit -m "feat(docs): link discovery + self-containment matrix"
```

---

## Task 4: `transforms.py` — source map + per-surface link rewriting

**Files:**
- Create: `scripts/docs/transforms.py`
- Test: `tests/docs/test_transforms.py`

**Interfaces:**
- Consumes: `manifest.Manifest`, `links.is_forbidden`, `links.find_links`.
- Produces:
  - `wiki_slug(title: str) -> str` (e.g. `"Adding a Service"` → `"Adding-a-Service"`)
  - `output_name(section, surface) -> str` (site: `<id>.md`, `overview`→`index.md`; wiki: `<slug>.md`, `overview`→`Home.md`)
  - `build_source_map(manifest, surface) -> dict[str, str]` (canonical repo-relative path → output filename)
  - `rewrite_for_surface(md: str, surface: str, source_map: dict[str, str]) -> str`

Assumption (holds for this repo): all published docs live flat in `docs/`, so a relative `.md` link is uniquely identified by its basename.

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_transforms.py`:
```python
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
              - { id: adding-service, title: Adding a Service, source: docs/adding-a-service.md }
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_transforms.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `scripts/docs/transforms.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_transforms.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/transforms.py tests/docs/test_transforms.py
git commit -m "feat(docs): source map + per-surface link rewriting"
```

---

## Task 5: `render_diagrams.py` — HTML master → SVG + PNG

**Files:**
- Create: `scripts/docs/render_diagrams.py`
- Test: `tests/docs/test_render_diagrams.py`

**Interfaces:**
- Consumes: `manifest.Manifest` (its `.diagrams`), `manifest.load_manifest` (CLI).
- Produces:
  - `extract_svg(html_text: str) -> str`
  - `svg_to_png(svg: str, out_path: str | Path, *, width: int = 1600) -> None`
  - `render_all(manifest, repo_root, site_img_dir, png_dir) -> None` (writes `<id>.svg` to `site_img_dir`, `<id>.png` to `png_dir`)
  - `copy_assets(repo_root, wiki_img_dir) -> None` (copies committed PNGs into the wiki image dir)
  - CLI: `python -m scripts.docs.render_diagrams` (renders using `docs/manifest.yaml`, cwd as repo root)

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_render_diagrams.py`:
```python
import textwrap
from pathlib import Path

import pytest

from scripts.docs.manifest import parse_manifest
from scripts.docs.render_diagrams import copy_assets, extract_svg, render_all, svg_to_png


def test_extract_svg_pulls_inline_svg():
    html = "<html><body><svg width='10'><rect/></svg></body></html>"
    assert extract_svg(html) == "<svg width='10'><rect/></svg>"


def test_extract_svg_sanitizes_named_entities():
    html = "<svg><text>A &middot; B &Sigma; C &amp; D &#160; E</text></svg>"
    out = extract_svg(html)
    assert "&middot;" not in out and "&Sigma;" not in out
    assert "·" in out and "Σ" in out
    assert "&amp;" in out  # standard XML entity preserved
    assert "&#160;" in out  # numeric entity preserved


def test_extract_svg_raises_when_absent():
    with pytest.raises(ValueError, match="no <svg>"):
        extract_svg("<html>nope</html>")


def test_svg_to_png_writes_png_magic(tmp_path):
    pytest.importorskip("cairosvg")
    svg = "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'><rect width='4' height='4' fill='red'/></svg>"
    out = tmp_path / "x.png"
    svg_to_png(svg, out, width=4)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_all_writes_svg_and_png(tmp_path):
    pytest.importorskip("cairosvg")
    (tmp_path / "docs" / "diagrams").mkdir(parents=True)
    (tmp_path / "docs" / "diagrams" / "d.html").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'><rect width='4' height='4'/></svg>"
    )
    m = parse_manifest(
        textwrap.dedent(
            """
            surfaces: [site]
            numbering: per-doc
            sections: [{id: overview, title: O, source: docs/diagrams/d.html}]
            diagrams: [{id: system, master: docs/diagrams/d.html}]
            """
        )
    )
    site_img = tmp_path / "generated" / "site" / "assets" / "img"
    png_dir = tmp_path / "docs" / "diagrams" / "img"
    render_all(m, tmp_path, site_img, png_dir)
    assert (site_img / "system.svg").is_file()
    assert (png_dir / "system.png").read_bytes()[:4] == b"\x89PNG"


def test_copy_assets_copies_pngs(tmp_path):
    src = tmp_path / "docs" / "diagrams" / "img"
    src.mkdir(parents=True)
    (src / "system.png").write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    wiki_img = tmp_path / "generated" / "wiki" / "img"
    copy_assets(tmp_path, wiki_img)
    assert (wiki_img / "system.png").read_bytes().startswith(b"\x89PNG")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_render_diagrams.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `scripts/docs/render_diagrams.py`**

```python
"""Render diagram HTML masters to SVG (site) and PNG (committed + wiki)."""

from __future__ import annotations

import html as _html
import re
import shutil
import sys
from pathlib import Path

from scripts.docs.manifest import Manifest, load_manifest

_SVG_RE = re.compile(r"<svg[\s\S]*?</svg>", re.IGNORECASE)
# Named entities that are NOT valid in standalone XML (exclude the 5 XML
# built-ins and numeric entities).
_ENTITY_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)[a-zA-Z][a-zA-Z0-9]*;")


def extract_svg(html_text: str) -> str:
    m = _SVG_RE.search(html_text)
    if not m:
        raise ValueError("no <svg> found in diagram master")
    svg = m.group(0)
    return _ENTITY_RE.sub(lambda mo: _html.unescape(mo.group(0)), svg)


def svg_to_png(svg: str, out_path: str | Path, *, width: int = 1600) -> None:
    import cairosvg  # lazy — only needed when rasterizing

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        write_to=str(out_path),
        output_width=width,
    )


def render_all(
    manifest: Manifest,
    repo_root: str | Path,
    site_img_dir: str | Path,
    png_dir: str | Path,
) -> None:
    repo_root = Path(repo_root)
    site_img_dir = Path(site_img_dir)
    png_dir = Path(png_dir)
    site_img_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)
    for d in manifest.diagrams:
        svg = extract_svg((repo_root / d.master).read_text(encoding="utf-8"))
        (site_img_dir / f"{d.id}.svg").write_text(svg, encoding="utf-8")
        svg_to_png(svg, png_dir / f"{d.id}.png")


def copy_assets(repo_root: str | Path, wiki_img_dir: str | Path) -> None:
    src = Path(repo_root) / "docs" / "diagrams" / "img"
    dst = Path(wiki_img_dir)
    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for png in src.glob("*.png"):
        shutil.copy2(png, dst / png.name)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path.cwd()
    manifest = load_manifest(repo_root / "docs" / "manifest.yaml", repo_root)
    render_all(
        manifest,
        repo_root,
        repo_root / "generated" / "site" / "assets" / "img",
        repo_root / "docs" / "diagrams" / "img",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_render_diagrams.py -v`
Expected: PASS (cairosvg tests run if libcairo2 is present; else skipped).

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/render_diagrams.py tests/docs/test_render_diagrams.py
git commit -m "feat(docs): diagram master -> SVG + PNG rendering"
```

---

## Task 6: `build_docs.py` — render site, wiki, mkdocs.yml + determinism

**Files:**
- Create: `scripts/docs/build_docs.py`
- Test: `tests/docs/test_build_docs.py`

**Interfaces:**
- Consumes: `manifest`, `transforms`, `render_diagrams`.
- Produces:
  - `render_site(manifest, repo_root, out_dir) -> None`
  - `render_wiki(manifest, repo_root, out_dir) -> None`
  - `render_mkdocs_yml(manifest) -> str`
  - `_assert_dirs_equal(a, b) -> None` (raises `AssertionError` on content-hash mismatch)
  - `build(path, repo_root, *, site=False, wiki=False, check=False) -> None`
  - CLI: `python -m scripts.docs.build_docs [--site] [--wiki] [--check]`
- Conventions: `generated_root = repo_root/"generated"`; site→`generated/site` (images `assets/img`), wiki→`generated/wiki` (images `img`), committed PNGs→`docs/diagrams/img`, `mkdocs.yml`→`repo_root/mkdocs.yml`.

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_build_docs.py`:
```python
import textwrap
from pathlib import Path

import pytest

from scripts.docs.build_docs import (
    _assert_dirs_equal,
    build,
    render_mkdocs_yml,
    render_site,
    render_wiki,
)
from scripts.docs.manifest import parse_manifest


def _fixture(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "stylesheets").mkdir(parents=True)
    (docs / "javascripts").mkdir(parents=True)
    (docs / "index.md").write_text("# 1. aws-tui\n\nWelcome.\n")
    (docs / "architecture.md").write_text(
        "# 1. Architecture\n\n![arch](diagrams/img/architecture.png)\n\n"
        "See [keys](keybindings.md) and [repo](https://github.com/thekaveh/aws-tui/blob/main/x).\n"
    )
    (docs / "keybindings.md").write_text("# 1. Keybindings\n\nKeys.\n")
    (docs / "stylesheets" / "extra.css").write_text("/* theme */\n")
    (docs / "javascripts" / "mathjax.js").write_text("window.MathJax = {};\n")
    (docs / "diagrams" / "img").mkdir(parents=True)
    (docs / "diagrams" / "img" / "architecture.png").write_bytes(b"\x89PNG\r\n\x1a\nX")
    m = parse_manifest(
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
                  - { id: keybindings, title: Keybindings, source: docs/keybindings.md }
            diagrams: []
            """
        )
    )
    return m, tmp_path


def test_render_site_emits_pages_assets_and_rewrites(tmp_path):
    m, root = _fixture(tmp_path)
    out = root / "generated" / "site"
    render_site(m, root, out)
    assert (out / "index.md").is_file()
    assert (out / "architecture.md").is_file()
    assert (out / "stylesheets" / "extra.css").is_file()
    assert (out / "javascripts" / "mathjax.js").is_file()
    body = (out / "architecture.md").read_text()
    assert "assets/img/architecture.svg" in body  # image rewritten to SVG
    assert "[keys](keybindings.md)" in body  # internal .md kept
    assert "https://github.com/thekaveh/aws-tui/blob" not in body  # forbidden stripped


def test_render_wiki_emits_special_pages_and_images(tmp_path):
    m, root = _fixture(tmp_path)
    out = root / "generated" / "wiki"
    render_wiki(m, root, out)
    assert (out / "Home.md").is_file()
    assert (out / "Architecture.md").is_file()
    assert (out / "Keybindings.md").is_file()
    assert (out / "_Sidebar.md").is_file()
    assert (out / "_Footer.md").is_file()
    assert (out / "img" / "architecture.png").is_file()
    body = (out / "Architecture.md").read_text()
    assert "img/architecture.png" in body
    sidebar = (out / "_Sidebar.md").read_text()
    assert "[Overview](Home)" in sidebar
    assert "Development" in sidebar
    assert "[Architecture](Architecture)" in sidebar


def test_render_mkdocs_yml_has_nav_and_no_repo_url(tmp_path):
    m, _ = _fixture(tmp_path)
    text = render_mkdocs_yml(m)
    assert "repo_url" not in text
    assert "edit_uri" not in text
    assert "docs_dir: generated/site" in text
    assert "Overview: index.md" in text
    assert "Development:" in text
    assert "Architecture: architecture.md" in text


def test_build_check_is_deterministic(tmp_path):
    _m, root = _fixture(tmp_path)
    (root / "docs" / "manifest.yaml").write_text(_manifest_yaml())
    # build --check must not raise (idempotent regeneration).
    build(root / "docs" / "manifest.yaml", root, site=True, wiki=True, check=True)


def _manifest_yaml() -> str:
    return textwrap.dedent(
        """
        surfaces: [repo, site, wiki]
        numbering: per-doc
        sections:
          - { id: overview, title: Overview, source: docs/index.md }
          - id: dev
            title: Development
            children:
              - { id: architecture, title: Architecture, source: docs/architecture.md }
              - { id: keybindings, title: Keybindings, source: docs/keybindings.md }
        diagrams: []
        """
    )


def test_assert_dirs_equal_detects_difference(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("one")
    (b / "f.txt").write_text("two")
    with pytest.raises(AssertionError):
        _assert_dirs_equal(a, b)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_build_docs.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `scripts/docs/build_docs.py`**

```python
"""Render the generated site + wiki surfaces and the root mkdocs.yml."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

from scripts.docs.manifest import Manifest, Section, load_manifest
from scripts.docs.render_diagrams import copy_assets, extract_svg, render_all
from scripts.docs.transforms import (
    build_source_map,
    output_name,
    rewrite_for_surface,
    wiki_slug,
)

_IMG_RE = re.compile(r"(!\[[^\]]*\]\()\s*((?:\.\./)*)diagrams/img/([\w-]+)\.png(\))")


def _rewrite_images(md: str, surface: str) -> str:
    def repl(m: re.Match[str]) -> str:
        head, prefix, name, tail = m.groups()
        if surface == "site":
            return f"{head}{prefix}assets/img/{name}.svg{tail}"
        return f"{head}{prefix}img/{name}.png{tail}"  # wiki

    return _IMG_RE.sub(repl, md)


def render_site(manifest: Manifest, repo_root: str | Path, out_dir: str | Path) -> None:
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_map = build_source_map(manifest, "site")
    for leaf in manifest.leaves():
        assert leaf.source is not None
        md = (repo_root / leaf.source).read_text(encoding="utf-8")
        md = rewrite_for_surface(md, "site", source_map)
        md = _rewrite_images(md, "site")
        (out_dir / output_name(leaf, "site")).write_text(md, encoding="utf-8")
    # theme assets
    (out_dir / "stylesheets").mkdir(exist_ok=True)
    (out_dir / "javascripts").mkdir(exist_ok=True)
    shutil.copy2(repo_root / "docs" / "stylesheets" / "extra.css", out_dir / "stylesheets" / "extra.css")
    shutil.copy2(repo_root / "docs" / "javascripts" / "mathjax.js", out_dir / "javascripts" / "mathjax.js")
    # diagram SVGs
    img_dir = out_dir / "assets" / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    for d in manifest.diagrams:
        svg = extract_svg((repo_root / d.master).read_text(encoding="utf-8"))
        (img_dir / f"{d.id}.svg").write_text(svg, encoding="utf-8")


def render_wiki(manifest: Manifest, repo_root: str | Path, out_dir: str | Path) -> None:
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_map = build_source_map(manifest, "wiki")
    for leaf in manifest.leaves():
        assert leaf.source is not None
        md = (repo_root / leaf.source).read_text(encoding="utf-8")
        md = rewrite_for_surface(md, "wiki", source_map)
        md = _rewrite_images(md, "wiki")
        (out_dir / output_name(leaf, "wiki")).write_text(md, encoding="utf-8")
    (out_dir / "_Sidebar.md").write_text(_wiki_sidebar(manifest), encoding="utf-8")
    (out_dir / "_Footer.md").write_text(
        "aws-tui documentation — generated; do not edit here.\n", encoding="utf-8"
    )
    copy_assets(repo_root, out_dir / "img")


def _wiki_link_name(section: Section) -> str:
    return "Home" if section.id == "overview" else wiki_slug(section.title)


def _wiki_sidebar(manifest: Manifest) -> str:
    lines: list[str] = []
    for section in manifest.sections:
        if section.is_group:
            lines.append(f"**{section.title}**")
            lines.extend(
                f"  - [{child.title}]({_wiki_link_name(child)})" for child in section.children
            )
        else:
            lines.append(f"- [{section.title}]({_wiki_link_name(section)})")
    return "\n".join(lines) + "\n"


_MKDOCS_TEMPLATE = """\
site_name: aws-tui
site_url: https://thekaveh.github.io/aws-tui/
docs_dir: generated/site
site_dir: site
use_directory_urls: true
theme:
  name: material
  palette:
    - scheme: slate
      primary: cyan
      accent: cyan
      toggle:
        icon: material/weather-sunny
        name: Switch to light
    - scheme: default
      primary: cyan
      accent: cyan
      toggle:
        icon: material/weather-night
        name: Switch to dark
  font:
    text: Inter
    code: JetBrains Mono
  features:
    - navigation.sections
    - navigation.indexes
    - navigation.top
    - toc.follow
    - content.code.copy
    - content.code.annotate
    - header.autohide
extra_css:
  - stylesheets/extra.css
markdown_extensions:
  - admonition
  - attr_list
  - md_in_html
  - footnotes
  - def_list
  - pymdownx.superfences
  - pymdownx.highlight
  - pymdownx.inlinehilite
  - pymdownx.details
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.keys
  - pymdownx.arithmatex:
      generic: true
  - toc:
      permalink: true
extra_javascript:
  - javascripts/mathjax.js
  - https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js
nav:
{nav}"""


def _mkdocs_nav(manifest: Manifest) -> str:
    lines: list[str] = []
    for section in manifest.sections:
        if section.is_group:
            lines.append(f"  - {section.title}:")
            for child in section.children:
                lines.append(f"      - {child.title}: {output_name(child, 'site')}")
        else:
            lines.append(f"  - {section.title}: {output_name(section, 'site')}")
    return "\n".join(lines) + "\n"


def render_mkdocs_yml(manifest: Manifest) -> str:
    return _MKDOCS_TEMPLATE.format(nav=_mkdocs_nav(manifest))


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _assert_dirs_equal(a: str | Path, b: str | Path) -> None:
    ha, hb = _hash_tree(Path(a)), _hash_tree(Path(b))
    assert ha == hb, f"regeneration not deterministic:\n  {a}: {sorted(ha)}\n  {b}: {sorted(hb)}"


def build(
    path: str | Path,
    repo_root: str | Path,
    *,
    site: bool = False,
    wiki: bool = False,
    check: bool = False,
) -> None:
    repo_root = Path(repo_root)
    manifest = load_manifest(path, repo_root)
    generated = repo_root / "generated"
    # Refresh committed PNGs + site SVGs whenever there are diagrams.
    if manifest.diagrams:
        render_all(
            manifest,
            repo_root,
            generated / "site" / "assets" / "img",
            repo_root / "docs" / "diagrams" / "img",
        )
    if site or check:
        render_site(manifest, repo_root, generated / "site")
        (repo_root / "mkdocs.yml").write_text(render_mkdocs_yml(manifest), encoding="utf-8")
    if wiki or check:
        render_wiki(manifest, repo_root, generated / "wiki")
    if check:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            render_site(manifest, repo_root, tmp / "site")
            render_wiki(manifest, repo_root, tmp / "wiki")
            _assert_dirs_equal(tmp / "site", generated / "site")
            _assert_dirs_equal(tmp / "wiki", generated / "wiki")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_docs")
    parser.add_argument("--site", action="store_true")
    parser.add_argument("--wiki", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    build(
        repo_root / "docs" / "manifest.yaml",
        repo_root,
        site=args.site,
        wiki=args.wiki,
        check=args.check,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

Note on the determinism test: `render_site` copies `extra.css`/`mathjax.js` and writes deterministic text; `render_wiki` copies the committed PNG (a deterministic byte copy). The `_assert_dirs_equal` check compares only the `generated/site` + `generated/wiki` trees, so cairosvg PNG byte-stability is irrelevant to the gate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_build_docs.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/build_docs.py tests/docs/test_build_docs.py
git commit -m "feat(docs): render site + wiki + mkdocs.yml with determinism check"
```

---

## Task 7: `check_docs.py` — the CI gate

**Files:**
- Create: `scripts/docs/check_docs.py`
- Test: `tests/docs/test_check_docs.py`

**Interfaces:**
- Consumes: `manifest`, `links.find_links`, `links.is_forbidden`, `build_docs.build`.
- Produces:
  - `@dataclass(frozen=True) Finding(severity: str, message: str)`
  - `INTERNAL_DOCS: frozenset[str]` (repo-relative top-level docs excluded from completeness)
  - `check_self_containment(generated_root, repo_root) -> list[Finding]`
  - `check_completeness(manifest, repo_root) -> list[Finding]`
  - `check_placeholders(generated_root) -> list[Finding]`
  - `check_numbering(manifest, repo_root) -> list[Finding]`
  - `check(repo_root, generated_root) -> int` (0 clean, 1 findings; builds first)
  - CLI: `python -m scripts.docs.check_docs`

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_check_docs.py`:
```python
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
    (gen / "site" / "a.md").write_text(
        "[x](https://thekaveh.github.io/aws-tui/other/)\n"
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_check_docs.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `scripts/docs/check_docs.py`**

```python
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
                    Finding("error", f"{leaf.source}: H2 must be '## 1.x. <title>' (got {m.group(0).strip()})")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_check_docs.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/check_docs.py tests/docs/test_check_docs.py
git commit -m "feat(docs): check_docs CI gate (containment/completeness/placeholders/numbering)"
```

---

## Task 8: `push_wiki.py` — sync `generated/wiki/` → `aws-tui.wiki.git`

**Files:**
- Create: `scripts/docs/push_wiki.py`
- Test: `tests/docs/test_push_wiki.py`

**Interfaces:**
- Produces:
  - `DEFAULT_REMOTE = "git@github.com:thekaveh/aws-tui.wiki.git"`
  - `authenticated_remote(remote, key_path) -> str` (a `GIT_SSH_COMMAND` string)
  - `sync_wiki(src, repo_dir) -> None` (mirror src into repo_dir, preserving `.git`, deleting stale files)
  - `push_wiki(src, remote, key_path, *, push=False) -> None`
  - CLI: `python -m scripts.docs.push_wiki [--check|--push]` (reads `WIKI_DEPLOY_KEY` = path to key file, `WIKI_REMOTE` optional override)

- [ ] **Step 1: Write the failing tests**

Create `tests/docs/test_push_wiki.py`:
```python
import subprocess
from pathlib import Path

from scripts.docs.push_wiki import DEFAULT_REMOTE, authenticated_remote, sync_wiki


def test_default_remote_targets_wiki_git():
    assert DEFAULT_REMOTE == "git@github.com:thekaveh/aws-tui.wiki.git"


def test_authenticated_remote_uses_key_path():
    cmd = authenticated_remote(DEFAULT_REMOTE, "/home/runner/.ssh/wiki_key")
    assert "ssh" in cmd
    assert "/home/runner/.ssh/wiki_key" in cmd
    assert "IdentitiesOnly=yes" in cmd


def test_sync_wiki_preserves_git_and_removes_stale(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (dst / ".git").mkdir()
    (dst / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    (dst / "Stale.md").write_text("old\n")
    (src / "Home.md").write_text("new\n")
    sync_wiki(src, dst)
    assert (dst / "Home.md").read_text() == "new\n"
    assert not (dst / "Stale.md").exists()  # stale removed
    assert (dst / ".git" / "HEAD").is_file()  # .git preserved


def _git(repo: Path, *args: str, env=None):
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )


def test_push_wiki_commits_with_default_identity_when_unset(tmp_path, monkeypatch):
    # Isolate from the developer's global git identity.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_NAME", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)

    from scripts.docs.push_wiki import _commit_if_changed

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "Home.md").write_text("hello\n")
    _commit_if_changed(repo)  # must not raise "empty ident name not allowed"
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True
    )
    assert log.returncode == 0 and log.stdout.strip()  # a commit exists


def test_commit_if_changed_is_noop_when_clean(tmp_path):
    from scripts.docs.push_wiki import _commit_if_changed

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "-c", "user.name=x", "-c", "user.email=x@y.z", "commit", "-q", "--allow-empty", "-m", "base")
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    _commit_if_changed(repo)  # nothing staged → no new commit
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/docs/test_push_wiki.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `scripts/docs/push_wiki.py`**

```python
"""Sync the generated wiki tree to ``aws-tui.wiki.git`` (pushes ``master``)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_REMOTE = "git@github.com:thekaveh/aws-tui.wiki.git"

_DEFAULT_IDENT = {
    "GIT_AUTHOR_NAME": "aws-tui docs bot",
    "GIT_AUTHOR_EMAIL": "docs-bot@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "aws-tui docs bot",
    "GIT_COMMITTER_EMAIL": "docs-bot@users.noreply.github.com",
}


def authenticated_remote(remote: str, key_path: str | Path) -> str:
    return (
        f"ssh -i {key_path} -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=accept-new"
    )


def _env_with_ident() -> dict[str, str]:
    env = dict(os.environ)
    for key, value in _DEFAULT_IDENT.items():
        env.setdefault(key, value)
    return env


def sync_wiki(src: str | Path, repo_dir: str | Path) -> None:
    src = Path(src)
    repo_dir = Path(repo_dir)
    for existing in repo_dir.iterdir():
        if existing.name == ".git":
            continue
        if existing.is_dir():
            shutil.rmtree(existing)
        else:
            existing.unlink()
    for item in src.iterdir():
        target = repo_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _commit_if_changed(repo_dir: str | Path) -> None:
    repo_dir = Path(repo_dir)
    env = _env_with_ident()
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, env=env)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo_dir, env=env
    )
    if staged.returncode == 0:
        return  # nothing staged — no-op
    subprocess.run(
        ["git", "commit", "-m", "docs: sync generated wiki"],
        cwd=repo_dir,
        check=True,
        env=env,
    )


def push_wiki(
    src: str | Path,
    remote: str,
    key_path: str | Path,
    *,
    push: bool = False,
) -> None:
    src = Path(src)
    if not push:
        # --check: validate we can init a repo and sync into it (no network).
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", "-b", "master", tmp], check=True)
            sync_wiki(src, tmp)
        return
    env = _env_with_ident()
    env["GIT_SSH_COMMAND"] = authenticated_remote(remote, key_path)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", remote, tmp],
            check=True,
            env=env,
        )
        sync_wiki(src, tmp)
        _commit_if_changed(tmp)
        subprocess.run(["git", "push", remote, "master"], cwd=tmp, check=True, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="push_wiki")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    remote = os.environ.get("WIKI_REMOTE", DEFAULT_REMOTE)
    key_path = os.environ.get("WIKI_DEPLOY_KEY", "")
    push_wiki(repo_root / "generated" / "wiki", remote, key_path, push=args.push)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/docs/test_push_wiki.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/docs/push_wiki.py tests/docs/test_push_wiki.py
git commit -m "feat(docs): push_wiki sync to aws-tui.wiki.git (master)"
```

---

## Task 9: Canonical content — index, manifest, theme assets, architecture diagram

**Files:**
- Create: `docs/index.md`
- Create: `docs/stylesheets/extra.css`
- Create: `docs/javascripts/mathjax.js`
- Create: `docs/diagrams/architecture.html` (via the architecture-diagram skill)
- Create: `docs/diagrams/img/architecture.png` (committed; produced by `render_diagrams`)
- Create: `docs/manifest.yaml`
- Modify: `docs/architecture.md` (add the diagram embed)

**Interfaces:**
- Consumes: all modules from Tasks 2–8.
- Produces: the canonical source the pipeline renders; `check_numbering` requires `docs/index.md` to use `# 1.` / `## 1.x`.

- [ ] **Step 1: Write `docs/index.md`** (Overview landing; per-doc numbering)

```markdown
# 1. aws-tui

Cross-platform TUI for AWS and S3-compatible services — a
Norton-Commander–style dual-pane file manager for S3 plus an EMR
Serverless console, built on [Textual](https://textual.textualize.io/)
and the VMx MVVM framework.

## 1.1. What it does

- **Dual-pane S3 ⇄ local file management** — copy, delete, and multi-select
  across an S3 (or S3-compatible) source and your local filesystem.
- **One-key source switching** across every configured AWS profile and
  S3-compatible connection.
- **EMR Serverless console** — application picker, job-runs master-detail
  with state-filter chips, and on-demand log streaming with a grep filter.
- **Themable, keyboard-driven** — built-in themes and fully customizable
  keybindings.

## 1.2. Where to start

- New here? Start with **Installation**, then **Platforms** and
  **Connections**.
- Daily use: **Keybindings**, the **Cookbook**, and **Theming**.
- Contributing or extending: **Architecture**, **Adding a Service**, and
  the **Contract Ledger**.
```

- [ ] **Step 2: Write `docs/stylesheets/extra.css`**

```css
/* aws-tui docs — Material theme overrides. */
:root {
  --md-primary-fg-color: #22d3ee;
  --md-accent-fg-color: #22d3ee;
}
[data-md-color-scheme="slate"] {
  --md-default-bg-color: #0b0f14;
  --md-code-bg-color: #11161d;
}
.md-typeset table:not([class]) {
  font-size: 0.72rem;
}
```

- [ ] **Step 3: Write `docs/javascripts/mathjax.js`**

```javascript
window.MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"]],
    displayMath: [["\\[", "\\]"]],
  },
};
```

- [ ] **Step 4: Author the architecture diagram master**

Invoke the **architecture-diagram skill** to create `docs/diagrams/architecture.html` — a dark-themed standalone HTML file with an inline `<svg>`. Depict aws-tui's layered MVVM architecture to match `docs/architecture.md §1.1 Layers`:
- Three stacked layers, top→bottom: **View** (Textual widgets/panes) → **ViewModel** (VMx VMs, bindings) → **Domain** (Service protocol, AWS/S3 clients).
- A **Composition Root** box wiring the layers (per §1.2).
- A left→right **Lifecycle** arrow (startup → mount → dispose) per §1.3.
- Dark background, cyan (`#22d3ee`) accents to match the site theme.

Requirements the renderer depends on: the file MUST contain a single inline `<svg>…</svg>` block (no external `<img>`/CSS refs inside it); any HTML named entities inside the SVG (e.g. `&middot;`) are fine — `extract_svg` sanitizes them.

- [ ] **Step 5: Write `docs/manifest.yaml`**

```yaml
# Single source of truth for the three-surface docs: hierarchy + nav order +
# published page set. Humans edit ONLY the canonical docs/*.md + this file;
# generated/site + generated/wiki + mkdocs.yml are regenerated and gitignored.
surfaces: [repo, site, wiki]
numbering: per-doc
sections:
  - { id: overview, title: Overview, source: docs/index.md }
  - id: getting-started
    title: Getting Started
    children:
      - { id: install, title: Installation, source: docs/homebrew-bootstrap.md }
      - { id: platforms, title: Platforms, source: docs/platforms.md }
      - { id: connections, title: Connections, source: docs/connections.md }
  - id: using
    title: Using aws-tui
    children:
      - { id: keybindings, title: Keybindings, source: docs/keybindings.md }
      - { id: cookbook, title: Cookbook, source: docs/cookbook.md }
      - { id: theming, title: Theming, source: docs/theming.md }
  - id: development
    title: Development
    children:
      - { id: architecture, title: Architecture, source: docs/architecture.md, diagrams: [architecture] }
      - { id: adding-service, title: Adding a Service, source: docs/adding-a-service.md }
      - { id: contract-ledger, title: Contract Ledger, source: docs/contract-ledger.md }
  - { id: releasing, title: Releasing, source: docs/RELEASING.md }
diagrams:
  - { id: architecture, master: docs/diagrams/architecture.html }
```

- [ ] **Step 6: Add the diagram embed to `docs/architecture.md`**

Insert, immediately after the `# 1. Architecture` H1 line (before the first paragraph), a blank line then:
```markdown
![aws-tui layered MVVM architecture — View over ViewModel over Domain, wired by a composition root, with a startup→mount→dispose lifecycle.](diagrams/img/architecture.png)
```
This canonical PNG path renders in-repo on GitHub; the surface renderers rewrite it to `assets/img/architecture.svg` (site) and `img/architecture.png` (wiki).

- [ ] **Step 7: Generate the committed PNG + verify the manifest loads**

The Makefile doesn't exist yet (Task 10), so render with an OS-aware invocation (see Global Constraints → macOS cairo exception). Run:
```bash
# Render the architecture diagram → SVG (site) + committed PNG.
if [ "$(uname -s)" = "Darwin" ]; then
  DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix cairo)/lib" ./.venv/bin/python -m scripts.docs.render_diagrams
else
  uv run python -m scripts.docs.render_diagrams
fi
uv run python -c "from pathlib import Path; from scripts.docs.manifest import load_manifest; load_manifest(Path('docs/manifest.yaml'), Path('.')); print('manifest OK')"
ls -l docs/diagrams/img/architecture.png
```
Expected: `manifest OK`; a PNG file with non-zero size exists.

- [ ] **Step 8: Commit**

```bash
git add docs/index.md docs/stylesheets docs/javascripts docs/diagrams/architecture.html docs/diagrams/img/architecture.png docs/manifest.yaml docs/architecture.md
git commit -m "docs: canonical index + manifest + theme assets + architecture diagram"
```

---

## Task 10: `Makefile` — docs targets (uv-native)

**Files:**
- Create: `Makefile`
- Test: manual (target invocation)

**Interfaces:**
- Consumes: all `scripts.docs.*` CLIs.
- Produces: `make docs-build`, `docs-serve`, `docs-check`, `docs-wiki`.

- [ ] **Step 1: Write `Makefile`**

```makefile
# cairosvg needs libcairo. On macOS, Homebrew installs it OUTSIDE the default
# dyld search path AND `uv run` drops DYLD_* (SIP strips it across the exec
# chain), so the docs render fails under `uv run`. Work around it by calling the
# venv python directly with DYLD_FALLBACK_LIBRARY_PATH pointed at Homebrew's
# cairo. On Linux (incl. CI), apt's libcairo2 is on a standard path and
# `uv run python` works. Run `uv sync --group docs` first so `.venv` exists.
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  CAIRO_PREFIX := $(shell brew --prefix cairo 2>/dev/null)
  DOCS_PY := DYLD_FALLBACK_LIBRARY_PATH=$(CAIRO_PREFIX)/lib ./.venv/bin/python
else
  DOCS_PY := uv run python
endif

.PHONY: help docs-diagrams docs-build docs-serve docs-check docs-wiki

help:
	@echo "docs-diagrams  render diagram masters -> SVG (site) + PNG (committed)"
	@echo "docs-build     render diagrams + site, then mkdocs --strict"
	@echo "docs-serve     render diagrams + site, then mkdocs serve"
	@echo "docs-check     render diagrams + check_docs + mkdocs --strict"
	@echo "docs-wiki      render wiki + push_wiki --check (no network)"

docs-diagrams:
	$(DOCS_PY) -m scripts.docs.render_diagrams

docs-build:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.build_docs --site
	uv run mkdocs build --strict

docs-serve:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.build_docs --site
	uv run mkdocs serve

docs-check:
	$(DOCS_PY) -m scripts.docs.render_diagrams
	$(DOCS_PY) -m scripts.docs.check_docs
	uv run mkdocs build --strict

docs-wiki:
	$(DOCS_PY) -m scripts.docs.build_docs --wiki
	$(DOCS_PY) -m scripts.docs.push_wiki --check
```

- [ ] **Step 2: Verify targets run**

Run:
```bash
make docs-build
make docs-check
make docs-wiki
```
Expected: `docs-build` produces `site/` (mkdocs output) with 0 strict warnings; `docs-check` prints `check_docs: clean` and 0 mkdocs warnings; `docs-wiki` exits 0.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build(docs): Makefile targets for build/serve/check/wiki"
```

---

## Task 11: CI workflows — `docs.yml` (gate) + `pages.yml` (publish)

**Files:**
- Create: `.github/workflows/docs.yml`
- Create: `.github/workflows/pages.yml`
- Test: `actionlint` (if available) + manual YAML review

**Interfaces:**
- Produces: a PR gate on `[main, develop]`; a `main`-triggered publish of Pages + wiki.

Pin actions to the SHAs already used in `.github/workflows/ci.yml` where the same action appears (e.g. `actions/checkout` is pinned there as `34e114876b0b11c390a56381ad16ebd13914f8d5 # v4`). For actions not present in `ci.yml` (`setup-python`, `configure-pages`, `upload-pages-artifact`, `deploy-pages`), pin to a current release SHA and add the `# vX` comment.

- [ ] **Step 1: Write `.github/workflows/docs.yml`**

```yaml
name: docs
on:
  push:
    branches: [main, develop]
    paths:
      - "docs/**"
      - "scripts/docs/**"
      - "Makefile"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/docs.yml"
  pull_request:
    branches: [main, develop]
    paths:
      - "docs/**"
      - "scripts/docs/**"
      - "Makefile"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/docs.yml"

jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - name: install libcairo2
        run: sudo apt-get update && sudo apt-get install -y libcairo2
      - uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78 # v7
      - name: install Python 3.12
        run: uv python install 3.12
      - name: sync (docs group)
        run: uv sync --frozen --group docs
      - name: docs check (render + check_docs + mkdocs --strict)
        run: make docs-check
      - name: ruff (docs scripts)
        run: uv run ruff check scripts/docs/
      - name: pytest (docs scripts)
        run: uv run pytest tests/docs -v
```

- [ ] **Step 2: Write `.github/workflows/pages.yml`**

```yaml
name: pages
on:
  push:
    branches: [main]
    paths:
      - "docs/**"
      - "scripts/docs/**"
      - "Makefile"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/pages.yml"

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - name: install libcairo2
        run: sudo apt-get update && sudo apt-get install -y libcairo2
      - uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78 # v7
      - run: uv python install 3.12
      - run: uv sync --frozen --group docs
      - run: uv run python -m scripts.docs.render_diagrams
      - run: uv run python -m scripts.docs.build_docs --site
      - run: uv run mkdocs build --strict
      - uses: actions/configure-pages@983d7736d9b0ae728b81ab479565c72886d7745b # v5
      - uses: actions/upload-pages-artifact@56afc609e74202658d3ffba0e8f6dda462b719fa # v3
        with:
          path: site

  deploy:
    needs: build
    runs-on: ubuntu-24.04
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4

  wiki:
    needs: deploy
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - name: install libcairo2
        run: sudo apt-get update && sudo apt-get install -y libcairo2
      - uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78 # v7
      - run: uv python install 3.12
      - run: uv sync --frozen --group docs
      - name: write wiki deploy key
        run: |
          mkdir -p ~/.ssh
          printf '%s\n' "${{ secrets.WIKI_DEPLOY_KEY }}" > ~/.ssh/wiki_key
          chmod 600 ~/.ssh/wiki_key
      - name: build + push wiki
        env:
          WIKI_DEPLOY_KEY: /home/runner/.ssh/wiki_key
        run: |
          uv run python -m scripts.docs.build_docs --wiki
          uv run python -m scripts.docs.push_wiki --push
```

- [ ] **Step 3: Validate the workflow YAML**

Run (if `actionlint` is available; otherwise skip):
```bash
actionlint .github/workflows/docs.yml .github/workflows/pages.yml || echo "actionlint not installed — review YAML manually"
uv run python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('workflows parse OK')"
```
Expected: `workflows parse OK`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/docs.yml .github/workflows/pages.yml
git commit -m "ci(docs): PR gate (docs.yml) + Pages/wiki publish (pages.yml)"
```

---

## Task 12: Full integration — green end-to-end locally

**Files:**
- Modify: none (verification + fixups only)
- Test: full suite

**Interfaces:**
- Consumes: everything from Tasks 1–11.
- Produces: a fully green local three-surface build; generated trees confirmed gitignored.

- [ ] **Step 1: Run the full docs gate**

Run:
```bash
make docs-check
```
Expected: `check_docs: clean` and `mkdocs build --strict` with **0 warnings**. If mkdocs reports broken internal links, fix the offending canonical link (make it a valid relative `.md` link to a published doc, or bare text) and re-run.

- [ ] **Step 2: Confirm generated trees + mkdocs.yml are gitignored**

Run:
```bash
git status --porcelain
git check-ignore generated/site/index.md mkdocs.yml site || true
```
Expected: `git status` shows **no** `generated/`, `mkdocs.yml`, or `site/` entries; `git check-ignore` echoes all three paths (they are ignored).

- [ ] **Step 3: Run the docs unit tests + pre-commit on new files**

Run:
```bash
uv run pytest tests/docs -v
uv run ruff check scripts/docs/
uv run pre-commit run --files $(git ls-files 'scripts/docs/*.py' 'tests/docs/*.py')
```
Expected: all tests PASS; ruff clean; pre-commit hooks pass (fix any ruff-format diffs it applies, then re-stage).

- [ ] **Step 4: Spot-check rendered content across surfaces**

Run:
```bash
uv run python -m scripts.docs.build_docs --site --wiki
grep -q "assets/img/architecture.svg" generated/site/architecture.md && echo "site img OK"
grep -q "img/architecture.png" generated/wiki/Architecture.md && echo "wiki img OK"
test -f generated/wiki/Home.md && test -f generated/wiki/_Sidebar.md && echo "wiki special pages OK"
grep -rL "github.com/thekaveh/aws-tui/blob" generated >/dev/null && echo "no forbidden repo-source links"
```
Expected: `site img OK`, `wiki img OK`, `wiki special pages OK`, `no forbidden repo-source links`.

- [ ] **Step 5: Confirm the full repo suite still passes**

Run:
```bash
uv run pytest tests/unit tests/integration -q
```
Expected: PASS (the docs additions don't touch `aws_tui`). If `pythonpath` changes disturbed collection, verify `pyproject.toml` has `pythonpath = ["src", "."]`.

- [ ] **Step 6: Final integration commit (if any fixups were made)**

```bash
git add -A
git commit -m "docs: integrate three-surface pipeline — green end-to-end" || echo "nothing to commit"
```

---

## Task 13: Phase 2 runbook — external/publish steps (GATED; not run during build)

**Files:**
- Create: `docs/superpowers/notes/2026-07-10-three-surface-docs-phase2-runbook.md`

**Interfaces:**
- Produces: a checklist the maintainer executes (with explicit approval) after the pipeline merges. **Do not perform these during implementation** — they enable public surfaces and register a deploy key.

- [ ] **Step 1: Write the runbook**

Create `docs/superpowers/notes/2026-07-10-three-surface-docs-phase2-runbook.md`:
```markdown
# Three-surface docs — Phase 2 publish runbook

Run these AFTER the pipeline is merged to `main`. Each step is outward-facing;
get explicit go-ahead before running it.

## 1. Enable repo features
- Settings → Features → **Wikis: ON** (else pushes 403 with a misleading auth error).
- Settings → Pages → Source: **GitHub Actions**.

## 2. Deploy key + secret (wiki push)
```bash
ssh-keygen -t ed25519 -f /tmp/wiki-key -N "" -C "aws-tui-wiki-sync"
gh repo deploy-key add /tmp/wiki-key.pub --title "aws-tui wiki sync (CI)" --allow-write
gh secret set WIKI_DEPLOY_KEY < /tmp/wiki-key          # secret holds the key CONTENT
# local verify (optional) before deleting:
WIKI_DEPLOY_KEY=/tmp/wiki-key uv run python -m scripts.docs.build_docs --wiki
WIKI_DEPLOY_KEY=/tmp/wiki-key uv run python -m scripts.docs.push_wiki --push
rm /tmp/wiki-key /tmp/wiki-key.pub
```

## 3. First publish
- Merge `develop → main`. `pages.yml` builds + deploys Pages, then the `wiki`
  job pushes `generated/wiki/` to `aws-tui.wiki.git` (**master**).

## 4. Verify all three surfaces
```bash
curl -sSfo /dev/null -w '%{http_code}\n' https://thekaveh.github.io/aws-tui/      # 200
curl -sSfo /dev/null -w '%{http_code}\n' https://github.com/thekaveh/aws-tui/wiki  # 200
# in-repo: browse docs/*.md on GitHub (canonical, always current)
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/notes/2026-07-10-three-surface-docs-phase2-runbook.md
git commit -m "docs: Phase 2 publish runbook (gated external steps)"
```

---

## Self-Review (completed against the spec)

**Spec coverage:** §1 goal → whole plan. §2 decisions → Task 1 (uv/gitignore), Task 9 (numbering-preserving index/manifest; one diagram; page set), all-three-surfaces (Tasks 6/8/11). §3 architecture → Tasks 6/12. §4 layout → Task 9. §5 manifest → Task 9 Step 5 + Task 2. §6 modules → Tasks 2–8. §7 diagram → Task 9 Step 4/6. §8 tooling → Tasks 1/10. §9 CI → Task 11. §10 tests → each module task's test step. §11 Phase 2 → Task 13. §12 verification → Task 12. §13 gotchas → Global Constraints + inline.

**Placeholder scan:** No "TBD/TODO/implement later" as instructions. (`TODO` appears only as test *data* for `check_placeholders` and as the `recording-todo.md` filename — intentional.) Every code step shows complete code.

**Type consistency:** `output_name`/`wiki_slug`/`build_source_map` (Task 4) are the exact names consumed by `build_docs` (Task 6). `render_all`/`extract_svg`/`copy_assets` (Task 5) match `build_docs` imports. `build(...)` signature (Task 6) matches the call in `check_docs.check` and both CLIs. `_commit_if_changed` (Task 8) is referenced by its own tests. `Manifest.leaves()`/`Section.is_group`/`Section.diagrams` (Task 2) are used consistently in Tasks 4/6/7.

**Gaps:** none identified.
