# Focus helper preconditions

## 1. Goal and evidence

Issue #276 retains two requirements: recover from a dropped deferred focus
request and reject a known invalid target promptly. Textual 8.2.8 evaluates
focusability when its deferred callback runs. Visibility, loading and disabled
state can change, so one observation cannot prove permanent unavailability.
The existing delayed-focus test demonstrates that distinction.

## 2. Selected behavior

Keep the helper's default bounded retry. Add the keyword-only argument
`require_focusable: bool = False`. A caller that has established readiness can
set it to `True`; hidden, disabled, loading or non-focusable targets then fail
before the first deferred focus request. Check display state through ancestors,
because Textual's `focusable` property does not include CSS display state.

Reject detached or unmounted targets in either mode. Recheck attachment during
the wait so removal does not consume the remaining budget. Keep the existing
retry and deadline when a target is attached and temporary unavailability is
allowed. Issue requests only while the target is focusable and displayed;
Textual can otherwise report focus on a CSS-hidden widget. Include target
identity, mount/attachment, focusability, visibility,
ancestor display/disabled state and loading state in assertion diagnostics.

The known-ready Glue Retry, Load more and Time travel controls opt into the
precondition check after their existing readiness assertions. Existing callers
otherwise retain their retry contract.

The disabled-picker regression establishes focus before disabling its target.
It then verifies that Enter cannot open the disabled picker. Waiting for focus
after disabling the target would assert an impossible precondition.

## 3. Alternatives and scope

Rejecting every initially non-focusable target would break valid delayed
availability. Shortening the timeout would hide that distinction and could
increase flakes. The explicit caller contract preserves both requirements.

This is test support only. It changes no application focus policy, dependency,
provider, user guide, snapshot or global configuration.

## 4. Verification and authority

Use real mounted widgets for ready, delayed, hidden, disabled, loading and
removed states. Simulate one dropped request to verify retry separately.
Bound failure tests independently of the helper's 30-second default. Run every
direct caller, the affected Glue tests repeatedly and supported local runtimes.
No hosted workflows or live AWS calls are needed. Missing platforms are
recorded as follow-up verification under the owner's local-only goal policy.

The owner authorized routine design decisions and implementation without
repeated approval in the sequential issue goal.
