# aws-tui

aws-tui lets you manage S3 and local files and inspect AWS services from a
terminal. Its service consoles cover EMR Serverless, AWS Glue and Amazon Athena.

This guide explains installation, source configuration and everyday workflows.
Start with the demo to explore sample resources without AWS credentials.

<img src="../assets/screenshots/aws-tui-running.png" alt="aws-tui showing a Glue Iceberg table and its metadata" width="100%">

## 1. What you can do

- Browse S3 or S3-compatible storage alongside local files; copy, delete and
  preview entries.
- Inspect EMR applications, job runs and logs; clone existing Spark runs and
  request cancellation of active runs.
- Browse Glue databases, tables, jobs and crawlers, including Iceberg metadata.
- Review and execute allowed read-only SQL in Athena, then inspect results
  and query history.

Glue table and Iceberg snapshot handoffs prefill Athena queries without
executing them. Athena queries can incur AWS charges and write query results.
S3 file operations and EMR clone or cancel actions can modify resources.

## 2. Get started

aws-tui supports Python 3.11, 3.12 and 3.13 on macOS, Linux and Windows.
Install the development build from Git; no aws-tui package is published on PyPI.

1. Follow [Installation](install.md) to install the application and launch the demo.
2. Read [Connections](connections.md) to select your AWS profile or configure an S3-compatible endpoint.
3. Use [Keybindings](keybindings.md) and the [Cookbook](cookbook.md) for navigation and common workflows.

The demo sends no AWS requests, but its local pane uses real files.
Local copy and delete operations affect your filesystem.

## 3. Service guides

| Task | Guide |
| --- | --- |
| Manage files, transfers and recovery | [S3 and local files](services/s3.md) |
| Inspect jobs, logs, cloning and cancellation | [EMR Serverless](services/emr-serverless.md) |
| Inspect tables and Iceberg metadata | [AWS Glue](services/glue.md) |
| Configure query context and inspect results | [Amazon Athena](services/athena.md) |

Glue, Athena and EMR require AWS connections. S3-compatible endpoints support
the file manager only. Transfer recovery starts an explicit new copy;
automatic replay and multipart resume are unsupported.

## 4. Configuration and development

Use [Configuration](configuration.md) for settings and file locations,
[Theming](theming.md) for appearance, and [Platforms](platforms.md) for terminal guidance.

For implementation and extension, read [Architecture](architecture.md) and
[Adding a service](adding-a-service.md). The [Contract ledger](contract-ledger.md)
records the external API and dependency contracts used by the project.
