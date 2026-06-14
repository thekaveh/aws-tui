# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-14

### Added

- **Infrastructure layer (M1).** Six independent boundary-layer modules
  under `src/aws_tui/infra/`, each unit-tested against tmp dirs:
  - `LogSink` — JSON-lines log writer with `RotatingFileHandler`
    rotation (5 MiB × 5 backups by default) at `~/.cache/aws-tui/log/`.
  - `ConfigStore` — TOML read/write of `~/.config/aws-tui/config.toml`
    via stdlib `tomllib` + `tomli-w`; atomic save via tempfile +
    `Path.replace`; `ConnectionEntry` / `Defaults` / `Keybindings` /
    `Config` frozen dataclasses; `kind` validation.
  - `KeychainBackend` protocol with `Keyring` (delegates to the
    `keyring` library) and `InMemoryKeychain` (test fake).
  - `ConnectionResolver` — unions explicit `[connections.*]` entries
    with AWS profiles auto-discovered from `~/.aws/{config,credentials}`
    via stdlib `configparser`; dispatches s3-compatible `credentials`
    against `keychain:` / `env:` / `aws-profile:` / `static` sources;
    `materialize()` promotes auto entries into the config file.
  - `AwsSession` — offline SSO cache probe (locates the cache file via
    `sha1(sso_session)` or `sha1(sso_start_url)`, reads `expiresAt`,
    compares against now-UTC with a 60-second skew buffer) and
    `aioboto3` client factory with botocore retries (adaptive, 6
    attempts), 10/60 s timeouts, force-path-style addressing, and
    `aclose_all_clients()` for graceful shutdown.
  - `ThemeStore` — layered `.tcss` loading (built-in via
    `importlib.resources` < user theme < user overlay); ships
    placeholder files for the four built-in themes (carbon, voidline,
    lattice, amber) to be filled in M5.
  - `KeymapStore` — action - keystroke indirection baked with the
    spec §4.2 defaults; overlay replaces per-action keys wholesale and
    refuses to introduce unknown actions.
- Integration sanity test composes all six components against tmp dirs
  to guard against circular imports and verify end-to-end probe success.
- Per-module strict-mypy + ruff + layer-rule clean.

### Changed

- Mypy config now ignores missing imports for `aioboto3` and `botocore`
  (no upstream stubs).

## [0.0.1] - 2026-06-14

### Added

- Initial project scaffold (M0): public GitHub repo, MIT license, VMx submodule, uv-managed dependencies, src-layout, hello-world Textual `AwsTuiApp` with `q`-to-quit, CI matrix on macos-14 / ubuntu-22.04 across Python 3.11–3.13.
- Full design spec at `docs/superpowers/specs/2026-06-13-aws-tui-design.md`.

[Unreleased]: https://github.com/thekaveh/aws-tui/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/thekaveh/aws-tui/compare/v0.0.1...v0.2.0
[0.0.1]: https://github.com/thekaveh/aws-tui/releases/tag/v0.0.1
