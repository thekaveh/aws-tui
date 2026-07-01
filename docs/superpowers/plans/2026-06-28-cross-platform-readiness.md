# 1. Cross-Platform Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove aws-tui works first-class on macOS, Linux, and Windows alike — audit the code, expand CI matrix, document per-platform install paths, gate releases on per-OS smoke installs, and polish docs.

**Architecture:** Five sequential tasks. Task 1 produces a verdict document; the remaining four use those findings to harden CI, document installs, gate releases, and polish docs. No source-code refactors are planned — if Task 1 finds a real bug, that becomes a separate fix PR outside this plan's scope.

**Tech Stack:** GitHub Actions (matrix expansion), `platformdirs` + `pathlib` (already in place — audit only), `uv` (release tool used in CI), `pipx` (documented install path), Homebrew (existing tap repo bring-up flow — uses the existing PR #95 pipeline).

## 1.1. Global Constraints

- Spec: `docs/superpowers/specs/2026-06-28-cross-platform-readiness-design.md`.
- Repo conventions: VMx MVVM, Textual, pytest tiers (unit/integration/snapshot/e2e), 10 themes, snapshot content-presence guards, ruff + mypy strict.
- Pre-commit hooks are the single source of truth for lint+type (`uv run pre-commit run --all-files`).
- Source files modified by tasks must remain under existing ruff + mypy strict gates.
- Python floor: 3.11 (`pyproject.toml` `requires-python`); the CI matrix and docs MUST cite the three supported versions verbatim: `3.11`, `3.12`, `3.13`.
- Supported OS matrix runners (verbatim): `macos-14`, `ubuntu-24.04`, `windows-latest`.
- No emojis in source code unless the user asks.
- `integration` (MinIO testcontainer) and `snapshot` (SVG goldens) jobs stay on `ubuntu-24.04` only — per spec §"Stream 2"; document the WHY in the workflow comment so a future contributor doesn't matrix them.
- Newly added shell snippets in workflows must run on bash on Linux + macOS AND `pwsh` on Windows OR be guarded with `shell: bash` so we don't accidentally invoke PowerShell semantics.
- Markdown files use ATX-style headings (`#`, `##`) consistently with the rest of `docs/`.

---

## 1.2. File Structure

| Path | Lifecycle | Owner task |
|---|---|---|
| `docs/cross-platform-audit-2026-06-28.md` | Create | Task 1 |
| `.github/workflows/ci.yml` | Modify (expand `lint-type` + `pkg` to matrix; add comments locking the Linux-only jobs) | Task 2 |
| `docs/installing.md` | Create | Task 3 |
| `README.md` | Modify (Install section restructure; link to `docs/installing.md`) | Task 5 |
| `.github/workflows/release.yml` | Modify (add `smoke-install` matrix job; reorder gate so PyPI publish depends on it) | Task 4 |
| `docs/RELEASING.md` | Modify (document the smoke gate; add per-OS rollback notes) | Task 4 |

No source code under `src/` is touched. No test files under `tests/` are added or modified. Any code-fix-worthy finding from Task 1 becomes a follow-on PR outside this plan.

---

### 1.2.1. Task 1: Audit platform-sensitive surfaces

**Goal:** Produce a single source-of-truth document — `docs/cross-platform-audit-2026-06-28.md` — listing every platform-sensitive surface in the codebase with a verdict (`clean` / `risky` / `broken`), the concrete file:line citations that back the verdict, and the proposed fix (if any). No code changes.

**Files:**
- Create: `docs/cross-platform-audit-2026-06-28.md`

**Interfaces:**
- Consumes: nothing — this is a research task.
- Produces: a document that Tasks 2 / 4 cite when justifying CI matrix decisions and smoke-install behaviour. Specifically Task 4's smoke job uses the audit's "filesystem paths" and "config + cache locations" findings to know what user-visible artefacts to inspect after `pip install`.

**Audit checklist** (each gets its own section in the output document with verdict + citations):

1. Filesystem paths — `Path()` vs `os.path.join` vs f-string concatenation.
2. Config + cache locations — `platformdirs.user_config_dir` / `user_cache_dir` usage; check `paths.py` is the only resolver and every consumer routes through it.
3. AWS profile / SSO discovery — `~/.aws/{config,credentials,sso/cache}` hardcoded paths in `infra/aws_session.py` and `infra/connection_resolver.py`; confirm boto3 handles these uniformly on all three OSes.
4. Keychain integration — `keyring` backend differences; fallback to plaintext TOML when no backend is available.
5. Terminal capabilities — Unicode glyphs (`▌`, `✓ ● ⏸ ↻ ✗ ⊘`), box-drawing borders, mouse + keyboard input.
6. Threading + asyncio policy — any `asyncio.set_event_loop_policy` calls or `WindowsSelectorEventLoop` references.
7. Line endings + text encodings — every file `open()` should pass `encoding="utf-8"` or use `Path.read_text(encoding="utf-8")`.
8. Process management — any place that shells out via `subprocess`, `os.system`, etc., with platform-specific commands (`aws sso login` documentation strings).

**Steps:**

- [ ] **Step 1: Create the document skeleton with all eight section headers.**

Run from the repo root:

```bash
mkdir -p docs && cat > docs/cross-platform-audit-2026-06-28.md << 'EOF'
# Cross-Platform Audit — 2026-06-28

**Branch:** `<branch-name>` &nbsp;&nbsp; **Commit:** `<short-sha>`
**Spec:** [`docs/superpowers/specs/2026-06-28-cross-platform-readiness-design.md`](superpowers/specs/2026-06-28-cross-platform-readiness-design.md)

Audit of every platform-sensitive surface in the aws-tui codebase. One section per surface; each carries a **verdict** (`clean` / `risky` / `broken`) backed by concrete `file:line` citations. Issues flagged here become follow-on PRs outside the cross-platform-readiness plan.

## 1. Filesystem paths

_Verdict:_ TBD.

## 2. Config + cache locations

_Verdict:_ TBD.

## 3. AWS profile / SSO discovery

_Verdict:_ TBD.

## 4. Keychain integration

_Verdict:_ TBD.

## 5. Terminal capabilities

_Verdict:_ TBD.

## 6. Threading + asyncio policy

_Verdict:_ TBD.

## 7. Line endings + text encodings

_Verdict:_ TBD.

## 8. Process management

_Verdict:_ TBD.

## Summary

Total verdicts: `<N>` clean, `<N>` risky, `<N>` broken. Follow-on PR plan: `<list>`.
EOF
```

Replace `<branch-name>` with the current branch (run `git rev-parse --abbrev-ref HEAD`) and `<short-sha>` with `git rev-parse --short HEAD`.

- [ ] **Step 2: Fill section 1 (Filesystem paths).**

Run from the repo root:

```bash
# Every place that constructs a path. Inspect each hit and judge: Path()-based + cross-platform clean, or platform-specific?
rg -n "Path\(|os\.path\.join|os\.sep|os\.path\.dirname|/\s*\"\$HOME|/\s*\"~" src/ | grep -v "test_" | grep -v "_test.py" | sort
```

Open the doc and replace section 1 with the verdict template:

```markdown
## 1. Filesystem paths

_Verdict:_ **clean** | **risky** | **broken** — pick one.

**Findings:**

- `src/aws_tui/infra/paths.py:50,54` — `Path.home() / ".config" / _APP_NAME` and `... / ".cache" / _APP_NAME`. These are the LEGACY fallback paths preferred when an existing directory exists from a pre-platformdirs install. Cross-platform safe (`Path.home()` is portable); the fallback is read-only on a fresh Windows install (no existing `~/.config/aws-tui` to find).
- `src/aws_tui/infra/aws_session.py:64,68` — `Path.home() / ".aws" / ...`. boto3 SSO + config locations; correct on every OS boto3 supports.
- `src/aws_tui/infra/connection_resolver.py:78,82` — same `~/.aws/config` + `~/.aws/credentials` pattern.
- `src/aws_tui/infra/log_sink.py:71,79,81` — `Path(self.baseFilename)` for log rotation; baseFilename comes from `paths.cache_dir()` so the location is already platformdirs-resolved.
- `src/aws_tui/infra/crash_dump.py:30,61` — same pattern; crash dump path is `paths.cache_dir() / "crash"`, fully resolved through paths.py.

**Conclusion:** All path construction goes through `pathlib.Path` and `paths.py` is the single resolver. Verdict: **clean**.
```

Substitute the actual file:line citations from your grep output. If the grep finds NEW hits not listed here, add them. If a hit is platform-specific, write the verdict as `risky` or `broken` and describe the problem.

- [ ] **Step 3: Fill section 2 (Config + cache locations).**

Run from the repo root:

```bash
# Every consumer of paths.py.
rg -n "from aws_tui\.infra\.paths|from \.paths import|paths\." src/ | sort

# Every place that touches a known config filename.
rg -n "config\.toml|connections\.toml|transfers/journal" src/ | sort
```

Replace section 2:

```markdown
## 2. Config + cache locations

_Verdict:_ **clean** | **risky** | **broken**

**Findings:**

- `src/aws_tui/infra/paths.py:66,75` — `Path(user_config_dir(_APP_NAME, appauthor=False, roaming=True))` and `Path(user_cache_dir(_APP_NAME, appauthor=False))`. The single resolver; `roaming=True` puts Windows config under `%APPDATA%` (synced across machines via roaming profile), `appauthor=False` removes the redundant author segment Windows would otherwise inject.
- `src/aws_tui/<every consumer>` — list every site that calls `paths.config_dir()` or `paths.cache_dir()` and confirm none manually constructs a `~/.config/aws-tui/...` string outside this resolver.

**Conclusion:** [your call]
```

- [ ] **Step 4: Fill section 3 (AWS profile / SSO discovery).**

Same pattern. Run:

```bash
rg -n "\.aws/sso|aws_session|UnauthorizedSSOTokenError|session\.get_credentials" src/ | sort
```

Verdict + citations.

- [ ] **Step 5: Fill section 4 (Keychain integration).**

Run:

```bash
rg -n "keyring\.|import keyring|NoKeyringError|KeychainError" src/ | sort
```

Then on a `Linux` machine (or `ubuntu-24.04` GitHub Actions runner if no Linux box is handy), confirm the fallback path triggers when D-Bus / Secret Service is unavailable. The verdict notes whether the fallback is reachable and what it stores.

- [ ] **Step 6: Fill section 5 (Terminal capabilities).**

Run:

```bash
# Every place we hardcode a Unicode glyph.
rg -n "▌|✓|●|⏸|↻|✗|⊘|🔥|🪣|⚙️" src/ tests/ | sort -u

# Any place we emit raw ANSI escapes.
rg -n "\\\\033\\[|\\\\x1b\\[" src/ | sort
```

Inspect each glyph site. Windows Terminal and macOS Terminal.app + iTerm2 both render every glyph the codebase uses today. Default Linux terminals (gnome-terminal, konsole, alacritty, kitty, wezterm) likewise. Note explicitly which terminals are KNOWN to break (`cmd.exe` legacy console — not Windows Terminal).

- [ ] **Step 7: Fill section 6 (Threading + asyncio policy).**

```bash
rg -n "set_event_loop|WindowsSelectorEventLoop|ProactorEventLoop|asyncio\.get_event_loop\(\)\b" src/
```

If the grep returns zero hits: verdict **clean** with the citation `(no hits — we rely on Textual's default loop policy)`.

- [ ] **Step 8: Fill section 7 (Line endings + text encodings).**

```bash
# Every file open without explicit encoding.
rg -n "\.open\(|open\(" src/ | grep -v "encoding=" | grep -v "binary" | grep -v "rb\"" | grep -v "wb\"" | sort
```

For every text-mode `open()` (or `Path.read_text()` / `Path.write_text()`) without `encoding="utf-8"`, document it. Even one bare `open()` in a text path is a Windows footgun.

- [ ] **Step 9: Fill section 8 (Process management).**

```bash
rg -n "subprocess\.|os\.system|os\.popen" src/ | sort
```

If we shell out to `aws sso login` or any AWS CLI command anywhere, note the per-platform syntax (PowerShell vs bash quoting).

- [ ] **Step 10: Fill the Summary block with a verdict tally.**

Replace the placeholder Summary with the actual count of `clean` / `risky` / `broken` verdicts across sections 1-8, and list any follow-on PRs you'd open (e.g., "Fix bare `open()` in `config_io.py:42`").

- [ ] **Step 11: Verify the doc renders cleanly.**

Run from the repo root:

```bash
test -f docs/cross-platform-audit-2026-06-28.md && echo "doc present"
grep -c "^## " docs/cross-platform-audit-2026-06-28.md
```

Expected output:
```
doc present
9
```

Eight section headers + the Summary header = nine `##` lines.

- [ ] **Step 12: Run pre-commit on the new doc.**

```bash
uv run pre-commit run --files docs/cross-platform-audit-2026-06-28.md
```

Expected: all hooks pass (trailing-whitespace, end-of-file-fixer, check-yaml all skip a markdown file; nothing should fail).

- [ ] **Step 13: Commit.**

```bash
git add docs/cross-platform-audit-2026-06-28.md
git commit -m "docs(cross-platform): audit platform-sensitive surfaces (Task 1)"
```

---

### 1.2.2. Task 2: Expand CI matrix for `lint-type` + `pkg` jobs

**Goal:** Run lint, type-check, layer rules, build, and twine-check on all three OSes × all three supported Python versions. Catch platform-specific lint/type/build regressions before they reach a release.

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 1's audit findings — specifically the line-endings + encoding verdict, which determines whether we add a `git config core.autocrlf input` step to the Windows checkout (to avoid CRLF in TOML test fixtures).
- Produces: a green CI matrix that proves the build artifacts work on every supported OS / Python combination. Task 4 reuses the same matrix shape for the release smoke job.

**Steps:**

- [ ] **Step 1: Replace the `lint-type` job in `.github/workflows/ci.yml` with the matrix version.**

Open `.github/workflows/ci.yml`. Find the current `lint-type:` job (lines 92-115). Replace its `runs-on:` and steps blocks so the final shape is:

```yaml
  lint-type:
    name: lint + type (${{ matrix.os }} / py${{ matrix.python }})
    strategy:
      fail-fast: false
      matrix:
        os: [macos-14, ubuntu-24.04, windows-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - name: install Python ${{ matrix.python }}
        run: uv python install ${{ matrix.python }}
      - name: sync
        run: uv sync --python ${{ matrix.python }}
      # Layer rules — POSIX shell only. Skip on Windows; the
      # rules are platform-neutral and a single OS proves them.
      - name: layer rules
        if: runner.os != 'Windows'
        run: ./scripts/check-layers.sh
      # Surfaces every hook defined in .pre-commit-config.yaml:
      # ruff, ruff-format, mypy, end-of-file-fixer, trailing-whitespace,
      # check-yaml, check-toml, mixed-line-ending, taplo-format,
      # taplo-lint. Single source of truth — when a hook fails CI
      # fails; a contributor who skips ``pre-commit install`` or
      # commits with ``--no-verify`` is still gated.
      - name: pre-commit (all hooks)
        run: uv run pre-commit run --all-files --show-diff-on-failure
        shell: bash
```

Note the two Windows-specific accommodations:

- `if: runner.os != 'Windows'` on the layer-rules step. `scripts/check-layers.sh` is bash-only; running it on Windows requires Git Bash and has no payoff (the layer rules are platform-neutral).
- `shell: bash` on the pre-commit step. Without it, GitHub Actions defaults to PowerShell on Windows runners; the pre-commit hook runner uses `sh` syntax internally that PowerShell mangles.

- [ ] **Step 2: Replace the `pkg` job in `.github/workflows/ci.yml` with the matrix version.**

Find the current `pkg:` job (lines 117-138). Replace so the final shape is:

```yaml
  pkg:
    name: package build (${{ matrix.os }} / py${{ matrix.python }})
    strategy:
      fail-fast: false
      matrix:
        os: [macos-14, ubuntu-24.04, windows-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - name: install Python ${{ matrix.python }}
        run: uv python install ${{ matrix.python }}
      - name: sync
        run: uv sync --python ${{ matrix.python }}
      - name: build wheel + sdist
        run: uv build
      - name: twine check
        run: uv run twine check dist/*
        shell: bash
      # Only one of the matrix legs needs to upload the artifact;
      # the wheel is pure-Python (``py3-none-any``) so all legs
      # produce the same bytes. Pick the canonical leg (ubuntu /
      # py3.12) to avoid actions/upload-artifact name collisions.
      - name: upload build artifacts
        if: matrix.os == 'ubuntu-24.04' && matrix.python == '3.12'
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/
          retention-days: 14
```

The `if:` guard on the upload step is critical. `actions/upload-artifact@v4` rejects two uploads with the same `name:`, so without the guard the second matrix leg crashes with `Conflicting artifact found for name 'dist'`.

- [ ] **Step 3: Add a comment block above the `integration` and `snapshot` jobs documenting why they STAY on `ubuntu-24.04` only.**

Find the `integration:` block (around line 47) and prepend:

```yaml
  # ── ubuntu-only jobs ──────────────────────────────────────────────────
  #
  # The two jobs below stay on ubuntu-24.04 and DO NOT matrix across OSes.
  # The trade-off is deliberate:
  #
  # - ``integration``: MinIO via testcontainers needs Docker. Docker
  #   support on macOS + Windows GitHub runners is fragile (rate-limited
  #   pulls, cold-start flakes, missing buildkit on Windows). The MinIO
  #   surface tests S3-protocol compliance, which is platform-neutral
  #   by definition; one OS is sufficient.
  #
  # - ``snapshot``: Textual SVG goldens are byte-sensitive. Cairo /
  #   Pango / font-fallback differences across OSes generate spurious
  #   diffs that no human can review. Lock the golden-rendering platform
  #   to ubuntu so the snapshots represent the user-facing rendering,
  #   not OS-specific artefacts.
  #
  # If a future contributor matrixes these jobs, expect noisy CI and
  # roll the change back.
  integration:
```

Find the `snapshot:` block and prepend the SAME block (copy-paste; the comment belongs above both for visibility).

Actually — to keep the comment block DRY, place it ONCE above the `integration:` block (since they're adjacent in the file), then add a one-line pointer above `snapshot:`:

```yaml
  # See "ubuntu-only jobs" comment above ``integration``.
  snapshot:
```

- [ ] **Step 4: Validate the workflow YAML is well-formed.**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml').read())" && echo "yaml ok"
```

Expected:
```
yaml ok
```

- [ ] **Step 5: Verify the workflow renders the expected job count locally.**

```bash
python3 -c "
import yaml
spec = yaml.safe_load(open('.github/workflows/ci.yml').read())
jobs = spec['jobs']
print('jobs:', sorted(jobs))
print('lint-type matrix:', jobs['lint-type'].get('strategy', {}).get('matrix', {}))
print('pkg matrix:', jobs['pkg'].get('strategy', {}).get('matrix', {}))
"
```

Expected (formatting may vary by Python version):
```
jobs: ['e2e', 'integration', 'lint-type', 'pkg', 'snapshot', 'unit']
lint-type matrix: {'os': ['macos-14', 'ubuntu-24.04', 'windows-latest'], 'python': ['3.11', '3.12', '3.13']}
pkg matrix: {'os': ['macos-14', 'ubuntu-24.04', 'windows-latest'], 'python': ['3.11', '3.12', '3.13']}
```

- [ ] **Step 6: Run the local pre-commit gate on the workflow file.**

```bash
uv run pre-commit run --files .github/workflows/ci.yml
```

Expected: all hooks pass (check-yaml will run and validate the file; other hooks no-op on yaml).

- [ ] **Step 7: Commit.**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(cross-platform): matrix lint-type + pkg across {mac, linux, win} × py{3.11-3.13} (Task 2)"
```

- [ ] **Step 8: Push the branch and observe the first CI run.**

```bash
git push
```

The newly-matrixed jobs WILL produce platform-specific failures on the first run (line endings, path separators in mypy error messages, pre-commit shell mismatches). Fix forward in separate commits to this branch — do NOT roll back the matrix. Each failure is a real cross-platform bug.

When CI is green on every matrix leg, Task 2 is done.

---

### 1.2.3. Task 3: Per-platform install paths documented

**Goal:** Create `docs/installing.md` — the single source of truth for "how do I install aws-tui on platform X." Each install path is concrete (exact command), tested by hand on the platform, and pinned with a "verified on `YYYY-MM-DD` against vX.Y.Z" note.

**Files:**
- Create: `docs/installing.md`

**Interfaces:**
- Consumes: nothing — the commands are platform-known.
- Produces: a document Task 4's smoke-install job uses to know which install command each OS matrix leg should run. Task 5 cross-references it from the README.

**Steps:**

- [ ] **Step 1: Create the document skeleton.**

```bash
cat > docs/installing.md << 'EOF'
# Installing aws-tui

aws-tui ships as a pure-Python `py3-none-any` wheel. Every platform install path below produces the same binary; the differences are which package manager you ask to run it.

## Quick install (any platform)

If you have `pipx` (recommended) or `uv` already:

```sh
pipx install aws-tui
# 2. or
uv tool install aws-tui
```

Then:

```sh
aws-tui --version
aws-tui
```

The CLI command name is always `aws-tui`. No imports — it's a terminal app.

## macOS

### Recommended: Homebrew

```sh
brew install thekaveh/aws-tui/aws-tui
```

This taps `thekaveh/homebrew-aws-tui` and installs aws-tui into its own Homebrew-managed virtualenv. The `aws-tui` binary lands on your PATH automatically.

### Alternative: pipx

```sh
# 3. One-time:
brew install pipx
pipx ensurepath

# 4. Then:
pipx install aws-tui
```

### Alternative: uv

```sh
# 5. One-time:
brew install uv

# 6. Then:
uv tool install aws-tui
```

## Linux

### Recommended: pipx

Most distributions package `pipx`. On Debian/Ubuntu:

```sh
sudo apt install pipx
pipx ensurepath

pipx install aws-tui
```

On Fedora:

```sh
sudo dnf install pipx
pipx ensurepath

pipx install aws-tui
```

If your distribution doesn't ship pipx, install via pip:

```sh
python3 -m pip install --user pipx
python3 -m pipx ensurepath

pipx install aws-tui
```

### Alternative: uv

```sh
# 7. One-time (see https://docs.astral.sh/uv/getting-started/installation/):
curl -LsSf https://astral.sh/uv/install.sh | sh

# 8. Then:
uv tool install aws-tui
```

### Headless servers

If you're installing on a headless Linux box (CI runner, SSH host without a desktop session), the `keyring` package will fall back to plaintext TOML storage because no D-Bus / Secret Service is available. That's expected and supported.

## Windows

**Recommended terminal: [Windows Terminal](https://aka.ms/terminal).** The legacy `cmd.exe` console renders some Unicode glyphs aws-tui uses as boxes; Windows Terminal renders them as intended.

### Recommended: pipx

```powershell
# 9. One-time:
python -m pip install --user pipx
python -m pipx ensurepath

# 10. Restart the terminal so pipx is on PATH, then:
pipx install aws-tui
```

### Alternative: uv

```powershell
# 11. One-time (see https://docs.astral.sh/uv/getting-started/installation/):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 12. Then:
uv tool install aws-tui
```

### Encoding

Windows defaults to `cp1252` for text files. aws-tui reads and writes all config files explicitly as UTF-8, so the OS default doesn't matter — but if you hand-edit `~\AppData\Roaming\aws-tui\config.toml`, save it as UTF-8.

## From source (any platform)

```sh
git clone https://github.com/thekaveh/aws-tui
cd aws-tui

# 13. uv is required for the dev workflow; install per the official docs.
uv sync

# 14. Run without installing:
uv run aws-tui

# 15. Or build + install the wheel:
uv build
pipx install dist/aws_tui-*.whl
```

## Verifying the install

After install, on every platform:

```sh
aws-tui --version
```

The first run also creates these paths the first time:

- **macOS**: `~/Library/Application Support/aws-tui/` (config), `~/Library/Caches/aws-tui/` (cache + logs + crash dumps).
- **Linux**: `~/.config/aws-tui/` (config), `~/.cache/aws-tui/` (cache + logs + crash dumps).
- **Windows**: `%APPDATA%\aws-tui\` (config), `%LOCALAPPDATA%\aws-tui\Cache\` (cache + logs + crash dumps).

If the directories don't exist after `aws-tui --version`, that's a bug — file an issue.

## Uninstalling

```sh
# 16. Homebrew (macOS):
brew uninstall aws-tui

# 17. pipx (any platform):
pipx uninstall aws-tui

# 18. uv tool (any platform):
uv tool uninstall aws-tui
```

The uninstall does NOT remove your config or cache directories. Delete them by hand if desired.

## Verified install paths

This document is tested by the `smoke-install` job in `.github/workflows/release.yml`, which runs the "Recommended: pipx" command on each platform in CI before every PyPI publish. The release fails if any platform's recommended install fails.

Manual sign-off log:

| Date | Version | Platform | Path | Result |
|---|---|---|---|---|
| _TBD by maintainer on first sign-off_ | _v0.8.0_ | macOS 14 | Homebrew | _PENDING_ |
| _TBD_ | _v0.8.0_ | macOS 14 | pipx | _PENDING_ |
| _TBD_ | _v0.8.0_ | Ubuntu 24.04 | pipx | _PENDING_ |
| _TBD_ | _v0.8.0_ | Windows 11 / Windows Terminal | pipx | _PENDING_ |
EOF
```

- [ ] **Step 2: Verify the document renders.**

```bash
test -f docs/installing.md && echo "doc present"
grep -c "^## " docs/installing.md
```

Expected:
```
doc present
8
```

(eight `##` sections: Quick / macOS / Linux / Windows / From source / Verifying / Uninstalling / Verified)

- [ ] **Step 3: Pre-commit.**

```bash
uv run pre-commit run --files docs/installing.md
```

Expected: all hooks pass.

- [ ] **Step 4: Commit.**

```bash
git add docs/installing.md
git commit -m "docs(cross-platform): per-platform install paths (Task 3)"
```

---

### 18.1.1. Task 4: Release-time smoke install gate

**Goal:** Add a `smoke-install` matrix job to `.github/workflows/release.yml` that downloads the just-built wheel and runs the "Recommended: pipx" install on each of `{macos-14, ubuntu-24.04, windows-latest}`. The PyPI publish step waits on it; a smoke-install failure blocks the publish.

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `docs/RELEASING.md`

**Interfaces:**
- Consumes: the `dist` artifact uploaded by the `verify` job in `.github/workflows/release.yml` (already exists per PR #95). The `aws-tui` CLI command per Task 3's install-path verification.
- Produces: a gating check on the release pipeline. Other jobs in `release.yml` already exist; this slots into the dependency graph between `verify` and `publish-pypi`.

**Steps:**

- [ ] **Step 1: Add the `smoke-install` job to `.github/workflows/release.yml`.**

Open `.github/workflows/release.yml`. Find the `publish-pypi:` job (it has `needs: verify`). Insert a new `smoke-install:` job between `verify:` and `publish-pypi:`.

The new job (paste verbatim, placement is between the two existing jobs):

```yaml
  # ── Per-OS smoke install of the freshly-built wheel ───────────────────
  #
  # Gates the PyPI publish. We install the wheel produced by the
  # ``verify`` job directly (no TestPyPI round-trip — that path can't run
  # before the project name is registered) and confirm ``aws-tui --version``
  # works on every supported OS. Failure here blocks publish-pypi.
  #
  # The recommended install on each platform comes from
  # ``docs/installing.md`` § per-platform sections.
  smoke-install:
    name: smoke install (${{ matrix.os }})
    needs: verify
    strategy:
      fail-fast: false
      matrix:
        os: [macos-14, ubuntu-24.04, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: download built wheel + sdist
        uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/

      - name: install pipx
        run: python -m pip install --user pipx
        shell: bash

      - name: ensure pipx on PATH (unix)
        if: runner.os != 'Windows'
        run: python -m pipx ensurepath
        shell: bash

      - name: ensure pipx on PATH (windows)
        if: runner.os == 'Windows'
        run: |
          python -m pipx ensurepath
          # Windows ensurepath updates the user registry but doesn't
          # propagate to THIS shell — add the pipx bin dir for the
          # rest of the job manually.
          $pipxBin = python -c "import pipx.paths; print(pipx.paths.DEFAULT_PIPX_BIN_DIR)"
          echo "$pipxBin" >> $env:GITHUB_PATH
        shell: pwsh

      - name: install the just-built wheel via pipx
        run: pipx install dist/aws_tui-*.whl
        shell: bash

      - name: verify CLI runs
        run: aws-tui --version
        shell: bash
```

- [ ] **Step 2: Update `publish-pypi`'s dependency to wait on `smoke-install`.**

In the same file, find the `publish-pypi:` job. Change its `needs:` line from:

```yaml
    needs: verify
```

to:

```yaml
    needs: [verify, smoke-install]
```

This is the gate. If any matrix leg of `smoke-install` fails, `publish-pypi` will not run.

- [ ] **Step 3: Validate the workflow YAML.**

```bash
python3 -c "import yaml; spec = yaml.safe_load(open('.github/workflows/release.yml').read()); print(sorted(spec['jobs']))"
```

Expected:
```
['bump-homebrew', 'publish-github', 'publish-pypi', 'smoke-install', 'verify']
```

- [ ] **Step 4: Update `docs/RELEASING.md` to mention the new gate.**

Open `docs/RELEASING.md`. Find the routine-release section (`## 1. Routine release`). After the `gh run watch` line, prepend a new bullet to the surrounding paragraph or add a paragraph:

```markdown
The pipeline runs `verify` → `smoke-install` → (gated) `publish-pypi` → `publish-github` + `bump-homebrew`. The `smoke-install` matrix pip-installs the freshly built wheel on macOS, Linux, and Windows and runs `aws-tui --version` on each. A failure on ANY OS blocks the PyPI publish — there is no "skip just Windows" lever. Fix forward and re-run the workflow on the same tag (the `verify` job's artifact upload uses the same tag's HEAD, so re-runs are deterministic).
```

Also under `## 3. Rollback`, add this bullet after the "Tag/version mismatch" one:

```markdown
- **Smoke install fails on one OS.** No PyPI artefact has shipped yet (the gate caught it). The actionable next step depends on whether the failure is a wheel bug (rare — same wheel works on the other OSes) or a CI environment bug (most common — pipx version drift, GitHub-hosted runner change). Both are fix-forward; do not yank the tag.
```

- [ ] **Step 5: Pre-commit on the changed files.**

```bash
uv run pre-commit run --files .github/workflows/release.yml docs/RELEASING.md
```

Expected: all hooks pass.

- [ ] **Step 6: Commit.**

```bash
git add .github/workflows/release.yml docs/RELEASING.md
git commit -m "ci(cross-platform): smoke-install gate before PyPI publish (Task 4)"
```

---

### 18.1.2. Task 5: README install section restructure + per-platform polish

**Goal:** The README is the front door — restructure the "Install" section so a first-time visitor on any of the three OSes sees their command verbatim, without scrolling past the other two. Cross-link to `docs/installing.md` for the full per-path matrix. Add a one-line "Terminal recommendation" callout for Windows.

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `docs/installing.md` from Task 3.
- Produces: a README that reads correctly to a new user on any of the three platforms.

**Steps:**

- [ ] **Step 1: Find the current Install section.**

```bash
grep -n "^## " README.md | head -20
```

Note the line range of the existing Install / Quickstart section so the next step can target it.

- [ ] **Step 2: Replace the Install section with the new per-platform shape.**

Find the section heading (likely `## Install` or similar) and replace its body with:

```markdown
## Install

Pick your platform. Each command produces the same binary; the differences are which package manager you ask to run it. See [`docs/installing.md`](docs/installing.md) for alternative install paths.

### macOS

```sh
brew install thekaveh/aws-tui/aws-tui
```

### Linux

```sh
sudo apt install pipx           # or your distro's equivalent
pipx ensurepath
pipx install aws-tui
```

### Windows

> **Use [Windows Terminal](https://aka.ms/terminal)**, not the legacy `cmd.exe` console. Windows Terminal renders the box-drawing and Unicode glyphs aws-tui uses; the legacy console renders some of them as boxes.

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
# 19. Restart the terminal, then:
pipx install aws-tui
```

### Verify

```sh
aws-tui --version
```
```

Keep the exact text of the rest of the README unchanged. Do NOT touch the project status line, features list, etc.

- [ ] **Step 3: Find every keyboard-shortcut chip in the README and ensure platform parity.**

```bash
rg -n "\`Cmd\+|\`Ctrl\+|\`⌘|Cmd-|Ctrl-" README.md
```

For every hit, audit: if the chip is platform-specific (`Cmd+,`), add the cross-platform variant (`Cmd+, / Ctrl+,`). The audit's verdict goes into Task 1's doc Section 5 if you missed it there.

- [ ] **Step 4: Verify the README renders.**

```bash
head -20 README.md
test -f docs/installing.md && grep -c "docs/installing.md" README.md
```

Expected:
```
... (first 20 lines of README) ...
1
```

(One internal link to `docs/installing.md`.)

- [ ] **Step 5: Pre-commit.**

```bash
uv run pre-commit run --files README.md
```

Expected: all hooks pass.

- [ ] **Step 6: Commit.**

```bash
git add README.md
git commit -m "docs(cross-platform): per-platform README install section (Task 5)"
```

---

## 19.1. Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| Stream 1: Audit | Task 1 |
| Stream 2: Expand CI matrix | Task 2 |
| Stream 3: Per-platform install paths verified | Task 3 + Task 4 (Task 3 documents; Task 4 verifies in CI) |
| Stream 4: Release-time pip-install smoke matrix | Task 4 |
| Stream 5: Docs polish | Task 3 (installing.md) + Task 5 (README) |

All five spec workstreams are mapped.

**2. Placeholder scan**

The plan contains TWO intentional placeholders that the implementer MUST fill in: `<branch-name>` and `<short-sha>` in Task 1 Step 1. Both are explicitly called out with substitution commands. No `TBD` / `TODO` / "implement later" left in the plan body itself; the audit doc skeleton contains `TBD` strings as the literal initial content (Step 1) that subsequent steps (Steps 2-10) replace.

**3. Type / signature consistency**

The plan touches three workflow shapes (`lint-type`, `pkg`, `smoke-install`) and two documents (`installing.md`, `cross-platform-audit-2026-06-28.md`). The matrix axes (`os` + `python`) are consistent across Tasks 2 + 4. The `needs:` chain in Task 4 (`needs: [verify, smoke-install]`) matches the job name added in the same step.
