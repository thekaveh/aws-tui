# 1. Installation

aws-tui supports Python 3.11, 3.12, and 3.13 on macOS, Linux, and Windows.
Until the first PyPI release is published, install the application directly
from its Git repository.

## 1.1. Isolated application install

Use either `pipx` or `uv tool` so aws-tui and its dependencies remain isolated
from system Python packages:

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

## 1.2. Development install

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

## 1.3. Demo mode

Launch the complete interface against deterministic in-memory providers when
AWS credentials are unavailable:

```bash
aws-tui --demo
```

Demo mode does not write the user's aws-tui configuration and does not issue
AWS requests. Its local filesystem pane still points at the real local
filesystem.

## 1.4. Release channels

The repository contains PyPI, TestPyPI, GitHub Release, and Homebrew automation,
but the public PyPI package and Homebrew tap are not yet available. The Git
installation above is the supported installation path until that first release
completes.
