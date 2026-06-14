# Connections (AWS profiles + S3-compatible)

> Mirror of spec §6.1–6.3 and §6.5. Lands in M1.

A **Connection** is the unit aws-tui authenticates as. Two kinds:

- `kind = "aws"` — uses the standard boto3 credential chain (env, shared credentials, SSO cache, IMDS, ECS task role). Auto-discovered from `~/.aws/{config,credentials}` on every launch.
- `kind = "s3-compatible"` — for MinIO, Cloudflare R2, Backblaze B2, Wasabi, Ceph, SeaweedFS, anything with an S3-compatible API.

## Config schema (`~/.config/aws-tui/config.toml`)

```toml
[connections.kaveh-dev]
kind = "aws"
profile = "kaveh-dev"
region = "us-east-1"

[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
credentials = "keychain:minio-local"      # or env:PREFIX_*, aws-profile:name, static
force_path_style = true
verify_tls = true

[defaults]
connection = "kaveh-dev"
```

## Credential sources for S3-compatible connections

| Spec | What it reads |
|---|---|
| `keychain:<service>` | macOS Keychain via Python `keyring` |
| `env:PREFIX_*` | `${PREFIX_ACCESS_KEY_ID}` + `${PREFIX_SECRET_ACCESS_KEY}` |
| `aws-profile:<name>` | An existing entry in `~/.aws/credentials` |
| `static` | Inline `access_key_id` / `secret_access_key` (startup warning + sticky toast) |

## Vendor quirks (manual checklist)

- **Cloudflare R2** — no bucket versioning, no replication; `region = "auto"`.
- **Backblaze B2** — smaller multipart limits than AWS.
- **MinIO** — uses path-style URLs (`force_path_style = true`); self-signed TLS dev setups need `verify_tls = false` (will emit a toast).
- **Wasabi** — mostly behaves like AWS.

## Recommended: 1-day MPU abort lifecycle rule

Set a 1-day lifecycle rule to abort incomplete multipart uploads on every bucket you write to from aws-tui or any other tool. aws-tui aborts MPUs on user cancel, but a network drop or app crash before the abort completes can leave orphans.
