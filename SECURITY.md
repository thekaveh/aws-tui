# Security Policy

## 1. Supported versions

aws-tui is pre-release. Only the latest tagged release receives security fixes.

| Version | Supported |
| ------- | --------- |
| 0.8.x   | latest    |
| 0.7.x   | no        |
| < 0.7   | no        |

## 2. Reporting a vulnerability

Please report security issues privately to **kaveh.razavi@gmail.com** rather than opening a public issue. Include:

1. A description of the issue and its impact.
2. Steps to reproduce.
3. Any proof-of-concept code or sample artifacts.

We aim to acknowledge reports within 72 hours and to issue a patch within 14 days for confirmed vulnerabilities.

## 3. Scope

aws-tui orchestrates the AWS CLI and the `boto3` credential chain for AWS connections and does not store AWS credentials itself. Reports involving the AWS CLI, `boto3`, or upstream Python libraries should be filed with those projects.

### 3.1. S3-compatible static credentials are persisted on disk

When a user adds an `s3-compatible` connection with `credentials = "static"` (the default written by the in-TUI first-run / add form), the `access_key_id` and `secret_access_key` are persisted **in plaintext** to `~/.config/aws-tui/config.toml` (or the platform-specific equivalent — see `docs/platforms.md`). Permissions on that file follow your home-directory umask. For non-throwaway credentials we recommend the `credentials = "keychain:<service>"` source, which delegates secret storage to the OS keychain via the `keyring` library. The static-credentials path emits a sticky launch-time toast as a reminder.

### 3.2. Crash dumps can contain log content

The crash-recovery flow writes a dump to `~/.cache/aws-tui/crash/<ts>.txt` containing the traceback, the last 1000 lines of the JSON log, and the last 100 user-action records. aws-tui's own source code does not log secrets, but a user who has added third-party logging or who shares a crash file with a maintainer should review the file first — values logged at boot (e.g. endpoint URLs containing embedded credentials, pre-signed URL query strings) would be reproduced verbatim.
