# Read-only doctor command

Issue: https://github.com/thekaveh/aws-tui/issues/246

## 1. Authority and scope

Implement all eight current acceptance criteria. The standing goal authorizes routine design decisions and the complete protected delivery cycle without another approval menu. Only applicable local checks run; GitHub Actions remain disabled. This command diagnoses startup and authentication using existing infrastructure, without replacing logging, crash capture, credential recovery, or first-run setup.

Python remains >=3.11,<3.14; dependencies and lockfile remain unchanged. The installed botocore 1.40.61 is the supported minimum. Preserve architecture rules and existing CLI contracts, including `--demo`, `--version`, `--help`, and `python -m aws_tui`.

## 2. CLI and reports

Add `aws-tui doctor [--json] [--probe NAME]`. A probe requires one exact source name. Dispatch before app composition, Textual startup, resize negotiation, LogSink, CrashDump, keyring access, or demo composition. The demo environment does not invent healthy doctor results. Reject incompatible doctor/root launch flags as argparse usage errors.

Text and JSON carry each check's `name`, `result`, `context`, and `next_step`; JSON has integer `schema_version: 1`. Results are stable machine-readable strings. A report has deterministic ordering and exit code 0 when no check is actionable, 1 for an actionable diagnostic failure, and argparse code 2 for invalid invocation. Informational `skipped` or `unverified` does not claim a check passed. Both formats are generated from the same immutable report.

Checks cover version, Python/runtime/platform, effective config/cache/log/crash paths, config readability and parsing, keymap validation, local source discovery, local authentication state, and the limits of offline checks. Missing optional app config is distinct from invalid config; if usable AWS profiles exist, absence of app config alone need not be an actionable error. No discovered source is actionable and suggests Settings or adding an AWS profile.

Use explicit results for `missing_config`, `invalid_config`, `invalid_keybinding`, `keybinding_collision`, `unknown_action`, `missing_credentials`, `expired_sso`, `unreadable_sso`, `unverified`, `skipped`, and healthy `ok`. Runtime/read errors are contained using safe fixed guidance rather than exception messages. Each actionable result provides a concrete next step.

## 3. Default collector

`DoctorPaths` describes effective config file, cache directory, AWS config file, AWS shared credentials file, and SSO cache directory. Compute defaults at call time, honoring current path/environment semantics. Default path lookup does not create directories.

Load ConfigStore with `read_only=True`, distinguish absent paths from parse/read failures, and validate configured keybindings with KeymapStore. Reuse ConnectionResolver discovery with no keychain backend. Report invalid AWS discovery inputs separately rather than pretending they do not exist. Do not invoke an AWS credential provider, credential_process, keyring, client, or socket in the default path. Inaccessible inputs must not become false healthy or false missing results.

Inspect only known local credential fields. S3-compatible static/env/shared-profile credentials can prove local presence, never permissions. Keychain credentials remain explicitly unverified until a named probe. AWS profiles with static shared keys can prove local presence. Dynamic role, process, web identity, container or metadata providers remain explicitly unverified offline; discover profile metadata without executing them. Follow role source profiles for local SSO diagnosis, with cycle detection. An explicit AWS profile does not inherit unrelated global environment keys merely because they exist.

Reuse AwsSession.probe_token for SSO freshness where applicable, and contain TokenLoadError and malformed cache shapes/encodings. Distinguish missing, expired and unreadable SSO cache. A valid expiration without a usable access token is not sufficient. Never report cached tokens, refresh tokens, client secrets, raw cache data, or raw loader errors. Non-SSO probe_token CONNECTED is not proof of authentication or permissions.

## 4. Named probes

Default report includes a non-actionable skipped probe with guidance to name a source. An explicit probe replaces that default skipped row with its actual result, preserving all local diagnostic checks. Explicit unknown names are actionable; never fall back to another source. Resolve credentials for the selected source only, so unrelated keychains never prompt. Add a narrowly scoped selected-source resolver API if necessary; preserve existing list/resolve semantics.

Use one bounded read-only API operation: AWS STS GetCallerIdentity or S3-compatible ListBuckets. Discard response payloads; do not enumerate accounts, services, bucket contents, objects, queries, or jobs. Set connect/read timeout 5 seconds and total_max_attempts 1, including credential-provider clients. Close clients on success and failures. Credential-source network requests are permitted only by the explicit named probe.

Reuse botocore's credential resolver and profile provider builder rather than a new authentication engine. For modern SSO, substitute an isolated read-only SSOTokenProvider that loads the cached token without refreshing or saving it; use the same provider in nested assume-role source profiles. Legacy SSO also reads cache without writes. In-memory derived role credentials are allowed; durable cache writes and SSO OIDC token refresh/rotation are forbidden. Skip credential_process and interactive MFA with explicit guidance, including nested source profiles, because unknown executables or prompts cannot meet this read-only diagnostic contract. Other supported SDK sources are not blanket-skipped. Guard supported private SDK hooks with focused real-SDK tests at the installed supported floor.

Classify success, denied, unreachable, timed_out and skipped separately. Missing/expired/unreadable local auth prerequisites retain their diagnostic meaning. Never emit vendor exception messages, full service responses, request headers, account identifiers, ARNs, or signed URLs. Fake clients test every result and lifecycle. Modern/legacy/nested SSO fixtures prove no OIDC call, token save or file changes.

## 5. Privacy and integrity

Whitelist context fields and safe metadata; never dump Config, Connection, os.environ, exception strings, log/crash tails, SQL, AWS files, cache payloads or SDK objects. Apply existing redact_text/redact_mapping to projected display fields without replacing stable result identifiers. Strip terminal control characters and render dynamic help paths literally. Strip endpoint URL userinfo/query/fragment. Retain existing redaction and log/crash formats unchanged.

Sentinel fixtures cover static/env/keychain/session keys, SSO access/refresh tokens and client secrets, SQL in config/log/error/env inputs, authorization strings and presigned endpoint secrets. Assertions cover both JSON and text and demonstrate reuse of redact_text. Socket blockers, forbidden write/permission/keychain/process/client spies, and before/after bytes/modes/directory inventories establish the default read-only contract. Named probes must preserve all config/cache/log/credential files and permissions too.

## 6. Help and documentation

HelpModal names `aws-tui doctor`, JSON and named-probe usage, and the actual running context's log/crash paths. App supplies those paths; help does not import AWS infrastructure or composition. Preserve existing optional keymap constructor callers. A real running-app Pilot test checks command and actual paths, safe literal rendering, and keyboard access to the new section.

Update canonical user docs, README and changelog to describe offline limits, result/exit behavior and safe probes; verify the existing generated site/wiki transformations locally without publication. Change snapshots only if observed rendered product content changes, with explicit content evidence; do not blindly regenerate unrelated goldens.

## 7. AC evidence map

| AC | Required evidence | Owner |
|---|---|---|
| 1 | main(argv doctor) with all sockets blocked, composition/run forbidden and no file changes | Task 3, collector tests Task 1 |
| 2 | JSON schema parsed and every field present; text shares check results and guidance | Task 1, CLI Task 3 |
| 3 | tmp fixtures for missing/invalid config, InvalidKeybinding/KeybindingCollision, absent auth and TokenLoadError/expired SSO | Task 1 |
| 4 | exact named target only, fake success/denied/unreachable/timeouts/skipped, bounded clients and cleanup | Task 2, CLI Task 3 |
| 5 | both formats reject planted secrets/SQL/raw payloads and redact_text strips projected secret patterns | Tasks 1–3 |
| 6 | SystemExit 0/1/2 for healthy/actionable/usage; skipped default non-actionable | Task 3 |
| 7 | existing test_log_sink.py and test_crash_dump.py unchanged and passing | Task 3/local final gates |
| 8 | real AwsTuiApp HelpModal Pilot asserts command and ctx paths | Task 3 |

## 8. Alternatives and self-review

Starting app composition then exiting was rejected because it writes log/cache paths. Running the ordinary aioboto3 client factory was rejected because its retries are larger and its SSO provider may refresh disk cache. Skipping every dynamic profile was rejected because optional probes must be useful for the app's supported sources. An entirely new credential engine was rejected in favor of the SDK's existing providers with a confined read-only token adapter. No new package or broad refactor is needed.

Self-review: all eight ACs mapped, default socket and write isolation separated from opt-in networking, safe output independent of arbitrary exceptions, nested SSO and process profiles covered, help uses real paths, and publication/protected workflow remain governed by the goal. No unresolved product decision requires human input.
