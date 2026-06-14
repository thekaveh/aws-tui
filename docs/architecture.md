# Architecture

> Human-readable mirror of §2 of [the design spec](superpowers/specs/2026-06-13-aws-tui-design.md). Fleshes out as layers land in M1+.

aws-tui follows a strict five-layer architecture with one-way dependencies:

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

Each layer only imports from the layer beneath it. `ruff` `flake8-tidy-imports` enforces.

- **View** — Textual widgets and `.tcss` themes. Never touches `boto3`.
- **ViewModel** — VMx-based viewmodels with reactive commands. Never imports Textual.
- **Service plugins** — One folder per top-level service (S3 in v0.1.0).
- **Domain** — `FileSystemProvider` protocol with `LocalFS` and `S3FS` implementations. The Norton-Commander unifier.
- **Infrastructure** — `AwsSession`, `ConnectionResolver`, `ConfigStore`, `ThemeStore`, `KeymapStore`, `LogSink`. The only layer that touches the OS or AWS APIs.

See the spec for the full VM tree, lifecycle invariants, and per-VM capability adoption.
