# aws-tui

aws-tui is a terminal interface for AWS and S3-compatible storage on macOS,
Linux and Windows. It supports Python 3.11, 3.12 and 3.13.

## 1. Capabilities

- Manage S3 and local files in two panes: copy, move, rename, create folders, delete and preview.
- Inspect EMR Serverless applications, job runs and logs; clone an existing
  Spark run, submit a new job or cancel an active run.
- Browse AWS Glue resources and Iceberg metadata.
- Review and execute allowed read-only SQL in Athena, then inspect results and history.

Glue-to-Athena handoffs prefill queries without executing them. Athena execution
can incur AWS charges and write results. S3 file operations and EMR submit/clone/cancel
operations modify resources.

## 2. Installation

Development build: no aws-tui package is published on PyPI.
For a standalone command, install `uv`, a supported Python version and Git first.
Git must be available on `PATH`. Then install the application:

```bash
uv tool install git+https://github.com/thekaveh/aws-tui.git
aws-tui --version
```

`pipx install git+https://github.com/thekaveh/aws-tui.git` is an optional alternative.
Both installers use a separate application environment.

If you run from a repository checkout, keep using `uv run aws-tui`. It uses your
checkout's code and project environment. See the
[checkout setup](https://github.com/thekaveh/aws-tui/blob/main/docs/install.md#1-run-from-a-checkout-with-uv).

To explore a standalone installation, run `aws-tui --demo`.
From a checkout, run `uv run aws-tui --demo`. AWS data is synthetic and no
AWS requests are sent. The local pane uses real files; local file operations can modify your filesystem.

The optional DuckDB extra enables Glue Iceberg **Peek**, which reads rows
from S3 without Athena. See the
[installation guide](https://github.com/thekaveh/aws-tui/blob/main/docs/install.md#2-optional-extras)
for that extra and alternative installs.

## 3. Documentation and support

- [Quickstart](https://github.com/thekaveh/aws-tui#4-quickstart)
- [Project documentation](https://thekaveh.github.io/aws-tui/)
- [Connection configuration](https://github.com/thekaveh/aws-tui/blob/main/docs/connections.md)
- [Security policy](https://github.com/thekaveh/aws-tui/blob/main/SECURITY.md)
- [Source and issue tracker](https://github.com/thekaveh/aws-tui)

Licensed under [Apache License 2.0](https://github.com/thekaveh/aws-tui/blob/main/LICENSE),
with attribution in [NOTICE](https://github.com/thekaveh/aws-tui/blob/main/NOTICE).
