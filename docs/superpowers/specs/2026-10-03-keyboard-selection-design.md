# Keyboard selection design for #241

Issue: https://github.com/thekaveh/aws-tui/issues/241. Baseline develop: `7664752034ff2c5e381e1407308c9cbff047a5b5`; identical main source tree `8110e45285569b769a26fdae0804eb9d8a6a1e2f`.

## 1. Scope and observed state

The eleven current ACs require all five existing selection commands to be reachable through bindings and the file-manager palette. Three IDs already have default keys but no app handlers; clear and exit have neither. The existing VM owns marks, filtered select-all, whole-list clear, parent exclusion, selected bytes and source reset. The current summary does not expose mode when no rows are marked. Reload replaces marks only after the provider read; pending/error/cancel behavior must be checked against the no-marks reload requirement. There are no comments or linked implementation PRs. #242 is downstream; #240 filtering is explicitly not a prerequisite.

## 2. Constraints

- Preserve Python 3.11+ support; add no dependencies and change no package version.
- Preserve Shift+arrow's leaving-row toggle, modifier-click, cursor fallback for copy/delete, and the existing approved alias pairs.
- Scope selection to the focused local/S3 file-manager pane; preserve the other pane and unrelated service behavior.
- Use applicable local checks only; do not dispatch GitHub Actions, touch live AWS, publish releases/packages, bypass protections, or push/merge from the implementer.
- Edit canonical documentation only; regenerate copied assets/site/wiki through existing scripts, never hand-edit generated output.

## 3. Chosen approach

Register five small App bridges to the existing VM commands. Keep intentional shared-key decisions at `action_dispatch`, using the normalized triggering key and the shared-key intersection (extend the existing normalized overlap seam as needed). If action key lists overlap only partly, nonshared shortcuts retain the action's explicit meaning. Registry handlers and explicit palette commands retain their named meanings. Unconditional handler fallbacks would couple separately remapped keys and explicit palette actions; collapsing duplicate resolver bindings would undermine the requirement that every registered ID emits a binding.

Keep defaults `v`, `space`, `a`; add `pane.clear_selection` on `u` and `pane.exit_multiselect` on `ctrl+v`. Make exit non-priority so text editors retain paste. Add labels and five S3/file-manager-scoped palette entries, not global entries or extra footer chips. No new approved alias pair is needed.

When the Space aliases overlap, physical dispatch chooses Quick Look outside multi-select and cursor marking inside it. Existing Glue/EMR Space activation remains intact. When the a aliases overlap, a ready/empty file pane selects all; a credential/error state or another AWS page retains credential recovery. Loading must not trigger a provider read or mark stale rows. If the aliases are remapped separately, each named action remains independent. Explicit credential recovery from the palette always retries; it never becomes select-all.

Selection handlers enforce screen-stack/coordinator modal guards. Palette callbacks for these synchronous selection actions schedule invocation after palette dismissal using the existing App refresh scheduler, rather than weakening the guard. Test real Enter execution for all five, with focus restoration and no selection mutation behind a separate confirm modal. Preserve native modal Space button activation; test each physical shortcut behind a fresh modal, checking pane marks at activation rather than after legitimate transfer reload.

## 4. VM and display contract

Reuse existing mark operations and predicates; do not redesign the mark model, filtering, cursor movement, or transfer pipeline. Select-all adds marks only to filtered real rows; clear removes all marks, including hidden ones. Transfer targets and summary count/bytes derive from the existing filtered marked_entries contract: hidden marks are retained but inactive until visible again. Parent links are never marked. Empty and parent-only panes cannot gain marks. Reset marks when reload starts if RED evidence confirms stale marks during pending/error/cancel refresh; preserve generation and provider-identity guards. Source swaps reset only their own pane.

The mounted pane summary names multi-select mode, marked count and selected-byte total, including zero marks after enter or clear. Exiting clears marks and restores normal summary. Publish the VM change so the UI updates without another cursor movement. Preserve normal-mode copy and keep the summary readable; verify 120×40 and examine 80×24 if layout changes.

## 5. Acceptance mapping

| AC | Required verification |
| --- | --- |
| 1: five handlers and emitted keys | App registry plus BindingResolver, defaults and independently remapped aliases |
| 2: five palette entries | File-manager entry IDs/labels and absence on unrelated services; actual Enter execution |
| 3: Space modes | Running pilot: normal Quick Look, multi-select toggle and no preview/storage read |
| 4: modal containment | Fresh confirm modal per shortcut: selection cannot mutate underlying mode/marks/targets; native Space activation retains captured targets |
| 5: filtered select-all/whole-list clear | VM fixtures with real rows, parent link, hidden marks and filter transitions |
| 6: visible mode/count/bytes and exit | Mounted pane summary before marking, after mark/clear and after exit |
| 7: parent/empty/swap/reload | Command matrix; reload pending, failure/cancel and stale/source replacement as applicable |
| 8: no selection storage calls/exact transfers | Recording in-memory provider baseline; selection makes zero calls; confirmed copy/delete consume exactly the active filtered marked_entries; retained hidden marks stay inactive until visible again |
| 9: other pane retention | Pilot selects distinct panes, changes focus and checks both mark sets |
| 10: existing interaction contracts | Border/swap marks, modifier-click, copy/delete and keymap suites; credential recovery and editor/modal containment regression suites |
| 11: truthful keybinding docs | Canonical selection and action tables show all five shipped keys/semantics, with unrelated deferred actions retained |

## 6. Delivery and review

Routine implementation choices use the user's standing unsupervised authorization. One cohesive task owns app wiring, keymap, projection, tests and docs because shared-key and modal behavior must work together. Independent task and whole-branch reviews must inspect all eleven ACs and the actual rendered changes. Diagnose snapshot differences before updating only affected goldens; refresh any canonical derived hero if its source changes. Controller owns broad local coverage/static/docs/package gates, protected develop/main PRs, source parity, post-promotion checks and closure/cleanup.
