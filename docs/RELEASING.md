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

- **PyPI project status.** Confirm the public project page is reachable, note
  the latest published version, and verify that the intended new version is
  still available on both PyPI and TestPyPI before creating a tag.
- **Clean install smoke.** Build the release artifacts, validate them with
  `uv run python -m scripts.check_dist dist/` and `uv run twine check dist/*`,
  confirming that the validator finds the complete source module, `py.typed`,
  and packaged theme payload; then install the wheel into a fresh temporary
  environment and run `aws-tui --version`, `aws-tui --help`, and
  `python -m aws_tui --version`.
- **Supported-platform status.** Confirm the latest required CI run is green on
  macOS, Linux, and Windows for Python 3.11, 3.12, and 3.13. Record any
  platform-specific exception in the release PR instead of silently relying on
  the Linux result.
- **Release classification.** Glue, Athena, and Iceberg integration are
  minor-version feature work targeting v0.9.0. They must remain in
  **Unreleased** until that cut and must not be presented as a v0.8.0 headline
  or included in a v0.8.1 patch. Do not change `src/aws_tui/version.py` while
  verifying Unreleased work.
- **Demo-mode smoke.** Run `AWS_TUI_DEMO=1 uv run aws-tui` from the release-PR
  branch. Verify the **DEMO MODE** chip appears in the banner, the four demo
  connections (`demo-dev`, `demo-prod`, `demo-shared`, `demo-minio`) cycle
  through Shift+S, the S3 pane shows demo objects, the EMR pane shows two
  applications plus about 10 job runs across states, and clone-from-detail
  visibly walks SUBMITTED→SCHEDULED→RUNNING→SUCCESS within about 5 seconds.
- **Interaction-surface smoke.** With at least two demo profiles, start on
  `demo-dev` (`us-east-1`). Use `Tab` / `Shift+Tab` to focus the bordered
  source selector and verify the forward focus ring and reverse focus ring.
  Press `Enter` or `Space` to open it, choose another demo profile such as `demo-shared`
  (`us-west-2`), and press `Enter` to commit. Verify the exact connection name
  and region changed as selected: the active source must now read `demo-shared`
  and `us-west-2`. Check Glue `Shift+F` / `Shift+G` filter commands and Athena
  `Shift+W` / `Shift+C` / `Shift+D` selector commands. Open the contextual
  command palette on Glue and Athena and confirm wrong-service commands are
  absent. Copy the selected table reference to the typed clipboard, then
  insert the copied table reference in Athena under the same source. Refuse a
  copied reference from another source and confirm the editor, typed clipboard,
  and active profile are unchanged.
- **Athena release smoke.** On `demo-dev`, execute a valid bounded query and
  observe QUEUED/RUNNING/SUCCEEDED lifecycle state. Enter `DELETE FROM events`
  and verify that aws-tui will reject an unsafe statement before dispatch.
  Start the demo running query, confirm `Esc` can cancel only the active query
  started by this page, and confirm a History-only execution is not
  cancellable. Open Results, page results when a continuation is available,
  and verify null/empty rendering. Inspect bytes scanned and reuse in the
  actual Query and History surfaces: Query shows bytes scanned, while History
  shows both bytes scanned and reused-result state. From the successful
  execution, hand off the exact S3 artifact and verify S3 opens under the same
  connection and region at `s3://athena-results/dev/q-dev-succeeded.csv`.
  Confirm named and prepared query detail load, `demo-prod` is disjoint, and
  `demo-shared` remains a scoped access-denied state.
- **Glue/Athena/Iceberg release smoke.** On `demo-dev`, open
  `dev_analytics.dev_events_iceberg` in Glue. Inspect Snapshots, History,
  Manifests, Files, Partitions, and References. Select snapshot `4201`, press
  `Shift+V`, and verify Athena is mounted under the same connection and region
  with `FOR VERSION AS OF 4201 LIMIT 100` in the editor, while no query starts
  automatically. Execute explicitly with `Ctrl+Enter`, compare displayed rows
  with the downloaded CSV, and hand the artifact to S3. Verify **Open query table in
  Glue** returns only for one unambiguous table. Repeat enough of the flow on
  `demo-prod` to prove disjoint content, then confirm `demo-shared` stays a
  scoped access state. Review bytes scanned and remember that every metadata
  tab is an Athena query with metadata-query costs; no create/edit/delete
  operation is in scope.
- **Automated-only Iceberg states.** Stock `demo-dev` does not seed metadata
  continuation, retry, or isolated-tab-failure states, so they are not manual
  smoke requirements. Keep them in automated release verification:
  `tests/integration/test_demo_mode.py` covers metadata continuation and retry,
  `tests/unit/vm/glue/test_iceberg_vm.py` covers pagination plus isolated pane
  failure/recovery, and `tests/unit/ui/glue/test_iceberg_view.py` covers the
  reachable Retry and Load more controls.

If any smoke step breaks, fix forward; do **not** tag the release.

The release commit must contain a dated changelog heading for the exact package
version. The workflow rejects `Pending` headings. Because the current tree
contains v0.9 feature work while package metadata still reads `0.8.0`, prepare
v0.9 by bumping the version and cutting its changelog section in the release PR;
do not tag the current tree as v0.8.0.

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
click **Approve** in the Actions UI. This is the final manual stop after all
mandatory release gates have passed.

After approval the pipeline:
1. Publishes only after `verify` builds and checks the artifacts, the mandatory
   `platform-tests` gate passes behavioral tests on macOS and Windows, and
   `smoke-install` clean-installs the built wheel on macOS, Linux, and Windows
   across Python 3.11, 3.12, and 3.13.
2. Requires `lowest-supported-dependencies` to install every declared direct
   dependency at its minimum compatible version with `--resolution
   lowest-direct`. It validates the S3 request-model members aws-tui uses and
   exercises representative runtime surfaces for Textual/app construction,
   aioboto3 client creation, LocalFS/AnyIO/aiofiles, SQLGlot, VMx reactive
   state, keyring/resolver behavior, and tomli-w configuration round trips.
3. Publishes to PyPI via Trusted Publisher (sigstore attestation).
4. Creates the GitHub Release with the changelog section as body
   and wheel + sdist attached.
5. Opens a PR in `thekaveh/homebrew-aws-tui` only when the formula has already
   been bootstrapped and `HOMEBREW_TAP_ENABLED=true`. Until then the entire
   Homebrew job is skipped; bootstrap the formula manually after the first
   PyPI release.

Skim the Homebrew PR and merge it when one is created.

Done.

## 1.2. Rehearsing the TestPyPI Pipeline

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
VERSION="X.Y.Z.dev<RUN_NUMBER>"  # copy from the release workflow's verify output
uv python install 3.13
uv venv --python 3.13 /tmp/aws-tui-dry
source /tmp/aws-tui-dry/bin/activate
mkdir -p /tmp/aws-tui-dry-artifacts
pip download --pre --no-deps -i https://test.pypi.org/simple/ \
    "aws-tui==$VERSION" \
    -d /tmp/aws-tui-dry-artifacts
pip install --index-url https://pypi.org/simple/ \
    /tmp/aws-tui-dry-artifacts/aws_tui-"$VERSION"-*.whl
aws-tui --version
```

The download step intentionally uses `--no-deps` so only the aws-tui
rehearsal artifact comes from TestPyPI. The install step then resolves
runtime dependencies (boto3, textual, etc.) from real PyPI only. Pinning
the exact `X.Y.Z.dev<N>` version is intentional: once `X.Y.Z` exists on
PyPI, an unpinned install may prefer the final PyPI release over the
TestPyPI rehearsal.

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
- **Smoke install fails on one OS.** No PyPI artifact has shipped yet;
  the gate caught the problem before the approval step. Fix forward on
  `main`, move or recreate the tag on the fixed commit before any PyPI
  approval, and re-run the workflow. Do not yank or retag a published
  version because nothing has been published yet.
- **Tag/version mismatch.** The `verify` job fails fast and
  publishes nothing. Fix `version.py`, retag.

## 1.4. One-time bring-up

These five console steps are not automatable. The maintainer
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
5. In the same **Actions** settings page, open **Variables** →
   **New repository variable** and set `HOMEBREW_TAP_ENABLED` to
   `true`. Until this variable is enabled, real releases deliberately
   skip the `bump-homebrew` job instead of failing against an absent or
   partially configured tap.

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
