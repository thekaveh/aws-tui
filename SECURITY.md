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

aws-tui orchestrates the AWS CLI and the `boto3` credential chain for AWS connections and does not store AWS credentials itself. Reports involving the AWS CLI, `boto3`, or upstream Python libraries should be filed with those projects.

### 1.3.1. S3-compatible static credentials are persisted on disk

When a user adds an `s3-compatible` connection with `credentials = "static"` (the default written by the in-TUI add form), the `access_key_id`, `secret_access_key`, and optional `session_token` are persisted **in plaintext** to `<config-dir>/config.toml` (see `docs/platforms.md` for the exact OS path). On POSIX filesystems aws-tui creates the config directory with owner-only permissions and writes the config file through an owner-only temporary file; platforms without POSIX permission bits depend on the OS profile's normal user isolation. For non-throwaway credentials we recommend the `credentials = "keychain:<service>"` source, which delegates secret storage to the OS keychain via the `keyring` library. The static-credentials path emits a launch-time warning toast as a reminder.

### 1.3.2. Crash dumps can contain redacted log content

The crash-recovery flow writes a dump to `<cache-dir>/crash/<ts>.txt` containing the traceback, the last 1000 lines of the JSON log, and the last 100 user-action records. aws-tui redacts secret-like structured fields, key/value text, URL userinfo, and URL query strings before writing durable logs or crash reports. A user who has added third-party logging or who shares a crash file with a maintainer should still review the file first because no text redactor can prove arbitrary third-party output is safe.
