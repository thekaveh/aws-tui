# 1. Implementation Plan Index

This directory contains historical and current implementation plans used to
stage larger aws-tui changes. These plans are provenance documents: prefer the
current code, tests, README, and focused specs for the live behavior contract.

## 1.1. Milestone Plans

1. [Bootstrap M0](2026-06-14-aws-tui-bootstrap-m0.md) — initial repository, packaging, docs, scripts, CI, and source skeleton.
2. [Infrastructure M1](2026-06-14-aws-tui-infrastructure-m1.md) — config, paths, AWS session, keychain, logging, and infra boundaries.
3. [Domain M2](2026-06-14-aws-tui-domain-m2.md) — filesystem providers, cross-filesystem operations, and transfer journal domain behavior.
4. [VM shell M3](2026-06-14-aws-tui-vm-shell-m3.md) — root VM, navigation, content host, pane state, and chrome viewmodel shell.
5. [VM file manager M4](2026-06-14-aws-tui-vm-filemgr-m4.md) — dual-pane file-manager viewmodels, transfers, selection, and copy flows.
6. [UI themes M5](2026-06-14-aws-tui-ui-themes-m5.md) — Textual widgets, themes, snapshots, and app composition.
7. [Polish M6](2026-06-14-aws-tui-polish-m6.md) — release polish, docs, tests, and final milestone hardening.

## 1.2. Feature And Maintenance Plans

1. [VMx PyPI migration](2026-06-17-vmx-pypi-migration.md) — migration from local/submodule VMx usage to PyPI-resolved VMx.
2. [Graceful unreachable connections](2026-06-19-graceful-unreachable-connections.md) — unreachable-source state handling and skip-toasts.
3. [Modal and toast polish](2026-06-19-modal-toast-polish.md) — modal layout, toast grammar, transfer overlay, and theme polish.
4. [App settings shell and S3 panel](2026-06-20-app-settings-shell-and-s3-panel.md) — superseded settings modal/panel design implementation record.
5. [Settings as first-class nav page](2026-06-20-settings-as-first-class-nav-page.md) — nav-routed Settings page and S3-compatible connection CRUD.
6. [Notification consistency](2026-06-24-notification-consistency.md) — toast/modal taxonomy, wording, and migration plan.
7. [EMR Serverless PR-A](2026-06-25-emr-serverless-pr-a.md) — first EMR Serverless service slice and read-only browser plan.
8. [EMR job-run logs pane](2026-06-26-emr-job-run-logs-pane.md) — job-run log discovery, filtering, and pane integration.
9. [Cross-platform readiness](2026-06-28-cross-platform-readiness.md) — macOS, Linux, and Windows install/smoke/readiness work.
10. [Demo mode](2026-06-28-demo-mode.md) — deterministic in-memory demo data and no-real-AWS launch path.

## 1.3. VMx 3.1 Adoption Plans

1. [VMx FormVM S3 settings](2026-07-02-vmx-formvm-s3-settings.md) — replacement of the local settings-form primitive with VMx `FormVM`.
2. [VMx 3.1 remaining adoption](2026-07-02-vmx-3-1-remaining-adoption.md) — remaining VMx 3.1 substitutions for palette, panes, focus, pagination, modals, and subscriptions.
