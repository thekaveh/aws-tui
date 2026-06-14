# Security Policy

## Supported versions

aws-tui is pre-release. Only the latest tagged release receives security fixes.

| Version  | Supported |
| -------- | --------- |
| 0.0.x    | latest only |

## Reporting a vulnerability

Please report security issues privately to **kaveh.razavi@gmail.com** rather than opening a public issue. Include:

1. A description of the issue and its impact.
2. Steps to reproduce.
3. Any proof-of-concept code or sample artifacts.

We aim to acknowledge reports within 72 hours and to issue a patch within 14 days for confirmed vulnerabilities.

## Scope

aws-tui orchestrates the AWS CLI and the `boto3` credential chain — it does not store credentials itself. Reports involving the AWS CLI, `boto3`, or upstream Python libraries should be filed with those projects.
