# aws-tui

Sleek macOS-tailored TUI for AWS and S3-compatible services. Powered by
[Textual](https://textual.textualize.io/) and the
[VMx](https://github.com/thekaveh/VMx) MVVM framework.

> **Status: v0.7.0** — feature-complete pre-PyPI release. All five
> milestones (M0 scaffold ▸ M6 polish) shipped. See
> [the design spec](docs/superpowers/specs/2026-06-13-aws-tui-design.md)
> for the full plan and `CHANGELOG.md` for the per-milestone delta.

<!-- screenshot: TODO - replace with an asciinema cast of `aws-tui` cold launch on Carbon theme; see docs/recording-todo.md -->

## Features

- **Norton-Commander–style dual pane.** S3 (or any S3-compatible bucket)
  on one side, your local filesystem on the other. Copy, move, rename,
  delete across panes with `c`, `m`, `r`, `d` (multi-select with `v` and
  `Space`).
- **First-class S3-compatible support.** MinIO, Cloudflare R2,
  Backblaze B2, Wasabi, Ceph, SeaweedFS — same code path as native
  AWS. Path-style addressing toggle and per-vendor docs.
- **Silent SSO.** Auto-discovers every AWS profile from
  `~/.aws/{config,credentials}` and cheaply probes the SSO token cache
  on launch (one `stat`, one ~1 KB JSON read, sub-millisecond).
- **Crash-recovery transfer journal.** Multi-GB uploads survive a
  restart: each completed multipart part appends a line to
  `~/.cache/aws-tui/transfers/<id>.jsonl`; on relaunch you get a
  resume/abort/keep modal.
- **Crash modal.** Unhandled exceptions write a dump to
  `~/.cache/aws-tui/crash/<ts>.txt` (traceback, last 1000 log lines,
  last 100 user actions) and show a recovery modal with view/continue/quit.
- **Four built-in themes.** Carbon (default), Voidline (neon),
  Lattice (mint), Amber CRT (retro). User overrides via
  `~/.config/aws-tui/theme.tcss` or full `.tcss` themes under
  `~/.config/aws-tui/themes/`.
- **Fully customizable keymap.** Action ↔ keystroke is config-driven —
  rebind anything in `[keybindings]` without touching code.
- **Streaming Quick Look.** `Space` on a file streams the first 64 KB
  with a syntax tint; `v` shells out to `$PAGER` for the full file
  (background-downloads with toast progress).
- **Command palette.** `:` or `Ctrl+K`. Fuzzy-filterable list of every
  action — including dynamic ones like `connection switch <name>` and
  `theme switch <name>`.
- **Strict layered architecture.** View ▸ ViewModel ▸ Service ▸ Domain
  ▸ Infra; enforced by `ruff` import rules + `scripts/check-layers.sh`.
  Mypy strict-clean. 478 tests across unit / snapshot / e2e tiers + 9
  integration tests against MinIO.

## Install

> **PyPI release is blocked** on VMx publishing to PyPI. Until then,
> install from Git:

```bash
pipx install git+https://github.com/thekaveh/aws-tui.git
```

For development:

```bash
git clone https://github.com/thekaveh/aws-tui.git
cd aws-tui
uv sync --dev
uv run aws-tui
```

Requirements: Python 3.11 / 3.12 / 3.13. macOS-tailored but Linux
works fine.

## Quickstart

```bash
aws-tui                       # launches with the default connection
```

If you've run `aws sso login --profile <name>` recently, aws-tui picks
up the cached token silently (no network round-trip just to render the
UI). Otherwise the picker shows the connection in `login needed`
state — press `a` to authenticate.

### First-time launch

If you have **no** `[connections.*]` in `~/.config/aws-tui/config.toml`
**and** `~/.aws/{config,credentials}` is empty, you'll see a welcome
modal (per spec §6.4 Flow 5):

```
welcome to aws-tui
no AWS or S3-compatible connections configured.
  add aws profile  (runs 'aws configure sso' in your terminal)
  add s3-compatible (in-TUI form for MinIO, R2, etc.)
  skip for now (you can add later via : connection add)
```

Pick one to get going.

<!-- screenshot: TODO - capture the first-run modal on Voidline; see docs/recording-todo.md -->

## Documentation

Numbered hierarchically per the project's documentation hierarchy mandate.

1. **User-facing**
   1. [Connections (AWS profiles + S3-compatible)](docs/connections.md) — configure connections; how the credential chain resolves; vendor quirks for MinIO / R2 / B2 / Wasabi.
   2. [Keybindings](docs/keybindings.md) — full key map; how to customize bindings via `~/.config/aws-tui/config.toml`.
   3. [Theming](docs/theming.md) — built-in palettes, runtime theme switch, `.tcss` overlay and custom-theme drop-ins.
   4. [Cookbook (common recipes)](docs/cookbook.md) — step-by-step walkthroughs (connect to MinIO, switch theme on the fly, customize bindings, resume after a crash).
2. **Contributor-facing**
   1. [Architecture](docs/architecture.md) — five-layer model + composition root + lifecycle + messaging primer.
   2. [Adding a new service](docs/adding-a-service.md) — the `Service` protocol + per-layer wiring.
   3. [VMx Python cheatsheet](docs/superpowers/notes/2026-06-14-vmx-python-cheatsheet.md) — facade pattern, message-protocol shape, lifecycle gotchas.
3. **Spec + plans**
   1. [Design spec (v0.1.0)](docs/superpowers/specs/2026-06-13-aws-tui-design.md) — authoritative source for behavior + acceptance.
   2. [Implementation plans (M0–M6)](docs/superpowers/plans/) — per-milestone breakdown, end-of-pass revisions captured in-tree.
4. **Maintainer-facing**
   1. [Recording todo](docs/recording-todo.md) — asciinema + screenshot artifacts the maintainer still needs to record manually.

## File locations

| Path | Contents |
|---|---|
| `~/.config/aws-tui/config.toml` | Connections + defaults + keybindings |
| `~/.config/aws-tui/theme.tcss` | Optional `.tcss` overlay over the active theme |
| `~/.config/aws-tui/themes/<name>.tcss` | Optional full custom themes |
| `~/.cache/aws-tui/log/aws-tui.log` | JSON-lines log (rotated 5 MB × 5) |
| `~/.cache/aws-tui/transfers/<id>.jsonl` | Per-transfer crash-recovery journal |
| `~/.cache/aws-tui/crash/<ts>.txt` | Full traceback + log/action tail per crash |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). License:
[MIT](LICENSE).
