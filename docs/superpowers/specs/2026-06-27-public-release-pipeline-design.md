# Public Release Pipeline — Design

## Goal

Get aws-tui onto its proper public distribution channels (PyPI as
primary, GitHub Releases, Homebrew tap) via a **sustainable**
automated pipeline. Every future release should be **one tag-push +
one approval click** away — no manual `twine upload`, no manual sha
recompute, no scrambled release-day improvisation.

First release through this pipeline: **v0.8.0** (current HEAD
`269427c` + ~90 commits since the v0.7.0 tag, including the EMR
Serverless service and many UX fixes).

## Non-goals

- Conda-forge channel (skipped — add only if users ask).
- Docker image (TUI without a daemon doesn't benefit).
- Auto-generated release notes from commits (hand-curated CHANGELOG
  block is the canonical source).
- Multi-formula Homebrew tap (single-formula `homebrew-aws-tui`
  is simpler today; splittable later).
- CalVer (SemVer is in place and working).
- Per-PR pre-release channels (`0.8.0.dev0+sha` etc.) — adds
  complexity not warranted for a solo-maintainer project.

## Architecture — 5 components

### 1. Version source of truth

- `src/aws_tui/version.py::__version__` is the single source.
  Hatchling already reads it via `[tool.hatch.version]` in
  `pyproject.toml`. No duplication.
- Bump-via-edit in the release-cut PR.
- A **tag/version-match assertion** in the release workflow:
  extracts `${{ github.ref_name }}`, strips the `v`, asserts
  equality with `aws_tui.__version__`. Mismatch → publish refused.
  Prevents the "tag says 0.8.0, wheel says 0.7.0" footgun.
- Classifier bumps from `Development Status :: 2 - Pre-Alpha` →
  `Development Status :: 3 - Alpha` for v0.8.0. Signals "safe to
  install for real use; API may still shift in 0.x."

### 2. Changelog cut process

- Documented in `docs/RELEASING.md` (six steps).
- `scripts/cut-changelog.sh <version>` automates:
  - renames `## [Unreleased]` → `## [<version>] - <today>`,
  - prepends a fresh empty `## [Unreleased]` block.
- Cut PR also bumps `version.py`, the Development-Status
  classifier (when transitioning), and the README "Status" line.
- PR-reviewed like any other change. Merge → tag → push.

### 3. Release workflow (`.github/workflows/release.yml`)

Single workflow, five jobs in sequence:

1. **`verify`** — checks tag matches `__version__` for PyPI
   publishes, then runs the test, audit, pre-commit, layer, build,
   and `twine check` gates inline. TestPyPI rehearsals patch the
   package version to a unique `X.Y.Z.dev<run_number>` before build.
2. **`smoke-install`** — installs the built wheel on macOS, Linux,
   and Windows and runs the console `--version` smoke check.
3. **`publish-pypi`** — uses **PyPI Trusted Publisher (OIDC)** via
   `pypa/gh-action-pypi-publish@release/v1`. No PyPI secret. Publishes
   wheel + sdist + sigstore attestation. Guarded by a GitHub
   Environment named `pypi` with **required reviewer = the
   maintainer** so every release pauses for one approval click.
4. **`publish-github`** — creates GitHub Release. Body = the
   changelog section just cut, parsed out by a shell snippet
   between `## [<version>]` and the next `## [` header. Attaches
   wheel + sdist artifacts.
5. **`bump-homebrew`** (depends on `publish-pypi`) — hashes the
   release workflow's built sdist artifact, uses
   `peter-evans/create-pull-request@v6` to open a PR in
   `thekaveh/homebrew-aws-tui` updating the
   formula's `url` + `sha256`. **Manual merge** — keeps Homebrew
   users one human-in-the-loop step removed from a bad upload.

Triggers:
- `push: tags: ['v*']` — the routine release path.
- `workflow_dispatch` with input `target = testpypi | pypi` —
  TestPyPI dry-run lane for rehearsing changes to the release
  machinery itself without burning a real version number.

### 4. Homebrew tap

New repo `thekaveh/homebrew-aws-tui`:
- One formula: `Formula/aws-tui.rb`.
- Uses `Language::Python::Virtualenv` so brew creates an isolated
  venv (doesn't pollute system Python).
- Pins `python@3.12` (matches CI happy-path).
- Test stanza runs `aws-tui --version`.
- Tap repo README: one-pager with `brew install thekaveh/aws-tui/aws-tui`
  + link back to main repo.
- The `bump-homebrew` job auto-opens PRs against this tap on every
  PyPI release; bootstrapping the initial formula is one human
  step after the first 0.8.0 publish lands.

### 5. TestPyPI dry-run rehearsal

Same `release.yml`, `workflow_dispatch` with `target = testpypi`.
When `testpypi`: publish-pypi job points at TestPyPI's Trusted
Publisher; publish-github + bump-homebrew jobs are skipped. Manual
button — only run when the release machinery itself changed.

## What the maintainer does for each release

1. Open release PR: run `scripts/cut-changelog.sh 0.8.0`, bump
   `version.py`, update README status line. (~5 min)
2. Merge PR.
3. `git tag v0.8.0 && git push --tags`.
4. Approve the `pypi` environment when prompted in GitHub Actions
   (~5 sec).
5. Skim the auto-opened Homebrew bump PR and merge.

Five minutes of human time per release.

## One-time bring-up (manual, maintainer-side)

These four console steps are **not automatable** and the
maintainer does them once before the first 0.8.0 cut.

1. **PyPI Trusted Publisher**: log into PyPI → project `aws-tui` →
   "Add a new pending publisher". Repo = `thekaveh/aws-tui`,
   workflow = `release.yml`, environment = `pypi`.
2. **TestPyPI Trusted Publisher**: same on test.pypi.org. Environment
   = `testpypi`.
3. **GitHub Environments**: in repo Settings → Environments,
   create `pypi` and `testpypi`. On `pypi`, add yourself as a
   required reviewer. (`testpypi` doesn't need one — it's a
   rehearsal lane.)
4. **Create `thekaveh/homebrew-aws-tui` repo** — empty is fine.
   The bootstrap formula is committed by hand after the first
   PyPI release succeeds.

## Failure modes & rollback

- **Tag/version mismatch.** Verify job fails fast → no publish.
  Fix: amend the cut PR's `version.py`, retag.
- **PyPI publish succeeds, GitHub Release fails.** Wheel is already
  on PyPI (immutable). Fix manually from the existing tag and built
  artifacts with `gh release create` / `gh release upload`; do not
  rerun the PyPI publish path for the same version.
- **Homebrew bump PR has wrong sha256.** Manual merge is the
  gate — review the diff. `pip install aws-tui` unaffected.
- **Bad release shipped to PyPI.** **PyPI does not allow
  republishing the same version.** Recovery: yank (hides from
  `pip install` solver, keeps existing pins working) + cut a
  patch version immediately. Documented in `docs/RELEASING.md`.
- **Trusted Publisher misconfigured.** Surfaced on the first
  TestPyPI dry-run, before any real release. That's the point of
  the rehearsal lane.

## Testing the pipeline

- **First action after merging the release-machinery PR**: run
  `workflow_dispatch` with `target = testpypi`. Confirm a wheel
  lands on TestPyPI. Install into a clean venv:
  `pip install -i https://test.pypi.org/simple/ aws-tui`. Run
  `aws-tui --version`. Smoke-test the S3 + EMR pages.
- **Then**: cut `v0.8.0` for real and watch the pipeline
  end-to-end.
- **Only after the real 0.8.0 lands**: bootstrap the
  `homebrew-aws-tui` formula by hand. From v0.8.1 onward the
  `bump-homebrew` job opens PRs automatically.
