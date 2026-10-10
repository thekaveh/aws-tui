# Contributing to aws-tui

Thanks for your interest. aws-tui is pre-release; the API and config schema may change before v1.0.

## 1. Quickstart

```bash
git clone https://github.com/thekaveh/aws-tui.git
cd aws-tui
./scripts/bootstrap.sh           # uv guard, Python 3.11 hook runtime, sync + hooks
uv run pytest                    # default non-Docker suite
uv run pytest tests/unit         # unit-only fast path
uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing
./scripts/dev.sh                 # launch with Textual dev tools (live-reload .tcss)
```

## 2. Layout

This repo follows a strict layer architecture; see [docs/architecture.md](docs/architecture.md):

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

`scripts/check-layers.sh` parses imports with `ast`, resolves relative imports, and rejects any forbidden edge.

## 3. Documentation

Documentation is generated, not hand-maintained per surface. `docs/manifest.yaml`
is the single source: it lists every page and the order they appear in. From it,
`scripts/docs/build_docs.py` renders the MkDocs site, the GitHub wiki, `PYPI.md`,
and `mkdocs.yml` itself.

Edit the files under `docs/` and `README.md`. Do **not** hand-edit `mkdocs.yml`, `PYPI.md`, or anything under `generated/`. They are produced from the manifest and overwritten on the next build. Adding a page means adding it to
`docs/manifest.yaml` too, or the completeness check fails.

The docs tooling lives in its own dependency group:

```
uv sync --group docs      # MkDocs Material, cairosvg + fontTools (diagrams), PyYAML
make docs-check           # documentation drift and strict build
uv run --group docs pytest tests/docs
```

`make docs-check` verifies self-containment (no surface links to another
surface or to GitHub source views), hierarchical heading numbering, manifest
completeness, and that the committed generated artifacts match a fresh render.
Run it locally before submitting documentation changes.

`./scripts/bootstrap.sh` syncs `--all-groups`, so a bootstrapped checkout can
run everything. If you sync the `dev` group alone, `uv run pytest` still
collects `tests/docs` — which imports `markdown` and `Pillow` — and those
surface as collection errors rather than skips.

### 3.1. Local verification and publication

Build and verify documentation locally before publication. Keep the generated
site and wiki from the same reviewed source commit:

```bash
make docs-check
uv run --group docs pytest tests/docs
```

Publish the generated wiki through its Git remote after reviewing the page and
asset diff. Copy generated files into a fresh wiki checkout. Remove stale files
only after confirming generator ownership; preserve other files and Git history.
The existing `scripts.docs.push_wiki` helper replaces the whole wiki tree, so
do not use it unchanged when handwritten files exist. Record the source and
wiki commit IDs with the publication result.

GitHub Pages requires a GitHub-managed deployment workflow, even when the site
was built locally. An Actions-based publishing source cannot accept a local
upload without that deployment. See the
[GitHub Pages publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

For local-only builds, an explicitly authorized alternative is branch
publication. Commit the verified `site/` contents and a `.nojekyll` file to a
dedicated `gh-pages` branch. Configure Pages to publish that branch's root.
This still runs GitHub's managed deployment; it does not run project tests.
Obtain authorization for that exception and the publishing-source change first.
Push the reviewed publication branch before selecting it in Pages settings.

After publication, check the public entry, task links and assets against the
reviewed sources. Record the canonical revision, website address and wiki
revision. A local build does not establish that the public documentation is
current. Keep the issue open if publication remains blocked.

## 4. Commits

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`.
Scopes follow the layer names (`infra`, `domain`, `vm`, `services`, `ui`, `app`, `ci`, etc.).

## 5. Pull requests

- Branch feature, fix, and maintenance work from `develop`. Reserve `main`
  for release-promotion PRs from `develop`. Open the PR early; mark draft
  until ready.
- Verification runs locally. Run applicable tests, `uv run pre-commit run --all-files`,
  and `make docs-check` before merging. Validate package changes with
  `uv build --no-build-isolation`, `uv run python -m scripts.check_dist dist/`,
  and `uv run twine check dist/*`. Record results and platform limitations in
  the PR. Snapshot test changes need explicit review of the goldens diff.
- GitHub Actions workflows are manual-only; pushes, pull requests, and tags do
  not start hosted checks. Do not dispatch workflows during local-only work.
  A macOS result does not establish Linux or Windows compatibility. Requirements
  for evidence from another platform remain open until that evidence exists.
- New services go under `src/aws_tui/services/<name>/` and register in `src/aws_tui/composition.py`. See [docs/adding-a-service.md](docs/adding-a-service.md).
- Adding an AWS API call? Run integration tests against `moto`. For S3-compatible quirks, add a note in [docs/connections.md](docs/connections.md).

### 5.1. Windows unit reliability

The unit workflow retains its manual OS/Python matrix: `macos-14`, `ubuntu-24.04`, and `windows-latest`, each on Python 3.11, 3.12, and 3.13. It uses `fail-fast: false` and sets no explicit `strategy.max-parallel` cap; actual capacity depends on runner availability. No capacity measurement supports a speculative change.

The `PermissionError` in #284 came from the test predicate racing atomic TOML replacement; #284 fixed that Settings polling/read race. The retained Windows Peek failure reached `the first preview scan finished` after the selected table's detail and preview availability waits passed. The old click had no rendered-geometry readiness check. #282 adds display, positive-geometry, and hit-test waits, asserts click delivery, and covers deferred projection with a controlled Pilot regression.

Historical click geometry and interleaving were not logged, so the exact cause of that occurrence is not established.

The following consecutive main promotions had all three Windows Python jobs pass on attempt 1:

| Promotion | Main CI run | Windows 3.11 / 3.12 / 3.13 |
| --- | --- | --- |
| [#304](https://github.com/thekaveh/aws-tui/pull/304) | [36764453874](https://github.com/thekaveh/aws-tui/actions/runs/36764453874) | pass / pass / pass |
| [#310](https://github.com/thekaveh/aws-tui/pull/310) | [36807953838](https://github.com/thekaveh/aws-tui/actions/runs/36807953838) | pass / pass / pass |
| [#315](https://github.com/thekaveh/aws-tui/pull/315) | [37036579717](https://github.com/thekaveh/aws-tui/actions/runs/37036579717) | pass / pass / pass |

The final PR-head push and pull-request runs also passed on attempt 1. These historical results do not establish current Windows or Linux verification or permanent reliability. See [#283](https://github.com/thekaveh/aws-tui/issues/283) for the diagnosis and decision record.

## 6. Code of conduct

Participation in this project is governed by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
