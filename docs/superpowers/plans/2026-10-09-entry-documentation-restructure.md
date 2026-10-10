# Repository Entry Documentation Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the eleven entry-documentation findings without losing valid product, safety, compatibility or release contracts.

**Architecture:** Reorganize the repository entry and existing landing sources around reader tasks. Keep detailed behavior in its canonical guides, update incidental prose-placement tests, and validate the complete result rather than only changed lines.

**Tech Stack:** Markdown, existing manifest-driven site/wiki/package generation, MkDocs Material, Python documentation tests, GitHub repository rendering.

## 1. Global Constraints

- This document is a plan. Its creation does not implement the aws-tui rewrite.
- Baseline: main `1e1bc7c050257be60132c714482e15166d9d4af7`; recheck state when execution starts.
- Local checks only. Do not dispatch GitHub Actions or depend on hosted CI gates. Preserve repository protection.
- Preserve Atlas, Docker, Ollama, credentials and shared infrastructure. Do not perform live AWS operations.
- Preserve Apache-2.0/NOTICE, accurate Python/platform support, exact API/UI identifiers, history and meaningful feature/safety constraints.
- Use `ste-software`: gradual explanation, logical transitions, one topic per paragraph; procedural sentences <=20 words, descriptive sentences <=25; paragraphs <=75 words/six sentences under source-aware counting. Do not claim strict dictionary conformance.
- The repo, site, wiki and package can have different audience-appropriate summaries. Shared facts must agree; identical introductory prose is not required.
- The root README is repo-only. `docs/index.md` generates site/wiki home; `docs/package-readme.md` generates `PYPI.md`. Generated outputs must follow the existing toolchain.
- Existing numbered headings and same-surface link conventions are project policy. Retain them unless a demonstrated improvement justifies changing them and migrating all affected anchors.
- Keep this plan as internal implementation material; do not add it to the product README or public manifest.

## 2. Task 1: Establish preservation and reader-flow contracts

**Files:** Inspect `README.md`, `docs/index.md`, `docs/package-readme.md`, `docs/install.md`, `docs/connections.md`, `docs/keybindings.md`, `docs/cookbook.md`, `docs/services/*.md`, `docs/RELEASING.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `docs/manifest.yaml`, `scripts/docs/links.py`.

**Consumes:** The eleven findings in `/private/tmp/aws-tui-readme-audit-98tvo_ze/findings.json` and this plan's matrix below.
**Produces:** A finding/claim disposition ledger outside published documentation and a chosen entry outline.

- [ ] Inspect clean/dirty state, local/remote branches, worktrees and protection; isolate execution on a new branch from current develop. Preserve this plan and unrelated edits without resetting or stashing them away.
- [ ] Record each retained fact and its destination; distinguish current capability, current limit, history and maintainer procedure. Check release availability again before authoring installation copy.
- [ ] Trace Recovery through `src/aws_tui/domain/transfer_journal.py`, `src/aws_tui/vm/file_manager/transfer_history_vm.py`, startup in `src/aws_tui/app.py`, and `docs/services/s3.md`.
- [ ] Trace source precedence through `ConnectionResolver.resolve_default` and explicit launch selection; preserve higher-priority configured defaults when explaining environment fallback.
- [ ] Use this project-specific outline: purpose and intended users; concise capabilities; requirements; installation; first use/basic controls; configuration and current limits; deeper documentation; contribution/security/license. Introduce purpose before decorative artwork.

| Finding | Required disposition |
|---|---|
| RA-01 | General purpose precedes details; each main reader task has a clear route. |
| RA-02 | Replace the extended feature manual with short observable capability summaries; keep technical detail in its existing guide. |
| RA-03 | Keep concise development/install availability near installation; retain metadata/tag preparation in release/history sources. |
| RA-04 | Use present-tense capabilities and current limitations; preserve chronology in CHANGELOG and archived plans. |
| RA-05 | Remove unexplained internal and colloquial vocabulary from entry prose; retain necessary technical explanation for contributors. |
| RA-06 | Each paragraph develops one topic; descriptions and instructions have distinct purposes. |
| RA-07 | Explain each entry concept once; remove documentation-edit narration and link deeper detail. |
| RA-08 | Separate user, contributor and maintainer routes; move the developer test harness out of the user-guide list. |
| RA-09 | Give a minimal first-use path; relocate CLI grammar, schema and exceptional-state detail. |
| RA-10 | Describe durable History and explicit endpoint-checked new-copy Recovery; preserve unknown outcomes and no automatic replay/multipart resume. |
| RA-11 | Remove unsupported “most common” diagnosis; explain actual selected-source precedence and an explicit profile/connection remedy. |

Validation: each existing qualification has a destination or a justified obsolete disposition. History preservation must not reintroduce history into the overview.

## 3. Task 2: Replace incidental test constraints without weakening contracts

**Files:** Modify `tests/docs/test_scaffolding.py`, `tests/docs/test_shipped_behavior.py` only where entry placement is incidental. Inspect `scripts/docs/check_docs.py`; preserve its link, asset, generation and safety checks.

**Consumes:** Canonical destinations from Task 1.
**Produces:** Documentation tests that permit an accurate concise entry while retaining detailed contract verification in the correct sources.

- [ ] Inspect every README assertion, not only the following named tests. Separate API/UI literals, real behavioral requirements and incidentally frozen prose.
- [ ] Remove the exact-introduction-equality and 100–150-word requirements in `test_readme_and_published_index_share_the_product_summary`; remove `_opening_prose` only if it has no remaining useful callers. Verify shared capabilities semantically during review, not through a replacement slogan assertion.
- [ ] Remove `test_readme_indexes_every_internal_superpowers_note` as a README-placement requirement. Keep archived files and the existing plan index; do not delete historical notes or invent a new public archive page.
- [ ] Redirect detailed binding/handler assertions in `test_readme_describes_shipped_runtime_bindings_quick_look_and_palette` to `docs/keybindings.md`; retain meaningful guards against false deferred/shipped claims.
- [ ] Scope advanced launch-selector, precedence, failure-mode and preview-budget assertions in `test_launch_selectors_document_values_precedence_and_session_contract` and `test_quick_look_formats_controls_and_budgets_documented` to the appropriate cookbook/keybinding reference. Keep basic launch examples reachable from the entry.
- [ ] Scope release-preparation assertions in `test_athena_release_framing_and_smoke_are_minor_unreleased_work` to `docs/RELEASING.md`/CHANGELOG; retain entry/package availability truthfulness and the distinction between available Git code and a released package.
- [ ] Replace fixed `#3-quickstart` assumptions in `test_package_metadata_tracks_quickstart_and_emr_clone_surface` if headings move. Let actual link/anchor validation establish target validity; keep the EMR clone capability check.
- [ ] Run focused documentation tests before prose edits to identify any accidental loss of contract coverage. A temporary failure due to a planned relocation must be documented, not hidden by disabling checks.

Commands:

```bash
uv run --no-sync pytest tests/docs/test_scaffolding.py tests/docs/test_shipped_behavior.py -q
```

Expected: retained contract checks pass or fail only on the explicitly scheduled content corrections. Test success is not editorial acceptance. Do not add tests that merely lock the new wording or heading count.

## 4. Task 3: Rewrite the repository entry and relocate detail

**Files:** Modify `README.md`; modify `docs/install.md`, `docs/connections.md`, `docs/cookbook.md`, `docs/keybindings.md`, `docs/services/s3.md`, `CONTRIBUTING.md` only where relocated information or corrected facts require it. Keep `CHANGELOG.md` and `docs/RELEASING.md` history intact.

**Consumes:** Outline and preserved contracts; placement-independent tests.
**Produces:** A complete reader-oriented repository entry with a short first-use procedure.

- [ ] Draft the overview before copying old prose. Example capability opening:

  > aws-tui is a terminal interface for AWS and S3-compatible storage. It provides S3 file management and consoles for EMR Serverless, Glue and Athena.
  >
  > Use it to browse resources, transfer files, inspect logs and query tables. AWS connections use your existing profiles.

- [ ] Summarize S3/local operations, EMR browsing/clone/cancel/logs, Glue/Iceberg inspection and Athena query/handoff behavior. Distinguish read-only Glue/Athena policy from S3 writes and EMR mutation. Keep optional DuckDB discoverable through installation/service links.
- [ ] Use a concise verified status near installation, such as “Development build. Install from Git; a PyPI package is not yet available.” Reconfirm the last clause rather than blindly preserving an old availability claim.
- [ ] Put Python and installation-tool prerequisites before their commands. Keep one supported isolated Git install and link alternate/developer setup. Use the actual supported versions, not a broader unverified Python range.
- [ ] Put the demo's real-local-filesystem caveat before its launch command. State that AWS providers are synthetic, while local copy/delete can affect real files.

```bash
pipx install git+https://github.com/thekaveh/aws-tui.git
aws-tui --demo
```

- [ ] Provide an existing-profile path after the demo path. Define `analytics` as an example profile that the user replaces; do not imply automatic credential setup.

```bash
aws-tui --profile analytics
```

- [ ] Keep a small set of basic navigation/command guidance and link the keybinding guide. Move path-copy variants, focus rings, polling intervals, parser limits, clipboard internals and diagnostic JSON schemas into their existing references.
- [ ] Correct the interrupted-transfer summary. State explicit Recovery capability and its limits without implying automatic retries or known success for interrupted outcomes.
- [ ] Replace the generic environment-export fix with diagnosis and an explicit source choice. Explain that environment fallback does not override a configured default; link the connection-resolution/diagnostic guide for details.
- [ ] Give a task-oriented documentation map and concise contributor/security/license references. Route developer setup and tooling through CONTRIBUTING; keep internal notes and maintenance ledgers out of the primary user inventory.

Validation: perform the complete reader walkthrough in the updated shared skill. For each retained sentence, identify its reader task and factual authority. Changes to image placement are optional; no image generation or asset deletion is required.

## 5. Task 4: Align the other entry surfaces and generated outputs

**Files:** Modify `docs/index.md`, `docs/package-readme.md`; regenerate `PYPI.md`, `generated/`, `mkdocs.yml` and `site/` only through existing tools. Modify `docs/manifest.yaml` only if source membership or navigation actually changes.

**Consumes:** Reviewed capabilities, availability and limits from Task 3.
**Produces:** Audience-appropriate entries with the same supported facts and preserved generation contracts.

- [ ] Make the site/wiki home a useful guide entry; remove release-tag instructions, per-PR narrative and explanations of self-containment transforms.
- [ ] Make the package source describe scope, installation availability and optional extras concisely. Update its repository quickstart anchor if the README heading changes.
- [ ] Preserve the repository's same-surface policy when relocating links. Do not add public site/wiki cross-links merely because another repo permits them.
- [ ] Regenerate and inspect actual site/wiki/package outputs; compare semantic facts as well as file membership. Recheck the prior 14 focused rendered boundaries, including both trailing paragraphs outside their lists.
- [ ] Record publication separately: local outputs are verified artifacts, not evidence that Pages/wiki have been updated. Do not dispatch Actions. A separate authorized local publishing step can be proposed if publication is wanted.

## 6. Task 5: Verification and whole-document review

**Files:** All edited sources, generated outputs and relevant tests. Store detailed review evidence outside published docs.

**Consumes:** Complete revised entries and contract disposition ledger.
**Produces:** Local mechanical evidence, factual evidence and an independent editorial review.

- [ ] Inspect wrappers/plugins and isolate generated execution as required. Use synthetic HOME and empty AWS files; do not install dependencies or invoke remote probes merely to validate prose.
- [ ] Run the applicable existing commands in the bounded environment:

```bash
uv run --no-sync python -m scripts.docs.build_docs --site --wiki --package
make docs-check
uv run --no-sync pytest tests/docs -q
uv run --no-sync pytest tests/unit/infra/test_connection_resolver.py tests/unit/test_launch_selectors.py tests/unit/domain/test_transfer_history.py tests/unit/vm/file_manager/test_transfer_history_vm.py -q
uv run --no-sync pre-commit run --all-files
```

Expected: generation/drift, strict build, documentation tests and relevant source-selection/recovery tests pass. Confirm available runtime/tool dependencies first; report missing coverage rather than fabricating a pass. Review `scripts/docs/build_docs.py` argument parsing at execution time to confirm this invocation is still valid.

- [ ] Validate links and anchors in actual rendered output. Inspect the repository README with GitHub/GFM rendering separately from MkDocs; private preview/browser limits must remain explicit.
- [ ] Review all three canonical entry sources from the beginning, not only the diff. Follow four reader tasks: evaluate suitability, install/demo, choose a real source, find deeper usage/support. Do not perform live AWS acceptance.
- [ ] Obtain independent report-only review when available and authorized. Require factual/qualification preservation and reader-task evidence; do not substitute a changed-line-only review or automated word counts.
- [ ] Verify every RA-01–RA-11 disposition. Repeat checks only for changed sources, failures or unresolved concerns. Record actual test counts and lexical/platform/rendering limits.

## 7. Task 6: Integration and closure after implementation

**Files:** Reviewed branch, PR descriptions and external evidence ledger.

**Consumes:** Verified changes and resolved review findings.
**Produces:** Reviewed changes integrated through develop and main, plus an accurate publication state.

- [ ] Commit the reviewed documentation/test changes, push the repair branch and create a PR into develop, using local verification evidence and `[skip ci]` where appropriate.
- [ ] Merge the reviewed exact PR head without bypassing protection; promote develop to main through a separate PR.
- [ ] Verify identical contents and each local branch matching origin. Delete the fully merged repair branch locally/remotely; preserve unrelated branches/worktrees.
- [ ] Report all finding dispositions, local verification, actual PR/merge state and public publication still pending if it was not performed.

## 8. Acceptance and handoff

No finding is cleared solely by a build, word count or unchanged literal. The new entry must present current user capabilities without requiring readers to reconstruct development chronology. All migrated facts remain reachable, and the tests permit concise, accurate audience-specific summaries while still guarding meaningful contracts.

This plan is ready for a later implementation turn. The separately requested documentation-skill improvements are applied now to the shared skill sources; they do not count as implementation of this repository rewrite.
