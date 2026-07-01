# 1. Releasing aws-tui

How to cut a release. Five minutes of human time per version.

```text
edit changelog + version + README
        ↓
open release PR · merge
        ↓
git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z
        ↓
approve `pypi` environment in GitHub Actions (one click)
        ↓
merge auto-opened Homebrew bump PR (skim diff first)
```

## 1.1. Routine release

From a clean `main`:

```bash
git checkout main && git pull --ff-only
git checkout -b release/vX.Y.Z

# 1. Cut the changelog: rename [Unreleased] → [X.Y.Z] - <today>
#    and prepend a fresh empty [Unreleased] block.
scripts/cut-changelog.sh X.Y.Z

# 2. Bump the version constant.
sed -i.bak 's/__version__ = "[^"]*"/__version__ = "X.Y.Z"/' \
    src/aws_tui/version.py && rm src/aws_tui/version.py.bak

# 3. Update the README "Status" line at the top of README.md to
#    point at the new version. (Manual edit — paragraph is
#    version-specific marketing copy.)

git add CHANGELOG.md src/aws_tui/version.py README.md
git commit -m "chore(release): cut vX.Y.Z"
git push -u origin release/vX.Y.Z
gh pr create --title "chore(release): cut vX.Y.Z" --fill
```

Review the PR like any other change. Merge when CI is green.

### 1.1.1. Pre-tag checklist

- **Demo-mode smoke.** Run `AWS_TUI_DEMO=1 uv run aws-tui` from the release-PR branch. Verify the **DEMO MODE** chip appears in the banner, the four demo connections (`demo-dev`, `demo-prod`, `demo-shared`, `demo-minio`) cycle through Shift+S, the S3 pane shows demo objects, the EMR pane shows two applications + ~10 job runs across states, and the clone-from-detail flow visibly walks SUBMITTED→SCHEDULED→RUNNING→SUCCESS within ~5 seconds. If any of these break, fix forward; do NOT tag the release.

Then tag the merge commit and push:

```bash
git checkout main && git pull --ff-only
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

The `release.yml` workflow fires. Watch it:

```bash
gh run watch
```

When the `publish-pypi` job hits the **environment approval gate**,
click **Approve** in the Actions UI. That's the safety belt that
makes the whole pipeline forgiving — if the verify or smoke-install
jobs somehow let a bad tag through, this is your last chance to stop.

After approval the pipeline:
1. Publishes only after `verify` builds the artifacts and
   `smoke-install` clean-installs the built wheel on macOS, Linux, and
   Windows.
2. Publishes to PyPI via Trusted Publisher (sigstore attestation).
3. Creates the GitHub Release with the changelog section as body
   and wheel + sdist attached.
4. Opens a PR in `thekaveh/homebrew-aws-tui` bumping the formula when
   the formula has already been bootstrapped. On the first PyPI release,
   the Homebrew job emits a notice and skips cleanly; bootstrap the
   formula manually afterward.

Skim the Homebrew PR and merge it when one is created.

Done.

## 1.2. Rehearsing the pipeline (TestPyPI dry-run)

Use this whenever the release machinery itself changes — a new
job, a tweaked artifact layout, anything that risks burning a
real version number.

```bash
gh workflow run release.yml --ref <branch-or-tag-with-workflow-changes> -f target=testpypi
```

The dry-run skips the GitHub Release + Homebrew steps and pushes
the wheel to test.pypi.org instead. The workflow rewrites the package
version to `X.Y.Z.dev<run_number>` for this lane so rehearsals are
repeatable despite TestPyPI's immutable versions. Use `--ref` to point
at the branch or tag containing the release-workflow changes you are
rehearsing; otherwise GitHub runs the workflow from the default branch.
Verify the install end-to-end:

```bash
python -m venv /tmp/aws-tui-dry && source /tmp/aws-tui-dry/bin/activate
pip install --pre -i https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    aws-tui
aws-tui --version
```

The `--extra-index-url` lets pip resolve the runtime dependencies
(boto3, textual, etc.) from real PyPI; TestPyPI only carries the
aws-tui rehearsal artifact.

## 1.3. Rollback

**PyPI does not allow republishing the same version.** Recovery
is always "fix forward, never overwrite":

- **Bad release shipped.** Yank the version on PyPI's web UI
  (Project → Manage → Release → "Yank release"). Yanking hides
  the version from `pip install aws-tui` solver resolutions but
  keeps existing `aws-tui==X.Y.Z` pins working. Then cut a patch
  version (for example, `0.8.1` after `0.8.0`) with the fix.
- **GitHub Release wrong / missing after PyPI succeeded.** Do **not**
  re-run the PyPI publish path for the same version. Create or repair
  the release manually from the existing tag and checked artifacts
  (`gh release create vX.Y.Z --target <tag-sha> dist/*`, or
  `gh release upload` for missing assets), using the matching
  changelog section as notes.
- **Homebrew bump PR has wrong sha256.** Don't merge it. Close the PR
  and hand-edit the formula against the PyPI sdist sha256 once PyPI is
  serving the final artifact.
- **Tag/version mismatch.** The `verify` job fails fast and
  publishes nothing. Fix `version.py`, retag.

## 1.4. One-time bring-up

These four console steps are not automatable. The maintainer
does them once before the first release through this pipeline.
PyPI/TestPyPI Trusted Publisher and GitHub environments may already
exist; the Homebrew bootstrap waits until the first PyPI artifact is
actually published.

### 1.4.1. PyPI Trusted Publisher

1. Log into [pypi.org](https://pypi.org) → **Your projects** →
   `aws-tui` → **Settings** → **Publishing** → **Add a new pending
   publisher**.
2. Fill in:
   - Owner: `thekaveh`
   - Repository name: `aws-tui`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. Save.

Repeat for [test.pypi.org](https://test.pypi.org) with environment
name `testpypi`.

### 1.4.2. GitHub Environments

In `thekaveh/aws-tui` → Settings → **Environments** → New
environment:

- `pypi` — add **Required reviewers** = `thekaveh` (you).
- `testpypi` — no protection rules.

### 1.4.3. Homebrew tap repo

1. Create empty repo `thekaveh/homebrew-aws-tui` on GitHub.
2. After the first PyPI release lands, bootstrap the
   formula manually — see [`docs/homebrew-bootstrap.md`](homebrew-bootstrap.md).
   From the next release onward the `bump-homebrew` workflow opens PRs
   automatically.

### 1.4.4. Homebrew tap token

The `bump-homebrew` workflow needs to push branches and open PRs
in a DIFFERENT repo than the one running the workflow. The default
`GITHUB_TOKEN` can't cross repo boundaries, so we use a
fine-grained PAT scoped to the tap repo only:

1. github.com → Settings → **Developer settings** → **Personal
   access tokens** → **Fine-grained tokens** → **Generate new**.
2. Resource owner: `thekaveh`. Repository access: **Only select
   repositories** → `homebrew-aws-tui`.
3. Repository permissions:
   - **Contents**: Read and write
   - **Pull requests**: Read and write
4. In `thekaveh/aws-tui` → Settings → **Secrets and variables**
   → **Actions** → **New repository secret** → name
   `HOMEBREW_TAP_TOKEN`, paste the token.

Token lifespan is the only routine recurring chore — set the
calendar reminder for the expiry date.

## 1.5. Version policy

Semantic Versioning. Pre-1.0 we're explicit:

- **Patch (`X.Y.(Z+1)`, e.g. `0.8.1`)**: bug fixes only, no API changes, no
  feature additions.
- **Minor (`X.Y+1.0`)**: new features, bug fixes; backward-
  compatible for users of the canonical `aws-tui` CLI.
- **Major (`X+1.0.0`)**: breaking changes to the public CLI
  surface (renamed bindings, removed services, renamed config
  keys, etc.).

The `Development Status` classifier in `pyproject.toml` tracks
the project maturity, not the version number:

- `2 - Pre-Alpha` (v0.0.x – v0.7.0): exploratory.
- `3 - Alpha` (v0.8.0 onward): public release, API may still
  shift in 0.x but breakage is documented in `CHANGELOG.md`.
- `4 - Beta` (when ready): API frozen for 1.0.
- `5 - Production/Stable` (1.0.0): committed SemVer.
