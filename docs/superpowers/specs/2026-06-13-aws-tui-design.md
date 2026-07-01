# aws-tui — design spec

| | |
|---|---|
| Date | 2026-06-13 |
| Author | Kaveh Razavi (brainstorming with Claude) |
| Status | Shipped through M6 / v0.7.0; post-tag train of PRs on `main` toward v0.8 |
| Target version | v0.1.0 (this doc); see CHANGELOG.md for v0.7.0+ post-ship changes |
| Repo | `thekaveh/aws-tui` |
| Companion | VMx (`thekaveh/VMx`) — PyPI dependency `vmx>=2.6.0,<3.0.0` |

> **Amendments since v0.1.0** — this spec is preserved as the canonical
> v0.1.0 design and is mostly accurate, but several sections have been
> superseded or extended by post-ship work. Treat the following as
> non-authoritative and consult the linked source instead:
>
> - **§3 file map** — `vendor/vmx` submodule wiring was removed when VMx
>   moved to PyPI (CHANGELOG `[Unreleased]`); `ui/widgets/services_menu.py`
>   and `vm/services_menu_vm.py` were renamed to `nav_menu.py` /
>   `nav_menu_vm.py` in PR #54; `vm/settings/` (`SettingsVM`,
>   `S3ConnectionsVM`) and the in-app Settings page were added in PR
>   #52/#54.
> - **§5.1 VM tree** — `ServicesMenuVM` → `NavMenuVM` (with the legacy
>   `RootVM.services_menu` property preserved as an alias); the
>   `SettingsVM` lives in `vm/settings/` and is built per-mount, not as a
>   singleton (PR #56). See
>   [`2026-06-20-settings-as-first-class-nav-page-design.md` §13](2026-06-20-settings-as-first-class-nav-page-design.md)
>   for the full amendment.
> - **§5.4 lifecycle + §6.4 Flow 6** — graceful shutdown drain
>   (`asyncio.wait(in_flight_tasks, timeout=5)`) is partially wired:
>   `AwsTuiApp.action_quit` is overridden to call `_aws_tui_shutdown`
>   (which awaits `aws_session.aclose_all_clients`, flushes the log
>   sink, disposes overlay VMs), but the full per-task drain step
>   described in §5.4 is deferred to v0.8.
> - **§4.2 input router** — the keymap→action indirection
>   (`BindingResolver`) is built but unwired; `BINDINGS` on
>   `AwsTuiApp` is the active source. Tracked in `CHANGELOG.md`
>   `[Unreleased]` ▸ `Deferred / v0.8 roadmap`.
> - **§4.2 keymap table** — the table itself lists several defaults
>   that no `BINDINGS` entry currently routes: `Ctrl+K` /
>   `Ctrl+P` / `/` / `n` / `v` / `a`, plus the original `m` = move
>   mapping (currently reserved/unbound while move remains deferred)
>   and lowercase `s` = services rail mapping (the live app uses
>   uppercase `S` for source/app switching; see
>   [`docs/keybindings.md`](../../keybindings.md) for the live key
>   map). New since v0.1.0: `,` opens Settings
>   (PR #54). These entries become active when the BindingResolver
>   lands; consult `keybindings.md` for the authoritative current
>   set.
> - **§6.5 example** — the illustrative `class S3Service: def supports(self, conn): return True` collapses the kind filter that
>   the shipping `S3Service.supports` actually applies
>   (`connection.kind in {"aws", "s3-compatible"}`). The §6.5 prose
>   about the menu collapsing to S3 still reads correctly under the
>   real filter.

---

## 0. Summary

aws-tui is a sleek, macOS-tailored terminal UI for the AWS CLI's domain — a dual-pane Norton-Commander–style file manager for S3 in v1, with a service-plugin spine so adding EC2 / IAM / Lambda / DynamoDB / CloudWatch / ECS later is additive. Authentication is fully delegated to the AWS CLI and boto3 credential chain — if a user has run `aws sso login`, aws-tui picks it up silently. Both panes consume a `FileSystemProvider` interface, so the S3↔Local split is symmetric. The same code paths and UI also serve S3-compatible backends (MinIO, Cloudflare R2, Backblaze B2, Wasabi). The UI layer is built on Textual with a fully MVVM architecture provided by VMx, ten built-in themes (Carbon default, Voidline, Lattice, Amber CRT, Solarized Light, GitHub Light, One Light, Nord, Dracula, Gruvbox Dark), and full user theme override via Textual `.tcss` files.

## 1. Goals & non-goals

### 1.1 Goals (v0.1.0)

- **Sleek, modern, macOS-tailored TUI** for browsing and managing S3 (and S3-compatible) storage.
- **Norton-Commander-style dual pane** — Tab to switch focus, single-letter actions, contextual hint legend at the bottom.
- **Full MVVM via VMx** in the UI layer — Textual widgets bind to view-models declaratively; viewmodels never import Textual.
- **Service-plugin spine** — adding a new AWS service later is a new folder under `services/<name>/` + one line in the registry, no edits anywhere else.
- **First-class S3-compatible support** — MinIO, R2, B2, Wasabi all work day one through the same code path.
- **Automagic auth** — cached SSO tokens are picked up silently; AWS profiles auto-discover on every launch.
- **Real theming** — ten built-in themes, the default theme is itself configurable, and users can drop `.tcss` overrides.
- **Distribution via `pipx`** day one (`pipx install git+https://github.com/thekaveh/aws-tui`); PyPI once VMx publishes.

### 1.2 Non-goals (explicit, so they don't sneak in)

- Web UI, browser tunnel, or remote-display rendering.
- Cost / billing visualizations.
- Resource creation wizards for non-S3 services (EC2 etc. — large, separate designs).
- aws-tui as a Python library (`import aws_tui` is not a sanctioned API).
- Offline mode (no AWS reachable).
- Multi-region browsing within a single connection (v1.3 feature).
- Drag-and-drop with the OS clipboard.
- Bandwidth throttling.
- Bundling into a standalone binary (PyInstaller / Briefcase) — breaks `.tcss` overrides and bloats the install.

### 1.3 Platforms

- **macOS** — primary, tier-1 in CI, tested on every PR.
- **Linux** — tier-2 best-effort, runs the heavy integration tier in CI (Docker is first-class there).
- **Windows** — non-blocking smoke until v2.0.

---

## 2. Architecture

### 2.1 Five-layer model

Strict one-way dependencies. Each layer only knows the layer beneath it.

```
┌────────────────────────────────────────────────────────────────────┐
│  View Layer  (Textual widgets, .tcss themes)                       │
│                                                                    │
│    AwsTuiApp ── NavMenu  ·  DualPane                               │
│              ·  CommandPalette  ·  HintLegend  ·  StatusBar        │
│              ·  QuickLookModal  ·  ConfirmModal  ·  ToastStack     │
│              ·  SettingsView                                       │
└──────────────────────────────┬─────────────────────────────────────┘
                               │  reactive bindings, RelayCommands
┌──────────────────────────────▼─────────────────────────────────────┐
│  ViewModel Layer  (VMx hierarchy)                                  │
│                                                                    │
│    RootVM : AggregateVM3                                           │
│     ├── NavMenuVM        : CompositeVM<ServiceItemVM>              │
│     │                       (renamed from ServicesMenuVM, PR #54;  │
│     │                       RootVM.services_menu alias kept)       │
│     ├── ContentHostVM    : ComponentVM (swaps per-service content) │
│     └── ChromeVM         : AggregateVM3 (hint, status, toasts)     │
│                                                                    │
│    S3 service content:                                             │
│      S3Content  : AggregateVM2<DualPaneVM, TransfersVM>            │
│      DualPaneVM : AggregateVM2<PaneVM, PaneVM>                     │
│      PaneVM     : CompositeVM<EntryVM>  + ISelectable +            │
│                   IFilterable + IPageable + ExpandableState        │
│      EntryVM    : ComponentVM<FileEntry>                           │
└──────────────────────────────┬─────────────────────────────────────┘
                               │  Service protocol, registry lookup
┌──────────────────────────────▼─────────────────────────────────────┐
│  Service-Plugin Spine                                              │
│                                                                    │
│    services/__init__.py — ServiceRegistry                          │
│      └─ register("s3", S3Service())                                │
│                                                                    │
│    Service protocol:                                               │
│      id · label · icon · build_vm(conn) · build_view(vm) ·         │
│      supports(connection) -> bool                                  │
│                                                                    │
│    Future (v0.2+): EC2Service, IAMService, LambdaService, ...      │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│  Domain Operations  (Norton-Commander unifier)                     │
│                                                                    │
│    FileSystemProvider  (Protocol)                                  │
│      list / stat / mkdir / delete / rename                         │
│      read_stream / write_stream                                    │
│      (cross-provider copy/move lives in CrossFsCopy /              │
│       CrossFsMove, not on the protocol itself)                     │
│                                                                    │
│    ┌──────────────────┐    ┌──────────────────┐                    │
│    │  LocalFS         │    │  S3FS            │                    │
│    │  (anyio +        │    │  (aioboto3)      │                    │
│    │   aiofiles)      │    │                  │                    │
│    └──────────────────┘    └──────────────────┘                    │
│                                                                    │
│    CrossFsCopy / CrossFsMove — stream A → B via async chunks       │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│  Infrastructure / Boundary                                         │
│                                                                    │
│    AwsSession          · profile/region picker, sso-login shellout │
│    ConnectionResolver  · ~/.aws/config + config.toml union         │
│    ConfigStore         · ~/.config/aws-tui/config.toml             │
│    ThemeStore          · built-ins + ~/.config/aws-tui/theme.tcss  │
│    KeymapStore         · action → key sequence indirection         │
│    LogSink             · structured logs → ~/.cache/aws-tui/log/   │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 Key invariants

- **View → VM → Service → Domain → Infra.** No upward calls. View never imports `boto3`. VM never imports Textual widgets. Enforced by `ruff` `flake8-tidy-imports` rules per-folder.
- **Both panes are the same widget** with a different `FileSystemProvider`. The S3↔Local asymmetry only exists in the providers, not in the UI or the dual-pane VM.
- **Every AWS call is async** via `aioboto3`. The UI never blocks. Long ops (multipart upload, recursive delete) report progress through VMx `PropertyChangedMessage` on a `TransferVM`.
- **VMx lifecycle drives mounting.** Switching service in the left menu triggers `ContentHostVM.set_content()` which `dispose()`s the previous content tree and `construct()`s the new one. No orphaned aioboto3 clients, no leaked subscriptions.
- **`reconstruct()` is not used in v1.** Every change is either a child-swap (`dispose` + `construct`) or a no-op. `reconstruct()` is reserved for a future "reset workspace" command.

---

## 3. Repo layout

```
aws-tui/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                  # matrix from §8; ruff + mypy + pytest
│   │   ├── release.yml             # deferred — see §9.5
│   │   ├── snapshot-drift.yml      # deferred — see §9.5
│   │   ├── submodule-bump.yml      # deferred — see §9.5
│   │   └── codeql.yml              # deferred — see §9.5
│   ├── ISSUE_TEMPLATE/
│   ├── pull_request_template.md
│   └── dependabot.yml
├── .gitignore  .gitattributes  .editorconfig
├── .pre-commit-config.yaml         # ruff, mypy, taplo, end-of-file-fixer
│
├── docs/
│   ├── architecture.md             # human-readable mirror of §2
│   ├── keybindings.md              # default keymap + customization
│   ├── theming.md                  # .tcss override guide + palette specs
│   ├── connections.md              # AWS profiles + S3-compatible vendors
│   ├── adding-a-service.md         # plugin author guide
│   └── superpowers/specs/
│       └── 2026-06-13-aws-tui-design.md  # ← this document
│
├── src/
│   └── aws_tui/
│       ├── __init__.py
│       ├── __main__.py             # `python -m aws_tui` entrypoint
│       ├── app.py                  # Textual App; builds RootVM, mounts AppScreen
│       ├── version.py
│       │
│       ├── infra/                  # boundary — only layer that touches OS/AWS
│       │   ├── aws_session.py      # boto3 + aioboto3 session factory + SSO probe
│       │   ├── connection_resolver.py
│       │   ├── config_store.py     # ~/.config/aws-tui/config.toml (tomllib)
│       │   ├── theme_store.py      # built-in .tcss bundle + user override
│       │   ├── keymap_store.py     # action → key sequence resolution
│       │   ├── log_sink.py         # structured logs → ~/.cache/aws-tui/log/
│       │   └── keychain.py         # macOS keychain via Python `keyring`
│       │
│       ├── domain/                 # Norton-Commander unifier
│       │   ├── filesystem.py       # Protocol + FileEntry, PathRef, TransferProgress
│       │   ├── local_fs.py         # LocalFS provider
│       │   ├── s3_fs.py            # S3FS provider
│       │   ├── cross_fs.py         # CrossFsCopy/Move stream A → B
│       │   └── transfer_journal.py # ~/.cache/aws-tui/transfers/*.jsonl
│       │
│       ├── vm/                     # ViewModel layer (no Textual import)
│       │   ├── root_vm.py
│       │   ├── services_menu_vm.py
│       │   ├── chrome/
│       │   │   ├── chrome_vm.py
│       │   │   ├── command_palette_vm.py
│       │   │   ├── hint_legend_vm.py
│       │   │   ├── status_bar_vm.py
│       │   │   ├── confirm_vm.py
│       │   │   ├── toast_vm.py
│       │   │   └── quick_look_vm.py
│       │   └── file_manager/       # reusable across storage-like services
│       │       ├── dual_pane_vm.py
│       │       ├── pane_vm.py
│       │       └── entry_vm.py
│       │
│       ├── services/               # plugin spine
│       │   ├── __init__.py         # re-exports Service / ServiceDescriptor /
│       │   │                       # ServiceRegistry from vm/services_protocol.py
│       │   └── s3/
│       │       └── service.py      # S3Service composes DualPaneVM[LocalFS, S3FS]
│       │
│       └── ui/                     # Textual widget layer (no boto3 import)
│           ├── widgets/
│           │   ├── services_menu.py
│           │   ├── dual_pane.py    │  pane.py
│           │   ├── command_palette.py
│           │   ├── hint_legend.py  │  status_bar.py
│           │   ├── quick_look.py   │  confirm_modal.py  │  toast.py
│           │   └── transfers_overlay.py
│           ├── bindings.py         # default action → key map
│           ├── actions.py          # action registry (string id → callable on VM)
│           └── themes/
│               ├── carbon.tcss      # default
│               ├── voidline.tcss
│               ├── lattice.tcss
│               └── amber.tcss
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── vm/    ...              # VM behavior, fake providers, no I/O
│   │   ├── domain/  ...            # FS providers with tmp_path + InMemoryFS
│   │   └── infra/  ...             # config/theme/keymap stores
│   ├── integration/
│   │   ├── test_s3_provider.py     # moto + testcontainer MinIO
│   │   └── test_cross_fs.py        # roundtrip + integrity
│   └── snapshot/
│       └── snapshots/<theme>/      # pytest-textual-snapshot goldens
│
├── scripts/
│   ├── bootstrap.sh                # uv sync + pre-commit install
│   └── dev.sh                      # textual run --dev src/aws_tui/app.py
│
├── pyproject.toml                  # PEP 621 + hatchling, src-layout, py>=3.11
├── uv.lock
├── README.md   CHANGELOG.md   CONTRIBUTING.md   CODE_OF_CONDUCT.md   SECURITY.md
└── LICENSE                         # MIT (matches VMx)
```

### 3.1 Layer enforcement

`ruff` `flake8-tidy-imports` rules per folder:

| Folder | Cannot import from |
|---|---|
| `vm/` | `ui/`, `boto3`, `aioboto3`, `botocore`, `textual` |
| `ui/` | `boto3`, `aioboto3`, `botocore`, `infra/aws_session`, `infra/connection_resolver` |
| `domain/` | `vm/`, `ui/`, `services/` |
| `infra/` | `vm/`, `ui/`, `services/`, `domain/` |
| `services/<name>/` | other `services/<name>/` modules |

---

## 4. UI design

### 4.1 Top-level screen anatomy

```
+--- top status strip (single row) ---------------------------------+
| aws.tui . conn <name> . region <r> . sso ok . transfers idle      |
+-- SERVICES ---+-- left pane breadcrumb --+-- right pane breadcrumb +
| > S3          | S3 / bucket / prefix     | local / ~/Downloads/   |
|   EC2 (auto-  | name  size  modified     | name  size  modified   |
|   filtered    | ...                      | ...                    |
|   per conn)   | [highlighted row]        |                        |
|               | ...                      |                        |
|               | summary footer           | summary footer         |
+---------------+--------------------------+------------------------+
| Enter open  Space peek  c copy  m move  d del  n new  : cmd  ? help |
+-------------------------------------------------------------------+
```

### 4.2 Keymap (macOS-tailored, customizable)

Default bindings — `⌘` is not used because terminals intercept it.

| Action | Default key | Notes |
|---|---|---|
| Move cursor | `↑ ↓` or `j k` | |
| Descend | `Enter` | Folder, bucket, or breadcrumb segment |
| Ascend | `Backspace` or `←` | Parent |
| Switch pane focus | `Tab` / `Shift+Tab` | |
| Quick Look | `Space` | Streams first 64 KB; `v` opens `$PAGER` for full file |
| Filter within pane | `/` | Incremental fuzzy filter (uses `SearchableState`) |
| Fuzzy find paths | `Ctrl+P` | Across current pane's tree |
| Command palette | `:` or `Ctrl+K` | Every action lives here, including `theme switch`, `connection switch` |
| Enter multi-select mode | `v` | Vim-style visual mode; `Esc` exits |
| Toggle selection on cursor row | `Space` (multi-select mode only) | In normal mode, Space is Quick Look |
| Select all | `a` | Within current pane |
| Copy selected | `c` | Selected → other pane |
| Move / rename | `m` | Prompt for new name; `m` on multi-selection moves |
| Delete | `d` | Always confirms |
| New folder / bucket | `n` | Provider-aware |
| Refresh pane | `r` | Re-runs `provider.list(path)` |
| Show keymap | `?` | Full overlay reference |
| Cancel / close overlay | `Esc` | |
| Authenticate (when toast is up) | `a` | Shell-out to `aws sso login --profile <name>` |
| Open theme picker (modal) | `t` | (post-v0.7.0: was `t = toggle transfers tray` in v0.1.0 spec; transfers now render as a permanent overlay so the `t` slot was reused for the theme picker) |
| Cycle to next theme (no modal) | `Shift+T` (`T`) | |
| Swap focused pane source (S3 ↔ local) | `Shift+S` (`S`) | |
| Toggle services rail | `s` | Also toggles on a mouse click on the rail |
| Extend selection one row | `Shift+↑` / `Shift+↓` | Marks current row + advances cursor |
| Quit | `q` or `Ctrl+C` | Triggers graceful shutdown sequence |

All bindings are configurable via `~/.config/aws-tui/config.toml`:

```toml
[keybindings]
"pane.copy" = "c"
"pane.move" = "m"
"app.command_palette" = ["Ctrl+K", ":"]
"app.quit" = ["q", "Ctrl+C"]
```

The input router goes through `ui/actions.py` (action registry) → `ui/bindings.py` (action ↔ key) → VM command. The indirection layer is what makes keymap customization free.

### 4.3 Contextual hint legend

A single dim row at the bottom of the screen. Its content is a `DerivedProperty<list[Action]>` over `root.focused_vm` — when focus moves to a different widget, the legend swaps automatically. Format: `<key in accent> <label in dim>` separated by generous spacing. Maximum 7-8 actions visible; the rest are reachable via `:` or `?`.

### 4.4 Modals

| Modal | Trigger | Behaviour |
|---|---|---|
| Command palette | `:` or `Ctrl+K` | Fuzzy filter; Enter runs the selected action |
| Confirm | destructive ops (delete, overwrite, switch-with-active-transfers) | `Enter` runs the dangerous default, `Esc` cancels |
| Quick Look | `Space` on a file | Streams first 64 KB with syntax tint; `v` opens `$PAGER` for full file (background-downloads with toast) |
| Connection switcher | `:` `connection switch` | Lists AWS profiles + S3-compatible connections with live token status |
| Conflict (copy/move) | destination exists | `overwrite / skip / rename` with "apply to all" for batches |
| First-run | no connections found | Offers to import AWS profiles, add S3-compatible, or skip |
| Crash | unhandled exception | Trace summary + path to dump file + open-issue link |

### 4.5 Themes

Ten built-ins ship (four dark originals, three light themes, and three community palettes); default is **Carbon** but the default is itself configurable (`theme = voidline` in config). Users can drop `<config-dir>/theme.tcss` for fine-grained overrides on top of the active built-in, or full custom themes under `<config-dir>/themes/<name>.tcss` selectable like any built-in.

#### Carbon (default) — near-monochrome with ice-blue accent

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0d0e10` | Frame background |
| `bg-sel` | `#16252e` | Selected row tint |
| `rule-dim` | `#2a2d33` | Thin dividers |
| `text` | `#e6e8eb` | Primary values |
| `text-muted` | `#8a8e96` | Secondary values |
| `text-dim` | `#5e6470` | Labels |
| `accent` | `#6fb8ff` | Focused/actionable glyphs, keymap chips |
| `accent-soft` | `#cfe4ff` | Selected-row foreground |
| `accent-hot` | `#c9a0ff` | Command palette `:` glyph |
| `success` | `#5cd693` | SSO ok, transfer up arrow |
| `warning` | `#f0c674` | Auth-pending state |
| `danger` | `#ff6b7a` | Destructive op modal accents |

Carbon's discipline: **one accent color**, **three-tier text hierarchy** (primary/secondary/label), semantic colors reserved for narrow, meaningful uses (`success` only on auth/transfer status; `danger` only on "cannot be undone" affordances; `warning` only on numerics in Quick Look).

#### Voidline — neon cyan + magenta on near-black

| Token | Hex | Use |
|---|---|---|
| `bg` | `#050810` | Frame background |
| `border-accent` | `#00d4ff` | Heavy double-line border |
| `accent` | `#00d4ff` | Primary accent |
| `accent-hot` | `#ff3df8` | Secondary accent / cmd palette |
| `success` | `#00ff9c` | |
| `danger` | `#ff3b5c` | |
| `text` | `#d9e1ec` | |
| `text-dim` | `#4a5a6e` | |

Voidline uses heavy double-line borders (`╔══╗`), powerline-style hint chips with background fills, and (in the real TUI only) a braille sparkline in the status bar.

#### Lattice — mint-teal + lavender on deep teal

| Token | Hex | Use |
|---|---|---|
| `bg` | `#061418` | Frame background |
| `border-accent` | `#4ce0d2` | Rounded corners (`╭─╮`) |
| `accent` | `#4ce0d2` | Primary accent |
| `accent-hot` | `#c9b5ff` | Secondary accent / cmd palette |
| `text` | `#d4ebe9` | |
| `text-dim` | `#4a7080` | |

#### Amber CRT — retro phosphor monitor

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0f0a06` | Frame background |
| `border-accent` | `#ff9d3a` | Heavy single-line border (`┏━┓`) |
| `accent` | `#ff9d3a` | Primary accent (single-color theme) |
| `text` | `#f0d9b8` | |
| `text-dim` | `#7a5a3a` | |

Amber CRT is intentionally an opt-in alt — beautiful and distinctive, harder on the eyes for long sessions.

---

## 5. MVVM wiring with VMx

### 5.1 VM tree with primitives

```
RootVM                 : AggregateVM3<ServicesMenuVM, ContentHostVM, ChromeVM>
├─ ServicesMenuVM      : CompositeVM<ServiceItemVM>   + ISelectable
│  └─ ServiceItemVM    : ComponentVM<ServiceDescriptor>
├─ ContentHostVM       : ComponentVM   (swaps inner per active service)
│  └─ S3Content        : AggregateVM2<DualPaneVM, TransfersVM>      [v1]
│     ├─ DualPaneVM    : AggregateVM2<PaneVM, PaneVM>
│     │  ├─ PaneVM[L]  : CompositeVM<EntryVM> + ISelectable +
│     │  │              IFilterable + IPageable + ExpandableState
│     │  └─ PaneVM[R]  : CompositeVM<EntryVM> + (same caps)
│     └─ TransfersVM   : CompositeVM<TransferVM>
│        └─ TransferVM : ComponentVM<TransferState>
└─ ChromeVM            : AggregateVM3<HintLegendVM, StatusBarVM, ToastStackVM>
   ├─ HintLegendVM     : ComponentVM      (DerivedProperty over focused VM)
   ├─ StatusBarVM      : ComponentVM      (DerivedProperty over Connection + Auth + Transfers)
   └─ ToastStackVM     : CompositeVM<ToastVM>
      └─ ToastVM       : ComponentVM<ToastModel>

Overlays (lifetime managed by RootVM, not in main tree):
   CommandPaletteVM    : ComponentVM + IFilterable + SearchableState
   ConfirmationVM      : (VMx opt-in notifications sub-package; uses ConfirmHelper)
   QuickLookVM         : ComponentVM
```

### 5.2 Commands (`RelayCommand` with reactive `canExecute`)

| VM | Command | canExecute condition |
|---|---|---|
| DualPaneVM | `CopyAcrossPanesCmd` | source pane has selection AND dest provider supports `write_stream` |
| DualPaneVM | `MoveAcrossPanesCmd` | copy condition AND source provider supports `delete` |
| DualPaneVM | `SwitchFocusCmd` | always |
| PaneVM | `OpenEntryCmd` | cursor on folder or file |
| PaneVM | `DeleteSelectedCmd` | selection.count > 0 AND provider supports `delete` |
| PaneVM | `RenameCmd` | exactly one selected AND provider supports `rename` |
| PaneVM | `NewFolderCmd` | provider supports `mkdir` |
| PaneVM | `RefreshCmd` | always |
| PaneVM | `AscendCmd` | not at provider root |
| EntryVM | `ToggleSelectCmd` | always |
| EntryVM | `PreviewCmd` | entry is a file |
| ServicesMenuVM | `SwitchServiceCmd(id)` | id in registry AND `service.supports(currentConnection)` |
| CommandPaletteVM | `ExecuteSelectedCmd` | filtered list non-empty |
| RootVM | `SwitchConnectionCmd(name)` | name resolves to a Connection |

`canExecute` triggers are reactive observables — VMx wires them automatically; the View just binds.

### 5.3 Messages on the hub

| Message | Publisher | Subscribers |
|---|---|---|
| `PropertyChangedMessage` | every VM | binding layer, DerivedProperty graph |
| `ConstructionStatusChangedMessage` | every VM | RootVM (service-switch orchestration) |
| `CollectionChangedEvent` (`BatchUpdate`) | CompositeVMs | binding layer; `AutoConstructOnAdd = true` for child VMs |
| `ConnectionChangedMessage` (custom) | RootVM | all service VMs, StatusBarVM |
| `ThemeChangedMessage` (custom) | RootVM | View layer (re-applies `.tcss`) |
| `AuthExpiredMessage` (custom) | infra.AwsSession | ToastStackVM (soft toast: "press a to sso-login") |
| `TransferProgressMessage` (custom) | CrossFsCopy/Move workers | TransferVM, StatusBarVM |
| `KeymapChangedMessage` (custom) | infra.KeymapStore | HintLegendVM, View input router |

### 5.4 Lifecycle invariants

| Trigger | Primitive | Scope |
|---|---|---|
| Re-select same service | no-op | — |
| Switch to different service | child swap: `dispose` old + `construct` new | `ContentHostVM` subtree only |
| Switch connection | child swap | `ContentHostVM` subtree only |
| Theme switch | none (just `.tcss` reload) | View layer |
| Keymap change | none | View layer |
| App exit | `RootVM.dispose()` | full tree, preceded by async drain |

**`reconstruct()` is not in v1.** Reserved for a future "reset workspace" command (`: reset workspace`).

**App exit sequence**:

```python
async def shutdown():
    transfers_vm.cancel_all()
    await asyncio.wait(in_flight_tasks, timeout=5)  # graceful drain
    await aws_session.aclose_all_clients()
    config_store.flush()
    log_sink.flush()
    root_vm.dispose()                               # sync depth-first cascade
```

**Status (v0.7.x):** `transfers_vm.cancel_all()` and `aws_session.aclose_all_clients()` are wired; the `asyncio.wait(..., timeout=5)` graceful-drain step is **deferred** because it depends on the cancellation-propagation refactor (see `TransfersVM.cancel_all` known gap). Today shutdown cancels and disposes without an explicit drain window; in-flight transfers complete naturally after `dispose()`.

If async drain exceeds 5 s, we log a warning and proceed to `dispose()` anyway; in-memory state is released, the OS reaps any leaked socket.

### 5.5 Async + threading

- **One event loop** — Textual's asyncio loop is the only loop. No worker threads.
- **`aioboto3` is asyncio-native** — every AWS call is `await client.list_objects_v2(...)`.
- **VMx `RxDispatcher`** is configured with Textual's loop as both foreground and background scheduler.
- **Long ops use `asyncio.create_task()`** and report progress via `PropertyChangedMessage` on the spawning VM.
- **Cancellation** — every long task is owned by a VM. `VM.destruct()` cancels its tasks before tearing down.

### 5.6 State helpers (VMx 2.5)

| Helper | Used by | Computes |
|---|---|---|
| `DerivedProperty<T>` | StatusBarVM | `connection_label = derived(connection, auth_state, transfers.count)` |
| `DerivedProperty<T>` | HintLegendVM | `actions = derived(root.focused_vm)` — auto-swaps per focus |
| `DerivedProperty<T>` | PaneVM | `summary = derived(entries, selection)` — "5 obj · 1 selected · 4.2 M" |
| `ExpandableState` | PaneVM | breadcrumb path history (`ascend()` walks back) |
| `SearchableState` | CommandPaletteVM, PaneVM (when `/` pressed) | fuzzy-filter state, persists across re-renders |

### 5.7 Capability adoption

| VM | Capabilities |
|---|---|
| PaneVM | `ISelectable` (multi-select), `IFilterable`, `IPageable` (virtualized for 100k-object buckets) |
| ServicesMenuVM | `ISelectable` (single) |
| CommandPaletteVM | `IFilterable` |
| EntryVM | `ISelectable` |
| ConfirmationVM | VMx `ConfirmHelper` (opt-in notifications sub-package) |

### 5.8 View ↔ VM binding (Textual side)

Textual widgets get the VM injected via constructor; each widget subscribes to its VM's `PropertyChangedMessage` and re-renders the affected attribute (no full redraw). Inputs (keypresses, mouse) route through `ui/actions.py` → `ui/bindings.py` → VM command. The View layer NEVER imports `boto3`, `aioboto3`, or anything from `infra/`. Enforced by `ruff` import rules.

---

## 6. AWS auth & service lifecycle

### 6.1 Connection model

A `Connection` is the unit aws-tui authenticates as. Two kinds:

```toml
[connections.kaveh-dev]              # aws kind
kind = "aws"
profile = "kaveh-dev"                # name in ~/.aws/{config,credentials}
region = "us-east-1"                 # optional; falls back to profile region

[connections.minio-local]            # s3-compatible kind
kind = "s3-compatible"
endpoint_url = "http://localhost:9000"
region = "us-east-1"
credentials = "keychain:minio-local" # or env:PREFIX_*, aws-profile:name, static
force_path_style = true
verify_tls = true
```

### 6.2 Credential resolution

For `kind = "aws"`: standard boto3 chain (`boto3.Session(profile_name=profile)`) — env, shared credentials, SSO cache, EC2 IMDS, ECS task role. We add nothing.

For `kind = "s3-compatible"`, the `credentials` field is dispatched:

| Spec | Source |
|---|---|
| `keychain:<service>` | macOS Keychain via `keyring` |
| `env:PREFIX_*` | `${PREFIX_ACCESS_KEY_ID}` + `${PREFIX_SECRET_ACCESS_KEY}` |
| `aws-profile:<name>` | reuses an entry in `~/.aws/credentials` |
| `static` | from config file — startup warning + sticky toast |

### 6.3 Auto-discovery + SSO cache probe

`ConnectionResolver.list()` unions on **every launch** (not just first run):

1. `[connections.*]` entries in `~/.config/aws-tui/config.toml`
2. AWS profiles in `~/.aws/config` and `~/.aws/credentials` — auto-promoted to `kind = "aws"`, `profile = "<name>"`

Explicit entries win on name collision. Auto-discovered entries show an `(auto)` badge in the picker; `: connection materialize <name>` writes a real entry to config.toml.

For each AWS connection, `AwsSession.probe_token(conn)` performs a cheap freshness check **without calling AWS**:

- Resolve the SSO cache filename via `botocore.tokens.SSOTokenLoader`
- Read `expiresAt`, compare to `datetime.now(timezone.utc)` with 60-second skew buffer
- Return `connected | expired | missing`

Total cost: one `os.stat` + one ~1 KB JSON read. Sub-millisecond. All connections probed on launch — drives the picker's status column.

### 6.4 End-to-end flows

#### Flow 1 — Cold start, default connection, valid SSO token

```
App launch
  -> ConfigStore.load()
  -> ConnectionResolver.list()                    # union explicit + auto
  -> AwsSession.probe_token(default_connection)
        valid    -> mark "connected"
        expired  -> mark "login needed"
        missing  -> mark "no session"
  -> RootVM.construct(connection=default)
  -> ServiceRegistry.filter(kind=default.kind)
  -> ServicesMenu shows: S3 (only one in v1; later EC2/IAM/... if kind==aws)
  -> ContentHostVM.build_vm(s3, default)
  -> S3Content.PaneVM[L].provider = S3FS(default)
  -> S3Content.PaneVM[R].provider = LocalFS()
  -> StatusBarVM: "conn kaveh-dev (aws) . region us-east-1 . sso ok"
  -> if state == "connected": proceed silently   # ← automagic
  -> else: soft toast "kaveh-prod: login needed - press a"
                       S3 view mounts; pane shows
                       "auth needed - press a" placeholder
```

aioboto3 clients are created **eagerly** at construction time but **bind sockets lazily** on first call — cold start makes no network round-trip just to render the UI.

#### Flow 2 — Switch connection (AWS → MinIO)

```
User: : connection switch -> minio-local
  -> RootVM.SwitchConnectionCmd("minio-local")
  -> if transfers.active > 0:
        ConfirmationVM: "3 transfers will be cancelled. Continue?"
        on cancel: abort the command
  -> ContentHostVM.current.dispose()
        closes aioboto3 client for kaveh-dev
        cancels in-flight list_objects + transfers
  -> RootVM.connection = minio-local
  -> ServicesMenuVM.refresh()                    # filters via service.supports()
                                                  # menu collapses to just S3
  -> ContentHostVM.set_content(
        s3_service.build_vm(minio-local))
  -> S3FS(endpoint_url=http://localhost:9000,
          force_path_style=true,
          creds=keyring.get_password("minio-local"))
  -> StatusBarVM: "conn minio-local (s3-compat) . region us-east-1 . keys"
```

#### Flow 3 — SSO login (first connect or expired)

```
User selects connection "kaveh-prod"
  -> AwsSession.try_get_credentials("kaveh-prod")
  -> botocore raises SSOTokenLoadError
  -> infra.AwsSession publishes AuthExpiredMessage{conn="kaveh-prod"}
  -> ToastStackVM: soft toast "kaveh-prod needs sign-in. press a"
  -> connection switch held; ContentHostVM not torn down yet

User: presses a
  -> AwsSession.authenticate("kaveh-prod")
  -> shell-out: aws sso login --profile kaveh-prod
                modal: "browser opened. complete sign-in then return here."
  -> AwsSession polls ~/.aws/sso/cache/ for new token
                (1 s interval, 5 min timeout)
  -> on success: dismiss modal + toast; retry SwitchConnectionCmd
  -> on timeout / user cancel: dismiss without retry
```

We **never** implement the OIDC device flow ourselves. The aws CLI owns that; we orchestrate around it.

#### Flow 4 — SSO token expires mid-session

```
User browses bucket; list_objects returns 401
  -> S3FS.list() catches botocore.exceptions.TokenRetrievalError
  -> publishes AuthExpiredMessage{conn=current}
  -> ToastStackVM raises soft toast (same as Flow 3)
  -> failing pane shows: "auth needed - press a"
  -> other VMs untouched; chrome stays put

User authenticates -> Flow 3's retry path -> pane re-runs the failed list
```

#### Flow 5 — First run, no connections configured

```
App launch
  -> ConfigStore.load() finds no [connections.*]
  -> if ~/.aws/{config,credentials} has any profile:
        auto-create [connections.<profile>] for each, kind=aws
        toast: "imported 3 profiles from ~/.aws/config"
  -> else:
        first-run modal:
          "no AWS or S3-compatible connections configured.
            -> add aws profile  (runs 'aws configure sso' via shell-out)
            -> add s3-compatible (in-tui form)
            -> skip for now"
  -> after add: rerun ConnectionResolver.list(); pick new one
```

The first-run flow is the only place we offer an in-TUI form for adding connections. After that, users edit `config.toml` directly or use `: connection edit <name>` which opens `$EDITOR`.

#### Flow 6 — App exit

Covered in §5.4 (lifecycle). `Ctrl+C` or `q` triggers the async shutdown sequence followed by `RootVM.dispose()`.

### 6.5 Service applicability per connection kind

The `Service` protocol has `supports(connection: Connection) -> bool`. Examples:

```python
class S3Service:
    def supports(self, conn): return True            # works for any kind

class EC2Service:  # future
    def supports(self, conn): return conn.kind == "aws"
```

`ServicesMenuVM.entries` is a `DerivedProperty` filtered by `supports()`. Switching to a `kind = "s3-compatible"` connection collapses the menu to just S3 — quietly, no error toast.

### 6.6 Regions

v1 keeps region as a **per-connection field**, not switchable mid-session. Multi-region browsing (one connection, switch region in-app) is a v1.3 feature — the design accommodates it (`S3FS` already takes a region), but the UI affordance + connection re-binding is YAGNI for now.

---

## 7. Error handling & resilience

### 7.1 Principle

Trust boto3's retry layer; own only the UX surface and the crash-recovery journal. We don't reinvent backoff, partial-failure parsing, or multipart mechanics — botocore already does that.

### 7.2 Taxonomy → surface mapping

| Class | Examples | Surface |
|---|---|---|
| Auth | `TokenExpired`, `SSOTokenLoadError` | Soft toast + pane placeholder (§6.4 Flow 4) |
| Permission | `AccessDenied` on action/resource | Toast naming the action: *"s3:DeleteObject denied on data-bucket-prod"* |
| Not-found | `NoSuchKey`, `NoSuchBucket` | Inline placeholder; toast only if user-initiated open |
| Throttling | `SlowDown`, `RequestLimitExceeded` | **Silent** — boto3 adaptive retry; visible only if exhausted |
| Network | `EndpointConnectionError`, `ConnectionError` | Boto3 retries; on exhaust → toast + `r`; transfers go `paused` |
| Conflict | object exists at destination | Modal: *overwrite / skip / rename* with "apply to all" |
| Validation | bad bucket name, illegal chars | Inline form error inside the input modal |
| Vendor unsupported | R2 versioning, B2 multipart limits, MinIO self-signed TLS | Toast: *"toggle versioning not supported by cloudflare-r2"* + link to `docs/connections.md` |
| Internal (bug) | unhandled exception | Crash modal + dump at `~/.cache/aws-tui/crash/<ts>.txt` |

Rule of thumb: **the more recoverable, the lighter the surface.**

### 7.3 Boto3 client config

Every aioboto3 client we construct uses:

```python
botocore.config.Config(
    retries={"max_attempts": 6, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=60,
    s3={"addressing_style": "path" if conn.force_path_style else "auto"},
    user_agent_extra=f"aws-tui/{__version__}",
)
```

`max_attempts=6` is well above the boto3 default (3) because adaptive mode already self-throttles. If 6 attempts fail, it's not transient.

### 7.4 Large transfers

`boto3.s3.transfer.TransferConfig` (reused unchanged by aioboto3):

| Setting | Default | Why |
|---|---|---|
| `multipart_threshold` | 64 MB | Below this, single PUT |
| `multipart_chunksize` | 8 MB | Balance of parallelism vs HTTP overhead |
| `max_concurrency` | 4 per transfer | Network-bound; diminishing returns past 4 |
| `max_io_queue` | 100 | RAM pressure guard |
| `use_threads` | False | asyncio-native; no thread pool |

Global cap: **8 concurrent transfers** (configurable). `TransfersVM` enforces; excess transfers queue with state `pending`. **Status (v0.7.x):** the global cap and `pending` queue are **deferred** — `TransfersVM.register()` currently accepts every transfer without limit. The per-transfer `boto3` knobs above (multipart_chunksize, max_concurrency) are wired.

### 7.5 Per-transfer state machine

```
pending -> running -> { completed | failed | cancelled }
                  -> paused (network down) -> running (recovered)
```

`TransferVM.state` is reactive; `TransfersVM.active_count` is a `DerivedProperty`.

### 7.6 Crash-recovery journal

After each completed multipart part, the transfer state appends a line to `~/.cache/aws-tui/transfers/<id>.jsonl`:

```jsonl
{"ts":"2026-06-13T23:45:11Z","upload_id":"abc...","part":1,"etag":"\"d41d...\"","bytes":8388608}
{"ts":"2026-06-13T23:45:13Z","upload_id":"abc...","part":2,"etag":"\"098f...\"","bytes":8388608}
```

On launch, aws-tui scans the directory. Any journal without a closing `{"event":"completed"}` or `{"event":"aborted"}` line is offered for resume:

```
Modal: "2 transfers from a previous session were not finished.
        - api-2026-06-13.json  (3.4 M / 4.2 M, 82%)
        - db-slowq-06-13.csv   (279 k / 892 k, 31%)
        [resume all] [abort all] [decide each] [keep journal for later]"
```

This early sketch predates the shipped v0.7 modal contract. The live
implementation and tests are authoritative: startup scans pending
journals, presents resume decisions through the transfer modal, keeps
`decide each` intentionally deferred, and preserves journal files when
abort cleanup cannot safely acquire or enter the S3 client. "Abort all"
calls `AbortMultipartUpload` per `upload_id` and deletes only journals
whose remote abort path completes. `docs/connections.md` recommends
users set a **1-day MPU abort lifecycle rule** on their buckets as a
backstop for any orphaned MPUs.

### 7.7 Inline pane placeholders

`PaneVM.state` is a `DerivedProperty` over the last operation's result:

| `state` | Pane renders |
|---|---|
| `idle` | the entry list (normal) |
| `loading` | `loading...` + spinner (after 200 ms — fast listings don't flicker; **v0.7.x:** spinner appears immediately, debounce deferred) |
| `empty` | `empty bucket` / `empty folder` |
| `auth_required` | `auth needed - press a to sign in` |
| `forbidden` | `access denied to <bucket>/<prefix>` + `: connection switch` hint |
| `unreachable` | `<endpoint> unreachable - press r to retry` |
| `error` | first line of error + `?` opens details modal |

### 7.8 Connectivity watcher

One asyncio task pings the current connection's endpoint every 30 s **when transfers are `paused`**. On 3 consecutive successes, resume paused transfers. On failure during normal ops, do nothing — boto3 will surface the next user-triggered call as `unreachable`.

### 7.9 Logging

| Path | Contents | Rotation |
|---|---|---|
| `~/.cache/aws-tui/log/aws-tui.log` | JSON lines (`ts`, `level`, `vm`, `event`, `correlation_id`, `details`) | 5 MB × 5 |
| `~/.cache/aws-tui/log/boto.log` | DEBUG-level botocore wire log (only with `--debug`) | 5 MB × 2 |
| `~/.cache/aws-tui/crash/<ts>.txt` | full traceback + last 1000 log lines + last 100 user actions | manual |
| `~/.cache/aws-tui/transfers/<id>.jsonl` | per-transfer journal | manual / on-completion |

`: log show` opens the active log in `$PAGER`. `: log level debug` toggles at runtime. No PII in logs at INFO; bucket/key names are logged (not secrets per AWS docs), but request bodies + response payloads are never logged.

### 7.10 Crash modal

```
unexpected error
  TypeError: ...
  
  ~/.cache/aws-tui/crash/2026-06-13T23-50-22.txt
  
  [view trace]  [continue]  [quit]
```

`continue` tries to recover (drop the offending action, restore VM state to before it); some error classes make `continue` unsafe and we disable that button.

> **Deferred from v0.1.0:** the original spec listed a fourth
> `[open issue with details (browser)]` button. v0.7.0 ships with the
> three buttons above; the issue-opener (which would `webbrowser.open` a
> pre-filled GitHub new-issue URL with the dump excerpt) is deferred
> until the project moves the repo out of pre-release. See
> `ui/widgets/crash_modal.py` for the current button set.

### 7.11 Non-goals

- Offline mode
- Multi-region browsing inside a connection
- Tag-based filtering
- Drag-and-drop to system clipboard
- Bandwidth throttling

---

## 8. Testing strategy

### 8.1 Pyramid

| Tier | Share | Speed | Tools | What it proves |
|---|---|---|---|---|
| Unit | ~70% | <100 ms | pytest, pytest-asyncio, fake providers | VM behavior, domain logic, infra parsing — no I/O |
| Integration | ~20% | <2 s | moto, testcontainers (MinIO), `tmp_path` | Providers against real-ish backends, including SigV4 + multipart + vendor quirks |
| Snapshot | ~5% | <500 ms | pytest-textual-snapshot | View rendering against golden SVGs per theme |
| E2E smoke | ~5% | <30 s total | `App.run_test()` (Pilot), moto + MinIO | Five key user journeys |

CI target: **unit + snapshot < 30 s, integration < 2 min, e2e < 1 min** — sub-3-minute total.

### 8.2 Unit tier — fake providers, not mocks

VM tests get an `InMemoryFS` (real implementation backed by a dict) — not `Mock(spec=FileSystemProvider)`. Fakes have predictable behaviors and let you write `populate(provider, structure)`-style fixtures; mocks invite false positives.

VM tests **never import** `aioboto3`, `boto3`, `botocore`, or `Textual`. Enforced by `ruff` import rules.

### 8.3 Integration tier — real-ish backends

| Backend | Tool | Scope |
|---|---|---|
| AWS S3 | `moto` (in-process) | `S3FS` happy paths + every error class in §7 |
| AWS S3 multipart | `moto-server` subprocess | concurrent part uploads |
| MinIO | `testcontainers-python` with `minio/minio:RELEASE.*` | `S3FS` against real S3-compat; catches path-style + TLS quirks |
| Local FS | `tmp_path` | `LocalFS` ops + symlinks + perm errors |
| Cross-FS copy | combinations | LocalFS↔S3FS, S3FS↔S3FS byte-equality + ETag check |

Container fixtures are session-scoped (one container per session, ~3 s startup amortized). Each test gets a fresh ephemeral bucket name.

### 8.4 Snapshot tier — Textual views

```python
def test_main_screen_carbon(snap_compare):
    assert snap_compare("snapshots/main-screen-carbon.svg", terminal_size=(120, 40))
```

Covers (per theme):

- Main screen, focused-left + focused-right
- Each modal (command palette, confirm, quick look, transfers tray, connection switcher, first-run, crash)
- Each pane state from §7 (`loading / empty / auth_required / forbidden / unreachable / error`)

Goldens under `tests/snapshot/snapshots/<theme>/`. Updates via `pytest --snapshot-update` are explicit — PR reviewer sees the diff in the GitHub UI.

### 8.5 E2E smoke — five journeys

1. **First launch with cached SSO → silent S3 view** (§6.4 Flow 1)
2. **Copy one object from S3 to local** (cursor → `c` → wait for `TransferVM.state == completed` → byte-check file)
3. **Switch AWS → MinIO mid-session with confirmation of in-flight transfer cancellation** (§6.4 Flow 2 + §7 cancel)
4. **Resume a transfer from journal after simulated crash** (write a journal manually, launch, assert resume modal)
5. **Delete with confirm → cancel → no AWS call made** (regression-class destructive path)

### 8.6 VMx contract tests

For each capability we adopt, run VMx's conformance fixture:

```python
from vmx.testing.conformance import selectable_contract, filterable_contract, pageable_contract

@pytest.mark.parametrize("contract", [selectable_contract, filterable_contract, pageable_contract])
def test_pane_vm_satisfies_capability(contract, pane_vm_factory):
    contract.run(pane_vm_factory)
```

If a VMx upgrade tightens `ISelectable` and we missed something, CI fails after the PyPI version bump.

### 8.7 aws-tui-specific lifecycle suite

| Assertion | Catches |
|---|---|
| `RootVM.dispose()` calls `dispose` on every child exactly once | leaked subtree, double-dispose |
| `ContentHostVM.set_content(B)` disposes A before A's tasks see B | teardown race |
| `aws_session.aclose_all_clients()` runs before `RootVM.dispose()` | order regression that would leak sockets |
| `service.supports(connection)` consulted on every menu render | regression if someone adds a service that crashes on MinIO |

### 8.8 Test infrastructure

| Concern | Choice |
|---|---|
| Runner | pytest + pytest-asyncio (`asyncio_mode = "auto"`) |
| Async time | `asyncio.sleep(0)` to drain microtasks; `freezegun` for wall-clock |
| Parallelism | pytest-xdist on unit tier only |
| Factories | factory_boy for `FileEntry`, `ServiceDescriptor`, `Connection`, `TransferState` |
| Coverage | pytest-cov, branch coverage |
| Lint | ruff (incl. tidy-imports for layer rules) |
| Type-check | mypy --strict on `src/aws_tui/` |
| Pre-commit | ruff + mypy + taplo + EOL-fixer |

### 8.9 CI matrix

| Job | OS | Python | Tier |
|---|---|---|---|
| `unit` | macos-14, ubuntu-22.04 | 3.11, 3.12, 3.13 | unit + snapshot |
| `integration` | ubuntu-22.04 | 3.12 only | integration |
| `e2e` | ubuntu-22.04 | 3.12 only | e2e |
| `lint+type` | ubuntu-22.04 | 3.12 only | ruff + mypy |
| `pkg` | ubuntu-22.04 | 3.12 only | `uv build` + `twine check` |
| `windows-smoke` | windows-2022 | 3.12 | unit only, non-blocking |

### 8.10 Coverage targets

- `vm/` and `domain/`: ≥90% branch
- `infra/`: ≥85% branch
- `ui/widgets/`: not targeted — snapshot tests cover rendering
- `services/`: ≥80% branch

We do not enforce 100% — chasing the last 10% encourages tests that exercise lines without verifying behavior.

### 8.11 Non-goals for v1 tests

- Property-based / hypothesis testing (v1.1 candidate)
- 100k-object bucket load tests (correctness is unit-tested; perf is v1.1)
- Vendor matrix beyond moto + MinIO (R2/B2/Wasabi manual checklists in `docs/connections.md`)

---

## 9. Distribution, CI release-side, repo bootstrap, milestones

### 9.1 Repo identity

| Field | Value |
|---|---|
| GitHub repo | `thekaveh/aws-tui` (public) |
| Description | *"Sleek macOS-tailored TUI for AWS and S3-compatible services. Powered by Textual + VMx MVVM."* |
| License | MIT |
| Topics | `aws`, `tui`, `s3`, `minio`, `textual`, `mvvm`, `python`, `macos` |
| Default branch | `main` |
| Branch protection | CI green + 1 reviewer required |

### 9.2 Distribution channels

| Channel | Status | When |
|---|---|---|
| `pipx install git+https://github.com/thekaveh/aws-tui` | primary, day 1 | from v0.1.0 |
| `pipx install aws-tui` (PyPI) | blocked on VMx PyPI publication | v1.0.0 |
| Homebrew tap | planned | v1.1+ |
| `nix profile install` | community contribution welcome | not us |
| Standalone binaries (PyInstaller / Briefcase) | rejected | bundling Textual breaks `.tcss` overrides, +30 MB |

### 9.3 PyPI release path

VMx now ships on PyPI (consumed as `vmx>=2.6.0,<3.0.0` — see the 2026-06-17 migration plan). aws-tui's own first PyPI release is the remaining gate; until that lands, `pipx install git+...` is the recommended install. The README install command swaps in one line when ready.

### 9.4 Versioning

- **SemVer.** `0.x` = pre-stable; `1.0` = PyPI release + API stability on commands & config schema.
- **VMx PyPI pin** lives in `pyproject.toml` `[project].dependencies`. Bumps are explicit PRs that adjust the version range and run the existing test suite (no separate "conformance suite" PR — the published VMx wheel is the contract).
- **Changelog** auto-generated from conventional commits via `git-cliff`. CI fails on PRs into `main` if a commit message doesn't match `<type>(<scope>): <subject>`.

### 9.5 CI workflows (release-side; test matrix in §8.9)

| Workflow | Trigger | Does | v0.7.x status |
|---|---|---|---|
| `ci.yml` | PR, push to `main` | matrix from §8.9 | shipped |
| `release.yml` | tag push (`v*`) | build wheel + sdist; `twine check`; GitHub release w/ changelog; upload to PyPI when configured | deferred (PyPI release blocked on VMx) |
| `snapshot-drift.yml` | nightly cron | snapshot tests on `main` to detect rendering drift; opens issue on diff | deferred |
| `submodule-bump.yml` | weekly cron | checks for new VMx tags; opens PR bumping the pin | deferred |
| `codeql.yml` | weekly + PR | security scanning | deferred |
| `dependabot.yml` | continuous | dep updates grouped weekly | shipped (`.github/dependabot.yml`) |

### 9.6 Repo bootstrap — first commits after spec sign-off

```
01. gh repo create thekaveh/aws-tui --public --license MIT --description "..."
02. cd /Users/kaveh/repos/aws-tui && git init && set upstream
03. add: .gitignore (Python + macOS), .editorconfig, .gitattributes,
         LICENSE (MIT), CHANGELOG.md, CODE_OF_CONDUCT.md, SECURITY.md
04. (Historical: M0 added vendor/vmx as a submodule pinned to
    python-v2.4.0. The 2026-06-17 vmx→PyPI migration removed it;
    aws-tui now consumes vmx>=2.6.0,<3.0.0 from PyPI. See
    docs/superpowers/plans/2026-06-17-vmx-pypi-migration.md.)
05. add: pyproject.toml (PEP 621, hatchling, py>=3.11), uv.lock
         (committed)
06. add: src/aws_tui/{__init__.py, __main__.py, version.py}
07. add: README.md (skeleton with install + quickstart)
08. add: docs/{architecture,keybindings,theming,connections,
         adding-a-service}.md (skeletons)
09. add: docs/superpowers/specs/2026-06-13-aws-tui-design.md  # this doc
10. add: .pre-commit-config.yaml
11. add: .github/workflows/ci.yml (matrix from §8.9, empty test suite passes)
12. add: .github/{ISSUE_TEMPLATE/*, pull_request_template.md, dependabot.yml}
13. add: scripts/{bootstrap.sh, dev.sh}
14. add: empty stubs under src/aws_tui/{infra,domain,vm,services,ui}/
         (each with __init__.py + py.typed marker)
15. Hello-world Textual app: src/aws_tui/app.py renders
       "aws-tui v0.0.1 - service menu placeholder" + exits on q
16. tests/conftest.py + sanity test that imports the app
17. git commit, push, verify CI green
```

This is M0 below.

### 9.7 Milestones (solo dev, calendar-time, ~50% buffer)

| # | Name | Duration | Deliverable |
|---|---|---|---|
| M0 | Bootstrap | 1-2 d | Public repo, scaffolding, hello-world Textual app, CI green |
| M1 | Infrastructure | 1 wk | `ConfigStore`, `ConnectionResolver` (auto-discovery), `AwsSession` (SSO cache probe), `ThemeStore`, `KeymapStore`, `LogSink` + unit tests |
| M2 | Domain | 1 wk | `FileSystemProvider`, `LocalFS`, `S3FS`, `CrossFsCopy/Move`, transfer journal + unit/integration tests (moto + MinIO) |
| M3 | VM layer — shell | 1.5 wk | `RootVM`, `ServicesMenuVM`, `ContentHostVM`, `ChromeVM`, `HintLegendVM`, `StatusBarVM`, `ToastStackVM`, `CommandPaletteVM`, `ConfirmationVM`, `QuickLookVM`, connection/theme switch cmds |
| M4 | VM layer — file mgr + S3 | 1.5 wk | `DualPaneVM`, `PaneVM`, `EntryVM`, `S3Service` + contract tests |
| M5 | UI layer + themes | 2 wk | All Textual widgets; Carbon `.tcss` (default); Voidline/Lattice/Amber; snapshot tests; E2E smoke tests |
| M6 | Polish & v0.1 release | 1 wk | Crash modal, transfer resume, first-run flow, full README, asciinema, `git tag v0.1.0` |

**Total: ~8 weeks** of focused solo work. First release: **v0.1.0 via `pipx install git+...`** at the end of M6.

### 9.8 Post-v0.1 roadmap (not v1)

| Version | Theme | Highlights |
|---|---|---|
| v0.2 | Second AWS service | EC2 or IAM via the plugin spine — proves the extension story |
| v0.3 | Preview upgrades | Syntax-highlit Quick Look, hexview for binary |
| v0.4 | Session restore | Preserve pane paths + selected entries across launches |
| v1.0 | PyPI release | aws-tui's own first PyPI publish (VMx PyPI gate lifted 2026-06-17); SemVer stability on CLI + config schema |
| v1.1 | Plugin discovery | `aws_tui.services` entry-point group — 3rd-party packages add services |
| v1.2 | Homebrew tap | `brew install thekaveh/tap/aws-tui` |
| v1.3 | Multi-region browsing | Region switch inside a connection |
| v2.0 | Linux hardened, Windows formal | All three platforms tier-1 in CI |

---

## 10. Decision log

Concise list of key decisions and rationale, for future readers.

| # | Decision | Rationale |
|---|---|---|
| 1 | Python + Textual + boto3 | Textual is the most polished TUI lib today; VMx's Python flavor names Textual as a supported UI layer; boto3 is the most complete AWS SDK |
| 2 | VMx as git submodule, not PyPI dep | VMx is not yet on PyPI; submodule + path dep until it publishes; one-line swap to `vmx>=2.5,<3` when ready |
| 3 | Delegate fully to AWS CLI for auth | We never own credential acquisition; boto3's credential chain + `aws sso login` shell-out covers every flow; smaller surface, no secrets in our code |
| 4 | macOS-tailored keymap, no F-keys | F-keys are dated; modern macOS dev tools (Helix, Zed, Lazygit) use letter-driven + `:` palette; fully customizable via config |
| 5 | Built-in themes + user `.tcss` overrides | Textual's CSS-like theming is essentially free; user themes drop into `~/.config/aws-tui/themes/` and select like built-ins |
| 6 | Service-plugin spine, in-tree registry | Adding a service later is additive (new folder + one line); promote to entry-point discovery in v1.1 — B is a strict subset of C |
| 7 | First-class S3-compatible support | MinIO/R2/B2/Wasabi all just need `endpoint_url` + creds + `force_path_style`; one code path serves all |
| 8 | Carbon as default theme | Near-monochrome with one accent — easiest on the eyes for long sessions; all four ship and the default is configurable |
| 9 | Auto-discover AWS profiles every launch | Add a new SSO profile via `aws configure sso`, restart aws-tui, it appears — no config edits |
| 10 | Silent SSO when cache is valid | Cheap probe (one `os.stat` + small JSON read) → proceed to S3 view; no toast/modal friction when nothing is wrong |
| 11 | `dispose()` not `reconstruct()` for service switch | Child swap is cleaner; `reconstruct()` reserved for future "reset workspace" |
| 12 | Trust boto3 retry; own only UX surface + crash journal | Don't reinvent backoff; do own how errors look and what survives a restart |
| 13 | Fake providers (not mocks) for unit tests | Mocks invite false positives; `InMemoryFS` is a real impl backed by a dict |
| 14 | `pipx install git+...` for v0.1; PyPI for v1.0 | Path-dep wheels won't work on PyPI; wait for VMx |
| 15 | SVG mockups, not `<pre>` | Browser `<pre>` rendering depends on font glyph widths; SVG positions each text element independently |

---

## 11. Glossary

| Term | Definition |
|---|---|
| **Connection** | aws-tui's unit of authentication. Kind = `aws` or `s3-compatible`. Holds endpoint, region, credential source |
| **Service** | A top-level AWS (or S3-compat) capability surfaced in the left menu (S3, EC2, ...). Each service is a folder under `services/` |
| **Service registry** | In-tree map of `id → Service` populated in `services/__init__.py`. The menu is rendered from this |
| **FileSystemProvider** | Protocol implemented by `LocalFS` and `S3FS` that abstracts list/stat/read/write/etc. Both panes consume one |
| **Pane** | One side of the dual-pane file manager. Holds a `provider`, current `path`, cursor, selection, filter state |
| **VMx** | Hierarchical lifecycle-aware MVVM viewmodel framework. Drives the VM layer; provides `RelayCommand`, `ComponentVM`, `CompositeVM`, capabilities |
| **Capability** | A VMx micro-interface (`ISelectable`, `IFilterable`, `IPageable`, ...) that a VM opts into |
| **DerivedProperty** | VMx primitive for computed values that auto-recompute when their sources change |
| **Hint legend** | The dim row at the bottom of the screen showing key bindings relevant to the focused widget |
| **Connection probe** | Cheap freshness check on the SSO cache file — returns `connected | expired | missing` without calling AWS |
| **Transfer journal** | Per-transfer `.jsonl` file in `~/.cache/aws-tui/transfers/` that lets us resume across crashes |
| **Quick Look** | macOS-borrowed term for the modal preview opened by `Space` |

---

*End of spec. Implementation plan to follow via the writing-plans skill.*
