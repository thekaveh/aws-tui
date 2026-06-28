# Bootstrapping the Homebrew formula

Run this **once**, immediately after the first PyPI release
(v0.8.0) lands. After that, the `bump-homebrew` job in
`.github/workflows/release.yml` opens PRs against the tap
automatically — bootstrapping just gets the first formula in
place.

## Prerequisites

- v0.8.0 wheel + sdist on PyPI: <https://pypi.org/project/aws-tui/0.8.0/>
- The `thekaveh/homebrew-aws-tui` repo exists and is empty.
- `brew` installed locally for the smoke test.

## Steps

```bash
# 1. Clone the tap repo.
git clone https://github.com/thekaveh/homebrew-aws-tui ~/repos/homebrew-aws-tui
cd ~/repos/homebrew-aws-tui

mkdir -p Formula

# 2. Compute the published sdist's sha256.
PKG_URL="https://files.pythonhosted.org/packages/source/a/aws-tui/aws_tui-0.8.0.tar.gz"
curl --fail --location --silent -o /tmp/aws-tui.tar.gz "$PKG_URL"
PKG_SHA="$(shasum -a 256 /tmp/aws-tui.tar.gz | awk '{print $1}')"
echo "PKG_URL=$PKG_URL"
echo "PKG_SHA=$PKG_SHA"

# 3. Generate the resource stanzas for runtime deps. brew has a
#    helper for this; without it you'd hand-enumerate every transitive.
#    pip-audit / pipgrip can produce equivalent output if brew's
#    bundled tooling isn't preferred.
brew install pipgrip || true
pipgrip --tree aws-tui  # inspect to confirm the closure
# Pour the auto-generated formula into ./Formula/aws-tui.rb.
poet-py3.12 -f aws-tui > Formula/aws-tui.rb || \
    homebrew-pypi-poet aws-tui > Formula/aws-tui.rb
```

If `homebrew-pypi-poet` isn't on PATH:

```bash
pipx install homebrew-pypi-poet
homebrew-pypi-poet aws-tui > Formula/aws-tui.rb
```

Then **manually clean up** the generated formula. The two
fields that the auto-bump workflow expects to be on their own
lines (so its `sed` works) are:

```ruby
url "https://files.pythonhosted.org/packages/source/a/aws-tui/aws_tui-0.8.0.tar.gz"
sha256 "<PKG_SHA from step 2>"
```

Add a test stanza at the bottom that smoke-tests the CLI entry
point:

```ruby
test do
  assert_match version.to_s, shell_output("#{bin}/aws-tui --version")
end
```

## Smoke test locally

```bash
brew install --build-from-source ./Formula/aws-tui.rb
aws-tui --version
brew test aws-tui
brew uninstall aws-tui
```

## Commit + push

```bash
git add Formula/aws-tui.rb
git commit -m "aws-tui 0.8.0 (initial)"
git push origin main
```

## Document the install

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

## After bootstrap

Tag v0.8.1 in the main repo (whenever the next patch ships) and
confirm the `bump-homebrew` job opens a PR against the tap
within a few minutes of the PyPI publish. From here it's
fully automated; you just merge the PR.
