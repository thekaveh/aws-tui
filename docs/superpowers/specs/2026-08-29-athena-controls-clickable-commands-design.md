# 1. Athena Controls and Clickable Commands Design

**Status:** Approved for implementation on 2026-08-29.

This document extends the approved Athena UI contract repair with the
follow-up issues found during hands-on review of `develop`. The existing
read-only Athena policy, Glue-to-Athena transaction, command registry, keymap,
VMx command ownership, focus order, and one-row command fitting remain intact.

## 1.1. Problems and Evidence

The Athena query controls pane is three terminal rows high, but each Textual
`Button` computes to two outer rows because of its own border. The pane has only
one content row after its border and padding are applied. Runtime geometry
therefore gives both buttons a zero-height content region, clipping their
symbols even though their outer rectangles remain inside the pane. Existing
tests checked only outer containment and missed this failure.

The Glue `Q` handoff is required to populate the mounted Athena editor with a
quoted, read-only `SELECT * ... LIMIT 5` starter query. The current automated
path populates both `AthenaQueryVM.sql` and `TextArea.text`, including after
revisiting Athena, but hands-on review observed a blank editor. The transaction
must make visible projection an explicit completion condition and retain
regression coverage for fresh and previously mounted destinations.

The bottom command legend displays action identity, enabled state, shortcut,
label, and tooltip but is presentation-only. Users should be able to click an
enabled command and receive exactly the same behavior as its keybinding or
command-palette entry.

## 1.2. Chosen Design

### 1.2.1. Query Controls

Keep the current order of Query controls, Query editor, and Execution detail.
Increase the controls track enough to contain standard three-row Textual icon
buttons with at least one visible content row. Run and Stop keep stable fixed
dimensions, existing tooltips, VMx-derived availability, and the established
editor -> Run -> Stop -> detail keyboard sequence. Geometry tests must assert
positive button content height as well as containment at wide, compact, and
narrow terminal sizes.

### 1.2.2. Starter Query Projection

`AthenaPageVM.open_table()` remains the owner of context resolution and starter
SQL generation. After it succeeds, the app-level handoff transaction explicitly
refreshes the currently mounted `AthenaPage` and waits for Textual refresh. The
transaction is visibly complete only when the mounted query view has projected
the VM value. It must not synthesize editor input, duplicate SQL policy in the
View, or execute the query.

Tests cover ordinary and Iceberg tables, a first Athena visit, a previously
visited Athena destination, exact `LIMIT 5` text in both VM and editor, and the
absence of `start_query` calls.

### 1.2.3. Clickable Command Hints

Each `_HintChip` remains content-sized and excluded from keyboard focus so the
current Tab order does not change. An enabled chip accepts a primary mouse
click and calls `AwsTuiApp.action_dispatch(action.action_id)`. This is the same
`ActionRegistry` route used by runtime bindings and command-palette entries.
The chip schedules an awaitable handler as an app-owned Textual worker when
necessary, so navigation may replace the chip without abandoning the action.

Disabled chips remain visible, dimmed, and tooltip-capable but ignore clicks.
Click handling does not synthesize a key event, depend on the displayed key,
or bypass the action registry. Rebuilt and retired chips cannot dispatch stale
actions. The pointer treatment distinguishes enabled commands without adding
focus rings or changing one-row fitting.

## 1.3. Alternatives Considered

Keeping the three-row pane and replacing `Button` with custom clickable
`Static` glyphs would be slightly shorter, but it would duplicate button
semantics and accessibility behavior. Restoring enough height for standard
buttons is safer and clearer.

Synthesizing the displayed shortcut on click was rejected because user keymap
overlays, widget key consumption, and priority rules could make mouse behavior
diverge from the named command. Direct action dispatch is canonical.

Making command chips keyboard-focusable was rejected because every command
already has a keybinding and palette entry; adding many footer Tab stops would
damage service navigation.

## 1.4. Error and Lifecycle Behavior

Unknown or unavailable actions remain governed by the existing action registry
and legend VM. Disabled chips do not dispatch. Awaitable handlers run under an
app-owned worker group so exceptions follow Textual's normal worker handling and
the action can outlive a chip removed by navigation.

The handoff retains generation guards, rollback, source identity validation,
and cancellation ownership. A superseded request must not project stale SQL
onto a newer Athena page.

## 1.5. Verification

Focused widget tests prove positive button content geometry and clickable
enabled/disabled behavior. Integration tests prove click/key/palette action
parity and durable starter SQL on fresh and revisited Athena destinations.
Affected Athena snapshots are regenerated and inspected at wide, compact, and
narrow sizes. The full test, lint, and type-check suites run before completion.
