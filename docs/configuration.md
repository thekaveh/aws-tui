# Configuration Reference

> Where aws-tui keeps its files, every environment variable it reads, and
> the localization position. The tables are the same ones the repository
> README carries; this page is what the published surfaces serve.

## 1. File locations

`<config-dir>` and `<cache-dir>` are platform-specific; see
[Supported Platforms](platforms.md#1-quick-reference) for exact
macOS, Linux, and Windows paths. Existing legacy XDG directories are
preserved when present.

| Path | Contents |
|---|---|
| `<config-dir>/config.toml` | Connections + defaults + keybindings |
| `<config-dir>/theme.tcss` | Optional `.tcss` overlay over the active theme |
| `<config-dir>/themes/<name>.tcss` | Optional full custom themes |
| `<cache-dir>/log/aws-tui.log` | JSON-lines log (rotated 5 MiB × 5) |
| `<cache-dir>/transfers/<id>.jsonl` | Per-transfer interrupted-operation diagnostics |
| `<cache-dir>/crash/<ts>.txt` | Full traceback + log/action tail per crash |

## 2. Environment variables

| Variable | Default | Effect |
|---|---|---|
| `AWS_DEFAULT_PROFILE` | unset | Preferred AWS profile at launch when `[defaults].connection` is unset. Takes precedence over `AWS_PROFILE`. |
| `AWS_PROFILE` | unset | AWS profile fallback after `[defaults].connection` and `AWS_DEFAULT_PROFILE`, before the first connection in resolver order (explicit `[connections.*]` entries precede auto-discovered profiles). |
| `AWS_DEFAULT_REGION` | unset | Region fallback after an explicit connection region and the selected AWS profile's configured region, before `us-east-1`. |
| `AWS_CONFIG_FILE` | `~/.aws/config` | Overrides the shared AWS config path used for profile discovery. `~` and environment variables in the value are expanded. |
| `AWS_SHARED_CREDENTIALS_FILE` | `~/.aws/credentials` | Overrides the shared AWS credentials path used for profile discovery. `~` and environment variables in the value are expanded. |
| `AWS_TUI_DEMO` | unset | Truthy values `1`, `true`, and `yes` launch demo mode with seeded in-memory data. Equivalent to `aws-tui --demo`. |
| `${PREFIX}_ACCESS_KEY_ID` / `${PREFIX}_SECRET_ACCESS_KEY` / optional `${PREFIX}_SESSION_TOKEN` | per-connection | Read by `ConnectionResolver` when a `[connections.<name>]` entry in `config.toml` sets `credentials = "env:PREFIX_"`. See [Connections](connections.md) for the full pattern. |
| `XDG_CONFIG_HOME` | per-OS default | Linux: used by `platformdirs` when no legacy `~/.config/aws-tui` directory already exists. macOS and Windows use the platform-native location regardless. |
| `XDG_CACHE_HOME` | per-OS default | Linux: used by `platformdirs` when no legacy `~/.cache/aws-tui` directory already exists. macOS and Windows use the platform-native location regardless. |
| `AWS_TUI_TRANSFER_LINGER` | `3.0` | Seconds a finished transfer's row stays visible in the transfers overlay before it fades. Test-only knob — short values make `pytest` runs faster. |

aws-tui does not launch AWS CLI SSO setup; run `aws sso login --profile
<name>` in a terminal when prompted. The app does not read `$PAGER` or
`$EDITOR` in v0.8.x. The Quick Look full-file `$PAGER` shell-out is spec'd
but not yet wired (see the
`Deferred / v0.9 roadmap` block in the `[0.8.0]` section of
`CHANGELOG.md`).

## 3. Localization

aws-tui is English-only in v0.8.x. User-facing strings are intentionally
hardcoded until a localization pass introduces translation bundles and
locale-aware formatting.
