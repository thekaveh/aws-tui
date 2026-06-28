# Cookbook

> Common recipes for daily aws-tui use. Each recipe is end-to-end —
> commands you can copy/paste plus the in-app key sequence.

1. [Connect to a local MinIO](#1-connect-to-a-local-minio)
2. [Switch the theme on the fly](#2-switch-the-theme-on-the-fly)
3. [Customize a keybinding](#3-customize-a-keybinding)
4. [Resume after a crash](#4-resume-after-a-crash)

---

## 1. Connect to and switch between data sources

Walks through three setups people hit on day one:

- **§1.1–§1.5** — connect to a local MinIO from scratch (the
  canonical "first s3-compatible endpoint" walkthrough).
- **§1.6** — jump between AWS profiles with one keystroke
  (multi-account flows).
- **§1.7** — run several `s3-compatible` endpoints side-by-side.
You have a MinIO running on `http://localhost:9000` with the dev
credentials `minioadmin / minioadmin`. Goal: a `minio-local`
connection in aws-tui that points at it.

### 1.1. Start MinIO (skip if already running)

**Quickest path — dev seeded MinIO** (recommended for first-time
exploration; ships ~5 buckets and ~90 objects so you have content to
navigate):

```bash
scripts/test-services/s3/up.sh
```

This wraps `docker compose` + `seed.py` and prints the config snippet
to add to `~/.config/aws-tui/config.toml`. Teardown is
`scripts/test-services/s3/down.sh` (add `--purge` to wipe the data
volume). See `scripts/test-services/README.md` for the seeded
dataset and how to extend it.

**Plain MinIO** (no seed):

```bash
docker run --rm -d --name minio \
    -p 9000:9000 -p 9001:9001 \
    -e MINIO_ROOT_USER=minioadmin \
    -e MINIO_ROOT_PASSWORD=minioadmin \
    minio/minio:latest server /data --console-address ":9001"
```

### 1.2. Store the credentials in the macOS Keychain (recommended)

The resolver expects two keychain entries under ONE service name
(matching the `credentials = "keychain:<service>"` value in
`config.toml`): one account named `access_key_id` and one named
`secret_access_key`. So for a `keychain:minio-local` config entry:

```bash
# service="minio-local", account="access_key_id"
security add-generic-password \
    -s minio-local -a access_key_id -w minioadmin

# service="minio-local", account="secret_access_key"
security add-generic-password \
    -s minio-local -a secret_access_key -w minioadmin
```

(The Python `keyring` library aws-tui uses delegates to the macOS
Keychain by default.)

### 1.3. Add via the in-TUI first-run / add form
The first time you run aws-tui with no connections configured, the
welcome modal pops up — pick **add s3-compatible** and fill the form:

| Field | Value |
|---|---|
| Name | `minio-local` |
| Endpoint URL | `http://localhost:9000` |
| Region | `us-east-1` |
| Access key ID | `minioadmin` |
| Secret access key | `minioadmin` |

That writes a `static` entry to `config.toml`. Note: every launch with
a `static`-credentials connection emits a sticky toast warning, per the
credential-source preference order documented in
[connections.md §2](connections.md#2-credential-sources-for-s3-compatible-connections);
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
verify_tls = true
```

### 1.4. Add by editing the file directly
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
`s3-compatible · minio-local · {endpoint}` — the bucket list
should populate immediately.

> The dedicated command-palette path (`: connection switch ▸ minio-local`)
> is spec'd but deferred to v0.9 — in v0.8.x ``:`` opens the
> help overlay as a placeholder. ``Shift+S`` is the one-keystroke
> equivalent today.

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
> [connections.md §3](connections.md#3-auto-discovery--sso-cache-probe));
> a profile with an expired token surfaces an "auth required"
> placeholder instead of hanging.

---

### 1.7. Run several s3-compatible endpoints side-by-side

There's no fixed limit on how many `s3-compatible` connections you
can configure. Each one shows up in the swap-source cycle and in
the in-app Settings page. Example config covering a local MinIO, a
Cloudflare R2 production bucket, and a Backblaze B2 archive:

```toml
# ~/.config/aws-tui/config.toml

[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
access_key_id = "minioadmin"
secret_access_key = "minioadmin"
force_path_style = true
verify_tls = false

[connections.r2-prod]
kind = "s3-compatible"
endpoint_url = "https://<account>.r2.cloudflarestorage.com"
region = "auto"
access_key_id = "<r2-key-id>"
secret_access_key = "<r2-secret>"
force_path_style = false

[connections.b2-archive]
kind = "s3-compatible"
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

> The command-palette path (`:` then `theme switch ▸ voidline`) is
> spec'd in the design but not wired in v0.7.x — the palette
> registers no entries yet, so `t` / `Shift+T` are the working
> shortcuts.

### 2.2. Persistent
```toml
# ~/.config/aws-tui/config.toml
[defaults]
theme = "voidline"
```

Theme names: `carbon` (default), `voidline`, `lattice`, `amber`,
`solarized-light`, `github-light`, `one-light`, `nord`, `dracula`,
`gruvbox-dark`. See [theming.md §1](theming.md#1-built-in-themes) for
the full per-theme palette breakdown.

### 2.3. Add a custom theme
Copy `src/aws_tui/ui/themes/carbon.tcss` to
`~/.config/aws-tui/themes/midnight.tcss`, edit the palette tokens,
and pick it from the theme picker (`t`) like any built-in. See
[theming.md](theming.md#32-full-custom-themes) for the full token table.

### 2.4. Tweak just one or two colors
Drop `~/.config/aws-tui/theme.tcss` and override what you need; the
overlay layers on top of the active built-in:

```tcss
/* ~/.config/aws-tui/theme.tcss */
.modal-title { color: #ff3df8; }
Footer { background: #050505; }
```

---

## 3. Customize a keybinding

> **v0.7.x status:** the `KeymapStore` accepts a `[keybindings]`
> overlay (passed via the constructor) and validates that every
> action id maps to a known one. The composition root does not yet
> read the overlay from `config.toml` — that wiring is part of the
> input-router work deferred from M6. The schema below is the
> intended shape for when the loader lands; today the same effect
> is achievable by editing
> `src/aws_tui/infra/keymap_store.py::DEFAULT_BINDINGS` directly in
> a fork.

Goal: rebind copy (`pane.copy`) from `c` to `y` (vim yank).

```toml
# ~/.config/aws-tui/config.toml
[keybindings]
"pane.copy" = "y"
```

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

In a future release you'll be able to trigger the action through
the command palette (`:` then search "delete"); the palette is
spec'd but deferred to v0.9, so for v0.8.x an empty
`[keybindings]` value effectively removes the action until you
rebind it.

### 3.2. See the active map
The full list of action IDs lives in
[`docs/keybindings.md`](keybindings.md#3-action-ids) and is the same set
declared in `src/aws_tui/infra/keymap_store.py:DEFAULT_BINDINGS`. There
is no `--print-bindings` CLI flag in v0.7; the launch path enters the
TUI directly.

### 3.3. Unknown action IDs raise on startup
If you overlay an action id that isn't in `KeymapStore.DEFAULT_BINDINGS`
(e.g. typo `pane.cpy`), `KeymapStore.resolve` raises `UnknownAction`
on startup. That's deliberate — silently dropping a binding is the
worst kind of bug.

---

## 4. Resume after a crash
Long-running multipart uploads survive aws-tui crashes. Here's the
flow:

### 4.1. What gets saved
After each completed multipart part, the in-flight transfer appends
a JSONL line to `~/.cache/aws-tui/transfers/<id>.jsonl`:

```jsonl
{"kind":"begin","transfer_id":"abc123","source_uri":"local:///x.bin","destination_uri":"s3://bucket/x.bin","bytes_total":104857600,"upload_id":"mpu-aaa","ts":"2026-06-13T23:45:11Z"}
{"kind":"part","part_index":1,"etag":"\"d41d...\"","bytes_written":8388608,"ts":"2026-06-13T23:45:13Z"}
{"kind":"part","part_index":2,"etag":"\"098f...\"","bytes_written":8388608,"ts":"2026-06-13T23:45:18Z"}
```

On normal completion it gets a trailing `{"kind":"finished",...}`
line; on user cancel a `{"kind":"aborted",...}` line. Either
terminates the journal.

### 4.2. What happens on next launch
`composition.py` calls `TransferJournal.find_unfinished()` after the
connection resolves. If any journal lacks a terminal record, the
resume modal pops up:

```
2 transfers from a previous session were not finished.
  - api-2026-06-13.json  (3.4 M / 4.2 M, 82%)
  - db-slowq-06-13.csv   (279 k / 892 k, 31%)
  [resume all] [abort all] [decide each] [keep for later]
```

| Choice | What it does |
|---|---|
| **resume all** | Rebuilds in-flight `TransferVM`s from the journal and continues the multipart upload from the next part. *(In v0.7.0 this is a no-op pending hookup with file-manager TransferVMs; the journal stays on disk for next time.)* |
| **abort all** | Calls `AbortMultipartUpload` per `upload_id` against the active S3 connection (cleans up server-side); marks the journal `aborted` and `purge()`s the file. |
| **decide each** | *(v0.7.0: equivalent to **keep for later**; per-entry modal lands in a follow-up.)* |
| **keep for later** | No mutation. The modal shows again on next launch. |

### 4.3. Manual cleanup
If you want to nuke the journals without going through the modal:

```bash
rm -f ~/.cache/aws-tui/transfers/*.jsonl
```

(But: this leaves orphaned MPUs on the server. The
[1-day MPU abort lifecycle rule](connections.md#6-recommended-1-day-mpu-abort-lifecycle-rule)
is your backstop.)

### 4.4. What gets dumped on a crash
If aws-tui hits an unhandled exception, it writes
`~/.cache/aws-tui/crash/<ts>.txt`:

```
aws-tui crash dump
timestamp: 2026-06-14T12:00:00+00:00
exception: TypeError: unsupported operand type(s) for +: ...

== traceback ==
  ...

== last user actions ==
2026-06-14T12:00:00 pane.move_down
2026-06-14T12:00:01 pane.refresh
2026-06-14T12:00:02 pane.delete

== log tail ==
... (last 1000 lines of aws-tui.log)
```

A crash modal also pops up:

```
unexpected error
  TypeError: ...

  ~/.cache/aws-tui/crash/2026-06-14T12-00-00.txt

  [view trace]  [continue]  [quit]
```

The `continue` button is enabled only when the last user action was
**read-only** (navigation, refresh, filter, palette open). Writes
(delete, copy, move, rename) disable it — you can't safely continue a
write that may have partially executed.
