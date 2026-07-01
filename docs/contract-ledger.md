# 1. Consumed Contract Ledger

This ledger records external contracts that aws-tui consumes and the pinned
versions checked during maintenance. It is not a replacement for tests; it is a
durable map of the real upstream surfaces that mocks and adapters must track.

## 1.1. 2026-07-01 maintenance pass

| Integration point | Pinned version / ref | Consumed contract | Verification method |
|---|---:|---|---|
| `aioboto3.Session().client("s3", ...)` via `S3FS` and MinIO seed scripts | `aioboto3==15.5.0`, `aiobotocore==2.25.1` from `uv.lock` | Async S3 client creation accepts botocore `Config`, `endpoint_url`, and `verify`; client exposes paginator/object APIs used by `S3FS`. | Source trace through `src/aws_tui/domain/s3_fs.py`, `src/aws_tui/services/s3/service.py`, and `scripts/test-services/s3/seed.py`; focused unit coverage for `verify_tls`; full pytest suite includes mock-backed and MinIO-marked paths where available locally. |
| Boto credential/profile and S3-compatible options | `boto3==1.40.61`, `botocore==1.40.61` from `uv.lock` | Profiles, regions, endpoint URLs, path-style addressing, TLS verification, retries, and read/connect timeout configuration. | Source trace through `src/aws_tui/infra/aws_session.py`, `src/aws_tui/infra/connection_resolver.py`, and S3 provider construction; config parser now rejects string booleans so TOML must match the bool contract before values reach botocore. |
| Textual app/runtime API | `textual==8.2.7` from `uv.lock` | App launch, bindings, modal/screen stack, widgets, pilot tests, and snapshot rendering. | Full integration/snapshot test run exercises app startup, modals, focus cycling, settings flows, theme propagation, and demo mode. |
| VMx view-model helpers | `vmx==2.6.1` from `uv.lock` | VM lifecycle, observable state, message protocol, and form/composite helper contracts referenced by the VM layer and docs. | Architecture/docs trace plus strict mypy over `src/aws_tui`; no new VMx adapter changes in this pass. |
| MinIO local S3 harness | Docker image `minio/minio:RELEASE.2025-09-07T16-13-09Z` | S3-compatible endpoint, readiness probe, seeded buckets/objects, path-style config, local credentials, and host port exposure. | Manual trace of `scripts/test-services/s3/docker-compose.yml`, `seed.py`, and `config-snippet.toml`; snippet updated to match current Settings/default-connection flows, and Compose ports now bind to `127.0.0.1` only. The Python `minio==7.2.20` package is consumed by the separate `testcontainers[minio]` integration fixture, not by this manual Compose harness. |
| Config, path, and secret-storage helpers | `keyring==25.7.0`, `tomli-w==1.2.0`, `platformdirs==4.10.0` from `uv.lock` | OS keychain get/set/delete behavior, TOML serialization for `config.toml`, and platform-native config/cache path resolution with legacy fallback directories. | Source trace through `src/aws_tui/infra/keychain.py`, `src/aws_tui/infra/config_store.py`, and `src/aws_tui/infra/paths.py`; unit coverage exercises keyring delegation/error handling, strict TOML bool parsing/saving, private config/journal permissions, and platformdirs fallback behavior. |
| Python package build backend | `hatchling==1.30.1` from `uv.lock`; `build-system.requires` constrained to `hatchling>=1.21,<2` | PEP 517 wheel/sdist build behavior, package metadata, and version-file inclusion. | Build backend installed in the locked dev environment; CI and release run `uv build --no-build-isolation` so artifacts do not resolve an untracked hatchling version at build time. |
| GitHub Actions CI/release/publish workflow | `actions/checkout@v4`, `astral-sh/setup-uv@v7` with `uv==0.11.19`, `actions/upload-artifact@v7`, `actions/download-artifact@v7`, `pypa/gh-action-pypi-publish@release/v1`, `peter-evans/create-pull-request@v6` | Checkout, pinned uv installation, CI/build artifact upload, release artifact download, Sigstore/OIDC PyPI publishing, TestPyPI rehearsal, GitHub Release asset upload, and Homebrew tap PR creation. | YAML parse plus workflow source review; release job now uses `uv sync --frozen`, builds without isolation, requires manual PyPI dispatch from the matching tag, checks the release tag is reachable from `origin/main`, pins `gh release create` to the resolved tag SHA, and hashes the built sdist for the Homebrew bump instead of fetching PyPI's CDN. |
| Pre-commit hooks | `pre-commit-hooks@v4.6.0`, `ruff-pre-commit@v0.15.17`, `taplo-pre-commit@v0.9.3`; local `mypy` via locked env | Formatting, linting, type checking, TOML validation, trailing whitespace, EOF, and large-file hygiene. | Local equivalent checks were run from `.venv` because the Homebrew `uv 0.5.7` cannot parse this lockfile revision; the pyenv `uv 0.11.19` shim runs the hook successfully, and CI uses setup-uv v7 with `uv sync --frozen`. |

## 1.2. Deferred contract checks

- GitHub Actions and pre-commit hook refs are tag-pinned, not SHA-pinned, except
  `pypa/gh-action-pypi-publish@release/v1`, which follows PyPA's Trusted
  Publishing guidance and is intentionally tracked as their stable release
  branch. This pass recorded the supply-chain risk but did not convert refs to
  SHAs because it would add maintenance churn and reduce readability without an
  established repo policy.
- External upstream documentation was not exhaustively re-queried for every
  library API. The concrete code paths above were checked against the locked
  dependency graph and strengthened with tests where this pass changed behavior.
