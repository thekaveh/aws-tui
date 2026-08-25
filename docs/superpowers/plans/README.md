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
11. [Three-surface documentation](2026-07-10-three-surface-docs.md) — canonical repository documentation projected to the site and wiki.
12. [Binding resolver keystone](2026-07-21-binding-resolver-keystone.md) — runtime keymap materialization and action dispatch.
13. [Quick Look wiring](2026-07-21-quick-look-wiring.md) — bounded file preview and command integration.
14. [Athena service](2026-07-22-athena-service.md) — read-only query, history, results, saved-query, and prepared-query workflows.
15. [AWS service profile switching](2026-07-22-aws-service-profile-switching.md) — exact profile and region identity across single-context services.
16. [Glue service](2026-07-22-glue-service.md) — read-only Catalog, Jobs, Crawlers, and S3 handoff implementation.
17. [Iceberg cross-service integration](2026-07-22-iceberg-cross-service-integration.md) — Glue metadata inspection and bounded Athena/S3 handoffs.
18. [Glue and Athena interaction polish](2026-07-30-glue-athena-interaction-polish.md) — selectors, focus rings, borders, and typed table transfer.
19. [Post-merge audit remediation](2026-07-30-post-merge-audit-remediation.md) — runtime, documentation, and verification corrections.
20. [Glue and Athena tab rail](2026-08-23-glue-athena-tab-rail.md) — standalone Glue source framing and selected/focused service tab presentation.
21. [Glue and Athena segmented tabs and layout fixes](2026-08-23-glue-athena-segmented-tabs-layout-fixes.md) — segmented tab frames, scoped source edges, and responsive command packing.
22. [Overlay pickers, one-line commands, and Athena handoffs](2026-08-24-overlay-pickers-command-handoffs.md) — implemented stable overlay selectors, compact tooltip-rich commands, and unified Glue-to-Athena table/snapshot actions.

## 1.3. VMx 3.1 Adoption Plans

1. [VMx FormVM S3 settings](2026-07-02-vmx-formvm-s3-settings.md) — replacement of the local settings-form primitive with VMx `FormVM`.
2. [VMx 3.1 remaining adoption](2026-07-02-vmx-3-1-remaining-adoption.md) — remaining VMx 3.1 substitutions for palette, panes, focus, pagination, modals, and subscriptions.
