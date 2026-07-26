# 1. Cookbook

> Common recipes for daily aws-tui use. Each recipe is end-to-end —
> commands you can copy/paste plus the in-app key sequence.

1. [Connect to and switch between data sources](#11-connect-to-and-switch-between-data-sources)
2. [Switch the theme on the fly](#12-switch-the-theme-on-the-fly)
3. [Customize a keybinding](#13-customize-a-keybinding)
4. [Resume after a crash](#14-resume-after-a-crash)
5. [Run standalone Athena queries safely](#16-run-standalone-athena-queries-safely)

---

## 1.1. Connect to and switch between data sources

Walks through three setups people hit on day one:

- **§1.1–§1.5** — connect to a local MinIO from scratch (the
  canonical "first s3-compatible endpoint" walkthrough).
- **§1.6** — jump between AWS profiles with one keystroke
  (multi-account flows).
- **§1.7** — run several `s3-compatible` endpoints side-by-side.

You have a MinIO running on `http://localhost:9000` with the dev
credentials `minioadmin / minioadmin`. Goal: a `minio-local`
connection in aws-tui that points at it.

### 1.1.1. Start MinIO (skip if already running)

**Quickest path — dev seeded MinIO** (recommended for first-time
exploration; ships ~5 buckets and ~90 objects so you have content to
navigate):

```bash
scripts/test-services/s3/up.sh
```

This wraps `docker compose` + `seed.py` and prints the config snippet
to add to `<config-dir>/config.toml`. Teardown is
`scripts/test-services/s3/down.sh` (add `--purge` to wipe the data
volume). See `scripts/test-services/README.md` for the seeded
dataset and how to extend it.

**Plain MinIO** (no seed):

```bash
docker run --rm -d --name minio \
    -p 127.0.0.1:9000:9000 -p 127.0.0.1:9001:9001 \
    -e MINIO_ROOT_USER=minioadmin \
    -e MINIO_ROOT_PASSWORD=minioadmin \
    minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e server /data --console-address ":9001"
```

### 1.1.2. Store the credentials in the macOS Keychain (recommended)

The resolver expects two required keychain entries under ONE service name
(matching the `credentials = "keychain:<service>"` value in
`config.toml`): one account named `access_key_id` and one named
`secret_access_key`. Temporary credentials may also provide an
optional `session_token` account. So for a `keychain:minio-local`
config entry:

```bash
# service="minio-local", account="access_key_id"
security add-generic-password \
    -s minio-local -a access_key_id -w minioadmin

# service="minio-local", account="secret_access_key"
security add-generic-password \
    -s minio-local -a secret_access_key -w minioadmin

# optional, only for temporary credentials
security add-generic-password \
    -s minio-local -a session_token -w '<session-token>'
```

(The Python `keyring` library aws-tui uses delegates to the macOS
Keychain by default.)

### 1.1.3. Add via the in-TUI Settings form
Open Settings with `,`, add an S3-compatible connection, and fill the
form:

| Field | Value |
|---|---|
| Name | `minio-local` |
| Endpoint URL | `http://localhost:9000` |
| Region | `us-east-1` |
| Access key ID | `minioadmin` |
| Secret access key | `minioadmin` |
| Session token | Optional; only for temporary credentials |

That writes a `static` entry to `config.toml`. Note: every launch with
a `static`-credentials connection emits a warning toast, per the
credential-source preference order documented in
[connections.md §1.2](connections.md#12-credential-sources-for-s3-compatible-connections);
the recommended path is to migrate to a `keychain:` source once
you've verified the connection works. To do that, edit your config
file (see [docs/platforms.md](platforms.md) for the path on each OS)
and change:

```toml
[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
credentials = "keychain:minio-local"
force_path_style = true
verify_tls = false              # http:// MinIO -> no cert to verify
```

### 1.1.4. Add by editing the file directly
If you already have other connections, just append:

```toml
[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
credentials = "static"          # tells the resolver to use inline keys below
access_key_id = "minioadmin"
secret_access_key = "minioadmin"
force_path_style = true
verify_tls = false              # http:// MinIO → no cert to verify
```

For **multiple** S3-compatible services, just add more
`[connections.<name>]` blocks — the `<name>` (e.g. `minio-local`,
`r2-prod`, `b2-archive`) becomes the source identifier shown in
the pane's bottom border (`s3-compatible · minio-local · localhost:9000`).
**Region is optional and intentionally not displayed** for
`s3-compatible` connections — MinIO/R2/B2/etc. don't have a
meaningful region, so the pane title shows `name · endpoint`
instead.

### 1.1.5. Use it
```bash
aws-tui
```

Then in-app, press `Shift+S` on the focused pane to **cycle
through every available source** — `local` → every TOML /
auto-discovered connection → wrap. AWS profiles auto-discovered
from `~/.aws/credentials` show up as
`aws s3 · {profile} · {region}`; TOML `s3-compatible` entries
show up as `s3-compatible · {name} · {endpoint}`. Tap
`Shift+S` until the pane title reads
`s3-compatible · minio-local · {endpoint}` — the bucket list
should populate immediately.

> The dedicated command-palette path (`: connection switch ▸ minio-local`)
> is spec'd but deferred to v0.9 — in v0.8.x ``:`` opens the
> help overlay as a placeholder. ``Shift+S`` is the one-keystroke
> equivalent today.

---

### 1.1.6. Jump between AWS profiles with one keystroke

If you have several `[profile *]` blocks in `~/.aws/config` (typical
for orgs with multiple AWS accounts or SSO permission sets), `Shift+S`
is the fastest way to flip between them. Each press re-mounts the
focused pane on the next profile in the cycle; the pane's bottom
border subtitle (`aws s3 · {profile} · {region}`) tells you which
identity you're on.

```text
~/.aws/config:
  [profile dev]
  region = us-east-1
  sso_session = my-org

  [profile staging]
  region = us-east-1
  sso_session = my-org

  [profile prod]
  region = us-west-2
  sso_session = my-org
```

In-app:

- Shift+S → left pane re-mounts on `aws s3 · dev · us-east-1`.
- Shift+S → `aws s3 · staging · us-east-1`.
- Shift+S → `aws s3 · prod · us-west-2`.

Per-pane independence: put `dev` on the left and `prod` on the right,
then `c` to copy an object between them — `CrossFsCopy` streams S3→S3
without an intermediate local hop.

The cycle also includes any `s3-compatible` entries from
`config.toml` and the local filesystem. Add MinIO / R2 / B2 / Wasabi
connections via the in-app **Settings** nav page (`,`) — they join
the cycle immediately, no relaunch.

> Expired SSO tokens are detected offline at launch via the SSO
> cache freshness probe (see
> [connections.md §3](connections.md#13-auto-discovery-sso-cache-probe));
> expired or missing SSO profiles are skipped by the boot chain,
> marked unreachable for the session, and surfaced through a recovery
> toast while the app mounts local panes instead of hanging. Run
> `aws sso login --profile <name>` and relaunch, or use the explicit
> retry path when prompted.

---

### 1.1.7. Run several s3-compatible endpoints side-by-side

There's no fixed limit on how many `s3-compatible` connections you
can configure. Each one shows up in the swap-source cycle and in
the in-app Settings page. Example config covering a local MinIO, a
Cloudflare R2 production bucket, and a Backblaze B2 archive:

```toml
# <config-dir>/config.toml

[connections.minio-local]
kind = "s3-compatible"
credentials = "static"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
access_key_id = "minioadmin"
secret_access_key = "minioadmin"
force_path_style = true
verify_tls = false

[connections.r2-prod]
kind = "s3-compatible"
credentials = "static"
endpoint_url = "https://<account>.r2.cloudflarestorage.com"
region = "auto"
access_key_id = "<r2-key-id>"
secret_access_key = "<r2-secret>"
force_path_style = false

[connections.b2-archive]
kind = "s3-compatible"
credentials = "static"
endpoint_url = "https://s3.us-west-002.backblazeb2.com"
region = "us-west-002"
access_key_id = "<b2-key-id>"
secret_access_key = "<b2-secret>"
force_path_style = false
```

Then `Shift+S` cycles through all three (plus your AWS profiles and
local) on the focused pane. If you'd rather edit interactively, open
**Settings** (`,`) → "S3-Compatible Connections" section → "+ Add"
to enter the same data through the inline form, or use the per-row
Edit / Delete chips to manage entries already there. Saves are
atomic (`tempfile` + `os.replace`) so the config can't end up
half-written.

See [`docs/connections.md` §4](connections.md#14-switching-between-connections-at-runtime)
for the full source-cycle semantics and the unreachable-skip behavior.

---

## 1.2. Switch the theme on the fly
### 1.2.1. One-off (session-only)
Two paths, both fire `ThemeChangedMessage` and reload the active
stylesheet instantly without a restart:

- Press `t` to open the theme picker modal, arrow to the theme you
  want, hit Enter.
- Press `Shift+T` (`T`) to cycle straight to the next theme without
  the modal — handy when you just want to flip carbon ↔ voidline.

> The command-palette path (`:` then `theme switch ▸ voidline`) is
> spec'd in the design but not wired in v0.8.x — the palette
> registers no entries yet, so `t` / `Shift+T` are the working
> shortcuts.

### 1.2.2. Persistent
```toml
# <config-dir>/config.toml
[defaults]
theme = "voidline"
```

Theme names: `carbon` (default), `voidline`, `lattice`, `amber`,
`solarized-light`, `github-light`, `one-light`, `nord`, `dracula`,
`gruvbox-dark`. See [theming.md §1](theming.md#11-built-in-themes) for
the full per-theme palette breakdown.

### 1.2.3. Add a custom theme
Copy `src/aws_tui/ui/themes/carbon.tcss` to
`<config-dir>/themes/midnight.tcss`, edit the palette tokens,
and pick it from the theme picker (`t`) like any built-in. See
[theming.md](theming.md#132-full-custom-themes) for the full token table.

### 1.2.4. Tweak just one or two colors
Drop `<config-dir>/theme.tcss` and override what you need; the
overlay layers on top of the active built-in:

```tcss
/* <config-dir>/theme.tcss */
.modal-title { color: #ff3df8; }
Footer { background: #050505; }
```

---

## 1.3. Customize a keybinding

> **Runtime status:** The composition root installs handled overrides
> on the live Textual keymap through `BindingResolver`. It validates
> action ids through `KeymapStore` and logs/falls back to defaults when
> an overlay is invalid. Handlerless deferred actions remain unbound.

Rebind copy (`pane.copy`) from `c` to `y` (vim yank):

```toml
# <config-dir>/config.toml
[keybindings]
"pane.copy" = "y"
```

For a fallback list (try `Ctrl+K` first, fall back to `:`):

```toml
[keybindings]
"app.command_palette" = ["Ctrl+K", ":"]
```

### 1.3.1. Disable a default binding
Set the action to an empty list:

```toml
[keybindings]
"pane.delete" = []   # nope, no quick delete
```

On the next launch, an empty `[keybindings]` value removes the live
keybinding until you edit the config back.

### 1.3.2. See the active map
The full list of action IDs lives in
[`docs/keybindings.md`](keybindings.md#13-action-ids) and is the same set
declared in `src/aws_tui/infra/keymap_store.py:DEFAULT_BINDINGS`. There
is no `--print-bindings` CLI flag in v0.8; the launch path enters the
TUI directly.

### 1.3.3. Unknown action IDs fall back to defaults
If you overlay an action id that isn't in `KeymapStore.DEFAULT_BINDINGS`
(e.g. typo `pane.cpy`), startup logs the `UnknownAction` and falls back
to the default keymap. That's deliberate: a bad override should not make
the TUI unlaunchable, and the log still gives maintainers the exact
action id to fix.

---

## 1.4. Resume after a crash
Long-running transfers leave local journals so interrupted work can be
inspected or cleaned up. Full startup resume and explicit S3 multipart
replay remain deferred in v0.8.x; this recipe documents the current
journal shape plus the planned modal flow.

### 1.4.1. What gets saved
The production transfer path writes a `begin` line and a terminal
`finished` or `aborted` line to `<cache-dir>/transfers/<id>.jsonl`:

```jsonl
{"kind":"begin","transfer_id":"abc123","source_uri":"local:///x.bin","destination_uri":"s3://bucket/x.bin","bytes_total":104857600,"upload_id":null,"ts":"2026-06-13T23:45:11Z"}
{"kind":"finished","ts":"2026-06-13T23:45:18Z"}
```

The journal schema can also replay optional `part` lines and an
`upload_id` for future explicit-MPU flows, but the current S3 transfer
path delegates multipart internals to boto and does not record those
values.

### 1.4.2. What happens on next launch
v0.8.x writes durable transfer journals, but startup scanning and the
resume modal are not wired yet. The planned modal flow will scan
`TransferJournal.find_unfinished()` after the connection resolves and
surface entries that lack a terminal record:

```
2 transfers from a previous session were not finished.
  - api-2026-06-13.json  (3.4 M / 4.2 M, 82%)
  - db-slowq-06-13.csv   (279 k / 892 k, 31%)
  [abort all] [decide each] [keep for later]
```

| Choice | What it does |
|---|---|
| **abort all** | Planned: mark journals `aborted`, purge them, and call `AbortMultipartUpload` only for entries that carry an `upload_id`. The current production transfer path does not record S3 MPU IDs yet, so bucket lifecycle cleanup remains the server-side backstop. |
| **decide each** | Deferred in v0.8.x: equivalent to **keep for later** until the per-entry modal lands. |
| **keep for later** | Planned: no mutation; once startup scanning is wired, the modal will show again on next launch. |

### 1.4.3. Manual cleanup
If you want to nuke the journals without going through the modal:

```bash
rm -f "<cache-dir>"/transfers/*.jsonl
```

For S3 uploads that were interrupted outside aws-tui's normal cancel
path, the [1-day MPU abort lifecycle rule](connections.md#16-recommended-1-day-mpu-abort-lifecycle-rule)
is the server-side backstop.

### 1.4.4. What gets dumped on a crash
If aws-tui hits an unhandled exception, it writes
`<cache-dir>/crash/<ts>.txt`:

```
aws-tui crash dump
timestamp: 2026-06-14T12:00:00+00:00
exception: TypeError: unsupported operand type(s) for +: ...

== traceback ==
  ...

== log tail ==
... (last 1000 lines of aws-tui.log)
```

The crash dump writer is live in v0.8.x. The interactive crash modal
below exists as UI scaffolding but is not wired into the unhandled
exception path yet:

```
unexpected error
  TypeError: ...

  <cache-dir>/crash/2026-06-14T12-00-00.txt

  [view trace]  [continue]  [quit]
```

The planned `continue` button is enabled only when the last user action was
**read-only** (navigation, refresh, filter, palette open). Writes
(delete, copy, move, rename) disable it — you can't safely continue a
write that may have partially executed.

## 1.5. Browse AWS Glue safely

Glue is an AWS-only, read-only service in aws-tui. Select **Glue** in
the nav rail, then use:

- `1` for Catalog databases, tables, schema/storage detail,
  partitions, and column statistics;
- `2` for job definitions and recent runs;
- `3` for crawler status, configuration, metrics, and latest crawl;
- `r` to refresh the active view;
- `Shift+S` to rebuild Glue under the next configured AWS connection.

The source header shows the configured connection name, distinct
profile name when present, and region. Switching profiles clears the
old page before the replacement VM loads, and remembered selections
are isolated by connection name and region.

### 1.5.1. Least-privilege Glue permissions

Grant only the read operations needed by the views you use. A complete
policy for the shipped Glue page may include:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "glue:GetDatabases",
        "glue:GetTables",
        "glue:GetTable",
        "glue:GetPartitions",
        "glue:GetColumnStatisticsForTable",
        "glue:GetJobs",
        "glue:GetJobRuns",
        "glue:GetCrawlers",
        "glue:GetCrawler",
        "glue:GetCrawlerMetrics",
        "glue:GetTags"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
```

AWS IAM and Lake Formation policies remain authoritative. An
`AccessDenied` response is shown only in the affected Glue pane; it
does not mark the connection unreachable or remove that profile from
other services.

### 1.5.2. Open a table location in S3

1. In Catalog, select the database and table.
2. Open the command palette with `:` or `Ctrl+K`.
3. Choose **Open table location in S3**.

aws-tui validates that the selected table has an `s3://` location,
resolves the exact Glue connection name, verifies its region still
matches, rebuilds S3 through the normal service factory, and navigates
the left pane to `/<bucket>/<prefix>`. Missing/malformed locations,
removed connections, and region mismatches produce an advisory and do
not navigate. The advisory and logs omit the full URI.

Browsing the destination normally needs `s3:ListAllMyBuckets` for the
initial S3 root load and `s3:ListBucket` for the target bucket/prefix;
reading an object also needs `s3:GetObject`.

### 1.5.3. Exercise Glue in demo mode

Launch `aws-tui --demo`. `demo-dev` and `demo-prod` expose disjoint
catalog, job/run, and crawler names, so `Shift+S` visibly proves
profile isolation. `demo-shared` demonstrates a Glue access-denied
state. The Catalog-to-S3 command uses the matching synthetic profile
and never makes a real AWS call.

## 1.6. Run standalone Athena queries safely

Athena is an AWS-only, standalone query service. Select **Athena** in the
nav rail and choose a workgroup, catalog, and database in the page header.
The four views are Query (`1`), History (`2`), Results (`3`), and Saved (`4`).
`Shift+S` rebuilds the whole Athena page for the next supported AWS connection;
the old page is disposed, so rows, selections, loaders, result fetches, and any
app-owned active query do not cross profiles or regions. Selections may be
remembered only within the same connection name and region and are revalidated
when the page returns.

### 1.6.1. Minimum Athena and data permissions

Start with the least privilege required for the views in use. The
[AWS Service Authorization Reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_athena.html)
defines each Athena action and its resource types. This is the minimum Athena
API policy used by aws-tui; scope workgroup and data-catalog resources where
the action supports resource-level permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "athena:ListWorkGroups",
      "athena:GetWorkGroup",
      "athena:ListDataCatalogs",
      "athena:ListDatabases",
      "athena:ListTableMetadata",
      "athena:ListQueryExecutions",
      "athena:GetQueryExecution",
      "athena:GetQueryRuntimeStatistics",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryResults",
      "athena:ListNamedQueries",
      "athena:BatchGetNamedQuery",
      "athena:ListPreparedStatements",
      "athena:GetPreparedStatement"
    ],
    "Resource": "*"
  }]
}
```

The query principal also needs access to catalog metadata and source data.
For ordinary IAM-controlled S3 tables, grant `s3:ListBucket` on the source
bucket/prefix and `s3:GetObject` on every underlying source-data object.
Cross-account source buckets also need a permitting bucket policy. AWS
documents this pass-through model in
[Control access to Amazon S3 from Athena](https://docs.aws.amazon.com/athena/latest/ug/s3-permissions.html).

For Lake Formation-governed data, grant the required Lake Formation
`DESCRIBE` and `SELECT` permissions and IAM
`lakeformation:GetDataAccess`. Lake Formation uses that IAM action to vend
temporary credentials to Athena. `DATA_LOCATION_ACCESS` permits creating or
altering Data Catalog resources that point at a registered location; it is not
a query permission and is not required merely to read an existing table. See
[Manage Lake Formation and Athena user permissions](https://docs.aws.amazon.com/athena/latest/ug/lf-athena-user-permissions.html)
and
[Underlying data access control](https://docs.aws.amazon.com/lake-formation/latest/dg/access-control-underlying-data.html).

Source data encrypted with a customer managed KMS key requires `kms:Decrypt`
for the source key in IAM and in the key policy. For customer-managed S3
results, require `kms:GenerateDataKey` and `kms:Decrypt` for the result key. An
encrypted Glue Data Catalog additionally requires `kms:GenerateDataKey`,
`kms:Decrypt`, and `kms:Encrypt`. AWS lists these separate source, result, and
catalog requirements in
[Encryption at rest](https://docs.aws.amazon.com/athena/latest/ug/encryption.html).

### 1.6.2. Customer S3 output versus managed results

aws-tui sends the selected workgroup and query execution context; it does not
supply a caller-side `ResultConfiguration`. The workgroup must therefore use
one of two distinct output modes:

- **Customer S3 output.** A workgroup-enforced customer S3 output location is
  authoritative when `EnforceWorkGroupConfiguration` is enabled. Grant
  `s3:GetBucketLocation`, `s3:ListBucket`, and
  `s3:ListBucketMultipartUploads` on the result bucket, plus `s3:PutObject`,
  `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts`, and
  `s3:GetObject` on the result prefix. Athena uses multipart uploads for query
  results, including partial failed or cancelled output. `s3:GetObject` is
  also required to retrieve output and to browse the artifact through S3.
  See
  [Work with query results and recent queries](https://docs.aws.amazon.com/athena/latest/ug/querying.html)
  and the AWS
  [S3 API permission mapping](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html).
- **Athena managed results.** Managed results do not create a customer S3
  result artifact. They remain available through Athena for 24 hours and
  managed results do not support result reuse. aws-tui continues to page rows
  with `GetQueryResults`; no S3 output location is required. If the workgroup
  uses a customer managed KMS key, both the query principal and the managed
  results key policy need the documented KMS access, including
  `kms:Decrypt`, `kms:GenerateDataKey`, and `kms:DescribeKey`. See
  [Managed query results](https://docs.aws.amazon.com/athena/latest/ug/managed-results.html).

The Query view labels the workgroup mode as managed results or S3 output. If
neither mode is configured, aws-tui shows the typed result-configuration error
instead of choosing a bucket. With managed results, **Open Athena result in
S3** remains on Athena and shows an advisory because there is no customer S3
artifact. Workgroup-enforced customer S3 and managed output are never treated
as interchangeable.

### 1.6.3. Exact read-only SQL grammar

Press `Ctrl+Enter` only after setting workgroup, catalog, and database. The
local `sqlglot` Athena-dialect parser fails closed and permits one statement
from this implemented grammar:

- SELECT roots and set operations (including `VALUES` operands) using `UNION`,
  `INTERSECT`, or `EXCEPT`, including read-only common table expressions and
  subqueries. A standalone `VALUES` statement is not an accepted root.
- `SHOW DATABASES`, `SHOW SCHEMAS`, `SHOW TABLES`, `SHOW COLUMNS`,
  `SHOW PARTITIONS`, `SHOW TBLPROPERTIES`, and `SHOW VIEWS`, with only the
  scopes, patterns, and property selectors covered by the policy tests.
- `DESCRIBE [EXTENDED|FORMATTED]` for a one- or two-part table name, with an
  optional literal-equality `PARTITION` selector and bounded column/complex
  field selectors.
- non-`ANALYZE` `EXPLAIN` of another allowed statement, with the implemented
  `TYPE` and `FORMAT` options.

It explicitly rejects `SHOW CREATE TABLE`, `SHOW CREATE VIEW`,
`EXPLAIN ANALYZE`, empty or unparsable input, multiple statements, DDL, DML,
CTAS, `UNLOAD`, procedure calls, and unknown or unbounded forms before any
`start_query_execution` call. The editor retains the rejection as validation
feedback and does not dispatch the SQL. IAM, Lake Formation, workgroup, S3,
bucket, and KMS policies remain the authorization boundary.

### 1.6.4. Execution, History, Results, and result artifacts

After submission, Query records an app-owned execution identity and polls its
detail through queued, running, and terminal states. On `SUCCEEDED`, it loads
the first Results page; `FAILED` and `CANCELLED` retain terminal detail. `Esc`
can call `stop_query_execution` only for a still-active query started by this
page. Switching context or source requests cancellation and awaits Athena
shutdown before disposal; History rows are never assumed safe to stop.

Results are fetched one page at a time and never fully materialized. `l` loads
the next page only when Athena returns a continuation token; each domain
request asks for at most 1,000 rows. Query execution detail reports bytes
scanned. History hydrated detail reports bytes scanned and whether Athena
reused a previous result. Results is the authoritative paged row surface, not
the cost-statistics surface.

History lists execution IDs for the selected workgroup and hydrates the
selected execution's detail. Choose a successful execution and open Results to
fetch its rows. The Saved view separately pages named queries and prepared
statements; selecting a prepared statement calls `GetPreparedStatement` for
its SQL. **Open in query editor** copies SQL without executing or bypassing the
read-only policy.

For customer S3 output, choose **Open Athena result in S3** from the command
palette. The Results VM does not trust History hydrated detail for navigation:
it performs an authoritative reload with `GetQueryExecution`, requires a
succeeded execution, exact execution ID, active connection, region and query
context, and a valid `s3://` output location. It then rebuilds S3 under the
exact connection and region, reveals that result artifact, and selects it.
Missing, managed, malformed, non-S3, foreign-context, or unsucceeded output
leaves the user on Athena with a redacted advisory; no fallback profile is
substituted.

Treat bytes scanned as the cost signal before broad queries. aws-tui does not
calculate a currency price, and a workgroup bytes-scanned cutoff can reject or
limit work.

### 1.6.5. Exercise Athena in demo mode and troubleshoot

Launch `aws-tui --demo`, select **Athena**, and begin on `demo-dev`. Its
`dev-analytics` workgroup has succeeded, running, failed, empty, missing-output,
and result-access-denied history scenarios, plus one named query and one prepared
statement. The successful `q-dev-succeeded` artifact opens at
`s3://athena-results/dev/q-dev-succeeded.csv`. `demo-prod` has different
workgroup, catalog, database, history, saved SQL, and a result artifact under
`s3://athena-results/prod/`; `demo-shared` returns a scoped Athena access-denied
state. All of this is in-memory and resets on launch.

- **Athena access is forbidden.** Verify the selected AWS profile, the Athena
  actions above, and the Lake Formation grants. The failure stays scoped to
  Athena and does not remove the profile from the source ring.
- **Result configuration is required.** Set an output location or managed
  query-results configuration on the selected workgroup. aws-tui will not
  silently send results to another bucket.
- **No rows or cannot load more.** A successful query can return no rows; `l`
  only works when Athena supplied another page token. Check the query state and
  execution detail in History.
- **Cannot open an artifact in S3.** The execution must be successful and its
  output URI must be valid, match the active connection/region, and be readable
  through S3. Check `s3:ListBucket` / `s3:GetObject` on the result prefix.

The standalone page intentionally does not expose Iceberg metadata tables or
Glue-to-Athena navigation. Do not treat those as available workflows.
