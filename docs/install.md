# Installation

aws-tui supports Python 3.11, 3.12, and 3.13 on macOS, Linux, and Windows.
Until the first PyPI release is published, install the application directly
from its Git repository.

## 1. Isolated application install

Install `pipx` or `uv` first, with a supported Python version available.
Git must be installed and available on `PATH` for the repository URLs below.
Use either tool to keep aws-tui isolated from system Python packages:

```bash
pipx install git+https://github.com/thekaveh/aws-tui.git
```

```bash
uv tool install git+https://github.com/thekaveh/aws-tui.git
```

Verify the installed console entry point:

```bash
aws-tui --version
```

## 2. Optional extras

The Git installation includes the application's required dependencies. The optional
DuckDB extra adds the larger engine needed for one feature:

| Extra | Installs | Enables |
| --- | --- | --- |
| `duckdb` | `duckdb>=1.3,<2` (~14 MB per platform) | The Glue Iceberg **Peek** tab, which previews table rows by reading S3 directly |

```bash
pipx install "aws-tui[duckdb] @ git+https://github.com/thekaveh/aws-tui.git"
```

```bash
uv tool install "aws-tui[duckdb] @ git+https://github.com/thekaveh/aws-tui.git"
```

Without the extra, aws-tui runs normally and the Peek tab reports the missing
engine and the install command rather than disappearing.

The first Peek query on a machine downloads DuckDB's `httpfs`, `aws`, and
`iceberg` extensions from `extensions.duckdb.org` and caches them under
`~/.duckdb/`. Queries after that need no connection beyond S3 itself. On a
host without egress to that domain the first query fails.
Populate the cache on a connected machine, or install the extensions for the same DuckDB version.

## 3. Development install

```bash
git clone https://github.com/thekaveh/aws-tui.git
cd aws-tui
uv sync --locked --all-groups
uv run aws-tui
```

The lockfile is the reproducibility baseline for development and CI. See
[Platforms](platforms.md) for terminal and font guidance, then
[Connections](connections.md) to configure AWS profiles or S3-compatible
endpoints.

## 4. Demo mode

Demo mode sends no AWS requests and does not write aws-tui configuration.
Its local pane uses your real filesystem; local copy and delete affect real files.

Launch the interface with synthetic AWS resources:

```bash
aws-tui --demo
```

A **DEMO MODE** indicator identifies the session.

## 5. Release channels

The repository contains PyPI, TestPyPI, GitHub Release, and Homebrew automation,
but the public PyPI package and Homebrew tap are not yet available. The Git
installation above is the supported installation path until that first release
completes.
