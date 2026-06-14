# Cookbook

> Common recipes for daily aws-tui use. Each recipe is end-to-end —
> commands you can copy/paste plus the in-app key sequence.

- [Connect to a local MinIO](#connect-to-a-local-minio)
- [Switch the theme on the fly](#switch-the-theme-on-the-fly)
- [Customize a keybinding](#customize-a-keybinding)
- [Resume after a crash](#resume-after-a-crash)

---

## Connect to a local MinIO

You have a MinIO running on `http://localhost:9000` with the dev
credentials `minioadmin / minioadmin`. Goal: a `minio-local`
connection in aws-tui that points at it.

### 1. Start MinIO (skip if already running)

```bash
docker run --rm -d --name minio \
    -p 9000:9000 -p 9001:9001 \
    -e MINIO_ROOT_USER=minioadmin \
    -e MINIO_ROOT_PASSWORD=minioadmin \
    minio/minio:latest server /data --console-address ":9001"
```

### 2. Store the credentials in the macOS Keychain (recommended)

```bash
# Stores under service="minio-local", account="default".
security add-generic-password \
    -s minio-local -a default -w minioadmin
```

You can repeat for `minio-local-secret`:

```bash
security add-generic-password \
    -s minio-local-secret -a default -w minioadmin
```

(The Python `keyring` library aws-tui uses delegates to the macOS
Keychain by default.)

### 3a. Add via the in-TUI first-run / add form

The first time you run aws-tui with no connections configured, the
welcome modal pops up — pick **add s3-compatible** and fill the form:

| Field | Value |
|---|---|
| Name | `minio-local` |
| Endpoint URL | `http://localhost:9000` |
| Region | `us-east-1` |
| Access key ID | `minioadmin` |
| Secret access key | `minioadmin` |

That writes a `static` entry to `config.toml`. To upgrade to
keychain-backed credentials, edit `~/.config/aws-tui/config.toml`
and change:

```toml
[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
credentials = "keychain:minio-local"
force_path_style = true
verify_tls = true
```

### 3b. Add by editing the file directly

If you already have other connections, just append:

```toml
[connections.minio-local]
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
credentials = "keychain:minio-local"
force_path_style = true
verify_tls = true
```

### 4. Use it

```bash
aws-tui
```

Then in-app: `:` `connection switch ▸ minio-local` `Enter`. The S3
pane should list your buckets.

---

## Switch the theme on the fly

### One-off (session-only)

```
:                       # open command palette
theme switch ▸ voidline # fuzzy-filter to the theme you want
Enter
```

The `ThemeChangedMessage` fires and the active stylesheet reloads
instantly — no restart needed.

### Persistent

```toml
# ~/.config/aws-tui/config.toml
[defaults]
theme = "voidline"
```

Theme names: `carbon` (default), `voidline`, `lattice`, `amber`.

### Add a custom theme

Copy `src/aws_tui/ui/themes/carbon.tcss` to
`~/.config/aws-tui/themes/midnight.tcss`, edit the palette tokens,
and pick it from the palette like any built-in. See
[theming.md](theming.md#full-custom-themes) for the full token table.

### Tweak just one or two colors

Drop `~/.config/aws-tui/theme.tcss` and override what you need; the
overlay layers on top of the active built-in:

```tcss
/* ~/.config/aws-tui/theme.tcss */
.modal-title { color: #ff3df8; }
Footer { background: #050505; }
```

---

## Customize a keybinding

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

### Disable a default binding

Set the action to an empty list:

```toml
[keybindings]
"pane.delete_marked" = []   # nope, no quick delete
```

You can still trigger it via the command palette (`:` `pane delete`).

### See the active map

```
aws-tui --print-bindings   # prints the resolved action ↔ key map and exits
```

(or check `~/.cache/aws-tui/log/aws-tui.log` for the `bindings.resolved`
record).

### Unknown action IDs raise on startup

If you typo `pane.cpy = "y"`, aws-tui refuses to launch and points
at the bad entry. That's deliberate — silently dropping a binding
is the worst kind of bug.

---

## Resume after a crash

Long-running multipart uploads survive aws-tui crashes. Here's the
flow:

### What gets saved

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

### What happens on next launch

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

### Manual cleanup

If you want to nuke the journals without going through the modal:

```bash
rm -f ~/.cache/aws-tui/transfers/*.jsonl
```

(But: this leaves orphaned MPUs on the server. The
[1-day MPU abort lifecycle rule](connections.md#recommended-1-day-mpu-abort-lifecycle-rule)
is your backstop.)

### What gets dumped on a crash

If aws-tui hits an unhandled exception, it writes
`~/.cache/aws-tui/crash/<ts>.txt`:

```
aws-tui crash dump
timestamp: 2026-06-14T12:00:00+00:00
exception: TypeError: unsupported operand type(s) for +: ...

== traceback ==
  ...

== last user actions ==
2026-06-14T12:00:00 pane.cursor_down
2026-06-14T12:00:01 pane.refresh
2026-06-14T12:00:02 pane.delete_marked

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
