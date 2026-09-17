# Audit Findings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the nine audit findings that survived independent verification on 2026-09-17: two runtime defects (EMR clone idempotency, unreachable Glue pagination) and seven documentation defects.

**Architecture:** Each finding becomes one task with its own test cycle. Runtime fixes follow the app's existing patterns exactly: the Athena `ClientRequestToken` pattern for EMR, and the Athena `athena.load_more` action + Iceberg `↓` control pattern for Glue. Documentation fixes each gain a contract test in `tests/docs/` or `tests/unit/test_workflow_guards.py` so the defect cannot silently return.

**Tech Stack:** Python 3.11+, Textual, VMx, pytest (`uv run pytest`), MkDocs (`make docs-check`), GitHub Actions.

**Spec:** The two audit reports pasted into the 2026-09-17 session, verified finding-by-finding against `develop` at `516d1f75` plus PR #220. Verification notes per finding are in the task headers below.

## 1. Global Constraints

- Branch every task from `develop`. `main` only receives promotion PRs from `develop`, merged with a **merge commit, never squash** (repo policy, CONTRIBUTING §5).
- The `gitflow` ruleset requires the `ci gate` status check with the strict up-to-date policy on both `develop` and `main`. Multiple PRs must merge as a serial train; rebase each before the next.
- Commit as `1766308+thekaveh@users.noreply.github.com`. GitHub rejects pushes whose commits carry the gmail address ("push declined due to email privacy restrictions"). The repo-local `user.email` is already set; verify with `git config user.email` before pushing.
- Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Every PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Do not bump `src/aws_tui/version.py`. Do not touch historical ledger sections §1–§6.
- `tests/docs` is the documentation-contract tier; run it after every docs change: `uv run pytest tests/docs -q -p no:cacheprovider`.
- `make docs-check` renders diagrams. On macOS it needs `brew install cairo` and `uv sync --group docs`; the Makefile already sets `DYLD_FALLBACK_LIBRARY_PATH`. Do not run diagram rendering through `uv run` on macOS (it drops `DYLD_*`).
- Known flaky CI tests unrelated to this work: `tests/snapshot/test_demo_mode.py::test_demo_iceberg_snapshot[<theme>]` and `tests/integration/test_settings_flow.py::test_add_inline_form_persists_to_toml` on Windows. Rerun the failed job once; do not add retry plugins.
- Task 8 and Task 9 change registered action / keybinding surfaces. Those surfaces are enumerated by contract tests in **seven** places; the task lists all of them. Missing one fails `ci gate`.

## 2. Finding verification summary

| Finding | Verdict | Evidence |
|---|---|---|
| DOC-01 / DOC01 lifecycle recipe replaces bucket config | **Confirmed** | `docs/connections.md:255-273`: single-rule `put-bucket-lifecycle-configuration`, fenced as `jsonc` with a `// lifecycle.json` comment. AWS `PutBucketLifecycleConfiguration` replaces the whole configuration. |
| DOC-02 / DOC02 Pages triggers omit root project docs | **Confirmed** | `.github/workflows/pages.yml:6-18` lists `assets/**`, `docs/**`, `scripts/docs/**`, `Makefile`, `pyproject.toml`, `uv.lock`, the workflow itself. `docs/manifest.yaml:42-44` publishes `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`. |
| DOC-03 / DOC03 rehearsal uses bare `pip` in an unseeded venv | **Confirmed** | `docs/RELEASING.md:207-217`: `uv venv` (no `--seed`) then `pip download` / `pip install`. `uv venv` does not install pip. |
| DOC-04 / DOC04 release recipe branches from `main` | **Confirmed** | `docs/RELEASING.md:19-22` vs `CONTRIBUTING.md:71-72`. The ruleset does not block it, so the policy conflict is real and the recipe drops unpromoted `develop` work. |
| DOC-05 / DOC05 diagram labels `y` as the query key | **Confirmed** | `docs/diagrams/table-handoff.html:91` says `y · Query table in Athena`; `keymap_store.py:151-152` binds `y` → `glue.copy_table_ref`, `Q` → `glue.query_in_athena`. |
| DOC-06 / DOC06 dead README section reference | **Confirmed** | `docs/platforms.md:80` cites a README "Environment variables" section; README §5 only links to `docs/configuration.md`. `docs/configuration.md:4-5` claims the README "carries" the tables. |
| DOC-07 / DOC07 source-cycle example order | **Confirmed** | `docs/connections.md:137-146` shows all AWS profiles then all s3-compatible. `connection_resolver.py:167-171` returns `[*explicit, *autos]`; `app.py:346-352` cycles that order after `local`. |
| R01 EMR `clientToken` not app-owned | **Confirmed** | `domain/emr_serverless.py:458-497` never sets `clientToken`; botocore mints a UUID per call. Already recorded as a known gap in `docs/contract-ledger.md` §8 ("Recorded for a future app-owned token"). The modal's re-entrancy guard covers only the in-flight double press, not a retry after an ambiguous failure. |
| R02 Glue lists show "more available" with no reachable pagination | **Confirmed** | `detail_rows.py:152` renders the suffix; `vm/glue/*_vm.py` expose `load_more_*`; no caller exists in `src/aws_tui/ui`, `app.py`, or the keymap (only Athena, EMR, and Iceberg have wiring). |
| R03 S3Mock / hook / action ledger drift | **Already fixed** | PR #220 (`chore/dependency-maintenance-2026-09-17`). Not in this plan. |
| Auditor note: ruff 0.16.8 vs lock 0.16.4 | **Already fixed** | PR #220 locks ruff 0.16.8. |

---

## 3. Task 1: S3 lifecycle recipe must merge into the existing configuration (DOC-01)

**Files:**
- Modify: `docs/connections.md:254-273` (section "6. Recommended 1-Day MPU Abort Lifecycle Rule")
- Test: `tests/docs/test_scaffolding.py` (append)

**Interfaces:**
- Consumes: `_read(path)` helper already defined in `tests/docs/test_scaffolding.py`.
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

Append to `tests/docs/test_scaffolding.py`:

```python
import json


def _fenced_blocks(text: str, language: str) -> list[str]:
    """Return the bodies of every ```<language> fenced block in ``text``."""
    pattern = re.compile(rf"^```{re.escape(language)}\s*\n(.*?)^```", re.S | re.M)
    return [match.group(1) for match in pattern.finditer(text)]


def test_lifecycle_recipe_merges_into_the_existing_bucket_configuration() -> None:
    """``PutBucketLifecycleConfiguration`` replaces the whole configuration.

    A single-rule payload silently deletes every expiration and transition
    rule already on the bucket, so the recipe must fetch, merge, then put,
    and the JSON it ships must be literal JSON a reader can save verbatim.
    """
    connections = _read("docs/connections.md")
    section = connections.split("## 6. Recommended 1-Day MPU Abort Lifecycle Rule", 1)[1]
    section = section.split("\n## ", 1)[0]

    assert "```jsonc" not in section
    assert "get-bucket-lifecycle-configuration" in section
    assert "replaces the bucket's entire lifecycle configuration" in section
    json_blocks = _fenced_blocks(section, "json")
    assert json_blocks, "expected a literal JSON rule block"
    for block in json_blocks:
        parsed = json.loads(block)
        assert parsed["Rules"][0]["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 1}
```

`re` is already imported at the top of the file; add `import json` next to the other stdlib imports if it is not.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_scaffolding.py::test_lifecycle_recipe_merges_into_the_existing_bucket_configuration -q -p no:cacheprovider`
Expected: FAIL on `assert "```jsonc" not in section`.

- [ ] **Step 3: Rewrite the recipe**

Replace everything from the ```` ```jsonc ```` fence through the closing ```` ``` ```` of the `aws s3api put-bucket-lifecycle-configuration` block in `docs/connections.md` §6 with:

````markdown
`put-bucket-lifecycle-configuration` replaces the bucket's entire lifecycle
configuration with the document you send. Never apply a single-rule file to a
bucket that already has rules: fetch the current rules, add this one, review
the merged result, then put the merged document.

```bash
# 1. Fetch the current rules. A bucket with no lifecycle configuration
#    returns NoSuchLifecycleConfiguration; start from an empty rule list then.
aws s3api get-bucket-lifecycle-configuration --bucket <name> \
    > current-lifecycle.json \
    || echo '{"Rules": []}' > current-lifecycle.json

# 2. Append the abort rule to the existing Rules array. jq keeps every
#    existing rule intact; an ID collision means the rule is already present.
jq '.Rules += [{
      "ID": "abort-incomplete-mpu",
      "Status": "Enabled",
      "Filter": {},
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 1 }
    }]' current-lifecycle.json > merged-lifecycle.json

# 3. Review merged-lifecycle.json, then apply the merged document.
aws s3api put-bucket-lifecycle-configuration \
    --bucket <name> --lifecycle-configuration file://merged-lifecycle.json
```

For a bucket with no existing rules the merged document is exactly:

```json
{
  "Rules": [
    {
      "ID": "abort-incomplete-mpu",
      "Status": "Enabled",
      "Filter": {},
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 1 }
    }
  ]
}
```
````

Keep the explanatory paragraph above it ("Set a 1-day lifecycle rule ...") unchanged.

- [ ] **Step 4: Run the docs tier**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Expected: all pass (the 5 cairo skips on macOS are normal).

- [ ] **Step 5: Add the changelog entry**

Under `## [Unreleased]` → `### Docs` in `CHANGELOG.md`, add as the first bullet:

```markdown
- **Lifecycle recipe no longer clobbers existing rules.** The MPU-abort recipe
  in `docs/connections.md` now fetches the bucket's current lifecycle
  configuration, appends the rule with `jq`, and puts the merged document,
  because `put-bucket-lifecycle-configuration` replaces every existing rule.
  The JSON is now literal JSON rather than a commented `jsonc` block.
```

- [ ] **Step 6: Commit**

```bash
git add docs/connections.md tests/docs/test_scaffolding.py CHANGELOG.md
git commit -m "docs(connections): merge the MPU-abort rule into the existing lifecycle configuration

put-bucket-lifecycle-configuration replaces the whole configuration, so the
single-rule recipe deleted every expiration and transition rule a bucket
already had. The recipe now fetches, appends with jq, and puts the merged
document, and ships literal JSON instead of a jsonc block with a comment.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 4. Task 2: Pages workflow must trigger on every manifest source (DOC-02)

**Files:**
- Modify: `.github/workflows/pages.yml:6-18`
- Test: `tests/unit/test_workflow_guards.py` (append)

**Interfaces:**
- Consumes: `_workflow(path)` and `REPO_ROOT` already defined in `tests/unit/test_workflow_guards.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_workflow_guards.py`:

```python
import fnmatch

import yaml


def _manifest_sources() -> set[str]:
    manifest = yaml.safe_load((REPO_ROOT / "docs" / "manifest.yaml").read_text(encoding="utf-8"))
    sources: set[str] = {manifest["package"]["source"]}

    def walk(entries: list[dict]) -> None:
        for entry in entries:
            if "source" in entry:
                sources.add(entry["source"])
            walk(entry.get("children", []))

    walk(manifest["sections"])
    for diagram in manifest.get("diagrams", []):
        sources.add(diagram["master"])
    return sources


def _path_filter_matches(pattern: str, path: str) -> bool:
    # GitHub's ``**`` matches across directory separators; fnmatch's ``*``
    # already does, so ``docs/**`` becomes ``docs/*``.
    return fnmatch.fnmatchcase(path, pattern.replace("**", "*"))


def test_pages_publication_triggers_on_every_manifest_source() -> None:
    """A manifest source outside the trigger filters publishes stale copies.

    ``CONTRIBUTING.md``, ``SECURITY.md``, and ``CODE_OF_CONDUCT.md`` are
    published from the manifest but lived outside ``docs/**``, so an edit
    confined to one of them never redeployed Pages or the wiki.
    """
    workflow = _workflow(".github/workflows/pages.yml")
    patterns = workflow[True]["push"]["paths"]

    missing = sorted(
        source
        for source in _manifest_sources()
        if not any(_path_filter_matches(pattern, source) for pattern in patterns)
    )
    assert missing == [], f"manifest sources absent from pages.yml push paths: {missing}"
```

`yaml` is already a dependency (`_workflow` parses YAML); if the module imports it under another name, reuse that import.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_workflow_guards.py::test_pages_publication_triggers_on_every_manifest_source -q -p no:cacheprovider`
Expected: FAIL listing `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`.

- [ ] **Step 3: Add the paths**

In `.github/workflows/pages.yml`, after the `- "docs/**"` line add:

```yaml
      # Root project documents the manifest publishes to the site and wiki.
      # They live outside docs/**, so an edit confined to one of them shipped
      # nothing until the next unrelated docs change.
      - "CONTRIBUTING.md"
      - "SECURITY.md"
      - "CODE_OF_CONDUCT.md"
```

- [ ] **Step 4: Run the guard tests**

Run: `uv run pytest tests/unit/test_workflow_guards.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Changelog**

Under `## [Unreleased]` → `### Build` add as the first bullet:

```markdown
- Pages and wiki publication now also trigger on `CONTRIBUTING.md`,
  `SECURITY.md`, and `CODE_OF_CONDUCT.md`; the manifest publishes all three,
  but the workflow's path filter only watched `docs/**`. A guard test now
  derives the required triggers from `docs/manifest.yaml`.
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/pages.yml tests/unit/test_workflow_guards.py CHANGELOG.md
git commit -m "ci(pages): trigger publication on the root project documents the manifest publishes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 5. Task 3: TestPyPI rehearsal must use a seeded environment's own pip (DOC-03)

**Files:**
- Modify: `docs/RELEASING.md:206-217`
- Test: `tests/docs/test_scaffolding.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/docs/test_scaffolding.py`:

```python
def test_testpypi_rehearsal_uses_the_seeded_environment_pip() -> None:
    """``uv venv`` creates no pip; a bare ``pip`` resolves to some other install."""
    releasing = _read("docs/RELEASING.md")
    section = releasing.split("## 2. Rehearsing the TestPyPI Pipeline", 1)[1]
    section = section.split("\n## ", 1)[0]
    bash = "\n".join(_fenced_blocks(section, "bash"))

    assert "uv venv --seed" in bash
    assert "/tmp/aws-tui-dry/bin/python -m pip download" in bash
    assert "/tmp/aws-tui-dry/bin/python -m pip install" in bash
    assert "/tmp/aws-tui-dry/bin/aws-tui --version" in bash
    for line in bash.splitlines():
        assert not line.lstrip().startswith("pip "), f"bare pip invocation: {line!r}"
        assert "source /tmp/aws-tui-dry" not in line
```

(`_fenced_blocks` was added in Task 1. If Task 1 has not landed yet, copy its definition into this test module.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_scaffolding.py::test_testpypi_rehearsal_uses_the_seeded_environment_pip -q -p no:cacheprovider`
Expected: FAIL on `"uv venv --seed" in bash`.

- [ ] **Step 3: Rewrite the rehearsal block**

Replace the bash block in `docs/RELEASING.md` §2 that starts with `VERSION="X.Y.Z.dev<RUN_NUMBER>"` with:

```bash
VERSION="X.Y.Z.dev<RUN_NUMBER>"  # copy from the release workflow's verify output
uv python install 3.13
# --seed installs pip into the environment; a plain `uv venv` has no pip, so a
# bare `pip` would resolve to whatever other interpreter is first on PATH.
uv venv --seed --python 3.13 /tmp/aws-tui-dry
mkdir -p /tmp/aws-tui-dry-artifacts
/tmp/aws-tui-dry/bin/python -m pip download --pre --no-deps \
    -i https://test.pypi.org/simple/ \
    "aws-tui==$VERSION" \
    -d /tmp/aws-tui-dry-artifacts
/tmp/aws-tui-dry/bin/python -m pip install --index-url https://pypi.org/simple/ \
    /tmp/aws-tui-dry-artifacts/aws_tui-"$VERSION"-*.whl
/tmp/aws-tui-dry/bin/aws-tui --version
```

Leave the paragraph after it ("The download step intentionally uses `--no-deps` ...") unchanged.

- [ ] **Step 4: Rehearse the recipe shape locally (no TestPyPI needed)**

```bash
uv venv --seed --python 3.13 /tmp/aws-tui-dry-plan-check
/tmp/aws-tui-dry-plan-check/bin/python -m pip --version
rm -rf /tmp/aws-tui-dry-plan-check
```

Expected: a pip version line naming `/tmp/aws-tui-dry-plan-check/lib/python3.13/site-packages/pip`.

- [ ] **Step 5: Run the docs tier**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Expected: pass.

- [ ] **Step 6: Changelog**

Under `### Docs` add:

```markdown
- **TestPyPI rehearsal installs into the environment it creates.** The recipe
  now seeds pip with `uv venv --seed` and invokes the environment's own
  `python -m pip`; the previous bare `pip` had no pip in that venv and fell
  through to whichever interpreter was first on `PATH`.
```

- [ ] **Step 7: Commit**

```bash
git add docs/RELEASING.md tests/docs/test_scaffolding.py CHANGELOG.md
git commit -m "docs(releasing): seed pip and call the rehearsal venv's own interpreter

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 6. Task 4: Release recipe must cut on `develop` and promote to `main` (DOC-04)

**Files:**
- Modify: `docs/RELEASING.md:17-54` (section "1. Routine release" up to "Review the PR like any other change") and `docs/RELEASING.md:147-151` (tag step)
- Test: `tests/docs/test_scaffolding.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_release_recipe_cuts_on_develop_and_promotes_to_main() -> None:
    """CONTRIBUTING reserves ``main`` for promotion PRs from ``develop``.

    The routine-release recipe branched from ``main`` directly, so following
    it produced a release that omitted every unpromoted ``develop`` commit and
    contradicted the branch policy the same repository publishes.
    """
    releasing = _read("docs/RELEASING.md")
    routine = releasing.split("## 1. Routine release", 1)[1].split("### 1.1.", 1)[0]
    bash = "\n".join(_fenced_blocks(routine, "bash"))

    assert "git checkout develop && git pull --ff-only" in bash
    assert "git checkout -b release/vX.Y.Z" in bash
    assert "git checkout main" not in bash
    assert "--base develop" in bash
    assert "promotion PR from `develop` to `main`" in routine
    assert "merge commit" in routine and "never squash" in routine

    contributing = _read("CONTRIBUTING.md")
    assert "Reserve `main`" in contributing
    assert "release-promotion PRs from `develop`" in contributing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_scaffolding.py::test_release_recipe_cuts_on_develop_and_promotes_to_main -q -p no:cacheprovider`
Expected: FAIL on `"git checkout develop && git pull --ff-only" in bash`.

- [ ] **Step 3: Rewrite the routine-release opening**

In `docs/RELEASING.md` replace the text from `From a clean `main`:` through the two lines `git checkout main && git pull --ff-only` / `git checkout -b release/vX.Y.Z` with:

````markdown
Releases are cut on `develop` and reach `main` only through a promotion PR,
the same path every other change takes (see CONTRIBUTING §5). From a clean
`develop`:

```bash
git checkout develop && git pull --ff-only
git checkout -b release/vX.Y.Z
```
````

(The steps 1–5 and the `git add` / `git commit` lines that follow are unchanged.)

Replace the two lines

```bash
git push -u origin release/vX.Y.Z
gh pr create --title "chore(release): cut vX.Y.Z" --fill
```

with

```bash
git push -u origin release/vX.Y.Z
gh pr create --base develop --title "chore(release): cut vX.Y.Z" --fill
```

Then replace the paragraph `Review the PR like any other change. Merge when CI is green.` with:

````markdown
Review the PR like any other change and merge it into `develop` when CI is
green. Then open the promotion PR from `develop` to `main`:

```bash
gh pr create --base main --head develop \
    --title "chore(release): promote vX.Y.Z to main" --fill
```

Merge the promotion PR with a **merge commit, never squash** and never rebase:
`main` must contain `develop`'s exact commits so the two branches stay
semantically identical and the next back-merge is a no-op. The `ci gate`
check must be green on the promotion PR itself; the ruleset's strict
up-to-date policy means a `develop` push after opening it requires a rerun.
````

- [ ] **Step 4: Fix the tag step**

The block under "Then tag the merge commit and push:" already starts with `git checkout main && git pull --ff-only`. Change the introductory sentence to:

```markdown
Then tag the promotion merge commit on `main` and push the tag:
```

- [ ] **Step 5: Run the docs tier and the strict site build**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Run: `make docs-check`
Expected: both pass. (`make docs-check` catches broken intra-doc anchors from the CONTRIBUTING reference.)

- [ ] **Step 6: Changelog**

Under `### Docs` add:

```markdown
- **Release recipe follows the branch policy.** `docs/RELEASING.md` now cuts
  the release branch from `develop`, merges it back to `develop`, and reaches
  `main` through a merge-commit promotion PR, matching CONTRIBUTING §5. The
  old recipe branched from `main` and would have shipped without unpromoted
  `develop` work.
```

- [ ] **Step 7: Commit**

```bash
git add docs/RELEASING.md tests/docs/test_scaffolding.py CHANGELOG.md
git commit -m "docs(releasing): cut releases on develop and promote to main by merge commit

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 7. Task 5: Table-handoff diagram must show the real query key (DOC-05)

**Files:**
- Modify: `docs/diagrams/table-handoff.html:91`
- Regenerate: `docs/diagrams/img/table-handoff.svg`, `docs/diagrams/img/table-handoff.png`
- Test: `tests/docs/test_contract_parity.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/docs/test_contract_parity.py`:

```python
def test_table_handoff_diagram_labels_the_registered_query_key() -> None:
    """The master said ``y``; ``y`` copies a reference and ``Q`` queries."""
    master = _text("docs/diagrams/table-handoff.html")
    labels = re.findall(r">([^<]*Query table in Athena)<", master)
    assert labels == ["Shift+Q · Query table in Athena"], labels
    assert "y · Query table in Athena" not in master
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_contract_parity.py::test_table_handoff_diagram_labels_the_registered_query_key -q -p no:cacheprovider`
Expected: FAIL with `['y · Query table in Athena']`.

- [ ] **Step 3: Fix the master**

In `docs/diagrams/table-handoff.html` change

```html
        <text x="211" y="205">y · Query table in Athena</text>
```

to

```html
        <text x="211" y="205">Shift+Q · Query table in Athena</text>
```

- [ ] **Step 4: Regenerate the derivatives**

Run: `make docs-diagrams`
Expected: `docs/diagrams/img/table-handoff.svg` and `.png` change; `git status --short docs/diagrams` shows exactly those two plus the master. If it fails with `OSError: cannot load library 'libcairo.2.dylib'`, run `brew install cairo` and `uv sync --group docs`, then retry.

- [ ] **Step 5: Verify freshness and look at the PNG**

Run: `make docs-check`
Expected: pass. Open `docs/diagrams/img/table-handoff.png` and confirm the top-left label reads `Shift+Q · Query table in Athena` and is not clipped by the box edge. If it is clipped, reduce that `<text>`'s `font-size` to `11` and re-run Step 4.

- [ ] **Step 6: Docs tier**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Expected: pass.

- [ ] **Step 7: Changelog**

Under `### Docs` add:

```markdown
- **Table-handoff diagram names the right key.** The diagram said `y` starts
  "Query table in Athena"; `y` copies the table reference and `Shift+Q`
  queries it. Master corrected, SVG/PNG regenerated, and a contract test now
  pins the label to the registered binding.
```

- [ ] **Step 8: Commit**

```bash
git add docs/diagrams/table-handoff.html docs/diagrams/img/table-handoff.svg docs/diagrams/img/table-handoff.png tests/docs/test_contract_parity.py CHANGELOG.md
git commit -m "docs(diagrams): label Shift+Q, not y, as the Athena query handoff key

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 8. Task 6: Point the environment-variable references at the configuration page (DOC-06)

**Files:**
- Modify: `docs/platforms.md:80`
- Modify: `docs/configuration.md:3-5`
- Test: `tests/docs/test_scaffolding.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_environment_variable_references_point_at_the_configuration_page() -> None:
    """README §5 only links to the configuration page; it has no such section."""
    readme = _read("README.md")
    platforms = _read("docs/platforms.md")
    configuration = _read("docs/configuration.md")

    assert "## Environment variables" not in readme
    assert 'README\'s "Environment variables"' not in platforms
    assert "configuration.md#2-environment-variables" in platforms
    assert "README carries" not in configuration
    assert "links here" in configuration
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_scaffolding.py::test_environment_variable_references_point_at_the_configuration_page -q -p no:cacheprovider`
Expected: FAIL on the `platforms` assertion.

- [ ] **Step 3: Fix both references**

`docs/platforms.md` line 80: replace `See the README's "Environment variables" section.` with

```markdown
See [Environment variables](configuration.md#2-environment-variables) in the
Configuration Reference.
```

`docs/configuration.md` lines 3–5: replace the blockquote with

```markdown
> Where aws-tui keeps its files, every environment variable it reads, and
> the localization position. The repository README links here rather than
> carrying its own copies, so the published surfaces serve one set of tables.
```

- [ ] **Step 4: Verify the anchor resolves**

Run: `make docs-check`
Expected: pass (the local-anchor check validates `configuration.md#2-environment-variables`; heading numbering is `per-doc`, so the slug is `2-environment-variables`).

- [ ] **Step 5: Docs tier**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Expected: pass.

- [ ] **Step 6: Changelog**

Under `### Docs` add:

```markdown
- **Environment-variable cross-references.** `docs/platforms.md` pointed at
  a README "Environment variables" section that no longer exists, and
  `docs/configuration.md` claimed the README still carries the tables. Both
  now point at the Configuration Reference.
```

- [ ] **Step 7: Commit**

```bash
git add docs/platforms.md docs/configuration.md tests/docs/test_scaffolding.py CHANGELOG.md
git commit -m "docs: point environment-variable references at the configuration page

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 9. Task 7: Source-cycle example must match resolver order (DOC-07)

**Files:**
- Modify: `docs/connections.md:132-146`
- Test: `tests/docs/test_scaffolding.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_source_cycle_example_states_the_resolver_order() -> None:
    """``ConnectionResolver.list()`` returns ``[*explicit, *autos]``.

    Explicit ``[connections.*]`` entries come first in config order whatever
    their kind, then auto-discovered AWS profiles not shadowed by an explicit
    entry. The example grouped every AWS profile before every s3-compatible
    endpoint, which is not an order the resolver can produce when an explicit
    s3-compatible entry exists alongside discovered profiles.
    """
    connections = _read("docs/connections.md")
    section = connections.split("## 4. Switching between connections at runtime", 1)[1]
    section = section.split("\n## ", 1)[0]

    assert "explicit `[connections.*]` entries first, in config-file order" in section
    assert "then auto-discovered AWS profiles" in section
    assert "shadow" in section
    assert "→ ... (every other AWS profile)" not in section
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/docs/test_scaffolding.py::test_source_cycle_example_states_the_resolver_order -q -p no:cacheprovider`
Expected: FAIL on the first assertion.

- [ ] **Step 3: Rewrite the paragraph and example**

Replace, in `docs/connections.md` §4, the sentence ending `Press **`Shift+S`** (or `S`) on a pane to step through it in this order:` and the fenced example that follows with:

````markdown
Every connection the resolver returns — AWS profiles, manually-configured
`s3-compatible` entries, and auto-discovered AWS profiles alike — joins
a single in-app source-cycle on the focused pane. The ring is `local` followed
by the resolver's order: explicit `[connections.*]` entries first, in
config-file order and regardless of kind, then auto-discovered AWS profiles
from `~/.aws/config` and `~/.aws/credentials`. An explicit entry whose name
matches a discovered profile shadows it, so each name appears once. Press
**`Shift+S`** (or `S`) on a pane to step through it:

```
local
  → s3-compatible · minio-local · localhost:9000     [connections.minio-local]
  → aws s3 · prod · us-east-1                        [connections.prod]
  → s3-compatible · r2-prod · <account>.r2.cloudflarestorage.com
  → ... (every other explicit [connections.*] entry, in file order)
  → aws s3 · dev-sso · us-west-2                     discovered [profile dev-sso]
  → aws s3 · analytics · eu-west-1                   discovered [profile analytics]
  → ... (every other discovered profile not shadowed by an explicit entry)
  → local   ← wraps
```
````

Keep the "Why this is useful day-to-day" bullets that follow.

- [ ] **Step 4: Check the ordering claim against the code one more time**

Run: `sed -n 165,172p src/aws_tui/infra/connection_resolver.py`
Expected: `return [*explicit, *autos]` with `autos` filtered by `c.name not in explicit_names`.

- [ ] **Step 5: Docs tier**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Expected: pass (the existing `test_public_docs_cover_integrated_iceberg_workflow` still finds "resolver order" in `connections.md`).

- [ ] **Step 6: Changelog**

Under `### Docs` add:

```markdown
- **Source-cycle order.** `docs/connections.md` §4 now describes the order
  the resolver actually produces: `local`, then explicit `[connections.*]`
  entries in config-file order regardless of kind, then auto-discovered AWS
  profiles not shadowed by an explicit entry. The old example grouped all AWS
  profiles ahead of all s3-compatible endpoints.
```

- [ ] **Step 7: Commit**

```bash
git add docs/connections.md tests/docs/test_scaffolding.py CHANGELOG.md
git commit -m "docs(connections): describe the source cycle in resolver order

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 10. Task 8: App-owned `clientToken` for EMR Serverless `StartJobRun` (R01)

**Files:**
- Modify: `src/aws_tui/domain/emr_serverless.py:159-168` (protocol) and `:458-497` (client)
- Modify: `src/aws_tui/services/emr_serverless/service.py:108-118` (failed-client stub)
- Modify: `src/aws_tui/demo/in_memory_emr.py:292-335`
- Modify: `src/aws_tui/vm/emr_serverless/clone_vm.py`
- Modify: `docs/contract-ledger.md` §8 "Known model gaps" (the `emr-serverless:StartJobRun` row only)
- Modify: `docs/services/emr-serverless.md` §3 "Clone workflow"
- Modify: `tests/unit/domain/test_emr_serverless.py`, `tests/unit/vm/emr_serverless/test_clone_vm.py`, `tests/unit/ui/emr_serverless/test_clone_modal.py:141-149` (docstring)

**Interfaces:**
- Produces: `EmrServerlessClientProtocol.start_job_run(application_id, *, execution_role_arn, entry_point, entry_point_arguments, spark_submit_parameters, client_token: str, name=None) -> str`. `client_token` is keyword-only and **required** so no implementer can silently drop it.
- Produces: `JobRunCloneVM.client_token -> str` (read-only property).
- Semantics: one token per *form intent*. The token is minted at construction, reused verbatim across submit attempts that raise (the ambiguous-retry case), rotated when any field value actually changes, and rotated after a successful submit.

- [ ] **Step 1: Write the failing domain test**

In `tests/unit/domain/test_emr_serverless.py`, edit `test_start_job_run_forwards_form_fields_to_boto` to pass `client_token="tok-nightly-1"` in the `client.start_job_run(...)` call and add after the existing kwargs assertions:

```python
    assert kwargs["clientToken"] == "tok-nightly-1"
```

Add `client_token="tok"` to the other three direct calls in that module (`test_start_job_run_omits_name_and_blank_spark_params_when_unset`, `test_start_job_run_maps_validation_exception_to_validation_error`, `test_in_memory_emr_start_job_run_records_and_materializes`).

Append a new test:

```python
async def test_in_memory_emr_reuses_the_run_for_a_repeated_client_token() -> None:
    """Mirror AWS: the same token returns the same job run, no second run."""
    fake = _InMemoryEmr()
    fake.add_application(app_id="00abc", name="etl")
    kwargs = dict(
        execution_role_arn="arn:aws:iam::123456789012:role/EmrJobRole",
        entry_point="s3://b/job.py",
        entry_point_arguments=(),
        spark_submit_parameters=None,
        client_token="tok-same",
    )
    first = await fake.start_job_run("00abc", **kwargs)
    second = await fake.start_job_run("00abc", **kwargs)
    assert first == second
    assert len([c for c in fake.calls if c[0] == "start_job_run"]) == 2
    assert len(fake.job_runs["00abc"]) == 1
```

(`_InMemoryEmr` is the existing import alias for `InMemoryEmr` in that module; if the fake stores runs under another attribute, use `await fake.list_job_runs_page("00abc", ...)` and count the rows instead. Check with `grep -n "job_runs" src/aws_tui/demo/in_memory_emr.py`.)

- [ ] **Step 2: Write the failing VM tests**

Append to `tests/unit/vm/emr_serverless/test_clone_vm.py`:

```python
from aws_tui.domain.filesystem import ProviderUnreachableError


async def test_submit_passes_the_vm_client_token_to_the_client() -> None:
    vm, fake = _make()
    token = vm.client_token
    await vm.submit()
    submit_calls = [c for c in fake.calls if c[0] == "start_job_run"]
    assert submit_calls[0][1][6] == token
    vm.dispose()


async def test_retry_after_an_ambiguous_failure_reuses_the_same_client_token() -> None:
    """A timeout after AWS accepted the request must not mint a second job.

    The first attempt raises; the user presses submit again; AWS sees the same
    ``clientToken`` and returns the run it already created.
    """
    vm, fake = _make()
    fake.start_job_run_exc = ProviderUnreachableError("read timeout")
    with pytest.raises(ProviderUnreachableError):
        await vm.submit()
    fake.start_job_run_exc = None
    await vm.submit()

    tokens = [c[1][6] for c in fake.calls if c[0] == "start_job_run"]
    assert len(tokens) == 2
    assert tokens[0] == tokens[1]
    vm.dispose()


async def test_editing_a_field_rotates_the_client_token() -> None:
    vm, _fake = _make()
    before = vm.client_token
    vm.apply_field("name", "nightly")  # unchanged value: same intent
    assert vm.client_token == before
    vm.apply_field("name", "nightly-rerun")
    assert vm.client_token != before
    vm.dispose()


async def test_successful_submit_rotates_the_client_token() -> None:
    vm, _fake = _make()
    before = vm.client_token
    await vm.submit()
    assert vm.client_token != before
    vm.dispose()
```

Add `import pytest` at the top if missing. Confirm the fake's recorded tuple index 6 will be the token (Step 5 defines it).

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py tests/unit/vm/emr_serverless/test_clone_vm.py -q -p no:cacheprovider`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'client_token'` and `AttributeError: 'JobRunCloneVM' object has no attribute 'client_token'`.

- [ ] **Step 4: Extend the protocol and the boto client**

In `src/aws_tui/domain/emr_serverless.py`, in both the protocol (line ~159) and `EmrServerlessClient.start_job_run` (line ~458), add the keyword-only parameter after `spark_submit_parameters`:

```python
        spark_submit_parameters: str | None,
        client_token: str,
        name: str | None = None,
```

In `EmrServerlessClient.start_job_run`, extend the docstring and the kwargs:

```python
        ``client_token`` is the app-owned ``clientToken``. The model marks it
        ``idempotencyToken: true``, so left unset botocore mints a fresh UUID
        per call; that turns a retry after an ambiguous failure (accepted
        request, lost response) into a second billable job. The clone VM owns
        one token per form intent and reuses it across such retries.
```

```python
                kwargs: dict[str, Any] = {
                    "applicationId": application_id,
                    "executionRoleArn": execution_role_arn,
                    "jobDriver": {"sparkSubmit": spark_submit},
                    "clientToken": client_token,
                }
```

- [ ] **Step 5: Extend the demo fake and the failed-client stub**

`src/aws_tui/demo/in_memory_emr.py`: add `client_token: str,` to the signature (same position). In `__init__` add `self._run_ids_by_token: dict[str, str] = {}`. Record the token as the seventh tuple element:

```python
        self.calls.append(
            (
                "start_job_run",
                (
                    application_id,
                    execution_role_arn,
                    entry_point,
                    entry_point_arguments,
                    spark_submit_parameters,
                    name,
                    client_token,
                ),
            )
        )
        if self.start_job_run_exc is not None:
            raise self.start_job_run_exc
        existing = self._run_ids_by_token.get(client_token)
        if existing is not None:
            return existing
        new_id = f"r-clone-{self._next_run_seq:03d}"
        self._run_ids_by_token[client_token] = new_id
```

`src/aws_tui/services/emr_serverless/service.py` `_FailedEmrClient.start_job_run`: add `client_token: str,` in the same position.

- [ ] **Step 6: Give the clone VM the token**

`src/aws_tui/vm/emr_serverless/clone_vm.py`:

```python
from uuid import uuid4
```

In `__init__`, after the form-state fields:

```python
        # One idempotency token per form intent. Reused verbatim when a
        # submit attempt raises (the ambiguous-retry case AWS's clientToken
        # exists for), rotated when a field value changes or a submit
        # succeeds, because either of those is a new intent.
        self._client_token: str = uuid4().hex
```

Add the property next to `submitted_id`:

```python
    @property
    def client_token(self) -> str:
        return self._client_token

    def _intent(self) -> tuple[object, ...]:
        return (
            self._name,
            self._execution_role_arn,
            self._entry_point,
            self._entry_point_arguments,
            self._spark_submit_parameters,
        )
```

In `apply_field`, capture `before = self._intent()` right after the `KeyError` guard, and immediately before the `send_value_free(...)` line add:

```python
        if self._intent() != before:
            self._client_token = uuid4().hex
```

In `submit`, pass the token and rotate on success:

```python
        new_id: str = await self._client.start_job_run(
            self._application_id,
            execution_role_arn=self._execution_role_arn,
            entry_point=self._entry_point,
            entry_point_arguments=self._entry_point_arguments,
            spark_submit_parameters=self._spark_submit_parameters,
            client_token=self._client_token,
            name=self._name,
        )
        self._submitted_id = new_id
        self._client_token = uuid4().hex
        return new_id
```

- [ ] **Step 7: Run the EMR suites and mypy**

Run: `uv run pytest tests/unit/domain/test_emr_serverless.py tests/unit/vm/emr_serverless tests/unit/ui/emr_serverless tests/integration/test_demo_mode.py -q -p no:cacheprovider`
Expected: pass.
Run: `uv run mypy`
Expected: `Success`. A failure naming another `start_job_run` implementer means a stub was missed; `grep -rn "def start_job_run" src tests` lists them all.

- [ ] **Step 8: Update the clone-modal docstring**

In `tests/unit/ui/emr_serverless/test_clone_modal.py`, `test_second_submit_while_the_first_is_in_flight_launches_one_job`, replace the sentence starting ```` ``clientToken`` does not save us here ```` through `de-duplicated retry.` with:

```
    The app-owned ``clientToken`` (``JobRunCloneVM.client_token``) covers a
    *retry* of one intent, not two concurrent activations: both in-flight
    submits would carry the same token, and AWS would collapse them, but only
    after two round trips. The guard keeps it to one.
```

- [ ] **Step 9: Record the change in the ledger and the service doc**

`docs/contract-ledger.md` §8 "Known model gaps": replace the `emr-serverless:StartJobRun` row with:

```markdown
| `emr-serverless:StartJobRun` idempotency | `botocore==1.40.61` service model, EMR Serverless API `2021-07-13` | `clientToken` is a required, `idempotencyToken: true` member; the SDK auto-fills a fresh UUID per call, not per user intent. | aws-tui supplies its own token: `JobRunCloneVM` mints one per form intent, reuses it across submit attempts that raise, and rotates it on a field edit or a successful submit, so a retry after an accepted-but-lost request returns the existing run instead of starting a second one. `tests/unit/vm/emr_serverless/test_clone_vm.py` covers reuse and rotation; `tests/unit/domain/test_emr_serverless.py` asserts the wire field. The Athena path supplies `ClientRequestToken` the same way. |
```

`docs/services/emr-serverless.md` §3 "Clone workflow": append a paragraph:

```markdown
Submission carries an app-owned `clientToken`. The token is minted when the
clone form opens and reused if a submit attempt fails before a response
arrives, so pressing submit again after a timeout returns the run AWS already
created instead of starting a second, separately billed job. Editing any field
or a successful submit starts a new intent with a new token.
```

- [ ] **Step 10: Docs tier and changelog**

Run: `uv run pytest tests/docs -q -p no:cacheprovider`
Expected: pass.

Under `### Fixed` add as the first bullet:

```markdown
- **EMR clone retries no longer start a second job.** `StartJobRun` now
  carries an app-owned `clientToken` held by the clone view model for the
  life of one form intent. Previously botocore minted a fresh token per call,
  so retrying after an accepted-but-lost request created a second billable
  run. The token rotates on a field edit or a successful submit.
```

- [ ] **Step 11: Commit**

```bash
git add src/aws_tui/domain/emr_serverless.py src/aws_tui/services/emr_serverless/service.py src/aws_tui/demo/in_memory_emr.py src/aws_tui/vm/emr_serverless/clone_vm.py tests/unit/domain/test_emr_serverless.py tests/unit/vm/emr_serverless/test_clone_vm.py tests/unit/ui/emr_serverless/test_clone_modal.py docs/contract-ledger.md docs/services/emr-serverless.md CHANGELOG.md
git commit -m "fix(emr): own the StartJobRun clientToken per clone intent

botocore fills the modeled idempotencyToken with a fresh UUID per call, so a
retry after an accepted request whose response was lost started a second
billable job. JobRunCloneVM now mints one token per form intent, reuses it
across submit attempts that raise, and rotates it on a field edit or success.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 11. Task 9: Reachable Glue pagination by keyboard, palette, and mouse (R02)

**Files:**
- Modify: `src/aws_tui/infra/keymap_store.py:94-104` (alias pairs) and `:146-153` (defaults)
- Modify: `src/aws_tui/app.py` (palette entry near line 216, registration near line 632, new action near line 2838, disabled set near line 4337, hint recompute senders near line 4281, `on_descendant_focus` near line 4377)
- Modify: `src/aws_tui/ui/widgets/glue/detail_rows.py` (`ResourceListPane`)
- Modify: `src/aws_tui/ui/widgets/glue/page.py` (`GluePage`)
- Modify: `src/aws_tui/vm/chrome/hint_legend_vm.py` (glue tuple near line 47, compact labels near line 124, descriptions near line 166, requirements near line 192)
- Modify: `src/aws_tui/vm/chrome/crash_vm.py:79-86`
- Modify: `docs/keybindings.md` (Glue table near line 150, Action IDs table near line 305), `docs/services/glue.md`
- Test: `tests/unit/infra/test_keymap_store.py:13-20`, `tests/integration/test_keybinding_wiring.py:63-77`, `tests/unit/vm/chrome/test_hint_legend.py:64-140`, `tests/integration/test_command_palette_wiring.py:226-270`, `tests/unit/ui/glue/test_page.py` (append), `tests/docs/test_contract_parity.py` (already covers the docs table)

**Interfaces:**
- Produces action id `glue.load_more`, default key `l` (shared with `athena.load_more`; the pages are never mounted together, same mechanism as `1` for `glue.catalog` / `athena.query`).
- Produces `GluePage.action_load_more() -> None` (dispatches a lifecycle worker) and `GluePage.can_load_more() -> bool`.
- Produces `ResourceListPane.LoadMoreRequested(pane_id: str)` message, posted when the footer is clicked while `has_more` is true.
- Target selection: the focused list wins (ancestor widget id `glue-databases-pane`, `glue-tables-pane`, `glue-jobs-pane`, `glue-runs-pane`, `glue-crawlers-pane`; `glue-table-detail-region` selects partitions). Without a focused list, the first list in the active view that reports `has_more` wins.

- [ ] **Step 1: Write the failing keymap test**

In `tests/unit/infra/test_keymap_store.py` add to `_APPROVED_ALIAS_PAIRS`:

```python
    ("athena.load_more", "glue.load_more"),
```

(Pairs are `sorted(actions)` order from `_collision_pairs`; `athena.` sorts before `glue.`.) Add a default test in `TestDefaults`:

```python
    def test_resolve_glue_load_more_default(self) -> None:
        assert KeymapStore().resolve("glue.load_more") == ("l",)
```

- [ ] **Step 2: Write the failing wiring, legend, palette, and docs expectations**

`tests/integration/test_keybinding_wiring.py` `_EXPECTED`: add after the `V` glue line:

```python
    ("l", "dispatch('glue.load_more')", False, False),
```

`tests/unit/vm/chrome/test_hint_legend.py`: add to `_EXPECTED_ACTION_DESCRIPTIONS` (the dict holding `"glue.time_travel_in_athena": (...)`):

```python
    "glue.load_more": "Load the next available page for the focused Glue list.",
```

to `_EXPECTED_ACTION_REQUIREMENTS`:

```python
    "glue.load_more": "Requires another page in the focused Glue list.",
```

to `_COMPACT_LABELS`:

```python
    "glue.load_more": "more",
```

`tests/integration/test_command_palette_wiring.py` lines 226–270: the seeded fake has page sizes of 100, so no Glue list ever has more; add `"glue.load_more"` to **every** expected disabled set in that test, including the two that are currently `{"glue.time_travel_in_athena"}` and the two that are `set()` (those become `{"glue.load_more"}`).

`docs/keybindings.md`: in the Glue action table (after the "Refresh active view" row) add:

```markdown
| Load more rows in the focused list | `l`, or `:` / `Ctrl+K`, then **Load more Glue rows** | Runs `glue.load_more`. Fetches the next page for the focused Glue list (databases, tables, partitions, jobs, runs, or crawlers); clicking a list footer that reads `more available` does the same. Disabled when the list has no further page or hit its 1,000-item safety limit. |
```

In the Action IDs table after the `glue.time_travel_in_athena` row add:

```markdown
| `glue.load_more` | `l` | yes | Fetch the next page for the focused Glue list |
```

`docs/services/glue.md`: add one paragraph to the section that describes lists (grep for "safety limit" or the list description; if none exists, add under the first section that describes the Catalog view):

```markdown
Lists page through the Glue API 100 rows at a time. A footer reading
`N items · more available` means another page exists: press `l`, run **Load
more Glue rows** from the command palette, or click the footer. Every list
stops at 1,000 rows and then reads `safety limit`.
```

- [ ] **Step 3: Write the failing page tests**

Append to `tests/unit/ui/glue/test_page.py`:

```python
from textual.widgets import Static


@pytest.mark.asyncio
async def test_load_more_action_pages_the_focused_runs_list() -> None:
    fake = seeded_glue()
    fake.run_page_size = 1
    vm, _ = _build_vm(fake)
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_select_view("jobs")
        await drain_workers(app)
        await vm.jobs.select_job("nightly")
        await drain_workers(app)
        assert len(vm.jobs.runs) == 1
        assert vm.jobs.has_more_runs

        page.query_one("#glue-runs-pane", ResourceListPane).option_list.focus()
        await pilot.pause()
        assert page.can_load_more()
        await page.action_load_more()
        await drain_workers(app)

        assert len(vm.jobs.runs) == 2
        assert not vm.jobs.has_more_runs
        assert not page.can_load_more()


@pytest.mark.asyncio
async def test_load_more_action_falls_back_to_the_list_with_another_page() -> None:
    fake = seeded_glue()
    fake.table_page_size = 1
    vm, _ = _build_vm(fake)
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await vm.select_database("analytics")
        await drain_workers(app)
        assert len(vm.catalog.tables) == 1
        app.set_focus(None)
        await pilot.pause()

        assert page.can_load_more()
        await page.action_load_more()
        await drain_workers(app)
        assert len(vm.catalog.tables) == 2


@pytest.mark.asyncio
async def test_clicking_a_more_available_footer_loads_the_next_page() -> None:
    fake = seeded_glue()
    fake.crawler_page_size = 1
    vm, _ = _build_vm(fake)
    await vm.setup()
    app = _GlueApp(vm)

    async with app.run_test() as pilot:
        await pilot.pause()
        page = app.query_one(GluePage)
        await page.action_select_view("crawlers")
        await drain_workers(app)
        assert len(vm.crawlers.crawlers) == 1

        footer = page.query_one("#glue-crawlers-pane .glue-list-footer", Static)
        assert "more available" in str(footer.renderable)
        await pilot.click(footer)
        await drain_workers(app)

        assert len(vm.crawlers.crawlers) == 2
        assert "more available" not in str(footer.renderable)
```

Verify the VM method and collection names with `grep -n "def select_job\|def runs\|def tables\|def crawlers\|def select_database" src/aws_tui/vm/glue/*.py` and adjust the test to the real names before running.

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/infra/test_keymap_store.py tests/unit/ui/glue/test_page.py -q -p no:cacheprovider -k "load_more or glue_load_more or more_available"`
Expected: FAIL with `UnknownAction('glue.load_more')` and `AttributeError: 'GluePage' object has no attribute 'can_load_more'`.

- [ ] **Step 5: Register the action**

`src/aws_tui/infra/keymap_store.py`: add to `APPROVED_ALIAS_PAIRS`:

```python
            ("athena.load_more", "glue.load_more"),
```

and to `DEFAULT_BINDINGS` after `"glue.time_travel_in_athena": ("V",),`:

```python
        # Shares ``l`` with athena.load_more the way ``1``–``3`` share view
        # selection: the two pages are never mounted together.
        "glue.load_more": ("l",),
```

`src/aws_tui/app.py`: after the `glue.query_in_athena` `PaletteEntry` add:

```python
    PaletteEntry(
        "glue.load_more",
        "Load more Glue rows",
        "glue",
        service_ids=_GLUE_SERVICE_IDS,
    ),
```

After `self._actions.register("glue.query_in_athena", ...)` add:

```python
        self._actions.register("glue.load_more", self.action_load_more_glue)
```

Next to `action_query_glue_table_in_athena` add:

```python
    async def action_load_more_glue(self) -> None:
        self.record_action("glue.load_more")
        page = self._glue_page()
        if page is not None:
            await page.action_load_more()
```

In `_recompute_hint_disables`, in the Glue branch after the `glue.time_travel_in_athena` check:

```python
            if not glue_page.can_load_more():
                glue_disabled.add("glue.load_more")
```

In the PropertyChanged handler near line 4281, widen the sender set so jobs/crawlers paging recomputes the legend:

```python
        if glue_page is not None and msg.sender_object in {
            glue_page.vm,
            glue_page.vm.catalog,
            glue_page.vm.catalog.iceberg,
            glue_page.vm.jobs,
            glue_page.vm.crawlers,
        }:
```

In `on_descendant_focus` (near line 4377), make the recompute fire for Glue too, since the target depends on focus:

```python
    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        if self._athena_page() is not None or self._glue_page() is not None:
            self._recompute_hint_disables()
```

`src/aws_tui/vm/chrome/hint_legend_vm.py`: add `"glue.load_more",` to the `"glue"` tuple after `"glue.time_travel_in_athena",`; add `"glue.load_more": "more",` to the compact-label dict; add `"glue.load_more": "Load the next available page for the focused Glue list.",` to the descriptions dict; add `"glue.load_more": "Requires another page in the focused Glue list.",` to the requirements dict.

`src/aws_tui/vm/chrome/crash_vm.py`: add `"glue.load_more",` after `"glue.time_travel_in_athena",` in the safe-to-continue set (it is read-only).

- [ ] **Step 6: Make the footer clickable**

`src/aws_tui/ui/widgets/glue/detail_rows.py`, inside `ResourceListPane`:

```python
    class LoadMoreRequested(Message):
        """The footer was clicked while another page was available."""

        def __init__(self, pane_id: str) -> None:
            super().__init__()
            self.pane_id = pane_id
```

Add `from textual.message import Message` and `from textual import events` to the imports. Track availability: in `__init__` add `self._has_more = False`; in `replace(...)` set `self._has_more = has_more and not limit_reached` before computing the suffix. Add:

```python
    def on_click(self, event: events.Click) -> None:
        if not self._has_more or self.id is None:
            return
        footer = self.query_one(".glue-list-footer", Static)
        if event.widget is not footer and footer not in event.widget.ancestors_with_self:
            return
        event.stop()
        self.post_message(self.LoadMoreRequested(self.id))
```

- [ ] **Step 7: Wire the page**

`src/aws_tui/ui/widgets/glue/page.py`, inside `GluePage`. Add `from collections.abc import Awaitable, Callable` if not imported, and:

```python
    _PANE_LOADERS: ClassVar[dict[str, str]] = {
        "glue-databases-pane": "databases",
        "glue-tables-pane": "tables",
        "glue-jobs-pane": "jobs",
        "glue-runs-pane": "runs",
        "glue-crawlers-pane": "crawlers",
    }

    def _focused_ids(self) -> set[str]:
        focused = self.app.focused
        if focused is None:
            return set()
        return {widget.id for widget in focused.ancestors_with_self if widget.id}

    def _loader(self, target: str) -> tuple[Callable[[], Awaitable[None]], bool]:
        vm = self._vm
        return {
            "databases": (vm.catalog.load_more_databases, vm.catalog.has_more_databases),
            "tables": (vm.catalog.load_more_tables, vm.catalog.has_more_tables),
            "partitions": (vm.catalog.load_more_partitions, vm.catalog.has_more_partitions),
            "jobs": (vm.jobs.load_more_jobs, vm.jobs.has_more_jobs),
            "runs": (vm.jobs.load_more_runs, vm.jobs.has_more_runs),
            "crawlers": (vm.crawlers.load_more_crawlers, vm.crawlers.has_more_crawlers),
        }[target]

    def _load_more_target(self) -> str | None:
        """The list the user means: the focused one, else the first with a page."""
        focused = self._focused_ids()
        for pane_id, target in self._PANE_LOADERS.items():
            if pane_id in focused:
                return target
        if "glue-table-detail-region" in focused:
            return "partitions"
        candidates = {
            "catalog": ("tables", "databases", "partitions"),
            "jobs": ("runs", "jobs"),
            "crawlers": ("crawlers",),
        }.get(self._vm.active_view, ())
        for target in candidates:
            if self._loader(target)[1]:
                return target
        return candidates[0] if candidates else None

    def can_load_more(self) -> bool:
        target = self._load_more_target()
        return target is not None and self._loader(target)[1]

    async def action_load_more(self) -> None:
        target = self._load_more_target()
        if target is None:
            return
        method, has_more = self._loader(target)
        if not has_more:
            return
        # Dispatch, never await: this runs inside the App's message pump and
        # the page fetch is a Glue round trip (see action_refresh_active).
        self._run_lifecycle_worker(method, group="glue-load-more")

    def on_resource_list_pane_load_more_requested(
        self, event: ResourceListPane.LoadMoreRequested
    ) -> None:
        event.stop()
        target = self._PANE_LOADERS.get(event.pane_id)
        if target is None:
            return
        method, has_more = self._loader(target)
        if has_more:
            self._run_lifecycle_worker(method, group="glue-load-more")
```

Import `ResourceListPane` from `aws_tui.ui.widgets.glue.detail_rows` if the page does not already.

- [ ] **Step 8: Run the touched suites**

Run: `uv run pytest tests/unit/infra/test_keymap_store.py tests/unit/ui/glue tests/unit/vm/chrome/test_hint_legend.py tests/integration/test_keybinding_wiring.py tests/integration/test_command_palette_wiring.py tests/unit/ui/test_bindings.py tests/docs -q -p no:cacheprovider`
Expected: pass. A `KeybindingCollision` at app start means the alias pair was added in the wrong order; a docs failure names the table row that is missing.

Run: `uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 9: Snapshot goldens**

Run: `uv run pytest tests/snapshot -q -p no:cacheprovider`
Expected: pass. If a Glue golden changes because the hint legend now shows a `more` chip, review the diff, then re-record with `uv run pytest tests/snapshot --snapshot-update` and commit the goldens with the change.

- [ ] **Step 10: Changelog**

Under `### Fixed` add:

```markdown
- **Glue pagination is reachable.** Databases, tables, partitions, jobs, runs,
  and crawlers reported `more available` but no keyboard, palette, or mouse
  path called the view models' load-more methods, so a filtered runs list
  could hide every older matching run. `l` (`glue.load_more`), the **Load
  more Glue rows** palette command, and clicking a list footer now fetch the
  next page for the focused list.
```

- [ ] **Step 11: Commit**

```bash
git add src/aws_tui/infra/keymap_store.py src/aws_tui/app.py src/aws_tui/ui/widgets/glue/detail_rows.py src/aws_tui/ui/widgets/glue/page.py src/aws_tui/vm/chrome/hint_legend_vm.py src/aws_tui/vm/chrome/crash_vm.py docs/keybindings.md docs/services/glue.md tests CHANGELOG.md
git commit -m "fix(glue): make list pagination reachable by key, palette, and footer click

The Glue view models already paged, but nothing in the UI called load_more_*,
so every list stopped at its first page while the footer promised more.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 12. Task 10: Integrate

- [ ] **Step 1: Full local verification on the integrated branch**

```bash
uv run pytest -q -p no:cacheprovider
uv run pytest tests/integration -m integration -q -p no:cacheprovider
make docs-check
```

Expected: all green (integration `-m integration` needs Docker for S3Mock).

- [ ] **Step 2: Open PRs as a serial train against `develop`**

Two PRs keep review tractable: one for Tasks 1–7 (`docs/audit-findings-2026-09-17`) and one for Tasks 8–9 (`fix/emr-client-token-and-glue-load-more`). Open the second after the first merges and rebase it. Both must pass `ci gate`; rerun once on the two known flaky tests.

- [ ] **Step 3: Promote**

After both land on `develop`, open the promotion PR `develop` → `main`, merge with a merge commit, then confirm `git diff --stat origin/main origin/develop` is empty.

## 13. Self-review

- **Coverage:** DOC-01 → Task 1, DOC-02 → Task 2, DOC-03 → Task 3, DOC-04 → Task 4, DOC-05 → Task 5, DOC-06 → Task 6, DOC-07 → Task 7, R01 → Task 8, R02 → Task 9. R03 and the ruff-version note are closed by PR #220.
- **Placeholders:** none; every step carries its content.
- **Consistency:** `client_token` is the keyword everywhere in Task 8; the recorded tuple index 6 matches the fake's seventh element. `glue.load_more` / `action_load_more_glue` / `GluePage.action_load_more` / `can_load_more` / `LoadMoreRequested` are used identically across Task 9's steps. `_fenced_blocks` is defined in Task 1 and reused in Tasks 3 and 4.
