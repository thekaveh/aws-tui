# Glue to Athena Repair Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the focused repair tasks and requesting-code-review before integration. Steps use checkbox syntax for tracking.

**Goal:** Make the actual Glue bottom-command handoff and Athena selection/query behavior match the approved design.

**Architecture:** Preserve the current RootVM/ContentHostVM adoption boundaries. Carry programmatic selection provenance through hub delivery, use supported mounted-node refresh synchronization, and coordinate table selection with existing authoring ownership.

**Tech Stack:** Python 3.11–3.13, asyncio, Textual, VMx, pytest, controlled in-memory AWS providers.

## 1. Global Constraints

- Exact Glue connection, region, catalog, database and table must reach Athena; use applicable same-source workgroup/output settings.
- Generate quoted read-only SELECT with LIMIT 5; never execute automatically.
- Retain a still-available table name; otherwise select the first displayed table and generate SQL. Empty databases clear untouched generated SQL.
- Protect manually edited SQL with existing confirmation. Stale metadata/navigation must not overwrite newer input.
- Exercise normal App.run_async/CLI semantics and eager/deferred scheduling, not only App.run_test.
- Applicable local checks only; skip GitHub Actions and hosted CI gates.
- Isolated configuration and controlled providers only; no live AWS account/resource scope is authorized.
- Preserve Atlas, Docker/Ollama, shared infrastructure, dependencies, global launchers, repository protections and unrelated PRs.

## 2. Task 1: Repair normal-launch navigation and refresh lifecycle

**Files:** `src/aws_tui/service_navigation.py`, `src/aws_tui/app.py`, selection-publication code in `src/aws_tui/vm/nav_menu_vm.py` (a focused supporting module if needed); `tests/unit/test_service_navigation.py`, `tests/integration/test_glue_athena_direct_selection.py` or a focused normal-launch test module.

**Interfaces:** Consume existing OpenAthenaTableRequest, ServiceNavigation.suppress_selection, NavMenuVM selection publications and RootVM adoption. Preserve user navigation supersession and the public table handoff.

- [ ] Add a regression using real MessageHub reentrant delivery and eager startup: publish a programmatic Athena selection from a child handoff while the outer hub drains; assert the publication does not supersede the table owner. Assert a later real user selection does supersede it, including the same service after navigating away.
- [ ] Add a normal App.run_async regression with isolated demo providers. Select Glue Iceberg metadata and invoke the real bottom action. Assert exact context and visible SQL after setup; record the pre-fix failure. The refresh check must not populate App._task through run_test.
- [ ] Run the new tests with the existing isolated runtime. Expected pre-fix results: default/empty Athena or App.wait_for_refresh assertion. Keep failure logs outside the source tree.
- [ ] Carry stable projection ownership in selection publication, independent of delivery task identity; do not globally suppress all same-service messages. Replace the two failing App refresh barriers with supported mounted-node synchronization. Preserve cancellation/rollback guards.
- [ ] Run navigation unit tests and focused Glue/Athena integration tests. Review the diff for lifecycle, shutdown, message-value privacy and genuine user supersession. Commit with `[skip ci]`.

## 3. Task 2: Coordinate Athena context, table and generated SQL

**Files:** `src/aws_tui/vm/athena/page_vm.py`, existing authoring/query helpers only as needed; `tests/unit/vm/athena/` and `tests/integration/test_athena_table_selection.py`.

**Interfaces:** Consume existing AthenaPageVM.select_database/select_catalog/select_table, table-list setup and AthenaAuthoringVM document/dirty tracking. Preserve explicit table handoff priming from Task 1.

- [ ] Seed two databases containing shared and distinct quoted table names. Test generated SQL followed by a database change, fallback to first table, retained shared table, empty database, and catalog changes. Assert selected_table_ref, dropdown value, query SQL and no start_query calls.
- [ ] Test edited-query confirmation (accept/cancel), metadata failure, and delayed old table listing versus a newer choice/edit. Use provider events instead of arbitrary delays.
- [ ] Run new tests before changes and record failures caused by stale/missing selection and SQL. Update old tests that explicitly expected no automatic first-table choice to the approved behavior.
- [ ] Implement one context-selection completion path using current revision/document ownership. Reuse select_table/authoring replacement and identifier quoting; preserve manual edits and table handoff ownership. Clear only untouched generated SQL for an empty database and expose metadata failures.
- [ ] Run affected Athena unit/integration suites and navigation handoff regressions. Review and commit with `[skip ci]`.

## 4. Task 3: Verify actual entrypoint and integrate

**Files:** applicable public Athena workflow documentation and changelog; verification evidence outside the source tree.

- [ ] Launch the normal application with the existing isolated runtime and unchanged demo providers in a real terminal. Click Glue table and the bottom Athena command, then change catalog/database/table. Verify visible fields/editor, no crash, no automatic execution. Repeat on the locally available Python 3.11 runtime and current Python 3.12 runtime; distinguish executed coverage from unsupported/unavailable coverage.
- [ ] Run applicable broader local tests, Ruff, mypy and layer/docs checks. Investigate any failures; do not claim a command passed if it failed. Update user-facing workflow documentation where behavior changed.
- [ ] Obtain independent code review of the whole branch against the spec, resolve important findings with covering tests, and commit the final result with `[skip ci]`.
- [ ] Push the repair branch and create/merge a PR into develop. Then create/merge develop-to-main promotion PR. Use `[skip ci]` in merge commits and bypass only hosted CI as already authorized, preserving protections.
- [ ] Fetch/prune; verify local/remote develop and main tree IDs match, both PRs are merged, repair branch is removed locally/remotely, no repair worktree or open repair PR remains, and worktree is clean.
