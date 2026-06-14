# aws-tui

Sleek macOS-tailored TUI for AWS and S3-compatible services. Powered by [Textual](https://textual.textualize.io/) and [VMx](https://github.com/thekaveh/VMx) MVVM.

> **Status: pre-release (v0.0.1).** Bootstrap milestone only. See [the design spec](docs/superpowers/specs/2026-06-13-aws-tui-design.md) for the full v0.1.0 plan.

## Features (v0.1.0 target)

- Norton-Commander–style dual pane: S3 ↔ local FS, copy/move/rename/delete across panes.
- First-class S3-compatible support: MinIO, Cloudflare R2, Backblaze B2, Wasabi — same code path.
- Auto-discovers AWS profiles + silently uses cached SSO tokens.
- Four built-in themes (Carbon default, Voidline, Lattice, Amber CRT) plus user `.tcss` overrides.
- Fully customizable keymap.
- Crash-recovery transfer journal — resume multi-GB uploads after restart.

## Install

> **PyPI release is blocked** on VMx publishing to PyPI. Until then:

```bash
pipx install git+https://github.com/thekaveh/aws-tui.git
```

## Quickstart

```bash
aws-tui                       # launches with the default connection
```

If you've run `aws sso login --profile <name>` recently, aws-tui picks up the cached token silently. Otherwise, press `a` in the connection picker (`:` `connection switch`) to authenticate.

## Documentation

- [Architecture](docs/architecture.md)
- [Keybindings](docs/keybindings.md)
- [Theming](docs/theming.md)
- [Connections (AWS profiles + S3-compatible)](docs/connections.md)
- [Adding a new service (for contributors)](docs/adding-a-service.md)
- [Full design spec](docs/superpowers/specs/2026-06-13-aws-tui-design.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). License: [MIT](LICENSE).
