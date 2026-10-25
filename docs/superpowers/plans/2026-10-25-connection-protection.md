# Connection Protection Implementation Plan

> Execute inline using the executing-plans skill. Routine decisions are authorized by the active goal.

**Goal:** Deliver #237 across every current remote mutation path.

**Architecture:** Persist a connection policy. Bind fresh admission callbacks to service-built providers and clients. Keep routing, reads and cleanup contracts.

**Tech stack:** Python 3.11–3.13, AnyIO, Textual, VMx, pytest.

## 1. Global constraints

Local checks only; no hosted workflows or real AWS. Preserve shared infrastructure.
No inferred production names, automatic replay, unsafe interruption or credential reads for policy.

## 2. Tasks

- [x] Config/resolver: add `protected: bool = False`; parse strictly and serialize true. Test old config, invalid input, AWS/S3 resolution and profile materialization first.
- [x] Domain admission: add an async callback and stable permission refusal. Guard S3 mutation methods, recovery destinations and client initiation. Test zero underlying calls, read/cancellation delegation and capability parity before implementation.
- [x] Transfer admission: call destination admission in `CrossFsCopy.copy`; call both endpoints in `CrossFsMove.move` before copying. Test protected-source moves produce no payload writes; downloads remain permitted.
- [x] Composition: bind each callback to captured source metadata, freshly read off-loop. Test edits, policy toggles, missing config, aliases and CLI overrides. Wire every service construction path.
- [x] Settings: expose all connection metadata, save policy transactionally, preserve it through credential edits, and show per-source policy. Test actual controls, differing panes and service context labels.
- [x] Document action policy, limitations and cancellation behavior in canonical configuration/service guides.
- [x] Run isolated affected pytest suites, Python-version checks, Ruff, mypy, architecture and docs checks. Independently review the complete diff, resolve findings and retest.
Integration: commit/push, feature PR to develop, promotion PR to main; verify exact trees, clean task branch/worktree and close #237 with evidence. Preserve unrelated Dependabot work.

For each implementation task: write the named behavioral tests, execute and record the expected failure, implement the smallest complete change, and rerun the affected suite. Keep receipts in the external progress ledger.
