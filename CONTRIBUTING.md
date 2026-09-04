# 1. Contributing to aws-tui

Thanks for your interest. aws-tui is pre-release; the API and config schema may change before v1.0.

## 1.1. Quickstart

```bash
git clone https://github.com/thekaveh/aws-tui.git
cd aws-tui
./scripts/bootstrap.sh           # uv guard, Python 3.11 hook runtime, sync + hooks
uv run pytest                    # default non-Docker suite
uv run pytest tests/unit         # unit-only fast path
uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing
./scripts/dev.sh                 # launch with Textual dev tools (live-reload .tcss)
```

## 1.2. Layout

This repo follows a strict layer architecture; see [docs/architecture.md](docs/architecture.md):

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

`scripts/check-layers.sh` parses imports with `ast`, resolves relative imports, and fails CI on any forbidden edge.

## 1.3. Documentation

Documentation is generated, not hand-maintained per surface. `docs/manifest.yaml`
is the single source: it lists every page and the order they appear in. From it,
`scripts/docs/build_docs.py` renders the MkDocs site, the GitHub wiki, `PYPI.md`,
and `mkdocs.yml` itself.

Edit the files under `docs/` and `README.md`. Do **not** hand-edit `mkdocs.yml`,
`PYPI.md`, or anything under `generated/` — they are produced from the manifest
and overwritten on the next build. Adding a page means adding it to
`docs/manifest.yaml` too, or the completeness check fails.

The docs tooling lives in its own dependency group:

```
uv sync --group docs      # markdown, Pillow, MkDocs Material
make docs-check           # the same gate CI runs
uv run --group docs pytest tests/docs
```

`make docs-check` verifies self-containment (no surface links to another
surface or to GitHub source views), hierarchical heading numbering, manifest
completeness, and that the committed generated artifacts match a fresh render.
CI runs it on every pull request, so a documentation change that skips it fails
there instead.

`./scripts/bootstrap.sh` syncs `--all-groups`, so a bootstrapped checkout can
run everything. If you sync the `dev` group alone, `uv run pytest` still
collects `tests/docs` — which imports `markdown` and `Pillow` — and those
surface as collection errors rather than skips.

## 1.4. Commits

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`.
Scopes follow the layer names (`infra`, `domain`, `vm`, `services`, `ui`, `app`, `ci`, etc.).

## 1.5. Pull requests

- Branch feature, fix, and maintenance work from `develop`. Reserve `main`
  for release-promotion PRs from `develop`. Open the PR early; mark draft
  until ready.
- CI must be green. Snapshot test changes need explicit review of the goldens diff.
- New services go under `src/aws_tui/services/<name>/` and register in `src/aws_tui/composition.py`. See [docs/adding-a-service.md](docs/adding-a-service.md).
- Adding an AWS API call? Run integration tests against `moto`. For S3-compatible quirks, add a note in [docs/connections.md](docs/connections.md).

## 1.6. Code of conduct

Participation in this project is governed by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
