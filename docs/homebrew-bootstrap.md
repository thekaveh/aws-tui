# Homebrew Bootstrap

This maintainer procedure prepares the first Homebrew formula after a PyPI release.
It is not an installation method available today.
The v0.9.0 development work is present in the repository; package publication remains pending.
Do not run the recipe until the intended wheel and source distribution (sdist) are available on PyPI.

The first complete recipe execution is **gated** on that publication prerequisite under [#267](https://github.com/thekaveh/aws-tui/issues/267).
Local command and failure-path checks do not establish a valid released formula.

## 1. Prerequisites

- The intended `aws-tui` version has both a wheel and sdist on PyPI.
- You have write access to `thekaveh/homebrew-aws-tui` and a local checkout.
- Bash, Python 3.12 with `venv`/pip, Ruby and Homebrew are installed locally.
- Release publication and any later hosted automation have separate authorization under [Releasing](RELEASING.md).

## 2. Steps

```bash
git clone https://github.com/thekaveh/homebrew-aws-tui ~/repos/homebrew-aws-tui
cd ~/repos/homebrew-aws-tui
```

Use the existing checkout if you already have one. Run the following block with Bash from its root.
Set `AWS_TUI_VERSION` to the exact published version; `0.9.0` is the intended first release.

The [upstream helper](https://github.com/tdsmith/homebrew-pypi-poet) installs the `poet` command.
Its `-f` option generates a formula from the target and runtime dependencies installed in the same environment.
The disposable environment below contains all three requirements: the target, its dependency closure and the helper.
The helper uses `pkg_resources`; the Setuptools pin retains that API.

```bash
(
  set -euo pipefail
  AWS_TUI_VERSION=0.9.0
  bootstrap_dir=$(mktemp -d "${TMPDIR:-/tmp}/aws-tui-homebrew.XXXXXX")
  formula_tmp=
  trap 'rm -rf "$bootstrap_dir"; if [ -n "$formula_tmp" ]; then rm -f "$formula_tmp"; fi' EXIT

  python3.12 -m venv "$bootstrap_dir/venv"
  "$bootstrap_dir/venv/bin/python" -m pip install \
    "aws-tui==$AWS_TUI_VERSION" "homebrew-pypi-poet==0.10.0" "setuptools==80.9.0"
  "$bootstrap_dir/venv/bin/python" -m pip check
  "$bootstrap_dir/venv/bin/python" -m pip freeze

  mkdir -p Formula
  formula_tmp=$(mktemp "Formula/.aws-tui.XXXXXX")
  "$bootstrap_dir/venv/bin/poet" -f aws-tui > "$formula_tmp"
  test -s "$formula_tmp"
  ruby -c "$formula_tmp"
  chmod 644 "$formula_tmp"
  mv -f "$formula_tmp" Formula/aws-tui.rb
)
```

The temporary output stays beside the destination, so the final rename uses the same filesystem.
Generator failure, empty output or failed syntax validation leaves an existing formula unchanged.
The trap removes the disposable environment and temporary output.

Review the generated formula before committing or installing it.
Nonempty output and Ruby syntax checks do not verify its dependency resources or Homebrew behavior.
For the first published package, run this exact recipe in a fresh environment and record:

- The installed versions printed by `pip freeze`.
- The top-level sdist URL and SHA-256, checked against the intended version's PyPI metadata and downloaded sdist.
- Every runtime dependency and transitive resource required on the Homebrew platform, excluding helper-only dependencies.
- Each resource's installed version, sdist URL and SHA-256, checked against its published artifacts.
- Correct description, homepage and license instead of generated placeholders.
- A supported versioned Python dependency and matching virtualenv interpreter, plus any required native build dependencies.
- Any manual formula adjustments, followed by the local smoke test below.

The helper can fall back to a newer version or emit empty artifact fields without failing.
Treat those results as invalid; resolve them before installation or commit.

Keep the top-level `url` and `sha256` on separate lines before any `resource` blocks.
The release workflow updates these fields. Replace the placeholders with verified published values:

```ruby
url "<verified published sdist URL>"
sha256 "<verified published sdist SHA-256>"
```

Replace the generated `test do false end` placeholder with a CLI smoke test.
Do not append a second test block:

```ruby
test do
  assert_match version.to_s, shell_output("#{bin}/aws-tui --version")
end
```

## 3. Smoke test locally

```bash
brew install --build-from-source ./Formula/aws-tui.rb
aws-tui --version
brew test aws-tui
brew uninstall aws-tui
```

## 4. Commit + push

```bash
git add Formula/aws-tui.rb
git commit -m "aws-tui 0.9.0 (initial)"
git push origin main
```

## 5. Document the install

In the tap repo's README:

```markdown
# homebrew-aws-tui

Homebrew tap for [aws-tui](https://github.com/thekaveh/aws-tui).

## Install

    brew install thekaveh/aws-tui/aws-tui

## Updates

Tracking [PyPI releases](https://pypi.org/project/aws-tui/);
bumps are opened automatically by the upstream release workflow.
```

## 6. After bootstrap

When hosted release automation is available and authorized, open the repository's **Settings**.
Select **Secrets and variables** → **Actions** → **Variables**.
Set `HOMEBREW_TAP_ENABLED` to `true`.
The workflow skips Homebrew updates until this switch is enabled.

At the next authorized patch release, confirm that `bump-homebrew` opens a tap PR after PyPI publication.
Review and merge that PR. Do not enable or dispatch hosted workflows while the local-only maintenance policy applies.
