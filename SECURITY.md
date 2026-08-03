# 1. Security Policy

## 1.1. Supported versions

aws-tui is pre-release. Only the latest tagged release receives security fixes.

| Version | Supported |
| ------- | --------- |
| 0.8.x   | pending / unreleased |
| 0.7.x   | latest tagged release |
| < 0.7   | no        |

## 1.2. Reporting a vulnerability

Please report security issues privately to **kaveh.razavi@gmail.com** rather than opening a public issue. Include:

1. A description of the issue and its impact.
2. Steps to reproduce.
3. Any proof-of-concept code or sample artifacts.

We aim to acknowledge reports within 72 hours and to issue a patch within 14 days for confirmed vulnerabilities.

## 1.3. Scope

aws-tui reads shared AWS configuration and SSO token caches through
`aioboto3`/`botocore`; it does not launch the AWS CLI or initiate sign-in.
Users run `aws sso login --profile <name>` themselves when authentication is
required. Reports involving the AWS CLI, `botocore`, or upstream Python
libraries should be filed with those projects.

### 1.3.1. S3-compatible credentials use OS-backed secret storage

The in-TUI Settings form stores secrets in the OS keychain through the Python
`keyring` library and persists only a `keychain:` reference in
`<config-dir>/config.toml` (see `docs/platforms.md` for the exact OS path).
Credential updates write to the inactive one of two bounded revision services
before the configuration reference changes, so the previous committed
credentials remain available if persistence fails. Hand-authored
`credentials = "static"` entries
remain supported for compatibility, but their key fields are plaintext in
`config.toml` and trigger a launch-time warning toast.

### 1.3.2. Crash dumps can contain redacted log content

The crash-recovery flow writes a dump to `<cache-dir>/crash/<ts>.txt` containing the traceback, the last 1000 lines of the JSON log, and the last 100 user-action records. aws-tui redacts secret-like structured fields, key/value text, URL userinfo, and URL query strings before writing durable logs or crash reports. A user who has added third-party logging or who shares a crash file with a maintainer should still review the file first because no text redactor can prove arbitrary third-party output is safe.
