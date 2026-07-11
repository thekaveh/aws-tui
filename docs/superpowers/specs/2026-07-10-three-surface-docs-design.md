# Three-surface documentation for aws-tui — design

**Date:** 2026-07-10
**Status:** Approved (design); implementation pending
**Skill:** `three-surface-docs` (adapted — no notebook subsystem)

## 1. Goal

Project **one canonical documentation source** (the repo's own `docs/*.md`)
into **three self-contained surfaces** that stay in sync *by construction*:

1. **In-repo markdown** — GitHub renders `docs/*.md` directly (no generation).
2. **`.io` site** — MkDocs Material, published to GitHub Pages.
3. **GitHub wiki** — generated pages pushed to `aws-tui.wiki.git`.

Humans edit **only** the canonical source. The site and wiki are **generated
and gitignored**, so drift is impossible — there is no manual sync step. No
surface links to another (self-containment is absolute).

Non-goal: the skill's notebook subsystem (Zeppelin/Scala/PySpark, `notebooks.py`,
per-notebook `spec.yaml`) is **out of scope** — aws-tui has no notebooks.

## 2. Scope decisions (locked)

| Decision | Choice |
| --- | --- |
| Surfaces | **All three** (in-repo + Pages site + wiki) |
| Rollout | **Build + commit the pipeline first**; external/publish steps gated (Phase 2) |
| Numbering | **Keep per-doc numbering**; titles-only nav; numbering probe checks *internal* per-doc consistency |
| Diagrams | **One** architecture diagram (full master→SVG/PNG path) |
| Page set | Publish all `docs/*.md` **except** `recording-todo.md` + `superpowers/**` |
| Tooling | **`uv`-native** — `[dependency-groups] docs`, `uv run` everywhere (not pip/`docs-requirements.txt`) |

## 3. Architecture (the shape)

```
CANONICAL (committed)                    GENERATED (gitignored)          SURFACE
docs/*.md (11 pages + new index.md) ─┐
docs/manifest.yaml (nav+order+set)  ─┼─ build_docs ─▶ generated/site/  ─▶ .io site (Pages)
docs/diagrams/architecture.html     ─┤              ─▶ generated/wiki/  ─▶ GitHub wiki
docs/diagrams/img/architecture.png ──┘              ─▶ mkdocs.yml       ─▶ (site config)
   (committed PNG)                                    ─▶ site/ (build)    ─▶ (Pages artifact)
in-repo surface = canonical docs/*.md rendered by GitHub directly (no generation step)
```

**Self-containment matrix (absolute):** site ✗ repo/wiki links; wiki ✗ repo/site
links; repo ✗ site/wiki links. `README.md` is gated too. MkDocs gets **no**
`repo_url` / `repo_name` / `edit_uri`. In-repo README may link to repo files but
gains **no** links to the site or wiki.

## 4. Canonical layout

New files added under `docs/`:

```
docs/
  index.md                         # NEW — Overview / landing page
  manifest.yaml                    # NEW — single source of hierarchy + order + page set
  diagrams/
    architecture.html              # NEW — dark-theme master (architecture-diagram skill)
    img/architecture.png           # NEW — committed PNG export (in-repo + wiki image)
  stylesheets/extra.css            # NEW — Material theme overrides (copied into site)
  javascripts/mathjax.js           # NEW — MathJax config (copied into site)
  architecture.md … theming.md     # EXISTING — unchanged headings (architecture.md gains the diagram embed)
```

Generated / gitignored (never committed): `generated/site/`, `generated/wiki/`,
root `mkdocs.yml`, `site/`.

Existing doc headings are **not renumbered** — every doc keeps its per-file
`# 1. Title` / `## 1.x` scheme.

## 5. The manifest (`docs/manifest.yaml`)

Titles-only nav: `title:` is the nav/sidebar label (no leading number). Manifest
order drives nav order. A section is **either** a `source` leaf **or** a
`children` group — never both (gotcha #14).

```yaml
surfaces: [repo, site, wiki]
numbering: per-doc            # per-file 1.x preserved; probe checks internal consistency
sections:
  - { id: overview, title: Overview, source: docs/index.md }
  - id: getting-started
    title: Getting Started
    children:
      - { id: install,     title: Installation, source: docs/homebrew-bootstrap.md }
      - { id: platforms,   title: Platforms,    source: docs/platforms.md }
      - { id: connections, title: Connections,  source: docs/connections.md }
  - id: using
    title: Using aws-tui
    children:
      - { id: keybindings, title: Keybindings, source: docs/keybindings.md }
      - { id: cookbook,    title: Cookbook,    source: docs/cookbook.md }
      - { id: theming,     title: Theming,     source: docs/theming.md }
  - id: development
    title: Development
    children:
      - { id: architecture,    title: Architecture,     source: docs/architecture.md, diagrams: [architecture] }
      - { id: adding-service,  title: Adding a Service, source: docs/adding-a-service.md }
      - { id: contract-ledger, title: Contract Ledger,  source: docs/contract-ledger.md }
  - { id: releasing, title: Releasing, source: docs/RELEASING.md }
diagrams:
  - { id: architecture, master: docs/diagrams/architecture.html }
```

`load_manifest(path, repo_root)` validates every `source`/`master` exists →
`ManifestError`.

**Internal-only docs** (excluded from the manifest, intentionally in-repo only):
`docs/recording-todo.md`, `docs/superpowers/**`. `check_docs` knows this set and
does **not** flag them as unreferenced.

## 6. Pipeline (`scripts/docs/`)

A Python package + unit tests, one job each. **No `notebooks.py`.**

- **`__init__.py`** — package marker.
- **`manifest.py`** — `ManifestError`; dataclasses `Manifest / Section / DiagramEntry`;
  `parse_manifest(text)` (wraps `yaml.YAMLError`/`KeyError` → `ManifestError`);
  `load_manifest(path, repo_root)` (validates referenced files exist). Supports
  leaf sections and children-groups.
- **`links.py`** — `Link(target)`, `find_links(md)`, `is_forbidden(target, surface) -> bool`.
  Constants `REPO_URL` (`https://github.com/thekaveh/aws-tui`), `WIKI_URL`
  (`…/aws-tui/wiki`), `SITE_URL` (`https://thekaveh.github.io/aws-tui/`). Guard so
  a wiki link (which contains `REPO_URL` as a substring) isn't misclassified as a
  repo link.
- **`transforms.py`** — `build_source_map(manifest, surface) -> dict[canonical, output]`
  (overview → `Home.md` on wiki / `index.md` on site); `rewrite_for_surface(md, surface, source_map)`
  — forbidden links → bare text; `.ipynb` → bare text; mapped `.md` → rewritten;
  other relative `.md` (non-manifest) → bare text; absolute / `../`-prefixed otherwise.
- **`render_diagrams.py`** — `extract_svg(html)` (regex `<svg[\s\S]*?</svg>`;
  sanitize non-XML named entities `&(?!amp;|lt;|gt;|quot;|apos;|#)[a-zA-Z]+;` →
  unicode via `html.unescape`); `svg_to_png(svg, out, *, width)` (lazy `import cairosvg`);
  `render_all(manifest, repo_root, site_img_dir, png_dir)` — writes `<id>.svg` to
  `generated/site/assets/img` (gitignored) **and** `<id>.png` to `docs/diagrams/img`
  (**committed**); `copy_assets(repo_root, wiki_dir)` copies committed PNGs into the
  wiki `img/`. CLI: `python -m scripts.docs.render_diagrams`.
- **`build_docs.py`** — `render_site` (emit pages via `rewrite_for_surface` +
  image-rewrite that **preserves `../`**, copy `extra.css` + `mathjax.js`, write
  SVGs from masters); `render_wiki` (emit `Home.md`, `_Sidebar.md`, `_Footer.md`
  — underscore prefix REQUIRED; image → `img/`; copy PNGs); `render_mkdocs_yml`
  (Python string template, single `{nav}` placeholder; `site_name`/`site_url`/theme
  are static literals; nested nav from the manifest groups); `build(path, repo_root, *, site, wiki, check)`;
  `_assert_dirs_equal(a, b)` — content-hash (sha256) determinism check. CLI:
  `--site` / `--wiki` / `--check`.
- **`check_docs.py`** — `Finding(severity, message)`; the CI gate:
  - `check_self_containment(generated_root)` — scan `generated/{site,wiki}` **and**
    `README.md` with `is_forbidden`.
  - `check_completeness` — every published `docs/*.md` is referenced by a manifest
    section (the **internal-only** set `{recording-todo.md, superpowers/**}` is
    excluded, not flagged).
  - `check_placeholders` — scan the **generated output** (site + wiki) + README for
    `TODO|TBD|FIXME|XXX` (scanning generated output means internal TODO docs never
    trip it).
  - `check_numbering` — each published doc's H1 is `# 1. …` and its H2s are `## 1.x …`
    (per-doc internal consistency; adapted from the skill's global cross-check).
  - `check(repo_root, generated_root)` — runs `build --check` then all probes; exit 1
    on any error. CLI.
- **`push_wiki.py`** — `DEFAULT_REMOTE = "git@github.com:thekaveh/aws-tui.wiki.git"`
  (override via `WIKI_REMOTE`); `authenticated_remote(remote, key_path)`;
  `sync_wiki(src, repo_dir)` (copy, preserve `.git`, remove stale);
  `push_wiki(src, remote, key_path, *, push)` (`--check` = `git init`; `--push` =
  clone + sync + commit-if-changed + **push `master`**); default git ident via
  `env.setdefault("GIT_AUTHOR_NAME"/…)`. CLI reads `WIKI_DEPLOY_KEY` (a **path** to
  the key file) + `WIKI_REMOTE`.

## 7. The one diagram

`docs/diagrams/architecture.html` is authored via the **architecture-diagram
skill** (dark theme, inline `<svg>`) depicting aws-tui's layered VMx/Textual
architecture (Domain → ViewModel → View, composition root, lifecycle) to match
`docs/architecture.md`. `render_diagrams` extracts the inline SVG → crisp SVG for
the site + a rasterized PNG committed at `docs/diagrams/img/architecture.png` for
in-repo + wiki. `docs/architecture.md` embeds the PNG with a canonical relative
path (`diagrams/img/architecture.png`); the surface renderers rewrite it
(site → `assets/img/architecture.svg`, wiki → `img/architecture.png`).

## 8. Tooling integration (`uv`-native)

- `pyproject.toml`: add `[dependency-groups] docs = ["mkdocs-material>=9.6,<10.0",
  "pyyaml>=6.0,<7.0", "cairosvg>=2.7,<3.0"]`. (ruff + pytest already present.)
- `pyproject.toml` `[tool.pytest.ini_options]`: `pythonpath = ["src", "."]` (add
  `"."` so `from scripts.docs.* import …` resolves).
- New `Makefile`:
  ```makefile
  PYTHON ?= uv run python
  docs-build:  # render diagrams → build site → mkdocs --strict
  docs-serve:  # render → build site → mkdocs serve
  docs-check:  # render → check_docs → mkdocs --strict
  docs-wiki:   # build wiki → push_wiki --check
  ```
  All script invocations use `-m scripts.docs.*` (gotcha #4). `.PHONY` + `help`.
- `.gitignore`: add `/generated/`, `/mkdocs.yml`, `/site/`.
- ruff already lints `scripts/**`; the new modules must be clean.

## 9. CI (matches existing conventions: `uv`, pinned action SHAs)

- **`.github/workflows/docs.yml`** — PR gate. Triggers on `push` **and**
  `pull_request` to **`[main, develop]`** (both — gotcha #22), paths-filtered to
  `docs/**`, `scripts/docs/**`, `Makefile`, `pyproject.toml`, `uv.lock`,
  `.github/workflows/docs.yml`. Steps: `apt-get install -y libcairo2` (before deps),
  `uv sync --group docs`, `make docs-check`, `ruff check scripts/docs/`,
  `uv run pytest tests/docs -v`.
- **`.github/workflows/pages.yml`** — publish, on `push: [main]`. `permissions:
  contents: read / pages: write / id-token: write`. `build` job: libcairo2 + uv
  sync → `render_diagrams` → `build_docs --site` → `mkdocs build --strict` →
  upload-pages-artifact. `deploy` job: `deploy-pages` (environment `github-pages`).
  `wiki` job (`needs: deploy`, `if: ref == main`): write `WIKI_DEPLOY_KEY` secret to
  `~/.ssh/wiki_key`, `build_docs --wiki`, `push_wiki --push`.

## 10. Tests (`tests/docs/`, one file per module, TDD)

`test_manifest` (parse/validate/`ManifestError`, leaf-vs-group), `test_links`
(3×3 matrix + wiki-contains-repo-substring), `test_transforms` (source-map site/wiki
overview; rewrite forbidden/`.ipynb`/non-manifest-`.md`/`../`-prefix),
`test_render_diagrams` (`importorskip("cairosvg")`; extract+sanitize; svg_to_png PNG
magic; render_all writes both svg+png), `test_build_docs` (render_site pages/assets/
image-prefix; render_mkdocs_yml no `repo_url` + nested nav; `--check` content-hash
determinism), `test_check_docs` (each probe incl. internal-doc exclusion + numbering),
`test_push_wiki` (authenticated_remote; sync_wiki preserves `.git`; no-op-commit
guard; commits with default ident when unset — isolate via `GIT_CONFIG_GLOBAL=/dev/null`
+ `GIT_CONFIG_NOSYSTEM=1`).

Tests run under the existing `uv run pytest`. Because `pyproject` `testpaths =
["tests"]`, `tests/docs/` is collected automatically. The docs tests are **not**
`aws_tui` unit tests, so they must not require the app import path — they import
`scripts.docs.*` (enabled by `pythonpath = ["src", "."]`).

## 11. Phase 2 — external / publish steps (each gated on explicit approval)

Performed **after** the pipeline is committed and green locally. Not part of the
build phase.

1. Repo **Settings → Features → Wikis: ON** (else pushes 403 with a misleading
   auth error).
2. Enable **GitHub Pages** (source: GitHub Actions).
3. Generate an ed25519 **write deploy key**; `gh repo deploy-key add … --allow-write`;
   `gh secret set WIKI_DEPLOY_KEY` (secret holds key **content**; CI writes it to a
   file and points the env var at the **path**).
4. First `push_wiki --push` bootstraps `aws-tui.wiki.git` (pushes **`master`**).
5. A `develop → main` merge triggers `pages.yml` → publishes site + wiki.

## 12. Verification (before merge)

- `make docs-check` → render diagrams + `check_docs` + `mkdocs build --strict`
  (0 warnings — catches broken internal links).
- `uv run pytest tests/docs -v` (docs-script unit tests) + `uv run pre-commit run
  --all-files` (ruff/ruff-format/existing gates on the new files).
- After Phase 2: `curl` the `.io` site (HTTP 200) and the wiki; `grep` the generated
  trees for expected content.

## 13. Gotchas carried forward (from the skill)

`master` for the wiki (#1); CI git-identity default (#2); `libcairo2` in CI (#3);
invoke as `python -m scripts.docs.*` (#4); sanitize non-XML SVG entities (#5); image
rewrite keeps `../` prefix (#6); absolute self-containment / no `repo_url` (#7);
in-repo links relative (#8); a section is leaf **or** group (#14); copy diagrams to
every surface — never cross-link (#11, #20); README stays user-facing — no MkDocs/wiki
mechanics (#21, already satisfied); CI triggers cover `main` **and** `develop` (#22);
generated trees + `mkdocs.yml` gitignored, determinism-checked (#10).
