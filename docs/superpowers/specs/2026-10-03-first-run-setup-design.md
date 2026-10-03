# First-run setup and connection rediscovery design

## 1. Intent and scope

Implement issue #243 in the existing empty-start flow: a user who has no
resolvable source can add an S3-compatible connection, read AWS profile setup
steps, and rediscover profiles without restarting. All ten acceptance criteria
remain binding. #244 is already delivered and remains responsible for recovery
on an active source. Existing configured and demo startup behavior stays intact.

Reuse ConnectionResolver, ConnectionFormInline, its validators, and
S3ConnectionsVM.add_async. Do not execute AWS CLI commands, launch browser login,
generate IAM policies, create cloud resources, rewrite AWS CLI files, or add a
second credential subsystem. AWS config and credentials bytes stay unchanged.

The user authorized autonomous routine design and execution, protected delivery
through develop then main, and only applicable local checks. These instructions
supersede redundant stage approval menus and hosted Actions requirements.

## 2. Selected approach and alternatives

Use a small first-run content view, an optional connection section in the existing
navigation rail, and the App's existing lifecycle workers and navigation lock.
The view owns presentation and focus; the resolver owns file parsing; the existing
settings VM owns atomic persistence. This keeps startup semantics and normal
service navigation intact.

Routing Add into the full Settings page would reuse persistence but would not
supply the requested direct flow or keep error/cancel restoration local. A new
wizard with its own credentials/parser/persistence would duplicate existing
contracts. Neither is selected.

## 3. Read-only discovery

Add immutable ConnectionDiscovery(connections: tuple[Connection, ...],
invalid_sources: tuple[str, ...]) and ConnectionResolver.discover(). Invalid
source tokens are app-config, aws-config, and aws-credentials. The snapshot has
no raw exception text or credential contents. discover catches malformed app
configuration and collects failures from the existing AWS INI reader while still
returning usable sources. Explicit connections still win name collisions.

Keep list() and resolve() backward compatible: primary config errors still raise
from list, unreadable AWS INI files still tolerate failure, and list re-reads files.
Share existing parsing and merge helpers; do not add another parser. Discovery
only loads existing local metadata and configured credentials; it never probes,
builds a service, opens a provider, or writes files. DemoConnectionResolver exposes
a matching discover snapshot with its existing four demo connections.

## 4. Content view, rail, and provenance

The UI must not import infra.connection_resolver, including type-only imports.
Use vm/connection_discovery.py read-only structural protocols: ConnectionDisplay
has name and source properties; ConnectionDiscoveryDisplay has connections
(tuple of ConnectionDisplay) and invalid_sources (tuple of str) properties.
Existing frozen Connection and ConnectionDiscovery satisfy them structurally.
The view consumes these minimum presentation contracts, with no resolver-type
re-export, credential exposure, copies, casts, or architecture-rule exception.

FirstRunView contains three separately focusable actions with literal labels:
Add S3-compatible connection; AWS profile setup; Retry discovery. It shows the
application config path and clear guidance that discovery does not open sources.
AWS profile setup reveals instructions for aws configure and aws configure sso,
followed by Retry discovery, without executing either command.

The view embeds the actual ConnectionFormInline and opens it with open_for_add().
Add a public optional submit_label constructor argument, default save for Settings,
and readonly has_errors property. This view uses Save and open and explains that
this saves and explicitly selects the new source. Validation continues to disable
Save while has_errors. Persistence failures retain the form and values, rearm
submission through existing public methods, and show safe next-step text.

While the first-run session is unresolved, NavMenu shows a scrollable connection
section. Each focusable ConnectionChoice row renders connection name and the
literal source token, including config, auto-aws-profile, and demo. First-run rail
width is 28 cells so auto-aws-profile fits; normal width remains 12. The section
also has a Connection setup action that returns from Settings to setup. Rows
highlight/focus on navigation only; Enter or click explicitly selects. Long names
may wrap or ellipsize visually but the exact name is retained for selection;
markup in names and sources is displayed as text, never interpreted.

A successful activation clears the extra section and restores normal rail width.
Demo never needs an invented empty-start state: origin evidence uses the same
rendered ConnectionChoice component and the real demo snapshot.

## 5. Save, selection, error, and cancellation semantics

Saving uses entry_from_form then add_async. Commit succeeds before the form closes;
then fresh discovery makes the saved connection selectable, and the explicit Save
and open intent activates that exact connection without restarting. Ordinary Retry
only refreshes the candidate rows and status. No probe or provider work starts
until explicit row selection or Save and open.

Selection re-resolves the named connection from a fresh discovery snapshot so
removed sources and changed regions/credentials do not activate stale objects.
Only then does it probe credentials off the event loop. A thrown probe or MISSING
or EXPIRED result keeps setup actionable, shows failure text, and does not build a
provider. A CONNECTED result opens S3 using the existing transactional
RootVM.switch_connection_and_service and App service mount path. The new flow does
not change #244 behavior for already-active sources. Provider/build/mount failure
returns to actionable setup with a safe connection failure message and candidates.

Canonical next-step text (additional context is allowed):
- Invalid configuration. Fix the configuration file, then Retry discovery.
- No AWS profiles or S3-compatible connections found. Add a connection or set up an AWS profile, then Retry discovery.
- Credential probe failed. Refresh credentials outside aws-tui, then select the connection again.

Cancel before save closes the form, restores prior discovery/status/actions, and
writes nothing. Once Save and open has submitted, disable edits, Save, and Cancel
until atomic persistence returns. This prevents a misleading cancellation while
an uncancellable disk/keychain transaction is committing. ConnectionFormInline
must guard its cancellation action during submission, and existing close,
clear_submitting, and mark_name_invalid must restore the controls consistently.

App workers serialize selection with the service navigation lock. In-flight
Retry, selection, Settings navigation, and shutdown carry generation/ownership
checks; superseded results cannot mount content, reopen a cancelled form, steal
focus, or choose another identity. Never restart a worker merely because a wait
expires. A completed save remains committed if navigation supersedes its visual
result; report that fact and do not silently launch a stale source.

## 6. Keyboard, focus, themes, and lifecycle

At 120x40 all three actions, status, setup instructions, form controls, and
connection choices are reachable with keyboard only. Tab and Shift+Tab cycle
first-run controls and rail choices. The existing form's cycle_focus retains its
field/button ring while open. App priority Enter explicitly routes to the
first-run view or connection section before normal rail/service activation.
Typing in form Inputs remains ordinary typing; no global action consumes it.

Give the empty-start Add action initial focus after refresh instead of the normal
startup focus-drop callback. Normal S3/demo startup focus remains unchanged.
Deferred focus uses active-screen checks so a modal is not disturbed. Busy
presentation cannot accept duplicate save/retry/selection actions. Returning to
setup from Settings works even if there are no candidate connections.

Use existing theme palette tokens and the shared ModalButton component. No new
hardcoded colors or global selector changes. A first-run-only rail width change
must not alter normal nav/demo snapshots.

## 7. Verification and acceptance map

| AC | Required authoritative evidence |
|---|---|
| 1 | Empty full-App Pilot verifies three action labels and separately reachable focus, beside existing app sanity mount test. |
| 2 | Pilot opens actual ConnectionFormInline; invalid fields keep Save disabled and emit no persistence. |
| 3 | Keyboard Save and open against stub provider persists, lists connection, and mounts service in the same session. |
| 4 | Create profile in fixture AWS config, physical Retry, verify real resolver.list and rendered nav row; no restart or automatic provider work. |
| 5 | Three full-App Pilot cases assert distinct next-step strings for malformed config, zero profiles, and explicit selection probe failure. |
| 6 | Cancel restores previous empty/status view; compare exact app config bytes (and absence where appropriate). |
| 7 | Compare AWS config/credentials bytes or absence across instructions, retry, invalid submit, cancel, successful save, and selection. |
| 8 | Recording provider and probe stubs have zero calls before selection and real calls after explicit selection/Save and open. |
| 9 | Unit-render rows for config, auto-aws-profile, demo; assert literal origins are visible, including markup-safe names. |
| 10 | Physical keyboard flow at 120x40; snapshots in shipped themes, matching visible-content guards. |

Also test malformed AWS INI, name collision precedence, external repair/retry,
duplicate Save, slow selection superseded by Settings/shutdown, provider failure,
removed/reconfigured selection identity, cancellation restoration, and an overfull
rail that remains keyboard reachable. Use isolated filesystem/keychain, real
resolver/form/settings persistence, recording InMemoryFS, deterministic worker
barriers, and tests.helpers.wait_until/drain_workers. No live AWS or blind sleeps.

Run scoped tests during each task, independent scoped reviews, one whole-branch
review, then applicable full local source/snapshot/coverage >=70%, all-file hooks,
strict docs/site/wiki parity and package build/distribution checks on final HEAD.
Preserve Python >=3.11,<3.14. Only after these pass publish and protected-merge
feature to develop, promote develop to main, verify source parity and post-merge
behavior, clean this ticket's branches, then conclude/close #243 and mark Done.

## 8. Self-review

All ten original criteria have an evidence owner. Save and open is explicit
selection rather than discovery autoactivation. Origins include demo through the
same real row renderer without changing demo startup. Cancellation cannot promise
to undo an already committing save. File parsing and credential persistence stay
in the existing owners, normal list error contracts and first-run-only width are
explicit, and no acceptance criterion is deferred to a later ticket.
