# 1. aws-tui

Cross-platform TUI for AWS and S3-compatible services — a
Norton-Commander–style dual-pane file manager for S3 plus an EMR
Serverless console and Unreleased AWS Glue, Amazon Athena, and Iceberg
inspection workflows,
built on [Textual](https://textual.textualize.io/) and the VMx MVVM
framework.

## 1.1. What it does

- **Dual-pane S3 ⇄ local file management** — copy, delete, and multi-select
  across an S3 (or S3-compatible) source and your local filesystem.
- **One-key source switching** across every configured AWS profile and
  S3-compatible connection.
- **EMR Serverless console** — application picker, job-runs master-detail
  with state-filter chips, and on-demand log streaming with a grep filter.
- **AWS Glue read-only operations console** — Catalog, Jobs, and Crawlers
  views with exact-profile S3 location handoff.
- **Amazon Athena read-only query console** — Query, History, Results, and
  Saved views with fail-closed SQL validation, app-owned cancellation,
  paginated rows, and exact-profile customer-S3 result handoff.
- **Integrated Iceberg operations** — bounded metadata views in Glue,
  generated Glue → Athena table and snapshot queries with explicit execution,
  Athena → Glue navigation for one unambiguous table, and S3 artifact handoff.
- **Themable, keyboard-driven** — built-in themes and fully customizable
  keybindings.

Glue, Athena, and Iceberg integration are Unreleased minor-version feature
work targeting v0.9.0. They remain read-only: generated SQL is placed in the
Athena editor for review and never executes automatically.

## 1.2. Where to start

- New here? Start with **Installation**, then **Platforms** and
  **Connections**.
- Daily use: **Keybindings**, the **Cookbook**, and **Theming**.
- Contributing or extending: **Architecture**, **Adding a Service**, and
  the **Contract Ledger**.
