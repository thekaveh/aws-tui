# 1. Recording TODO

> Items the maintainer (i.e. **you**) needs to record manually before
> the v0.9.0 development docs feel complete. These artifacts require a
> manual terminal recording session, so this file is a hand-off list. Each
> item lists where it lands in the docs and the rough recipe.

Place finished artifacts under `docs/assets/` (create if missing) and
embed them at the section named in each item:

- For `.cast` files: `[![asciicast](https://asciinema.org/a/<id>.svg)](https://asciinema.org/a/<id>)`
- For PNG: `\![<alt>](assets/<file>.png)` (remove the leading backslash when embedding it)

## 1.1. Quickstart launch (`README.md` hero)

Format: asciinema, 30 seconds, 100×30.

Recipe:

```bash
asciinema rec -t "aws-tui quickstart" --idle-time-limit 1 \
    docs/assets/quickstart.cast
# inside the recording:
aws-tui
# wait for the dual-pane to render with default Carbon theme
# arrow down a few times in the S3 pane to show navigation
# press q to exit
```

Embed under the README hero image / status block if a fresh terminal
recording is still useful after the current hero asset.

## 1.2. First-run startup placeholder (`README.md` Quickstart section)

Format: PNG screenshot at 120×40.

Recipe:

```bash
# Force the current no-connection startup path with an isolated HOME:
tmp_home="$(mktemp -d)"
HOME="$tmp_home" aws-tui
# take a screenshot of the local-only/no-connection placeholder
# (macOS: Cmd+Shift+4, drag the terminal window). Save as
# docs/assets/first-run-voidline.png.
# Cleanup after the recording:
rm -rf "$tmp_home"
```

Or set the theme to `voidline` first via `theme = "voidline"` in
the config of the temp HOME for a more striking image.

The current capture should show only shipped behavior; there is no separate
welcome-modal recording.

## 1.3. S3 to local copy walkthrough (cookbook: connect to S3Mock)

Format: asciinema, 60 seconds, 120×40.

Recipe:

```bash
asciinema rec -t "aws-tui: S3Mock to local copy" \
    docs/assets/s3mock-copy.cast
# inside:
aws-tui
# Shift+S until the left pane title shows dev-s3
# navigate into a bucket
# use Shift+Down or Ctrl+Click to mark two files
# c (copy)
# confirm
# transfers overlay shows progress
# q
```

Embed below the "Use it" step of the S3Mock recipe in
[cookbook.md](cookbook.md#115-use-it).

## 1.4. Theme switch (cookbook: switch theme)

Format: asciinema, 20 seconds, 120×40.

Recipe:

```bash
asciinema rec -t "aws-tui: theme switch" \
    docs/assets/theme-switch.cast
# inside:
aws-tui
# t
# select voidline -> Enter
# pause 2 seconds to admire
# Shift+T until amber is active
# pause 2 seconds
# q
```

Embed below the "One-off (session-only)" step of the theme recipe.

## 1.5. Transfer-journal diagnostics

Format: asciinema, 60 seconds, 120×40.

Recipe:

```bash
# Step 1: seed a fake unfinished journal:
CACHE_DIR="$(uv run python -c 'from aws_tui.infra.paths import cache_home; print(cache_home())')"
mkdir -p "$CACHE_DIR/transfers"
cat > "$CACHE_DIR/transfers/5eedabc05eedabc0.jsonl" <<'EOF'
{"kind":"begin","transfer_id":"5eedabc05eedabc0","source_uri":"local:///tmp/api.json","destination_uri":"s3://bucket/api.json","bytes_total":4200000,"upload_id":"mpu-zzz","ts":"2026-06-13T23:45:11Z"}
{"kind":"part","part_index":1,"etag":"\"a\"","bytes_written":2097152,"ts":"2026-06-13T23:45:13Z"}
EOF

asciinema rec -t "aws-tui: transfer journal diagnostics" \
    docs/assets/transfer-journal.cast
# inside:
ls -l "$CACHE_DIR/transfers"
aws-tui
# verify startup does not claim to resume or abort the stale journal
# q
```

Cleanup after recording: `rm "$CACHE_DIR/transfers/5eedabc05eedabc0.jsonl"`.

Do not imply that an interactive resume modal exists; recovery remains a
journal and cleanup contract until a new user-facing flow is designed.

## 1.6. Crash dump (interactive modal deferred)

Format: terminal text or PNG screenshot of the generated dump path.

Recipe:

The crash dump writer is live. The interactive crash modal exists as UI
scaffolding but is not wired into the unhandled exception path. Do not record
or claim the modal until that hosting integration ships. To capture the current
behavior, trigger an unhandled exception only in a disposable local checkout,
record the redacted dump path written under `<cache-dir>/crash/`, and revert the
temporary exception before committing.

Embed the resulting artifact below the "What gets dumped on a crash" section.

## 1.7. When you're done

Once the artifacts land, embed them in the target sections named above
and (optionally) delete this file. The CHANGELOG entry for the
docs-completion can be a trailing `docs:` commit.
