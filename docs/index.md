# aws-tui

<p align="center">
  <img src="../assets/aws-tui-poster.png" alt="A wireframe cloud of teal light anchored by golden tethers to a glowing point on a dark sea, the AWS-TUI wordmark seated at its luminous core." width="100%">
</p>

<p align="center">
  <img src="../assets/screenshots/aws-tui-running.png" alt="aws-tui in demo mode with the S3, EMR, Glue, and Athena service rail; the Glue catalog is showing an Iceberg table, its metadata tabs, and snapshot history." width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white" alt="Python 3.11, 3.12, and 3.13">
  <img src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-4c566a" alt="Runs on macOS, Linux, and Windows">
  <img src="https://img.shields.io/badge/built%20with-Textual%20%2B%20VMx-5a4fcf" alt="Built with Textual and the VMx MVVM framework">
  <img src="https://img.shields.io/badge/license-Apache--2.0-3DA639" alt="Apache-2.0 licensed">
</p>

Cross-platform TUI for AWS and S3-compatible services — runs on macOS,
Linux, and Windows. Powered by
[Textual](https://textual.textualize.io/) and the
[VMx](https://github.com/thekaveh/VMx) MVVM framework.

aws-tui puts the AWS work that usually means switching between the web console
and a shell behind a single keyboard-driven terminal interface: a
Norton-Commander-style dual-pane file manager for S3 and S3-compatible storage,
an EMR Serverless console, and read-only operations consoles for AWS Glue and
Amazon Athena. What sets it apart is that those services are wired to each
other rather than merely bundled together — a Glue catalog table generates the
Athena SQL that reads it, Athena results hand back to the S3 pane as artifacts,
and Iceberg table metadata is reachable from both directions. Destructive
operations always confirm first, long operations run on cancellable background
workers, and one keystroke re-points the whole application at a different AWS
profile or S3 endpoint.

> **Status: v0.9.0 development; no package release published** — install from Git
> until the `aws-tui` project name is available on PyPI. Glue, Athena, and
> their integrated Iceberg workflows are Unreleased v0.9.0 feature work. The
> package metadata remains `0.8.0` until the release-preparation PR bumps it;
> the current tree must not be tagged as v0.8.0. See the changelog
> in the repository for the full per-PR delta.

## 1. Features

- **Dual-pane S3 ⇄ local file management** — copy, delete, and multi-select
  across an S3 (or S3-compatible) source and your local filesystem.
- **One-key source switching** across every configured AWS profile and
  S3-compatible connection.
- **EMR Serverless console** — application picker, job-runs master-detail
  with state-filter chips, and on-demand log streaming with a grep filter.
- **AWS Glue read-only operations console** — Catalog, Jobs, and Crawlers
  views with an exact-source bordered picker, bordered job/crawler state
  selectors, and a typed copied-table reference.
- **Amazon Athena read-only query console** — Query, History, Results, and
  Saved views with fail-closed SQL validation, app-owned cancellation,
  paginated rows, exact-profile customer-S3 result handoff, keyboard-focusable
  context selectors, and same-source copied-table insertion without execution.
- **Integrated Iceberg operations** — bounded metadata views in Glue,
  generated Glue → Athena table and snapshot queries with explicit execution,
  Athena → Glue navigation for one unambiguous table, and S3 artifact handoff.
- **Themable, keyboard-driven** — built-in themes and fully customizable
  keybindings; valid overlays apply on the next launch while invalid overlays
  fall back atomically. The command palette shows service commands only for
  the active service.

Glue, Athena, and Iceberg integration are Unreleased minor-version feature
work targeting v0.9.0. They remain read-only: generated SQL is placed in the
Athena editor for review and never executes automatically.

## 2. Where to start

- New here? Start with [Installation](install.md), then
  [Platforms](platforms.md) and [Connections](connections.md).
- Daily use: [Keybindings](keybindings.md), the [Cookbook](cookbook.md), and
  [Theming](theming.md).
- Service behavior: [S3 and Local File Manager](services/s3.md),
  [EMR Serverless](services/emr-serverless.md),
  [AWS Glue and Iceberg Metadata](services/glue.md), and
  [Amazon Athena](services/athena.md).
- Contributing or extending: [Architecture](architecture.md),
  [Adding a Service](adding-a-service.md), and the
  [Contract Ledger](contract-ledger.md).
