# 1. VMx 3.23 maintenance audit for aws-tui

| Field | Value |
|---|---|
| Status | Implemented on the 2026-08-25 maintenance branch |
| Date | 2026-08-25 |
| Current dependency | `vmx>=3.23.0,<4` resolving to `vmx==3.23.0` |
| Previous locked dependency | `vmx==3.1.0` |
| Comparison baseline | `746a94c4` (maintenance branch point from `develop`) |
| Runtime implementation | `6d0321d8` |
| Historical audit | [VMx 3.1.0 adoption audit](2026-07-02-vmx-3-1-adoption-audit.md) |

## 1.1. Scope and method

This audit rechecked the VM and its Textual adapters against the installed VMx
3.23 public surface. It compared each existing VMx use with the closest current
primitive, inspected the installed implementations where lifecycle or
cancellation semantics mattered, and exercised the affected behavior against the
locked package. The audit covers compatibility, abstraction fit, production
line-count impact, and test impact. It does not treat a smaller diff as a reason
to weaken aws-tui's domain-specific stale-result, source-identity, or shutdown
contracts.

## 1.2. Adopted substitutions

| Area | Prior shape | VMx 3.23 shape | Result |
|---|---|---|---|
| Modal focus | `FocusCoordinatorVM` cleared and restored discriminator state itself. | Public `DiscriminatorVM.modal_open()` and `modal_close()` own modal save/restore. | Removes private-state coupling while retaining aws-tui's typed focus slots and message facade. |
| S3 connection validation | A mutable `FormVM` compatibility path registered validators after construction. | `FormVMBuilder.validator(...)` and `model_validator(...)` construct the complete immutable validation graph. | Matches the 3.23 construction contract and keeps field/model errors and approval gating in VMx. |
| Athena query admission | Local command-task state could outlive a cancelled command wrapper. | `AsyncRelayCommand.is_executing` remains the public admission source and is drained during shutdown. | New queries cannot overlap a still-draining cancelled operation. |
| Athena result loading | Shutdown tracked the outer VMx command task but not necessarily provider I/O that suppressed cancellation. | The VM tracks the provider fetch task behind the VMx command and drains it before disposal. | Preserves VMx command ownership while making teardown wait for real I/O completion. |

The existing VMx 3.1 substitutions remain the best fitting choices in 3.23:
`TokenPagedComposition` for EMR opaque-token accumulation,
`FilteredCompositeVM` for S3 pane projection,
`ScoredFilteredCompositeVM` for command-palette ranking, `ModalVM` for
result-bearing modal state, and `when_property_changed` for typed hub
subscriptions.

## 1.3. Compatibility fixes accompanying the bump

- The dependency floor and lock now resolve VMx 3.23.0.
- Settings widgets rebuild a complete form VM when validator-bearing UI state
  changes, instead of mutating a constructed validator graph.
- Focus tests assert single-modal save/restore and repeated-open no-op behavior
  through the public discriminator API.
- Athena query and result tests cover cancellation-resistant provider work and
  teardown admission.
- Test-only stand-ins were removed where they masked the installed package's
  actual command and component behavior.

## 1.4. Candidates retained or rejected

| Candidate | Decision | Rationale |
|---|---|---|
| `DialogService.present` as the Textual screen host | Retained as a future architecture change | It would move modal presentation ownership from Textual's screen stack into an app-level dialog adapter. That is broader than a dependency compatibility pass and would require one migration of every modal, focus, result, and shutdown route. Existing `ModalVM` composition already delegates result state to VMx without creating two modal hosts. |
| Replace EMR pollers with `AsyncRelayCommand` | Rejected | Pollers are long-lived, independently scheduled service operations. A user-invoked command abstraction would obscure cadence, terminal-state suppression, and explicit shutdown ownership. |
| Replace cross-service navigation with VMx dialogs or commands | Rejected | Immutable messages plus the app transaction preserve exact AWS identity, validate destination state, and support rollback. They are orchestration events, not dialogs or a single-VM command. |
| Remove the `S3ConnectionFormVM` facade | Rejected | The facade owns S3 field names, normalized form-to-config mapping, dynamically supplied UI validators, and domain-specific model rules. `FormVM` is the validation engine, not the domain API. |
| Replace `ContentHostVM` with a generic composite | Rejected | Hosted services require async worker drain before disposal and app-owned setup/shutdown behavior beyond a generic child collection. |

## 1.5. Production line-count metric

The metric compares the branch point (`746a94c4`) with the runtime maintenance
commit (`6d0321d8`) using `git diff --numstat`, restricted to the directly
affected VM and adapter files. Generated files, tests, lockfiles, and unrelated
hardening are excluded.

| File | Added | Removed | Net |
|---|---:|---:|---:|
| `ui/widgets/settings/connection_form.py` | 35 | 19 | +16 |
| `vm/athena/query_vm.py` | 7 | 4 | +3 |
| `vm/athena/results_vm.py` | 22 | 18 | +4 |
| `vm/chrome/focus_coordinator_vm.py` | 4 | 13 | -9 |
| `vm/settings/s3_connection_form_vm.py` | 33 | 55 | -22 |
| **Total** | **101** | **109** | **-8** |

The two direct VMx abstraction substitutions, form construction and modal focus,
remove a net 31 production lines. Cancellation-resistant Athena lifecycle guards
add a net seven lines, and the Textual form adapter adds a net 16 lines to honor
the immutable builder contract. The complete compatibility slice therefore
removes eight production lines while strengthening lifecycle behavior. This
metric intentionally does not claim that tests removed from the repository are
production savings.

## 1.6. Test impact

The maintenance slice adds or updates focused coverage for:

- immutable VMx form construction, dynamic validators, and approve gating;
- single-modal focus open/close restoration and repeated-open no-op behavior;
- Athena query cancellation, command admission, provider-task drain, and result
  shutdown;
- installed-package smoke behavior rather than test shims;
- Settings Tab order and the Textual adapter behavior affected by form rebuilds.

The initial locked-environment verification completed 3,227 unit tests plus 94
Moto-backed AWS cases. The final maintenance gate reruns the repository's unit,
integration, snapshot, E2E, package, type, lint, and documentation tiers; those
results belong in the branch maintenance report rather than this dependency
decision record.

## 1.7. Conclusion

The VM layer uses VMx 3.23 wherever its public abstractions fit the actual
contract. The remaining aws-tui facades carry application-specific behavior
rather than duplicating an available VMx primitive. The only material unadopted
candidate, `DialogService.present`, is an intentional modal-hosting architecture
decision and not a missed mechanical substitution.
