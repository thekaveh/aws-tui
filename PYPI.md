# 1. aws-tui

Cross-platform terminal UI for AWS and S3-compatible services, powered by
Textual and the VMx MVVM framework.

> **Development status:** AWS Glue, Amazon Athena, and their integrated
> Iceberg workflows are unreleased v0.9.0 work. Package metadata remains at
> v0.8.0 until release preparation, and no aws-tui package is published on
> PyPI. Install the current development tree from Git rather than treating
> these features as part of a released package.

aws-tui provides a dual-pane S3/local file manager plus operational views for
EMR Serverless, AWS Glue, Amazon Athena, and Apache Iceberg metadata. The EMR
view is read-mostly, with focused clone submission for an existing job run.
It supports multiple AWS profiles and S3-compatible connections, keyboard-first
navigation, deterministic demo mode, and built-in themes.

![aws-tui Glue and Iceberg demo](https://raw.githubusercontent.com/thekaveh/aws-tui/main/assets/screenshots/aws-tui-running.png)

- [Project documentation](https://thekaveh.github.io/aws-tui/)
- [Canonical documentation source](https://github.com/thekaveh/aws-tui/tree/main/docs)
- [Installation and quickstart](https://github.com/thekaveh/aws-tui#13-quickstart)
- [Connection configuration](https://github.com/thekaveh/aws-tui/blob/main/docs/connections.md)
- [Security policy](https://github.com/thekaveh/aws-tui/blob/main/SECURITY.md)
- [Source and issue tracker](https://github.com/thekaveh/aws-tui)
