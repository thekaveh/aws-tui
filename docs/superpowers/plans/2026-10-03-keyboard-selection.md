# Keyboard selection implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver every current #241 AC: five keyboard/palette selection controls with correct aliases, modal containment, visible selection state, safe lifecycle and exact transfer targets.

**Architecture:** App bridges invoke existing PaneVM commands; binding dispatch alone resolves shared Space/a keys, while explicit registry/palette actions stay independent. PaneVM retains ownership of marks and summary projection. No storage or cross-service selection feature is introduced.

**Tech Stack:** Python 3.11+, Textual, VMx, pytest/asyncio/pilot, existing in-memory providers and snapshot/docs tooling.

**Spec:** `docs/superpowers/specs/2026-10-03-keyboard-selection-design.md`.

## 1. Global Constraints

- Preserve Python 3.11+ support; add no dependencies and change no package version.
- Preserve Shift+arrow's leaving-row toggle, modifier-click, cursor fallback for copy/delete, and the existing approved alias pairs.
- Scope selection to the focused local/S3 file-manager pane; preserve the other pane and unrelated service behavior.
- Use applicable local checks only; do not dispatch GitHub Actions, touch live AWS, publish releases/packages, bypass protections, or push/merge from the implementer.
- Edit canonical documentation only; regenerate copied assets/site/wiki through existing scripts, never hand-edit generated output.

## 2. Task 1: Complete file-manager selection wiring

**Authoritative spec:** `docs/superpowers/specs/2026-10-03-keyboard-selection-design.md`.

**Deliverable:** One reviewed vertical change implementing all eleven issue criteria together. The spec is authoritative; preserve its exact defaults, alias and lifecycle semantics.

**Constraints (copied verbatim):**

- Preserve Python 3.11+ support; add no dependencies and change no package version.
- Preserve Shift+arrow's leaving-row toggle, modifier-click, cursor fallback for copy/delete, and the existing approved alias pairs.
- Scope selection to the focused local/S3 file-manager pane; preserve the other pane and unrelated service behavior.
- Use applicable local checks only; do not dispatch GitHub Actions, touch live AWS, publish releases/packages, bypass protections, or push/merge from the implementer.
- Edit canonical documentation only; regenerate copied assets/site/wiki through existing scripts, never hand-edit generated output.

**Files and responsibilities:**
- `src/aws_tui/app.py`: register `pane.enter_multiselect`, `pane.toggle_select`, `pane.select_all`, `pane.clear_selection`, `pane.exit_multiselect`; small focused-pane command bridges; strict modal guards; contextual physical alias routing; five scoped palette entries and post-dismissal selection invocation.
- `src/aws_tui/infra/keymap_store.py`: keep `v`, `space`, `a`; add clear on `u`, exit on `ctrl+v`; preserve APPROVED_ALIAS_PAIRS unchanged.
- `src/aws_tui/ui/bindings.py`: descriptions for clear/exit; exit yields to editors. Every ID must materialize its own Binding.
- `src/aws_tui/vm/file_manager/pane_vm.py`: mode/count/selected-byte projection and notification; minimum lifecycle reset required by RED evidence, preserving all existing mark/filter/cursor/generation semantics.
- Tests: extend `tests/integration/test_keybinding_wiring.py`, `test_command_palette_wiring.py`, `test_copy_delete_actions.py`; create `tests/integration/test_keyboard_selection.py` for mounted behavior; extend `tests/unit/ui/test_bindings.py`, `tests/unit/infra/test_keymap_store.py`, and `tests/unit/vm/file_manager/test_pane_vm_border_swap_marks.py` or existing focused VM tests.
- `docs/keybindings.md`: selection and action tables describe shipped defaults and shared key context; clear and exit included, unrelated deferred entries preserved.
- Only genuinely affected snapshot goldens/canonical derived assets after reviewed mismatch diagnosis. No blanket update or assertion weakening.

**All eleven ACs and verification requirements:**

| AC | Required verification |
| --- | --- |
| 1: five handlers and emitted keys | App registry plus BindingResolver, defaults and independently remapped aliases |
| 2: five palette entries | File-manager entry IDs/labels and absence on unrelated services; actual Enter execution |
| 3: Space modes | Running pilot: normal Quick Look, multi-select toggle and no preview/storage read |
| 4: modal containment | Running confirm modal: all five shortcuts leave mode, marks and captured transfer targets unchanged |
| 5: filtered select-all/whole-list clear | VM fixtures with real rows, parent link, hidden marks and filter transitions |
| 6: visible mode/count/bytes and exit | Mounted pane summary before marking, after mark/clear and after exit |
| 7: parent/empty/swap/reload | Command matrix; reload pending, failure/cancel and stale/source replacement as applicable |
| 8: no selection storage calls/exact transfers | Recording in-memory provider baseline; selection makes zero calls; confirmed copy/delete consume exactly marked rows, including hidden marks |
| 9: other pane retention | Pilot selects distinct panes, changes focus and checks both mark sets |
| 10: existing interaction contracts | Border/swap marks, modifier-click, copy/delete and keymap suites; credential recovery and editor/modal containment regression suites |
| 11: truthful keybinding docs | Canonical selection and action tables show all five shipped keys/semantics, with unrelated deferred actions retained |


**Action/default contract:**

| ID | Default | Explicit command |
| --- | --- | --- |
| pane.enter_multiselect | v | Enter focused-pane mode without adding a mark |
| pane.toggle_select | space | Toggle cursor real row, using existing command semantics |
| pane.select_all | a | Add marks to filtered non-parent rows |
| pane.clear_selection | u | Clear the entire listing's marks, including hidden ones; mode remains until exit |
| pane.exit_multiselect | ctrl+v | Exit mode and clear marks; editor paste wins in editable focus |

**Step 1: Write and run RED regressions before implementation.**
- [ ] Prove registry and emitted binding gaps for all five IDs. Update the exact runtime contract with positive assertions; do not weaken handlerless-action guards for unrelated IDs.
- [ ] Prove palette discovery/scope and actual Enter execution for enter/toggle/select/clear/exit after dismissal. The palette VM invokes before Screen dismissal today; account for this at the App bridge without a modal loophole.
- [ ] Pilot normal Space opens Quick Look; v enters mode; Space toggles only the cursor mark; u clears; ctrl+v exits. Inspect the actual mounted summary for mode, zero/nonzero marked count and selected bytes.
- [ ] Pilot a healthy focused pane selects filtered rows without credential recovery. Error/source contexts preserve auth recovery. Separate Space/a overlays keep explicit actions independent and do not rely on binding ordering.
- [ ] Push a confirm modal with a captured copy/delete selection; press every new key and assert mode, marks and target list stay unchanged. Exercise coordinator-only modal state too.
- [ ] VM matrix: filtered select-all, hidden clear, parent-only and empty states, source swap, reload, pending/error/cancel and stale results as applicable. Selection itself must issue zero provider reads or mutations after startup baselines.
- [ ] Pilot distinct marks on both panes survive focus changes and operations on the focused pane only. Confirmed copy and delete target exactly the marked rows; cursor fallback remains unchanged when no rows are marked.
- [ ] Preserve existing Shift+arrow leaving-row toggle and modifier-click behavior.

**Step 2: Implement the smallest complete change for GREEN.**
- [ ] Reuse PaneVM command properties and their can_execute checks; do not reimplement marking in the App.
- [ ] Put shared-key decisions in physical binding dispatch using normalized overlap checks. Palette/direct registry commands retain explicit semantics. Space on unrelated Glue/EMR pages keeps existing activation; auth palette recovery remains explicit. Loading selection is inert without storage calls.
- [ ] Strict handler guards reject modal precedence and unrelated services. Schedule only the new synchronous palette selection callbacks after dismissal; verify focused-pane restoration.
- [ ] Add readable mode-aware summary even at zero marks and publish changes; retain normal-mode formatting. Clear/exit semantics must match the existing VM commands.
- [ ] If pending/error/cancel RED confirms stale reload marks, reset marks at reload start without bypassing generation guards or mutating the other pane. Do not redesign selection or transfer state.
- [ ] Update canonical docs and runtime expectation lists to the new true behavior; keep alias approval and unrelated deferred functionality unchanged.

**Step 3: Verify and review artifacts.**
- [ ] Run all changed/new tests. Run the protected regressions: `tests/unit/vm/file_manager/test_pane_vm_border_swap_marks.py`, `tests/integration/test_modifier_click_multiselect.py`, `tests/integration/test_copy_delete_actions.py`, `tests/unit/infra/test_keymap_store.py`, `tests/integration/test_keybinding_wiring.py`, `tests/integration/test_command_palette_wiring.py`, `tests/integration/test_credential_recovery.py`, and `tests/integration/test_modal_key_containment.py`.
- [ ] Run relevant pane UI/VM suites and snapshot families. Use `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/cairo/lib .venv/bin/python -m pytest ...` where Cairo rendering applies. Diagnose each changed golden and inspect final renders, including selection mode. Do not run controller-owned full coverage/docs/build gates in parallel or restart live test handles.
- [ ] Record RED/GREEN commands, counts, all eleven AC-to-test mappings, storage-call/transfer evidence, keys/overlays, rendered screenshots and changed files in the task report. Explicitly distinguish direct pilot evidence from unit-only claims.
- [ ] Commit coherent implementation/tests/docs with normal hooks. No bypass, push, merge, issue/board writes, or child-agent delegation. Return only DONE/NEEDS_CONTEXT/BLOCKED, commit(s), one-line test summary and material concerns.

**Controller completion:** Independent task review then whole-branch review; applicable local full source coverage, all-file hooks, docs generation/check and package gates; protected PR to develop, promotion to main, source parity/post-promotion checks, feature cleanup, substantive issue closure and board Done. Complete that cycle before #240.
