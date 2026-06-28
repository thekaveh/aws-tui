# Recording TODO

> Items the maintainer (i.e. **you**) needs to record manually before
> the v0.8.0 docs feel done. A subagent cannot drive a real terminal
> to produce asciinema casts or PNG screenshots, so this file is a
> hand-off list. Each item lists where it lands in the docs and the
> rough recipe.

Place finished artifacts under `docs/assets/` (create if missing) and
swap the matching `<!-- screenshot: TODO ... -->` comment for an
embed:

- For `.cast` files: `[![asciicast](https://asciinema.org/a/<id>.svg)](https://asciinema.org/a/<id>)`
- For PNG: `![<alt>](assets/<file>.png)`

## 1. Quickstart launch (`README.md` hero)

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

Replace the README's first `<!-- screenshot: TODO ... -->` comment.

## 2. First-run modal (`README.md` Quickstart section)

Format: PNG screenshot at 120×40.

Recipe:

```bash
# Force the first-run path with empty stubs:
mv ~/.config/aws-tui ~/.config/aws-tui.bak 2>/dev/null
HOME=/tmp/aws-tui-firstrun aws-tui &
# take a screenshot of the modal (macOS: Cmd+Shift+4, drag the
# terminal window). Save as docs/assets/first-run-voidline.png.
# Restore:
mv ~/.config/aws-tui.bak ~/.config/aws-tui 2>/dev/null
```

Or set the theme to `voidline` first via `theme = "voidline"` in
the config of the temp HOME for a more striking image.

## 3. S3 → local copy walkthrough (cookbook: connect to MinIO)

Format: asciinema, 60 seconds, 120×40.

Recipe:

```bash
asciinema rec -t "aws-tui: MinIO -> local copy" \
    docs/assets/minio-copy.cast
# inside:
aws-tui
# : connection switch -> minio-local
# navigate into a bucket
# v (multi-select), space-space on two files
# c (copy)
# confirm
# t (transfers tray) shows progress
# q
```

Embed below the "Use it" step of the MinIO recipe in
[cookbook.md](cookbook.md#1-connect-to-a-local-minio).

## 4. Theme switch (cookbook: switch theme)

Format: asciinema, 20 seconds, 120×40.

Recipe:

```bash
asciinema rec -t "aws-tui: theme switch" \
    docs/assets/theme-switch.cast
# inside:
aws-tui
# :
# theme switch -> voidline -> Enter
# pause 2 seconds to admire
# :
# theme switch -> amber -> Enter
# pause 2 seconds
# q
```

Embed below the "One-off (session-only)" step of the theme recipe.

## 5. Crash-recovery flow (cookbook: resume after a crash)

Format: asciinema, 60 seconds, 120×40.

Recipe:

```bash
# Step 1: seed a fake unfinished journal:
mkdir -p ~/.cache/aws-tui/transfers
cat > ~/.cache/aws-tui/transfers/seedabc.jsonl <<'EOF'
{"kind":"begin","transfer_id":"seedabc","source_uri":"local:///tmp/api.json","destination_uri":"s3://bucket/api.json","bytes_total":4200000,"upload_id":"mpu-zzz","ts":"2026-06-13T23:45:11Z"}
{"kind":"part","part_index":1,"etag":"\"a\"","bytes_written":2097152,"ts":"2026-06-13T23:45:13Z"}
EOF

asciinema rec -t "aws-tui: resume after crash" \
    docs/assets/resume.cast
# inside:
aws-tui
# observe: resume modal pops up listing the seeded transfer
# press k (keep for later) or a (abort all) — either works for the demo
# q
```

Cleanup after recording: `rm ~/.cache/aws-tui/transfers/seedabc.jsonl`.

Embed below "What happens on next launch" in the resume recipe.

## 6. Crash modal (cookbook: resume after a crash, second half)

Format: PNG screenshot.

Recipe:

There's no built-in way to trigger an unhandled exception
short of editing the code, so the recommended approach is to
temporarily add a `raise TypeError("demo")` inside e.g.
`pane.action_open()`, launch, trigger the action, and screenshot the
crash modal. Revert the change before committing.

Save as `docs/assets/crash-modal-carbon.png`. Embed below the "What
gets dumped on a crash" section.

## 7. When you're done

Once the artifacts land, update the `<!-- screenshot: TODO ... -->`
markers in `README.md` and `docs/cookbook.md` and (optionally) delete
this file. The CHANGELOG entry for the docs-completion can be a
trailing `docs:` commit.
