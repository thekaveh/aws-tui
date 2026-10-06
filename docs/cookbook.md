# Cookbook

> Common recipes for daily aws-tui use. Each recipe is end-to-end —
> commands you can copy/paste plus the in-app key sequence.

1. [Connect to and switch between data sources](#1-connect-to-and-switch-between-data-sources)
2. [Switch the theme on the fly](#2-switch-the-theme-on-the-fly)
3. [Customize a keybinding](#3-customize-a-keybinding)
4. [Inspect transfer history and recovery after a restart](#4-inspect-transfer-history-and-recovery-after-a-restart)
5. [Browse AWS Glue safely](#5-browse-aws-glue-safely)
6. [Run Athena queries safely](#6-run-athena-queries-safely)
7. [Inspect and query Glue tables through Athena](#7-inspect-and-query-glue-tables-through-athena)
8. [Diagnose local setup and source access](#8-diagnose-local-setup-and-source-access)

---

## 1. Connect to and switch between data sources

Walks through three setups people hit on day one:

- **§1.1–§1.5** — connect to local Adobe S3Mock from scratch (the
  canonical "first s3-compatible endpoint" walkthrough).
- **§1.6** — jump between AWS profiles with one keystroke
  (multi-account flows).
- **§1.7** — run several `s3-compatible` endpoints side-by-side.

You have S3Mock running on `http://localhost:9000` with arbitrary test
credentials `test / test`. The shipped harness creates the canonical
`dev-s3` connection; the manual examples below use `s3mock-local`
to show that connection names are user-defined.

### 1.1. Start S3Mock (skip if already running)

**Quickest path — dev seeded S3Mock** (recommended for first-time
exploration; ships ~5 buckets and ~90 objects so you have content to
navigate):

```bash
scripts/test-services/s3/up.sh
```

This wraps `docker compose` + `seed.py` and prints the path to
`scripts/test-services/s3/config-snippet.toml`; add that snippet to
`<config-dir>/config.toml` to create `dev-s3`. Teardown is
`scripts/test-services/s3/down.sh` (add `--purge` to wipe the data
volume). See `scripts/test-services/README.md` for the seeded
dataset and how to extend it.

**Plain S3Mock** (no seed):

```bash
docker run --rm -d --name s3mock \
    -p 127.0.0.1:9000:9090 \
    adobe/s3mock:5.2.2@sha256:e7c36014dcf4c7f0f6bec9de888477d1d9b8f55eceb7d9c1186f70d7aadf81ca
```

### 1.2. Store the credentials in the macOS Keychain (recommended)

The resolver expects two required keychain entries under ONE service name
(matching the `credentials = "keychain:<service>"` value in
`config.toml`): one account named `access_key_id` and one named
`secret_access_key`. Temporary credentials may also provide an
optional `session_token` account. So for a `keychain:s3mock-local`
config entry:

```bash
# service="s3mock-local", account="access_key_id"
security add-generic-password \
    -s s3mock-local -a access_key_id -w test

# service="s3mock-local", account="secret_access_key"
security add-generic-password \
    -s s3mock-local -a secret_access_key -w test

# optional, only for temporary credentials
security add-generic-password \
    -s s3mock-local -a session_token -w '<session-token>'
```

(The Python `keyring` library aws-tui uses delegates to the macOS
Keychain by default.)

### 1.3. Add via the in-TUI Settings form
Open Settings with `,`, add an S3-compatible connection, and fill the
form:

| Field | Value |
|---|---|
| Name | `s3mock-local` |
| Endpoint URL | `http://localhost:9000` |
| Region | `us-east-1` |
| Access key ID | `test` |
| Secret access key | `test` |
| Session token | Optional; only for temporary credentials |

The form stores the secret fields in the OS keychain through `keyring` and
persists only a `keychain:` reference in `config.toml`. New saves use the
URL-escaped `aws-tui:connections/<url-escaped-name>` namespace; later edits
alternate between the `aws-tui:connection-revisions/<url-escaped-name>/0` and
`/1` services. The resulting entry is equivalent to the following shape:

```toml
[connections.s3mock-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
credentials = "keychain:aws-tui:connections/s3mock-local"
force_path_style = true
verify_tls = false              # http:// S3Mock -> no cert to verify
```

### 1.4. Add by editing the file directly
If you already have other connections, just append:

```toml
[connections.s3mock-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
credentials = "static"          # tells the resolver to use inline keys below
access_key_id = "test"
secret_access_key = "test"
force_path_style = true
verify_tls = false              # http:// S3Mock -> no cert to verify
```

For **multiple** S3-compatible services, just add more
`[connections.<name>]` blocks — the `<name>` (e.g. `s3mock-local`,
`r2-prod`, `b2-archive`) becomes the source identifier shown in
the pane's bottom border (`s3-compatible · s3mock-local · localhost:9000`).
**Region is optional and intentionally not displayed** for
`s3-compatible` connections — MinIO/R2/B2/etc. don't have a
meaningful region, so the pane title shows `name · endpoint`
instead.

### 1.5. Use it
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
`s3-compatible · s3mock-local · {endpoint}` — the bucket list
should populate immediately.

> `:` opens the command palette. Its `Switch source` command invokes
> `app.swap_source` and cycles resolver order; `Shift+S` is the one-keystroke
> equivalent. Neither path selects one exact source.

To discover and select an exact source:

1. Press `?` to open Help. Global and active-service sections show available
   commands with their configured shortcuts; `Unbound` means no shortcut is
   configured. Press `Esc` to close Help.
2. Open the palette with `:` or `Ctrl+K`, type **Go to Glue** (or **Go to Athena**,
   **Go to EMR Serverless**, **Go to S3**, or **Settings**), and press `Enter`.
   Service choices follow the active connection's supported services; an
   S3-compatible connection offers S3 and Settings.
3. Reopen the palette and type a configured connection name or region. Select
   **Use &lt;name&gt; · &lt;region&gt; for &lt;service&gt;** with the arrow keys, then press
   `Enter`. For example, **Use prod · us-west-2 for Glue** selects that exact
   configured source. On S3, focus the pane to change before opening the palette;
   Athena, Glue and EMR Serverless change the whole page's source.

Names and regions display literally. Unavailable commands are omitted, and
Settings has no source choices. Opening the palette reads local configuration;
it performs no remote resource search or extra region enumeration.

---

### 1.6. Jump between AWS profiles with one keystroke

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
> [connections.md §3](connections.md#3-auto-discovery-and-sso-cache-probe));
> expired or missing SSO profiles are skipped by the boot chain,
> marked unreachable for the session, and surfaced through a recovery
> toast while the app mounts local panes instead of hanging. Run
> `aws sso login --profile <name>` externally, then press `a` or choose
> **Retry active source credentials** from the command palette.

---

### 1.7. Run several s3-compatible endpoints side-by-side

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

See [`docs/connections.md` §4](connections.md#4-switching-between-connections-at-runtime)
for the full source-cycle semantics and the unreachable-skip behavior.

---

## 2. Switch the theme on the fly
### 2.1. One-off (session-only)
Two paths, both fire `ThemeChangedMessage` and reload the active
stylesheet instantly without a restart:

- Press `t` to open the theme picker modal, arrow to the theme you
  want, hit Enter.
- Press `Shift+T` (`T`) to cycle straight to the next theme without
  the modal — handy when you just want to flip carbon ↔ voidline.

The command palette has two working global theme commands: `:` then
**Theme picker** opens the same picker as `t`, and `:` then **Cycle theme**
has the same effect as `Shift+T`. Per-theme dynamic entries such as
`theme switch ▸ voidline` remain deferred and are not registered, so use
**Theme picker** to select a specific built-in or custom theme.

### 2.2. Persistent
```toml
# <config-dir>/config.toml
[defaults]
theme = "voidline"
```

Theme names: `carbon` (default), `voidline`, `lattice`, `amber`,
`solarized-light`, `github-light`, `one-light`, `nord`, `dracula`,
`gruvbox-dark`. See [theming.md §1](theming.md#1-built-in-themes) for
the full per-theme palette breakdown.

### 2.3. Add a custom theme
A full replacement bypasses the built-in composition, so a repository checkout
must combine the raw built-in theme, then the shared operational layer, before
installing a custom file:

```bash
# Substitute your platform's config directory (see docs/platforms.md).
THEME_DIR="$HOME/.config/aws-tui/themes"
mkdir -p "$THEME_DIR"
cat src/aws_tui/ui/themes/carbon.tcss \
    src/aws_tui/ui/themes/operational-panes.tcss \
    > "$THEME_DIR/midnight.tcss"
```

Edit `midnight.tcss`, then select it with `t` or `:` then **Theme picker**.
Including `operational-panes.tcss` retains the Glue and Athena borders and
focus styling that built-in themes receive automatically. See
[theming.md](theming.md#32-full-custom-themes) for the full token table.

### 2.4. Tweak just one or two colors
Drop `<config-dir>/theme.tcss` and override what you need; the
overlay layers on top of the active built-in:

```tcss
/* <config-dir>/theme.tcss */
.modal-title { color: #ff3df8; }
Footer { background: #050505; }
```

---

## 3. Customize a keybinding

> **Runtime status:** The composition root installs handled overrides
> on the live Textual keymap through `BindingResolver`. It validates
> action ids through `KeymapStore` and logs/falls back to defaults when
> an overlay is invalid. Handlerless deferred actions remain unbound.

Rebind copy (`pane.copy`) from `c` to `Ctrl+Y`:

```toml
# <config-dir>/config.toml
[keybindings]
"pane.copy" = "ctrl+y"
```

Bare `y` is reserved by `glue.copy_table_ref` for copying a selected Glue
table reference.

For a fallback list (try `Ctrl+K` first, fall back to `:`):

```toml
[keybindings]
"app.command_palette" = ["Ctrl+K", ":"]
```

### 3.1. Disable a default binding
Set the action to an empty list:

```toml
[keybindings]
"pane.delete" = []   # nope, no quick delete
```

On the next launch, an empty `[keybindings]` value removes the live
keybinding until you edit the config back.

### 3.2. See the active map
The full list of action IDs lives in
[`docs/keybindings.md`](keybindings.md#3-action-ids) and is the same set
declared in `src/aws_tui/infra/keymap_store.py:DEFAULT_BINDINGS`. There
is no `--print-bindings` CLI flag in v0.8; the launch path enters the
TUI directly.

### 3.3. Unknown action IDs fall back to defaults
If you overlay an action id that isn't in `KeymapStore.DEFAULT_BINDINGS`
(e.g. typo `pane.cpy`), startup logs the `UnknownAction` and falls back
to the default keymap. That's deliberate: a bad override should not make
the TUI unlaunchable, and the log still gives maintainers the exact
action id to fix.

---

## 4. Inspect transfer history and recovery after a restart
Open **Transfer history and recovery** from the command palette or press
`Ctrl+T` (`app.transfer_history`). The Transfers overlay also has **History**
and **Recovery** controls, which remain available after live progress rows
expire. `Ctrl+T` works with an empty overlay too. Startup scans current-schema
metadata in a background worker; it never starts a transfer automatically.

### 4.1. What gets saved
Each transfer has a private durable summary containing its operation, original
connection names and fingerprints, literal source and effective destination,
UTC timestamps, observed bytes, outcome, publication certainty, and a fixed
failure category. Zero bytes and an unknown total remain distinct. Credentials,
raw provider errors, client objects, multipart IDs, and raw endpoint settings
are excluded. Begin and attempted-publication metadata are saved before
provider mutation; terminal summaries are saved before the worker settles.
A history-save warning after a successful copy does not undo that copy.

The default retention is the **100 newest summaries**, ordered by updated UTC
timestamp and then ID, newest first. Success, skip, failure, and cancellation
survive a restart. Interrupted current-schema journals appear as **outcome
unknown**. A summary with a known terminal outcome takes precedence over an
interrupted journal with the same ID. Corrupt, truncated, legacy-schema,
missing, unreadable, or unowned metadata is skipped safely.

Older diagnostic journals may contain a line like this:

```jsonl
{"kind":"begin","transfer_id":"abc123abc123abcd","source_uri":"local:///x.bin","destination_uri":"s3://bucket/x.bin","bytes_total":104857600,"upload_id":null,"ts":"2026-06-13T23:45:11Z"}
```

This legacy example remains diagnostic evidence; it has no verified connection
identity and is excluded from the recovery UI. S3 multipart upload IDs and
part state are not persisted for restart recovery.

### 4.2. Recheck and explicitly retry a copy
Use `h` for History or `r` for Recovery. Recovery groups records by **never
attempted**, **possibly published**, and **confirmed terminal**. Outcome and
publication certainty are separate: a failed or cancelled transfer may have
published bytes. A journal, a destination file, or a matching size never proves
that an interrupted transfer succeeded.

Select a record with arrows or the mouse. The scrollable details show full
literal paths and fingerprints; **Copy details** copies the complete text.
`Tab` and `Shift+Tab` move between the list, details and controls. `Escape`
closes the screen and restores file-pane focus.

**Recheck** resolves both original connections and reads both original paths.
It preserves the recorded outcome. An uncertain copy can become eligible for
a new retry only when a fresh recheck proves its destination absent. A present
uncertain destination remains ambiguous and retry is refused. Moves, deletes,
completed or skipped operations, missing or changed connections, and changed
endpoints cannot be retried.

**Retry copy** starts a new transfer from the beginning with a new ID, normal
progress and cancellation. It always asks for a fresh conflict choice and
shows both original endpoints. **Refuse overwrite** is the safe default;
**Skip**, **Rename**, and **Overwrite** require an explicit selection. Both
connections and file observations are checked again after that decision;
changes invalidate the request. There is no automatic replay, byte-offset
resume, multipart resume, or inference of prior success.

### 4.3. Clear metadata only
**Clear history** asks for confirmation, starting on Cancel. It removes only
validated owned history and interrupted metadata; corrupt, unowned and legacy
artifacts are preserved. It does not remove source or destination bytes, delete
remote multipart uploads, or cancel active transfers. Active operation metadata
remains protected until the transfer settles.

For S3 uploads interrupted outside aws-tui's normal cancel path, the
[1-day MPU abort lifecycle rule](connections.md#6-recommended-1-day-mpu-abort-lifecycle-rule)
is the server-side backstop. Recovery does not clean up endpoint artifacts.

### 4.4. What gets dumped on a crash
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

The writer reads backward across the active log and numbered rotations. It
retains up to 1,000 lines while reading at most 1 MiB in total, so producing a
crash report does not load every retained log into memory.

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

## 5. Browse AWS Glue safely

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

For the shared source, state, and table-reference workflow:

1. `:` opens the command palette. Its `Switch source` command invokes
   `app.swap_source` and cycles resolver order; it does not select an exact
   source.
2. Use `Tab` / `Shift+Tab` to focus the bordered source selector, then press
   `Enter` or `Space` to open it. Use `Up` / `Down`, commit with `Enter`, and
   cancel with `Escape`.
3. In Glue Jobs press `Shift+F`; in Crawlers press `Shift+G`.
4. In Athena press `Shift+W`, `Shift+C`, or `Shift+D` for the corresponding
   context selector.
5. In Glue Catalog select a table and press `y` to copy its canonical,
   fully quoted identifier.
6. In Athena press `i` outside the editor, or choose **Insert copied table
   reference**, to replace the editor selection or insert at the cursor.
7. A connection/region mismatch is refused without changing the editor,
   clipboard, or active profile.

This is the copy table reference workflow for the typed app clipboard.

### 5.1. Least-privilege Glue permissions

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

### 5.2. Open a table location in S3

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

### 5.3. Exercise Glue in demo mode

Launch `aws-tui --demo`. `demo-dev` and `demo-prod` expose disjoint
catalog, job/run, and crawler names, so `Shift+S` visibly proves
profile isolation. `demo-shared` demonstrates a Glue access-denied
state. The Catalog-to-S3 command uses the matching synthetic profile
and never makes a real AWS call.

## 6. Run Athena queries safely

Athena is an AWS-only, read-only query service. Select **Athena** in the
nav rail and choose a workgroup, catalog, and database in the page header.
Each is a bordered, keyboard-focusable selector; use `Shift+W`, `Shift+C`,
or `Shift+D` to focus the corresponding control and commit or cancel it with
the shared picker workflow above.
The four views are Query (`1`), History (`2`), Results (`3`), and Saved (`4`).
`Shift+S` rebuilds the whole Athena page for the next supported AWS connection;
the old page is disposed, so rows, selections, loaders, result fetches, and any
app-owned active query do not cross profiles or regions. Selections may be
remembered only within the same connection name and region and are revalidated
when the page returns.

### 6.1. Minimum Athena and data permissions

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
      "athena:BatchGetQueryExecution",
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

### 6.2. Customer S3 output versus managed results

The shipped Query view sends the selected workgroup and query execution
context without a caller-side `ResultConfiguration`; its
`AthenaQueryVM` runner does not pass `output_location` to the domain client.
The lower-level `AthenaClient.start_query(...)` contract is broader: callers
may pass an optional `output_location`, in which case the client sends exactly
`ResultConfiguration.OutputLocation`. The shipped query path intentionally
omits it and therefore requires the workgroup to use one of two distinct output
modes:

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

When Athena rejects `StartQueryExecution`, Execution detail distinguishes an
inaccessible workgroup result destination, a rejected catalog/database
context, and a rejected workgroup. These categories use fixed copy and do not
echo raw SQL, S3 locations, profiles, workgroups, catalogs, databases, tables,
or request tokens. Other validation failures retain the generic safe fallback.

### 6.3. Exact read-only SQL grammar

Press `Ctrl+Enter` only after setting workgroup, catalog, and database. The
local `sqlglot` Athena-dialect parser fails closed and permits exactly one
statement from the following implemented grammar.

**SELECT, set operations, and VALUES**

A root `SELECT` may contain a `VALUES` relation, read-only common table
expressions, and read-only subqueries. A root set operation may use `SELECT` or
`VALUES` operands with `UNION`, `INTERSECT`, or `EXCEPT`, including the
parser-supported `ALL` or `DISTINCT` qualifier (or the default `DISTINCT`).
For each `operation` (`UNION`, `INTERSECT`, or `EXCEPT`), the matching forms
accepted by the current parser are:

- `operation [ALL|DISTINCT] BY NAME [ON (column-list)]`
- `operation [ALL|DISTINCT] [STRICT] CORRESPONDING [(ON|BY) (column-list)]`

`ALL` or `DISTINCT` must appear immediately after the operation and before the
matching modifier; forms such as `UNION BY NAME ALL` and `UNION CORRESPONDING
DISTINCT` are rejected. The local policy does not resolve or validate matching
column names, compare them to either operand, or impose extra semantic rules on
the parser-accepted parenthesized column list. A standalone `VALUES` root is
rejected; it becomes allowed only below a `SELECT` or a root set operation.
Nested DDL, DML, commands, `INTO`, locks, analysis, execution, or transaction
nodes are rejected.

**SHOW**

These are the complete accepted forms. A `catalog`, `database`, and `table` may
be a regular or backtick-quoted identifier; patterns are string literals. A
`SHOW TBLPROPERTIES` property selector is a string literal only, not a regular
or backtick-quoted identifier.

- `SHOW DATABASES|SCHEMAS [IN catalog] [LIKE 'pattern']`
- `SHOW TABLES [IN [catalog.]database] ['pattern']`
- `SHOW COLUMNS FROM|IN table`, where `table` may have one, two, or three
  parts. A one-part table may additionally use `FROM|IN [catalog.]database`;
  that second scope is rejected after a multi-part table.
- `SHOW PARTITIONS table`, where `table` has one, two, or three parts.
- `SHOW TBLPROPERTIES table [('property')]`, where `table` has one, two, or
  three parts.
- `SHOW VIEWS [IN [catalog.]database] [LIKE 'pattern']`

`SHOW CREATE TABLE`, `SHOW CREATE VIEW`, unknown verbs, missing required
targets, extra clauses, and write text appended to an otherwise allowed form
are rejected. `SHOW TABLES` uses its optional bare string pattern; `LIKE` in
that position is not accepted by this policy.

**DESCRIBE**

`DESCRIBE [EXTENDED|FORMATTED] [database.]table` accepts a one- or two-part
table name, not a three-part catalog/database/table name. It may then contain,
in this order:

1. `PARTITION (key = value [, ...])`, with one or more identifier keys and
   each value restricted to literal string or number equality; and
2. one root column identifier followed only by dotted identifier fields or
   the exact complex selectors `'$elem$'`, `'$key$'`, or `'$value$'`.

The partition and column selector are independently optional. Operators other
than `=`, booleans, column references, calls, subqueries, empty or trailing
partition entries, unknown dollar selectors, array indexing, expressions, and
trailing identifiers are rejected.

**EXPLAIN**

`EXPLAIN` accepts another statement that independently satisfies this
allowlist. Its optional parenthesized choices are
`FORMAT GRAPHVIZ|JSON|TEXT` and `TYPE DISTRIBUTED|IO|LOGICAL|VALIDATE`, at most
once each, in either order when comma-separated. `EXPLAIN ANALYZE`, unknown or
quoted option names, unknown option values, duplicate options, and an unsafe
or missing body are rejected.

Accepted examples (one statement per line):

```sql
SELECT orderkey FROM analytics.orders LIMIT 10
SELECT * FROM (VALUES (1), (2)) AS samples(value)
SELECT 1 UNION ALL VALUES (2)
SELECT a, b FROM left_relation UNION BY NAME SELECT a, b FROM right_relation
SELECT a, b FROM left_relation UNION ALL BY NAME ON (a, b) SELECT a, b FROM right_relation
SELECT a, b FROM left_relation UNION DISTINCT BY NAME ON (a, b) SELECT a, b FROM right_relation
SELECT a, b FROM left_relation UNION CORRESPONDING SELECT a, b FROM right_relation
SELECT a, b FROM left_relation UNION ALL CORRESPONDING ON (a, b) SELECT a, b FROM right_relation
SELECT a, b FROM left_relation UNION DISTINCT CORRESPONDING BY (a, b) SELECT a, b FROM right_relation
SELECT a, b FROM left_relation UNION STRICT CORRESPONDING ON (a, b) SELECT a, b FROM right_relation
VALUES (1) INTERSECT SELECT 1
VALUES (1) EXCEPT VALUES (2)
SHOW DATABASES IN AwsDataCatalog LIKE 'analytics*'
SHOW SCHEMAS
SHOW TABLES IN AwsDataCatalog.analytics '*logs'
SHOW COLUMNS FROM AwsDataCatalog.analytics.orders
SHOW COLUMNS FROM orders FROM AwsDataCatalog.analytics
SHOW PARTITIONS AwsDataCatalog.analytics.orders
SHOW TBLPROPERTIES analytics.orders('comment')
SHOW TBLPROPERTIES `orders table`('comment')
SHOW VIEWS IN AwsDataCatalog.analytics LIKE 'orders*'
DESCRIBE FORMATTED analytics.orders
DESCRIBE analytics.orders PARTITION (`event date` = '2026-07-25', shard = 7) payload.'$elem$'.field
EXPLAIN (TYPE VALIDATE, FORMAT GRAPHVIZ) SELECT 1
EXPLAIN (TYPE IO, FORMAT TEXT) DESCRIBE analytics.orders payload.'$key$'.'$value$'
```

Rejected examples (one statement per line):

```sql
VALUES (1), (2)
SELECT 1; SELECT 2
SHOW CREATE TABLE analytics.orders
SHOW COLUMNS
SHOW TABLES IN analytics LIKE 'orders*'
SHOW TBLPROPERTIES analytics.orders(comment)
SHOW TBLPROPERTIES analytics.orders(`comment`)
SELECT a FROM left_relation UNION BY NAME ALL SELECT a FROM right_relation
SELECT a FROM left_relation UNION CORRESPONDING DISTINCT SELECT a FROM right_relation
DESCRIBE AwsDataCatalog.analytics.orders
DESCRIBE orders PARTITION (shard > 1)
DESCRIBE orders payload.'$unknown$'
EXPLAIN ANALYZE SELECT 1
EXPLAIN (FORMAT YAML) SELECT 1
EXPLAIN (TYPE IO, TYPE LOGICAL) SELECT 1
CREATE TABLE copy AS SELECT * FROM analytics.orders
INSERT INTO archive SELECT * FROM analytics.orders
UNLOAD (SELECT 1) TO 's3://example/results/'
CALL system.runtime.kill_query()
```

Empty or unparsable input, multiple statements, all other DDL and DML, CTAS,
and every form outside the grammar above are also rejected before any
`start_query_execution` call. The editor retains the rejection as validation
feedback and does not dispatch the SQL. IAM, Lake Formation, workgroup, S3,
bucket, and KMS policies remain the authorization boundary.

### 6.4. Execution, History, Results, and result artifacts

After submission, Query records an app-owned execution identity and polls its
detail through queued, running, and terminal states. On `SUCCEEDED`, it loads
the first Results page; `FAILED` and `CANCELLED` retain terminal detail. `Esc`
interrupts query submission before an execution is available and stops only an
active app-owned execution started by this page through `stop_query_execution`.
Switching context or source requests cancellation and awaits Athena shutdown
before disposal; History rows are never assumed safe to stop.

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

### 6.5. Exercise Athena in demo mode and troubleshoot

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
- **Cannot access the workgroup result location.** The workgroup can still be
  configured for enforced S3 output while the active principal lacks the
  required bucket, object, or KMS permissions. Check the result-prefix grants
  listed in §6.2; aws-tui does not replace the enforced destination.
- **Selected query context or workgroup was rejected.** Re-select the catalog,
  database, and enabled workgroup for the active profile and region. The
  generated SQL is database/table relative because the exact catalog and
  database travel in `QueryExecutionContext`.
- **No rows or cannot load more.** A successful query can return no rows; `l`
  only works when Athena supplied another page token. Check the query state and
  execution detail in History.
- **Cannot open an artifact in S3.** The execution must be successful and its
  output URI must be valid, match the active connection/region, and be readable
  through S3. Check `s3:ListBucket` / `s3:GetObject` on the result prefix.

### 6.6. Save and recover local SQL drafts

1. Open Settings with `,`. Tab to the **Athena SQL drafts** section and
   **Enable local SQL drafts**, then press Enter. Settings shows the actual
   local path and plaintext retention explanation. On a short terminal, collapse
   the Connections section with Enter to expose the complete draft panel.
2. Return to Athena. Select the intended Source, Workgroup, Catalog, and Database,
   enter SQL, and wait until the editor border says **Draft saved**. **Draft
   pending** means the write has not been acknowledged. **Draft not saved** means
   the write failed; read Execution detail and edit again to retry.
3. Quit and relaunch aws-tui. Select the exact original source (connection and
   region), workgroup, catalog, and database before recovery.
4. Tab to **Drafts** and press Enter. Use Up and Down to highlight the record;
   selection alone does not change the editor. Tab to **Restore**, then press
   Enter. If prompted with **Replace unsaved SQL?**, accept only when ready to
   replace that editor text. A successful restore focuses the editor and does
   not execute SQL or load results. Press `Ctrl+Enter` separately after checking
   the SQL and account.

After an abrupt exit, restart and follow steps 3–4 to recover a completed
autosave. Stalled writes remain **Draft pending**; errors show **Draft not saved**.
Neither state guarantees recovery of the latest text. Keep a separate copy when
a save has not been confirmed.

aws-tui waits up to two seconds for draft flushing and reports edits not confirmed saved.
An already-running filesystem operation may finish later. This limit does not bound AWS cancellation or total process shutdown.

**Delete** or **Clear all** in the manager deletes owned records without
recreating them from untouched editor text. **Disable and delete drafts** in
Settings asks for confirmation, turns persistence off, and deletes owned records;
**Retry draft cleanup** retries an unsuccessful cleanup. Draft contents are not
collected by diagnostics. Demo mode does not save local SQL drafts. See
[Configuration](configuration.md#5-local-athena-sql-drafts) for schema, byte limits,
and permissions, and [Athena](services/athena.md#6-local-sql-draft-recovery) for
fresh context validation and same-name credential-account limitations.

### 6.7. Inspect, copy, filter, and sort loaded result cells

1. Open Results with `3` after a successful query or a History selection.
   Use arrow keys to select a row and column, including either column when
   labels are duplicated. Press Enter or click the current cell to select it,
   including the first cell or a result containing only one cell.
2. Press `Alt+Enter` to read the full original value. Scroll the read-only
   inspector; Escape or Close returns to the same cell if it is still current.
3. Press `Alt+C` for one cell or `Alt+Shift+C` for a row. The payload is JSON:
   null is `null`, empty is `""`, and a literal `NULL` string is `"NULL"`.
   Rows are arrays preserving column order, newlines (escaped in JSON), and
   readable Unicode. Terminal/platform clipboard delivery can fail; the app's
   outcome message identifies whether a write was acknowledged.
4. Press `Alt+F`, type a literal substring, and Apply. Matching ignores case
   across all loaded cells; null matches `null`. Cancel keeps the old filter;
   Clear shows all loaded rows. Read visible/loaded counts and `local` in the
   footer: this is loaded-only scope, even when zero rows match.
5. On a selected column, press `Alt+S` for ascending, again for descending,
   and again for loaded order. Strings compare lexically by Unicode value,
   including numeric-looking text; null stays last and equal values keep
   loaded order. `Alt+R` clears both filter and sort.
6. Use **Load more** or `l` explicitly to include another page in the current
   filter and order. `more available` means unfetched rows may match; `safety
   limit` means the 10,000-row ceiling prevented further loading. These controls
   neither rerun SQL nor perform server-side operations or full-result export.
   Full-execution export is tracked separately in
   [#254](https://github.com/thekaveh/aws-tui/issues/254).

Changing source/context or replacing/reloading the execution clears these
transient controls and selection. Filtering a selected row out clears its
selection; sorting and explicit paging preserve a surviving original cell.
An open inspector is invalidated when its execution is retired. Help and the
Athena palette show configured keys; customize the six `athena.*` actions in
[Keybindings](keybindings.md#110-amazon-athena).

## 7. Inspect and query Glue tables through Athena

Glue → Athena navigation is an explicit, read-only handoff. It carries the
selected table's catalog, database, table, connection name, and region in an
immutable `TableRef`; it does not pass a client or reuse a different profile.

1. Select **Glue**, then choose a Catalog database and table.
2. Open `:` or `Ctrl+K` and choose **Query table in Athena**.
3. Review the generated statement:

    ```sql
    SELECT * FROM "database"."table" LIMIT 5
    ```

4. Edit it if needed, then press `Ctrl+Enter` to execute.

aws-tui quotes the database and table identifiers independently, discovers the
destination catalog/database through bounded pagination, sends both through
Athena's query execution context, and selects an enabled workgroup for the same
source. The editor shows the starter statement as soon as Athena
mounts, even while remote context discovery is still in progress. Run remains
disabled and the query controls report `RESOLVING TABLE CONTEXT` until that
exact context is ready. aws-tui never executes generated queries
automatically. If the handoff is cancelled, superseded, or cannot mount the
destination, the composition root restores the prior Glue/Athena state instead
of leaving a partially switched page.

From an Athena Query view, **Open query table in Glue** is available through
the command palette when the read-only SQL resolves to exactly one visible
table. Queries with zero or multiple table references remain on Athena.

### 7.1. Iceberg detection and metadata views

Glue table detail classifies a table as Iceberg when normalized Glue
parameters such as `table_type`, `tableType`, `classification`, `provider`, or
`spark.sql.sources.provider`, or the Glue `TableType`, contains the exact
`iceberg` marker. Only then does the Catalog detail surface show Iceberg
metadata.

The tabs are:

- **Snapshots** — commit time, snapshot/parent IDs, operation, manifest list,
  and summary; bounded to 100 rows.
- **History** — current-snapshot ancestry; bounded to 100 rows.
- **Manifests** — manifest paths, lengths, partition-spec IDs, and file
  counts; bounded to 500 rows.
- **Files** — data/delete file paths, format, partition, record count, and
  bytes; bounded to 1,000 rows.
- **Partitions** — partition values and aggregate file/record/delete metrics;
  bounded to 500 rows.
- **References** — branches/tags, snapshot IDs, and retention fields; bounded
  to 100 rows.

Each tab loads on demand through `IcebergInspector`, which submits a bounded
read-only query to Athena metadata tables such as
`"table$snapshots"` and `"table$files"`. These caps are metadata row limits. Each inspection requests at most one extra
sentinel row, which is removed before display. An extra row proves **truncated**
coverage; without it, exhausted result pagination proves **complete** coverage.
Exactly reaching the cap alone does not prove truncation. Missing exhaustion
evidence is reported as **unknown**.

The footer separates visible rows, fetched display rows, collection coverage,
and the metadata row limit. The UI reveals 50 cached rows at a time;
**Load more** exposes cached rows without issuing another Athena query. A
truncation warning remains after every fetched row is visible, and failed
refreshes retain prior coverage and fetched rows.
**Retry** reruns only the active tab. A permission, throttling, shape, or
network error is a partial failure of that metadata pane and does not erase
successful sibling tabs.

Metadata row limits do not bound bytes scanned or Athena costs.
These are real Athena queries, not free Glue lookups. Account for
metadata-query costs, workgroup limits, bytes scanned, result storage, and
concurrency exactly as for other Athena statements. The selected profile needs
the Glue read operations in §5.1, the Athena operations in §6.1, source
data/catalog authorization where Athena requires it, and result-location
permissions for its workgroup. Lake Formation-governed tables additionally
need `lakeformation:GetDataAccess` plus the relevant `DESCRIBE`/`SELECT`
grants. A denied metadata table is reported generically; raw SQL, table
metadata values, and provider exception text are not written to UI errors or
diagnostic logs.

### 7.2. Snapshot time travel

In **Snapshots**, highlight a visible snapshot and press `Shift+V`, click the
time-travel control, or choose **Query Iceberg snapshot in Athena**. The
destination editor receives:

```sql
SELECT * FROM "database"."table"
FOR VERSION AS OF 4201 LIMIT 5
```

The snapshot ID must be a non-negative integer present in the visible snapshot
page. Switching to another metadata tab clears the actionable snapshot
selection. As with an ordinary table handoff, generated SQL remains
unexecuted until `Ctrl+Enter`; review the catalog, database, table, workgroup,
snapshot ID, and expected scan before running it.

After a successful customer-S3 execution, **Open Athena result in S3**
authoritatively reloads the execution and reveals its exact CSV artifact under
the same connection name and region. Athena managed results have no customer
S3 artifact, so the command remains on Athena.

### 7.3. Demo journey and limitations

Launch `aws-tui --demo` and follow this no-network path:

1. On `demo-dev`, open Glue table `dev_analytics.dev_events_iceberg`.
2. Inspect its metadata tabs, choose snapshot `4201`, and press `Shift+V`.
3. Confirm Athena contains `FOR VERSION AS OF 4201` and has not started an
   execution.
4. Press `Ctrl+Enter`, inspect the two profile-specific result rows, then open
   the generated CSV in S3.
5. Use `Shift+S` to compare the disjoint `demo-prod` data and the scoped
   `demo-shared` access state.

Current scope is read-only operational visibility. aws-tui does not create,
alter, optimize, expire, rollback, branch, tag, or delete Iceberg resources;
it does not create/edit/delete Glue or Athena resources; and it does not infer
a write workflow from metadata. Large metadata tables remain bounded by the
limits above, so the UI is an operational inspection surface rather than a
complete metadata export tool.

### 7.4. Local row preview with DuckDB (Peek)

The **Peek** tab previews rows of the selected Iceberg table by querying its
S3 location directly with a local DuckDB engine — no Athena workgroup, no
query execution, no AWS query bill. It needs the optional `duckdb` extra
(`pip install aws-tui[duckdb]`) and an AWS profile connection; it is not
available on `s3-compatible` connections. Without the extra installed, the
tab is still present and selectable; choosing it reports the missing engine
and states the install command instead of disappearing silently.

1. Select **Glue**, open an Iceberg table, and switch to the **Peek** tab
   (it sits after **Refs** in the Iceberg tab strip).
2. aws-tui runs one `iceberg_scan(...)` statement against the table's S3
   root, pinned to the selected snapshot when one is chosen on **Snaps**.
   Rows, columns, and `NULL`s (dimmed, distinct from a column that genuinely
   contains the four-character string `"NULL"`) render in the same table
   widget the six metadata tabs use.
3. The footer reports the exact row count and the row-limit ceiling, e.g.
   `100 rows · limit 100`. Unlike the six metadata tabs' load-more control,
   which only widens an already-capped local window over rows already in
   hand, Peek's `↓` issues a genuinely new query at the next row-limit step
   (100 → 1,000 → 10,000); it disables once the top step is reached or a
   page comes back short of the limit.
4. `↻` retries the current query, honoring the active snapshot pin and row
   limit.

DuckDB resolves AWS credentials itself, through a fresh `credential_chain`
secret bound to the connection's exact profile and region on every query —
never the ambient default credential chain, and never a cached token, so an
`aws sso login` run in another terminal is picked up on the next `↻` the same
way an Athena query already is. Queries run entirely on your machine: the
engine reads S3 objects directly with whatever permissions your profile
already grants on that bucket, and it never touches Athena, a workgroup, or a
query-result location, so it incurs none of the costs or setup in §6 and
§7.1. The one exception is setup, not querying: the first Peek query on a
machine downloads DuckDB's `httpfs`, `aws`, and `iceberg` extensions from
`extensions.duckdb.org` and caches them under `~/.duckdb/`, so on a host
without egress to that domain the first query fails until the cache is
populated (see [Installation](install.md)). Forbidden, not-found, not-Iceberg, and expired-credential failures
surface through the same placeholder styling as the six metadata tabs.

## 8. Diagnose local setup and source access

Use diagnostics when startup fails or a source cannot authenticate:

```sh
aws-tui doctor
aws-tui doctor --json
aws-tui doctor --probe dev
aws-tui doctor --json --probe 'local-s3'
```

### 8.1. Read the offline report

The default command reads local app and AWS configuration, validates the
keymap, discovers sources, checks credential presence, and inspects SSO cache
freshness. It opens no socket, calls no keyring or credential provider, runs
no subprocess, and creates or changes no files, directories, or permissions.
It works even when malformed configuration prevents the TUI from starting.
`AWS_TUI_DEMO=1` still inspects the actual local configuration; `--demo` and
`--version` cannot be combined with `doctor`.

JSON reports have integer `schema_version: 1` and a `checks` array. Each
check has `name`, `result`, `context`, `next_step`, and `actionable`. Context
uses safe source ordinals, kind, and origin rather than raw source names;
secrets and sensitive output are filtered. Text and JSON share the same
checks and repair instructions.

| Exit | Meaning |
|---|---|
| `0` | No actionable failure found; access may still be unverified. |
| `1` | At least one actionable configuration or authentication failure. |
| `2` | Invalid command usage, such as `--probe` without a name. |

An absent optional app config, a skipped probe, or offline `unverified`
credentials is informational. Follow each `next_step` for failures; local
credential presence alone does not verify network access or permissions.

### 8.2. Explicitly probe one source

`--probe NAME` requires one exact configured connection name or discovered
AWS profile name. It retains every local diagnostic check and replaces the
default skipped probe with that source's result. Unknown names fail without
trying another source. AWS sources perform STS `GetCallerIdentity`;
S3-compatible sources perform S3 `ListBuckets`. Responses are discarded,
and success confirms only that operation. An explicitly selected
S3-compatible source may read its configured keychain credentials.

Probes use the standard SDK credential chain with connect and read timeouts
of 5 seconds and `total_max_attempts=1`, including credential-provider clients.
These are per-request limits, not a total deadline for a multi-step chain.
No credentials or SSO tokens are persisted. Cached SSO is used without OIDC
refresh or rotation. Expired SSO, `credential_process`, and interactive MFA
require authentication in another terminal; the probe does not launch a
process or prompt for MFA. Dynamic providers may require network requests
only after an explicit probe request.

### 8.3. Locate runtime evidence

Press `?` in the running TUI and scroll down to **Diagnostics** for the
doctor command, active log file, and crash directory. Those locations come
from the running app's log and crash path selection, including custom
context paths and legacy/platform-native cache resolution. Diagnostic path
text displays brackets literally and escapes control characters. Doctor
also reports effective local paths without creating them.
