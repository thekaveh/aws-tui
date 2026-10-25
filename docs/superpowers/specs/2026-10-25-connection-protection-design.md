# Connection protection

Issue: #237. Baseline: `22faffcdbf594524233f004c9a4fe84566a30be7`.

## 1. Policy

Persist `protected` on each connection; old configurations default to false.
A connection's policy is separate from routing identity and credentials.
Settings exposes both AWS profiles and S3-compatible connections without reading secrets.
Materialize discovered profiles when their policy is saved. Preserve policy during credential edits.

Block initiating remote mutations: S3 writes, deletion, rename, marker creation,
uploads, sync, recovery and retries; EMR submission; Athena execution and
Athena-backed Iceberg inspection. Permit reads, loaded results, downloads and
explicit Athena/EMR cancellation under existing ownership/confirmation rules.
Never infer policy from a connection name. IAM remains authoritative.

## 2. Boundaries

Use a domain admission callback captured for one connection. Composition supplies
fresh, off-loop configuration reads, without resolving credentials. Missing,
changed or unreadable captured sources fail closed. Policy changes do not change
source identity or redirect work. Service factories apply the same guards to
real and controlled clients.

An S3 adapter guards public mutation methods and preserves only capabilities
provided by its wrapped provider. Recovery destinations must inherit the same
admission callback. Copy checks its destination; move checks both endpoints
before payload transfer. Provider mutation admission occurs after UI confirmation.
An admitted provider operation completes its existing cleanup; the adapter does
not inject checks inside accepted multipart operations. Later batch operations
receive fresh admission checks. A blocked operation is never queued for replay.

Athena and EMR client adapters guard only initiating methods. Reads and
cancellation remain delegated. This covers Athena Query and Glue's inspector
without requiring every UI entry point to implement a separate policy.

## 3. Alternatives

UI-only disabled buttons miss retries and alternate entry points. Modifying only
SDK clients misses controlled providers and service adapters. A shared admission
callback with scoped adapters covers those paths while retaining existing cleanup.
Do not add a general authorization framework or replace provider capabilities.

## 4. Verification

Use recording clients, temporary configurations and actual service/VM/UI flows.
Cover policy changes after confirmation, source removal/replacement, mixed pane
connections, aliases, protected-source downloads, zero initiating calls and
cancellation. Check adapter capability parity on supported Python versions.
Run existing affected suites, type/lint, architecture and documentation checks.
Local evidence is authorized for completion. Live AWS and unavailable native
platform checks are deferred, nonblocking and never reported as passed.
