# 1. Connections (AWS profiles + S3-compatible)

> Mirror of spec §6.1–6.3 and §6.5. See also the
> [cookbook](cookbook.md) for the "connect to local MinIO" walkthrough.

A **Connection** is the unit aws-tui authenticates as. Two kinds:

- `kind = "aws"` — uses the standard boto3 credential chain (env,
  shared credentials, SSO cache, EC2 IMDS, ECS task role). Auto-
  discovered from `~/.aws/{config,credentials}` on every launch.
- `kind = "s3-compatible"` — for MinIO, Cloudflare R2, Backblaze B2,
  Wasabi, Ceph, SeaweedFS, anything with an S3-compatible API.

## 1.1. Config schema (`<config-dir>/config.toml`)
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
verify_tls = false                        # http:// MinIO -> no cert to verify

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

## 1.2. Credential sources for S3-compatible connections
The `credentials` field is dispatched at runtime:

| Spec | Source |
|---|---|
| `keychain:<service>` | macOS Keychain via the Python `keyring` library |
| `env:PREFIX_*` | `${PREFIX_ACCESS_KEY_ID}` + `${PREFIX_SECRET_ACCESS_KEY}` |
| `aws-profile:<name>` | An existing entry in `~/.aws/credentials` |
| `static` | Inline `access_key_id` / `secret_access_key` in `config.toml` — startup warning toast |

Recommended order of preference: `keychain` ▸ `env` ▸ `aws-profile`
▸ `static`. The in-TUI Settings form writes a `static` entry; the
follow-up step is to move the credentials to the keychain via
`keyring set <service> <key>` and switch the config to
`credentials = "keychain:<service>"`.

## 1.3. Auto-discovery + SSO cache probe
`ConnectionResolver.list()` unions on **every launch**:

1. `[connections.*]` entries in `<config-dir>/config.toml`
2. AWS profiles in `~/.aws/config` and `~/.aws/credentials` —
   auto-promoted to `kind = "aws"`, `profile = "<name>"`,
   `source = "auto-aws-profile"`.

Explicit entries win on name collision. Auto-discovered entries show
an `(auto)` badge in the picker.

> The dedicated command-palette path
> (`: connection materialize <name>`) for promoting an
> auto-discovered AWS profile into a real `[connections.*]` block is
> spec'd but deferred to v0.9 — the palette doesn't register
> connection-management entries in v0.8.x. To materialize today, add
> the `[connections.<name>]` block to `<config-dir>/config.toml`
> by hand (the schema is shown in [§1.1](#11-config-schema-config-dirconfigtoml)).

For each AWS connection, `AwsSession.probe_token(conn)` performs a
cheap freshness check **without calling AWS**:

- Resolve the SSO cache filename by mirroring the pinned
  `botocore.tokens.SSOTokenLoader` cache-key contract.
- Read `expiresAt`, compare against now-UTC with a 60-second skew
  buffer.
- Return `connected | expired | missing`.

Total cost: one `os.stat` + one ~1 KB JSON read. Sub-millisecond.

## 1.4. Switching between connections at runtime

Every connection the resolver returns — AWS profiles, manually-configured
`s3-compatible` entries, and auto-discovered AWS profiles alike — joins
a single in-app source-cycle on the focused pane. Press **`Shift+S`** (or
`S`) on a pane to step through it in this order:

```
local
  → aws s3 · profile-1 · us-east-1
  → aws s3 · profile-2 · us-west-2
  → ... (every other AWS profile)
  → s3-compatible · minio-local · localhost:9000
  → s3-compatible · r2-prod · <account>.r2.cloudflarestorage.com
  → ... (every other s3-compatible connection)
  → local   ← wraps
```

Why this is useful day-to-day:

- **Multi-account AWS work** — if you have several `[profile *]` blocks
  in `~/.aws/config` (typical for orgs with multiple AWS accounts or
  SSO permission sets), `Shift+S` is the fastest way to jump between
  them. One keystroke per profile; the pane re-mounts in place with
  the new identity in the bottom border subtitle. No command palette,
  no modal, no re-launch.
- **Multiple `s3-compatible` endpoints** — there's no fixed limit. Add
  as many MinIO, Cloudflare R2, Backblaze B2, Wasabi, Ceph, SeaweedFS
  endpoints as you want (in the in-app Settings nav page, or by hand
  via additional `[connections.<name>]` blocks). Every new entry joins
  the cycle automatically on next launch (or immediately if added
  through Settings — the rail's `ConnectionListChangedMessage`
  refreshes the candidate ring without a relaunch).
- **Cross-account / cross-vendor transfers** — put one account on the
  left pane, a different account on the right pane (each pane cycles
  independently), then `c` (copy) streams between them via
  `CrossFsCopy` — no intermediate local hop required. The
  `CrossFsMove` engine exists, but `m` move UI wiring is deferred to
  v0.9.

The `,` key opens **Settings** where you can add, edit, or delete
`s3-compatible` connections (see the
[`docs/cookbook.md` MinIO walkthrough](cookbook.md#11-connect-to-and-switch-between-data-sources)).
AWS profiles are read-only from aws-tui's perspective — manage those
through the standard `~/.aws/` tooling.

`Shift+S` filters out connections that have been observed unreachable
during the session (e.g. a stopped MinIO container). A one-line info
toast names what was skipped on the first press. Selecting S3 from the
nav after a local-only fallback retries the initial connection and
clears that connection's unreachable mark; pressing `r` on an
unreachable pane and recovering it also clears the mark.

## 1.5. Vendor quirks (manual checklist)
- **Cloudflare R2** — no bucket versioning, no replication;
  `region = "auto"`; uses HTTPS at
  `https://<account>.r2.cloudflarestorage.com`.
- **Backblaze B2** — smaller multipart limits than AWS (5 MiB min
  part vs. 5 GiB max); long-lived buckets need keys with `b2-` prefix.
- **MinIO** — uses path-style URLs (`force_path_style = true`);
  self-signed TLS dev setups need `verify_tls = false` (will emit a
  warning toast at launch).
- **Wasabi** — mostly behaves like AWS; region matters (us-east-1 vs.
  us-east-2 buckets).
- **Ceph RGW / SeaweedFS** — typically path-style + custom region.

## 1.6. Recommended: 1-day MPU abort lifecycle rule
Set a 1-day lifecycle rule to abort incomplete multipart uploads on
every bucket you write to from aws-tui (or any other tool). aws-tui
aborts user-cancelled transfers when the provider exposes the abort
path, but startup resume/abort and explicit MPU-id journaling remain
deferred in v0.8.x. A network drop or app crash can therefore leave
orphans that accrue charges until the bucket lifecycle rule catches
them.

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

## 1.7. First-run flow
If `ConfigStore.load()` returns no `[connections.*]` and
`~/.aws/{config,credentials}` is also empty, v0.8.x opens the main
screen with a local-only placeholder. The welcome modal below exists
in the UI surface and remains the planned v0.9 startup flow:

```
welcome to aws-tui
no AWS or S3-compatible connections configured.
  add aws profile  (runs 'aws configure sso' in your terminal)
  add s3-compatible (in-TUI form)
  skip for now
```

Until that startup wiring lands, use `aws configure sso` /
`aws sso login` for AWS profiles, or open Settings with `,` to add an
S3-compatible endpoint.

## 1.8. Crash-recovery transfer journal
aws-tui writes a JSONL journal under
`<cache-dir>/transfers/<id>.jsonl` for each transfer, including
`begin` and terminal `finished` / `aborted` records. Startup scanning,
automatic resume, and the abort / decide-each / keep modal remain
deferred in v0.8.x; see the [cookbook](cookbook.md#14-resume-after-a-crash)
for the planned flow and manual cleanup notes.
