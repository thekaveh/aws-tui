# Installation

aws-tui supports Python 3.11, 3.12, and 3.13 on macOS, Linux, and Windows.
Until the first PyPI release is published, install the application directly
from its Git repository.

## 1. Run from a checkout with uv

Install `uv` 0.13.4 or newer, a supported Python version and Git first.
Git must be available on `PATH`. From a new checkout:

```bash
git clone https://github.com/thekaveh/aws-tui.git
cd aws-tui
uv sync --locked
uv run aws-tui --version
```

Run the app from the checkout root:

```bash
uv run aws-tui
```

`uv run` uses the project's environment and current checkout code. The lockfile
is the dependency baseline. This workflow does not require `pipx` or a standalone
installation. Use `uv run aws-tui --demo` to explore without AWS credentials.

For contribution tools and documentation dependencies, run `uv sync --locked --all-groups`.
See [Contributing](../CONTRIBUTING.md) for the development workflow.

## 2. Optional extras

Both checkout and standalone installations include the required dependencies. The optional
DuckDB extra adds the larger engine needed for one feature:

| Extra | Installs | Enables |
| --- | --- | --- |
| `duckdb` | `duckdb>=1.3,<2` (~14 MB per platform) | The Glue Iceberg **Peek** tab, which previews table rows by reading S3 directly |

From the checkout root:

```bash
uv sync --locked --extra duckdb
uv run --extra duckdb aws-tui
```

For a standalone installation, choose the command for your installer:

```bash
uv tool install "aws-tui[duckdb] @ git+https://github.com/thekaveh/aws-tui.git"
```

```bash
pipx install "aws-tui[duckdb] @ git+https://github.com/thekaveh/aws-tui.git"
```

Without the extra, aws-tui runs normally and the Peek tab reports the missing
engine and the install command rather than disappearing.

The first Peek query on a machine downloads DuckDB's `httpfs`, `aws`, and
`iceberg` extensions from `extensions.duckdb.org` and caches them under
`~/.duckdb/`. Cached extensions avoid repeated downloads.

Queries still need S3 access and any network access required by the selected
AWS profile's credential providers, such as SSO or role assumption. On a
host without egress to that domain the first query fails.
Populate the cache on a connected machine, or install the extensions for the same DuckDB version.

## 3. Standalone application install

Use this option for a separate `aws-tui` command without running from a checkout.
Install `uv` 0.13.4 or newer, or `pipx`, a supported Python version and Git first.
Git must be available on `PATH` for these repository URLs. Choose one installer:

```bash
uv tool install git+https://github.com/thekaveh/aws-tui.git
```

```bash
pipx install git+https://github.com/thekaveh/aws-tui.git
```

Verify the installed command:

```bash
aws-tui --version
```

Both installers create a separate application environment. They do not run your
checkout's local changes. `pipx` is optional; existing `uv` users can use `uv tool install`.

## 4. Demo mode

Demo mode sends no AWS requests and does not write aws-tui configuration.
Its local pane uses your real filesystem; local copy and delete affect real files.

From the checkout root, launch the interface with synthetic AWS resources:

```bash
uv run aws-tui --demo
```

For a standalone installation:

```bash
aws-tui --demo
```

A **DEMO MODE** indicator identifies the session.

## 5. Release channels

The repository contains PyPI, TestPyPI, GitHub Release, and Homebrew automation,
but the public PyPI package and Homebrew tap are not yet available. The Git
installation above is the supported installation path until that first release
completes.
