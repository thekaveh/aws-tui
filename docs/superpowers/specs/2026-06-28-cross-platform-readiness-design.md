# Cross-Platform Readiness — Design

## Goal

Make aws-tui a first-class citizen on **macOS, Linux, and
Windows** alike. "First-class" means: install instructions that
work, every keystroke that the docs claim works, every file path
that resolves to the right place, every encoding round-trip
correctly, and every CI lane green on all three platforms.

## Where we are today

- **Platforms claimed.** `pyproject.toml` advertises macOS, Linux,
  and Windows classifiers. CI's `unit` job runs the matrix
  `{macos-14, ubuntu-24.04, windows-latest} × {py 3.11, 3.12, 3.13}`.
- **Platforms NOT covered by CI today.**
  - The `integration` job runs on `ubuntu-24.04` only (MinIO
    testcontainer).
  - The `snapshot` job runs on `ubuntu-24.04` only — visual
    goldens were captured in Linux's character cell metrics.
  - The `e2e` job runs on `ubuntu-24.04` only.
  - The `lint-type` + `pkg` jobs run on `ubuntu-24.04` only.
- **Known platform-skipped surfaces in the code** (`pragma: no
  cover` / `if sys.platform`-style guards):
  - `keyring` is in dependencies but config storage falls back
    to plain TOML on platforms where the OS keychain is missing.
  - `platformdirs` is used for cache/config locations — good
    cross-platform default, but not exhaustively audited.
  - Many file-path constructions use `pathlib.Path` (good); a
    handful of f-string path concatenations exist and could
    misbehave on Windows.
  - No explicit POSIX-only system calls (no `os.fork`,
    `signal.SIGCHLD`, etc.) — Textual handles the terminal abstraction.
- **Memory note**: `Working style — macOS-first` (per
  [[user-role]]). Linux and Windows have been a stated goal
  since v0.6; CI proves they BUILD; the question is whether they
  actually WORK end-to-end for a real user.

## Non-goals

- Auto-elevation / admin install flows (msi, pkg). Use the
  standard Python/Homebrew channels.
- Native filesystem watchers (we don't depend on inotify /
  FSEvents / ReadDirectoryChangesW).
- Native notifications (we use in-app toasts, not OS-level).
- iOS / Android (Textual doesn't target them; out of scope).
- WSL as a distinct platform — treat it as Linux.

## Architecture — 5 work-streams

### Stream 1: Audit known platform-sensitive surfaces

Read-the-code pass with a checklist. No fix work, just produce a
report of every site that has known platform differences in
behavior.

**Surfaces to audit:**

1. **Filesystem paths.**
   - All path construction must go through `pathlib.Path` (no
     `os.sep` literals, no `"/"` concatenations).
   - All path comparison must use `Path.resolve()` (Windows is
     case-insensitive; macOS APFS is case-insensitive by default
     unless the user opted into case-sensitive on format).
   - All path serialization in TOML / journal / cache uses
     `Path.as_posix()` for round-trip stability.

2. **Config + cache locations.** `platformdirs` resolves to:
   - macOS: `~/Library/Application Support/aws-tui/` (config),
     `~/Library/Caches/aws-tui/` (cache).
   - Linux: `~/.config/aws-tui/` (XDG), `~/.cache/aws-tui/`.
   - Windows: `%APPDATA%\aws-tui\` (config),
     `%LOCALAPPDATA%\aws-tui\Cache\` (cache).

   Audit: does every read/write of config or cache use the
   resolver, or are there hardcoded `~/.config/...` strings?

3. **AWS profile / SSO discovery.** boto3 + botocore resolve
   `~/.aws/config` + `~/.aws/credentials` on every platform the
   same way; SSO cache lives at `~/.aws/sso/cache/` on all three.
   Audit: any place that reads these directly instead of through
   the boto3 session?

4. **Keychain integration.** `keyring` is conditional: on macOS
   it uses Keychain, on Linux it tries Secret Service (gnome-keyring
   / KWallet), on Windows it uses the Windows Credential Manager.
   - When the platform's keyring backend is missing (headless
     Linux, no D-Bus), `keyring` raises; we fall back to
     plaintext TOML. Audit: is the fallback path actually
     reached on a headless Linux runner?
   - Windows credential names have length limits (~256 chars);
     audit our naming scheme.

5. **Terminal capabilities.**
   - Color support: Textual handles the truecolor / 256 / 16
     downgrade; the only thing we add is the per-theme `.tcss`
     palette. Audit: do we ever hardcode an ANSI escape?
   - Unicode rendering: the ribbon glyph `▌`, the state markers
     (✓ ● ⏸ ↻ ✗ ⊘), the box-drawing characters used by
     Pane borders. Default Windows console fonts (Consolas,
     Cascadia Code) cover all of these; Windows Terminal is the
     recommended runtime. Audit: is there a fallback for legacy
     `cmd.exe`?
   - Mouse + keyboard input: Textual abstracts the differences
     (Win32 Console API vs. termios / ioctl on POSIX). Nothing
     we add should interfere.

6. **Threading + asyncio policy.** Default event loop on Windows
   was `ProactorEventLoop` in py 3.8+, now `WindowsSelectorEventLoop`
   on 3.10+. aioboto3 + textual rely on the default. Audit: do we
   `asyncio.get_event_loop()` anywhere or `set_event_loop_policy`?

7. **Line endings + text encodings.** Reading user files (TOML
   config, transfer journal, etc.). Audit: do all reads/writes
   pass `encoding="utf-8"` explicitly? Default encoding on Windows
   is `cp1252` unless overridden.

8. **Process management.** Anywhere we shell out (e.g.,
   `aws sso login` advice messages). Audit: are these
   instructions correct on PowerShell + cmd.exe + bash + zsh?

**Deliverable:** `docs/cross-platform-audit-2026-06-28.md` —
one section per surface, listing every concrete site checked, the
verdict (clean / risky / broken), and the proposed fix where applicable.

### Stream 2: Expand CI matrix to PROVE the platforms work

Bring the slower jobs into the cross-platform matrix so a
regression on Windows lint or Linux snapshot blocks the merge.

**Changes to `.github/workflows/ci.yml`:**

1. **`lint-type` job**: extend to `{macos-14, ubuntu-24.04, windows-latest}`.
   - Catches pre-commit / mypy strictness differences that only
     surface on one OS (e.g., line-ending hooks, path
     normalization in error messages).
2. **`pkg` job**: extend to the same matrix.
   - Wheel + sdist build must succeed on all three. `twine check`
     must pass on all three.
3. **`snapshot` job**: stay on `ubuntu-24.04` only.
   - **Why**: Textual SVG goldens are byte-sensitive; subtle
     Cairo / Pango differences across platforms would generate
     spurious diffs. Lock the golden-rendering platform to one
     OS (ubuntu) — the snapshots represent the user-facing
     rendering, not OS-specific differences.
4. **`integration` job (MinIO testcontainer)**: stay on
   `ubuntu-24.04` only.
   - **Why**: testcontainers / Docker on Windows + macOS CI
     runners is fragile (rate-limited Docker pulls, longer cold
     starts, occasional flakes). The MinIO surface tests S3
     protocol compliance, which is cross-platform by definition
     — Linux coverage is sufficient.
5. **`e2e` job**: extend to the macOS lane only (skip Windows
   for now — Pilot's keyboard event tests may need Windows-specific
   key code mapping).
   - Documented as `KNOWN: e2e on Windows blocked on T-keymap audit`
     in the progress journal so we don't forget to revisit.

**Deliverable:** CI matrix doubled. Total job count goes from
~10 → ~20, but runtime parallelism is unchanged (independent
runners). Wall-clock CI stays under 5 min.

### Stream 3: Install paths verified per platform

Real install dry-runs for each platform's typical user. Recipes
captured in README + `docs/installing.md`.

For each platform, a tested install path:

| Platform | Channel | Command |
|---|---|---|
| macOS | Homebrew | `brew install thekaveh/aws-tui/aws-tui` |
| macOS | PyPI (via pipx) | `pipx install aws-tui` |
| Linux | PyPI (via pipx) | `pipx install aws-tui` |
| Linux | PyPI (via uv) | `uv tool install aws-tui` |
| Windows | PyPI (via pipx) | `pipx install aws-tui` |
| Windows | PyPI (via uv) | `uv tool install aws-tui` |
| Any | From source | `git clone … && uv sync && uv run aws-tui` |

For each, we verify post-install:
- `aws-tui --version` prints the version.
- The app launches and the LEFT pane lists files.
- The keymap docs match: Tab cycles, arrow keys navigate,
  `Shift+S` switches source.
- The crash dump path (`platformdirs.user_cache_dir() / "crash"`)
  exists and is writable.

**Deliverable:** new `docs/installing.md` with the per-platform
matrix above; README points to it. The doc is the source of
truth for "did this install path work for me on platform X?" so
future contributors don't reinvent the verification.

### Stream 4: Cross-platform release packaging

The release pipeline (PR #95) builds `py3-none-any` wheels on
Linux. That wheel IS cross-platform (pure Python; no native
extensions), but two release-time gates need adding:

1. **Per-platform smoke install** in the release workflow:
   after publishing to TestPyPI, a matrix smoke-install job runs on
   `{macos-14, ubuntu-24.04, windows-latest}` and confirms
   `pip install aws-tui` + `aws-tui --version` works on each.
   Blocks the PyPI publish if any platform fails.
2. **Homebrew formula `bottle` consideration.** The auto-bump
   PR opens a formula with `url` + `sha256` only. macOS users on
   Apple Silicon vs Intel get the same Python source install, so
   no bottle is needed today. If install times become a complaint,
   the tap can ship pre-built bottles per arch later.

**Deliverable:** new `release-smoke` job added to
`.github/workflows/release.yml`; documented in `docs/RELEASING.md`
as the new pre-PyPI gate.

### Stream 5: Documentation polish for cross-platform UX

For each platform, the docs need to use the right idiom:

- **Keyboard hints.** `Cmd+,` on macOS, `Ctrl+,` on Linux+Windows.
  Audit every README chip that mentions a modifier.
- **AWS CLI install link.** Per-platform link to the right AWS
  CLI installer (Homebrew on Mac, .deb / .rpm / curl on Linux,
  msi on Windows).
- **Terminal recommendations.**
  - macOS: Terminal.app (default) or iTerm2.
  - Linux: any modern terminal emulator (gnome-terminal, konsole,
    alacritty, kitty, wezterm).
  - Windows: **Windows Terminal** (recommended). Document the
    "do NOT use cmd.exe" footnote.
- **Font recommendations.** Cascadia Code (Windows default), SF
  Mono (macOS default), DejaVu Sans Mono (Linux default) all
  render the box-drawing + ribbon + colored-glyph palette
  correctly. Call out fonts to AVOID (legacy bitmap fonts).

**Deliverable:** README's "Install" section restructured by
platform (mac / Linux / Windows tabs in the markdown);
`docs/installing.md` carries the detailed per-platform recipes.

## Failure modes & rollback

- **CI matrix expansion is noisy at first.** Per-platform lint /
  type bugs will surface that didn't matter before. Expectation:
  one or two waves of fix PRs while the matrix stabilizes.
  Plan: triage each failure, fix forward, no rollback of the
  matrix itself.
- **Audit finds a deep platform-specific bug.** E.g., a Windows
  file-handle leak in cross-FS copy. Cut a separate plan;
  cross-platform readiness is the umbrella, individual deep
  bugs are children.
- **TestPyPI smoke install fails on Windows.** Block the PyPI
  publish until the cause is identified. Common causes: a
  Windows-only optional dep, a path-separator bug in the
  metadata. The smoke install is exactly the gate that catches
  these BEFORE real users hit them.

## What we'll have when done

- A green CI matrix on `{mac, linux, win} × {py 3.11–3.13}` for
  unit, lint, type, and pkg.
- A real per-platform install path documented and verified in CI.
- An audit doc that lists every platform-sensitive surface with
  a verdict, so future contributors don't re-investigate the
  same questions.
- A release pipeline that pre-flights `pip install` on all three
  platforms before publishing to PyPI.
- README + `docs/installing.md` that don't lie about any
  platform.

## Out of scope (deliberate YAGNI)

- Code-signing the macOS installer / Windows binary. Pure-Python
  package; no installer to sign.
- An "aws-tui-platform-extras" optional extras section in
  `pyproject.toml` (e.g., `pip install "aws-tui[windows]"`). Our
  runtime deps are uniform across platforms; no need.
- Per-platform telemetry. We don't collect any.
- A separate Windows-installer wrapper (`pyinstaller` / `nuitka`
  one-binary distribution). pipx covers the use case.
