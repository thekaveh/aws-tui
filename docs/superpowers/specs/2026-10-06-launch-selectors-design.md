# Session-scoped launch selectors — issue #264

## Goal and authority

Open a known connection/profile, region, registered service or file location
directly from the CLI. Explicit arguments override startup defaults for this
session without saving configuration. The owner authorizes routine design and
implementation decisions and the complete sequential delivery cycle without
intermediate approval. GitHub Actions remain disabled; applicable checks run
locally. This specification preserves all nine acceptance criteria in #264.

The intake and linked-work responses are retained under
`.superpowers/sdd/2026-10-06-launch-selectors`. The issue is open, has no comments
or linked implementation, and does not identify a dependency blocker. The base
is develop `2bf47d17b3d7acf86a6ce0d82ea556934d4fdf28` on
`codex/issue-264-launch-selectors`. The primary checkout is clean and is the
sole worktree; unrelated dependency PRs remain untouched.

## Current behavior and alternatives

`main()` already supports the doctor command in addition to demo, help and
version; the ticket's historical line numbers predate doctor. Context creation
is delayed until after parser/help/version handling. Normal startup chooses
the configured default, then `AWS_DEFAULT_PROFILE`/`AWS_PROFILE`, then the
first discovered connection. Its asynchronous boot chain retries other
connections and ultimately displays local fallback panes.

The chosen approach is read-only preflight followed by an explicit startup
path. A small launch module owns request/resolution/location data and early
validation. The composition root wires the selected session identity into
the app, and the app owns its mounted startup lifecycle. Keeping everything
in `main()` would grow an already large composition boundary and make pure
validation harder to test. Resolving selectors only after mounting would
delay deterministic CLI errors and make preserving early-exit and account
boundaries harder. Neither alternative is needed.

## Global constraints

- Preserve all nine #264 acceptance criteria; no new limitation waiver.
- Support Python >=3.11 and existing layer boundaries; add no dependency.
- GitHub Actions remain disabled; perform applicable verification locally.
- Preserve normal protected push/PR/merge workflow; no admin bypass or force push.
- Explicit selector failures never start a client for another account,
  enter the legacy connection retry chain, or silently become local fallback.
- Help, version and argument errors exit before `build_app_context`, client
  creation, terminal protocol negotiation or UI mounting.
- Launch arguments do not save, materialize or change configuration bytes.
- Startup location handling lists/reads only; it never copies, deletes,
  uploads, submits SQL or starts a job.
- Keep no-argument startup, demo behavior, doctor, existing assertions,
  platform skips, coverage floor, snapshot schema and golden bytes intact.
- Use exact connection/profile/region identity. Session overrides must be
  compatible with existing current-source checks and credential recovery;
  they must not make a valid launch look stale or reroute it to another source.
- Do not replace the navigation/focus architecture or add saved workspaces,
  credential flags, ARN routing, releases or remote publication.
- Preserve unrelated branches, PRs, worktrees, evidence and shared caches.

## Argument semantics

`--connection NAME` selects one exact known connection name, including an
explicit AWS alias or an S3-compatible connection. `--profile NAME` selects
one exact locally discoverable AWS profile, including when a configured
connection of another kind has the same name. These flags are mutually
exclusive and unknown values fail rather than choosing a default. Resolving
the selected name/profile does not dereference unrelated credentials.

`--region REGION` replaces only the selected session connection's region.
Reject empty/control-character/malformed region identifiers before startup;
describe the valid identifier form and examples in help. Do not enumerate
remote regions, fetch credentials or build clients to validate the argument.
Use immutable replacement rather than writing the resolved config entry.

`--service ID` accepts exactly the IDs registered by the application's
service definitions, currently S3, Athena, EMR Serverless and Glue. Settings
is an app screen, not a registered launch service. Parser choices and support
validation derive from the same descriptor IDs and support predicates used
by the real registry; never maintain a second literal ID/support table.
Reject an unsupported service/connection pair before composition.

When a connection/profile flag is absent, other explicit options may use
the ordinary default resolution precedence. Once an explicit launch is
resolved, that selected identity is pinned for startup: a failure must not
try the next connection. The default launch service is S3. A remote location
requires a supported selected connection. A local location with no available
remote connection can open local panes without inventing or persisting an
AWS connection. A requested AWS-only service with no usable connection fails.

`--location LOCATION` implies the file service when `--service` is omitted;
combining it with a different service is an argument error. Supported forms
are `s3://BUCKET[/PREFIX]` and an existing native local directory path. A
local relative path is resolved from CWD; `~` is expanded. Reject missing
S3 buckets, unsupported URI/ARN forms, missing local directories, file
paths, NUL/control characters and invalid combinations with a one-line
safe message. Preserve literal S3 key characters and prefixes rather than
URL-decoding or changing their account/path meaning. Native local handling
must respect the existing LocalFS/PathRef drive/root representation.

S3 locations initialize and focus the remote left pane. Local locations
initialize and focus the local right pane, or the right pane of a local-only
view when there is no remote connection. Set the chosen initial path before
its first listing so startup does not first list an unrelated root and then
race a navigation task. Provider canonical path is the authoritative native
local-path assertion; the pane displays the selected location coherently.

All five flags are real-context selectors. They conflict with effective demo
mode, including `AWS_TUI_DEMO`, and with doctor. Preserve argparse's mutual
exclusion/usage-error behavior (status 2). Help still exits through argparse
before local discovery. Version exits without composition or discovery;
syntactic/demo/doctor conflicts are handled before that early exit.

## Data flow, validation and lifecycle

1. Parse flags using a mutually exclusive connection/profile group and
   choices from the service definitions. Reject incompatible demo/doctor
   combinations without context construction.
2. Return early for doctor/help/version through their existing paths.
3. For explicit selectors, use a read-only ConfigStore and resolver to obtain
   a fresh local identity and validate region, location and service support.
   Retain both underlying resolved identity and effective session identity
   when a region/profile override requires that distinction. No app context,
   providers or clients are built during failed preflight.
4. Pass immutable resolved launch state through composition. It must not be
   stored in config defaults or environment variables. Identity checks must
   re-read the underlying selected source while accepting the legitimate
   session override. Actual source edits/removal still invalidate it.
5. The explicit mount path adopts only the selected service and location.
   Observe the actual readiness/failure state using existing bounded worker
   mechanisms; do not treat successful scheduling as successful startup.
   Keep the ordinary boot chain untouched for launches without selectors.
6. Deterministic selector/preflight errors are one line on stderr and nonzero
   with no traceback. Render untrusted values safely without embedded
   newlines or secrets. An explicit startup failure records a safe failure,
   exits the app nonzero and drains its lifecycle workers rather than
   invoking crash-dump behavior or falling through to another account.
   Construction failures release an unstarted context through the existing
   cleanup contract. Cancellation/shutdown must drain owned work.

A small pure launch module is preferred to further expanding `app.py`.
Service support methods currently depend only on the connection and can
be exposed safely for preflight without constructing service clients.
Exact implementation helper names are an internal choice; these observable
contracts and the real-registry correspondence are binding.

## Acceptance-to-evidence map

| AC | Required evidence |
| --- | --- |
| 1 | Real `main()` help output contains all five flags, value forms/registered service IDs and explicit-over-config/environment precedence; no context/client/UI calls. |
| 2 | Both connection/profile flags cause status 2 before discovery/composition. |
| 3 | Compare choices and support with the real ServiceRegistry; each registered ID accepted and unknown IDs rejected; fake registry/resolver tests isolate validation. |
| 4 | Unknown exact connection/profile, unsupported pair and invalid location produce one stderr line, nonzero, no traceback and zero context/client/UI calls. |
| 5 | Selected failure with a valid different default available exits; factory spies record no other account or fallback mount. Include runtime failure, not just parsing. |
| 6 | Actual hosted S3-prefix and native local-directory journeys verify chosen pane/path/focus and provider reads; mutation spies remain empty. Include local-only without AWS. |
| 7 | Isolated config bytes before/after startup and shutdown are identical; absent config is not created by selectors. |
| 8 | Patched real `main()` covers no arguments, version, help, demo flag/env, demo-selector conflicts and preserved doctor behavior. |
| 9 | Parser/semantic errors, help and version bar context, clients, UI mount and resize negotiation. |

Additional focused regressions cover profile/name collisions, an aliased
profile, overridden region reaching actual factories and source validity,
source edits after resolution, location/service mismatch, cancellation and
startup worker failure. All tests use isolated local inputs and fake/demo
providers; no live AWS mutation or network evidence is inferred.

## Verification and delivery

The feature is one coherent implementation task: parsing, preflight and
startup wiring cannot independently satisfy the account/early-exit contracts.
Use behavioral RED tests, focused GREEN/covering tests, docs and an independent
combined spec/quality task review, then a most-capable whole-branch review.
Evidence records exact tested input hashes, commands, exits and raw logs.

After reviewed source is committed, run the applicable complete local gate:
native hooks, minimum-runtime grammar, default pytest plus coverage, canonical
combined statement/branch coverage >=70%, unchanged snapshot schema/goldens,
strict site/wiki/docs generation, wheel/sdist checks, Twine and actual built
wheel CLI/discovery smoke. Native macOS/Python 3.12 evidence is not native
Windows or Python 3.11 runtime evidence. Preserve existing warnings/skips.

Push and attach a feature PR into develop, merge normally, then attach and
merge a develop-to-main PR. Verify identical reviewed source and fresh local/
origin refs, run bounded post-promotion regressions/docs, safely delete only
the owned completed branch, post the substantive conclusion, close #264 and
mark Done. Hash-archive evidence before owned scratch cleanup. Only then
begin #261; #283's Windows requirement remains unwaived.
