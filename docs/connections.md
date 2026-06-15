# Connections (AWS profiles + S3-compatible)

> Mirror of spec §6.1–6.3 and §6.5. See also the
> [cookbook](cookbook.md) for the "connect to local MinIO" walkthrough.

A **Connection** is the unit aws-tui authenticates as. Two kinds:

- `kind = "aws"` — uses the standard boto3 credential chain (env,
  shared credentials, SSO cache, EC2 IMDS, ECS task role). Auto-
  discovered from `~/.aws/{config,credentials}` on every launch.
- `kind = "s3-compatible"` — for MinIO, Cloudflare R2, Backblaze B2,
  Wasabi, Ceph, SeaweedFS, anything with an S3-compatible API.

## 1. Config schema (`~/.config/aws-tui/config.toml`)
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

[connections.r2-personal]
kind = "s3-compatible"
endpoint_url = "https://<account>.r2.cloudflarestorage.com"
region = "auto"
credentials = "keychain:r2-personal"
force_path_style = false

[defaults]
connection = "kaveh-dev"
theme = "carbon"
```

## 2. Credential sources for S3-compatible connections
The `credentials` field is dispatched at runtime:

| Spec | Source |
|---|---|
| `keychain:<service>` | macOS Keychain via the Python `keyring` library |
| `env:PREFIX_*` | `${PREFIX_ACCESS_KEY_ID}` + `${PREFIX_SECRET_ACCESS_KEY}` |
| `aws-profile:<name>` | An existing entry in `~/.aws/credentials` |
| `static` | Inline `access_key_id` / `secret_access_key` in `config.toml` — startup warning + sticky toast |

Recommended order of preference: `keychain` ▸ `env` ▸ `aws-profile`
▸ `static`. The in-TUI first-run form writes a `static` entry; the
follow-up step is to move the credentials to the keychain via
`keyring set <service> <key>` and switch the config to
`credentials = "keychain:<service>"`.

## 3. Auto-discovery + SSO cache probe
`ConnectionResolver.list()` unions on **every launch**:

1. `[connections.*]` entries in `~/.config/aws-tui/config.toml`
2. AWS profiles in `~/.aws/config` and `~/.aws/credentials` —
   auto-promoted to `kind = "aws"`, `profile = "<name>"`,
   `source = "auto"`.

Explicit entries win on name collision. Auto-discovered entries show
an `(auto)` badge in the picker; `: connection materialize <name>`
writes a real entry into `config.toml`.

For each AWS connection, `AwsSession.probe_token(conn)` performs a
cheap freshness check **without calling AWS**:

- Resolve the SSO cache filename via `botocore.tokens.SSOTokenLoader`.
- Read `expiresAt`, compare against now-UTC with a 60-second skew
  buffer.
- Return `connected | expired | missing`.

Total cost: one `os.stat` + one ~1 KB JSON read. Sub-millisecond.

## 4. Vendor quirks (manual checklist)
- **Cloudflare R2** — no bucket versioning, no replication;
  `region = "auto"`; uses HTTPS at
  `https://<account>.r2.cloudflarestorage.com`.
- **Backblaze B2** — smaller multipart limits than AWS (5 MiB min
  part vs. 5 GiB max); long-lived buckets need keys with `b2-` prefix.
- **MinIO** — uses path-style URLs (`force_path_style = true`);
  self-signed TLS dev setups need `verify_tls = false` (will emit a
  sticky toast at launch).
- **Wasabi** — mostly behaves like AWS; region matters (us-east-1 vs.
  us-east-2 buckets).
- **Ceph RGW / SeaweedFS** — typically path-style + custom region.

## 5. Recommended: 1-day MPU abort lifecycle rule
Set a 1-day lifecycle rule to abort incomplete multipart uploads on
every bucket you write to from aws-tui (or any other tool). aws-tui
aborts MPUs on user cancel and on the resume modal's `abort all`,
but a network drop or app crash before the abort completes can leave
orphans that accrue charges.

```jsonc
// lifecycle.json
{
  "Rules": [{
    "ID": "abort-incomplete-mpu",
    "Status": "Enabled",
    "Filter": {},
    "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 1 }
  }]
}
```

```bash
aws s3api put-bucket-lifecycle-configuration \
    --bucket <name> --lifecycle-configuration file://lifecycle.json
```

## 6. First-run flow
If `ConfigStore.load()` returns no `[connections.*]` and
`~/.aws/{config,credentials}` is also empty, aws-tui shows a welcome
modal on launch (per spec §6.4 Flow 5):

```
welcome to aws-tui
no AWS or S3-compatible connections configured.
  add aws profile  (runs 'aws configure sso' in your terminal)
  add s3-compatible (in-TUI form)
  skip for now
```

`add aws` shells out (synchronous; TUI freezes for the duration of
the wizard, which is expected) to `aws configure sso`. `add
s3-compatible` opens an in-TUI form prompting for name, endpoint URL,
region, access key, secret key. `skip` proceeds to the main screen
with no connection selected.

## 7. Crash-recovery transfer journal
aws-tui appends a JSONL line per completed multipart part to
`~/.cache/aws-tui/transfers/<id>.jsonl`. On launch it scans the
directory for journals lacking a terminal `finished` / `aborted`
record and offers a resume / abort / decide-each / keep modal — see
the [cookbook](cookbook.md#4-resume-after-a-crash) for the walkthrough.
